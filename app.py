import streamlit as st
import pandas as pd
import requests
import asyncio
import websockets
import json
import time
import threading

st.set_page_config(page_title="NXT 실시간 주가 대시보드", layout="wide")

st.markdown("""
    <style>
    @media (max-width: 768px) {
        .block-container { padding-top: 3.4rem !important; }
        h1 { font-size: 22px !important; padding-top: 0rem !important; padding-bottom: 0.4rem !important; }
        [data-testid="stVerticalBlock"] { gap: 0.2rem !important; }
        [data-testid="stMetric"] { margin-bottom: -5px !important; }
    }
    </style>
""", unsafe_allow_html=True)

st.title("📈NXT 실시간 대시보드 (Websocket)")

# --- [보안] KIS API 키 ---
try:
    APP_KEY = st.secrets["kis"]["app_key"]
    APP_SECRET = st.secrets["kis"]["app_secret"]
except:
    st.error("API 키가 설정되지 않았습니다. .streamlit/secrets.toml 파일을 확인해주세요.")
    st.stop()

URL_BASE = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000"

@st.cache_data(ttl=3600*20)
def get_approval_key():
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "secretkey": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/Approval", headers=headers, json=body)
    return res.json().get("approval_key") if res.status_code == 200 else None

# --- 데이터 로드 (예외 처리 포함) ---
try:
    df = pd.read_excel("지겹다_완성.xlsx", sheet_name=0)
except FileNotFoundError:
    st.error("엑셀 파일을 찾을 수 없습니다. 경로를 확인해주세요.")
    st.stop()

valid_stocks = []
for idx, row in df.iterrows():
    if pd.notna(row.iloc[3]) and str(row.iloc[3]) != "검색불가":
        valid_stocks.append({
            "name": str(row.iloc[2]),
            "ticker": str(row.iloc[3]).zfill(6),
            "marcap": float(row.iloc[4]) if df.shape[1] > 4 and pd.notna(row.iloc[4]) else 1
        })

if 'price_data' not in st.session_state:
    st.session_state.price_data = {s['ticker']: {"price": 0, "diff": "-", "prev": 0} for s in valid_stocks}

# --- 웹소켓 수신 함수 (백그라운드 실행용) ---
async def nxt_websocket_handler(approval_key):
    try:
        async with websockets.connect(WS_URL, ping_interval=None) as ws:
            # 구독 요청 전송
            for stock in valid_stocks:
                send_data = {
                    "header": {
                        "approval_key": approval_key,
                        "custtype": "P",
                        "tr_type": "1", 
                        "content-type": "utf-8"
                    },
                    "body": {
                        "input": {
                            "tr_id": "H0NXSTC0", 
                            "tr_key": stock['ticker']
                        }
                    }
                }
                await ws.send(json.dumps(send_data))
                await asyncio.sleep(0.1) 

            # 실시간 데이터 수신 루프
            while True:
                data = await ws.recv()
                if data[0] in ['0', '1']: 
                    parts = data.split('|')
                    content = parts[-1].split('^')
                    if len(content) > 4: # 데이터 길이 검증
                        ticker = parts[3]
                        current_price = int(content[2])
                        diff = int(content[4])
                        sign = content[3]
                        
                        diff_prefix = "▲" if sign in ['1', '2'] else "▼" if sign in ['4', '5'] else ""
                        prev_price = current_price - diff if sign in ['1', '2'] else current_price + diff if sign in ['4', '5'] else current_price
                        
                        # 세션 상태 업데이트 (스레드 안전성 확보를 위해 딕셔너리 재할당)
                        st.session_state.price_data[ticker] = {
                            "price": current_price,
                            "diff": f"{diff_prefix} {diff:,}",
                            "prev": prev_price
                        }
    except Exception as e:
        print(f"Websocket connection error: {e}")

# 비동기 루프를 실행할 래퍼 함수
def run_asyncio_loop(approval_key):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(nxt_websocket_handler(approval_key))

# --- 백그라운드 스레드 시작 ---
approval_key = get_approval_key()

if approval_key:
    if 'ws_thread' not in st.session_state:
        # 데몬 스레드로 실행하여 메인 프로세스 종료 시 함께 종료되도록 설정
        t = threading.Thread(target=run_asyncio_loop, args=(approval_key,), daemon=True)
        t.start()
        st.session_state.ws_thread = True

    # --- UI 렌더링 ---
    st.markdown("<hr style='margin: 5px 0px; border: 1px solid #ddd;'>", unsafe_allow_html=True)
    
    base_total = 0
    current_total = 0
    display_list = []

    # 세션 상태 데이터를 기반으로 UI 구성
    for s in valid_stocks:
        info = st.session_state.price_data.get(s['ticker'], {"price": 0, "diff": "-", "prev": 0})
        p = info['price']
        prev_p = info['prev']
        m = s['marcap']
        
        if prev_p > 0:
            base_total += m
            current_total += m * (p / prev_p if p > 0 else 1)
        
        display_list.append({
            "종목명": s['name'],
            "종목코드": s['ticker'],
            "현재가(NXT)": f"{p:,}" if p > 0 else "대기 중",
            "전일대비": info['diff']
        })

    if base_total > 0:
        nxt_index = (current_total / base_total) * 1000
        index_diff = nxt_index - 1000
        index_pct = (index_diff / 1000) * 100
    else:
        nxt_index, index_diff, index_pct = 1000, 0, 0

    st.metric(label="🚀 커스텀 NXT 지수 (Base: 1000 pt)", 
                value=f"{nxt_index:,.2f} pt", 
                delta=f"{index_diff:+,.2f} pt ({index_pct:+.2f}%)")

    st.dataframe(pd.DataFrame(display_list), use_container_width=True)

    # 1초 대기 후 화면 자동 새로고침 (Streamlit 권장 방식)
    time.sleep(1)
    st.rerun()
else:
    st.error("웹소켓 접속용 승인 키 발급에 실패했습니다.")
