import streamlit as st
import pandas as pd
import requests
import asyncio
import websockets
import json
import time
import threading

# ==========================================
# [1. 라이브러리 임포트 및 페이지 기본 설정]
# ==========================================
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


# ==========================================
# [2. 보안 및 API 설정]
# ==========================================
try:
    APP_KEY = st.secrets["kis"]["app_key"]
    APP_SECRET = st.secrets["kis"]["app_secret"]
except Exception as e:
    st.error("API 키가 설정되지 않았습니다. .streamlit/secrets.toml 파일을 확인해주세요.")
    st.stop()

URL_BASE = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000"

@st.cache_data(ttl=3600*20)
def get_approval_key():
    """한국투자증권 웹소켓 접속용 승인키(Approval Key) 발급"""
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "secretkey": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/Approval", headers=headers, json=body)
    return res.json().get("approval_key") if res.status_code == 200 else None


# ==========================================
# [3. 데이터 로드 (엑셀 파일)]
# ==========================================
# ⭐ 주의: 전역 상태 저장소를 만들기 전에 반드시 엑셀 데이터가 먼저 로드되어야 합니다.
try:
    df = pd.read_excel("지겹다_완성.xlsx", sheet_name=0)
except FileNotFoundError:
    st.error("엑셀 파일을 찾을 수 없습니다. '지겹다_완성.xlsx' 파일이 같은 폴더에 있는지 확인해주세요.")
    st.stop()

valid_stocks = []
for idx, row in df.iterrows():
    if pd.notna(row.iloc[3]) and str(row.iloc[3]) != "검색불가":
        valid_stocks.append({
            "name": str(row.iloc[2]),
            "ticker": str(row.iloc[3]).zfill(6),
            "marcap": float(row.iloc[4]) if df.shape[1] > 4 and pd.notna(row.iloc[4]) else 1
        })


# ==========================================
# [4. 전역 상태 관리 (캐시 활용)]
# ==========================================
@st.cache_resource
def get_shared_state():
    """
    백그라운드 스레드(웹소켓)와 메인 스레드(Streamlit UI)가 데이터를 주고받기 위한 공용 저장소.
    엑셀에서 로드한 valid_stocks 정보를 바탕으로 초기화됩니다.
    """
    return {
        "ws_status": "연결 대기 중...",  # UI 상단에 표시될 웹소켓 현재 상태
        "prices": {s['ticker']: {"price": 0, "diff": "-", "prev": 0} for s in valid_stocks}
    }

# 캐시된 상태 저장소 불러오기
shared_state = get_shared_state()


# ==========================================
# [5. 웹소켓 비동기 처리 함수]
# ==========================================
async def nxt_websocket_handler(approval_key):
    try:
        shared_state["ws_status"] = "🔄 서버 연결 시도 중..."
        async with websockets.connect(WS_URL, ping_interval=60) as ws:
            shared_state["ws_status"] = "✅ 서버 연결 성공, 구독 요청 중..."
            
            # 1. 구독(Subscribe) 요청 전송
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
                await asyncio.sleep(0.1) # 과부하 방지
                
            shared_state["ws_status"] = "🟢 데이터 수신 중..."

            # 2. 실시간 데이터 수신 루프
            while True:
                data = await ws.recv()
                
                # [JSON 메시지 처리] PINGPONG 또는 에러/성공 메시지
                if data.startswith('{'):
                    parsed = json.loads(data)
                    tr_id = parsed.get("header", {}).get("tr_id", "")
                    
                    if tr_id == "PINGPONG":
                        continue # 핑퐁 연결 유지 메시지는 무시
                        
                    msg = parsed.get("body", {}).get("msg1", "")
                    if "ALREADY IN USE" in msg:
                        shared_state["ws_status"] = "❌ 중복 접속 에러 (모든 창을 닫고 5분 뒤 다시 실행하세요)"
                        break # 루프 강제 종료
                    elif "SUBSCRIBE SUCCESS" in msg:
                        continue # 구독 성공 메시지 통과
                    continue
                    
                # [실제 체결가 파싱] 데이터가 '0' 또는 '1'로 시작할 때
                if data[0] in ['0', '1']: 
                    parts = data.split('|')
                    content = parts[-1].split('^')
                    if len(content) > 4:
                        ticker = parts[3]
                        current_price = int(content[2])
                        diff = int(content[4])
                        sign = content[3]
                        
                        # 전일대비 부호 설정 (1,2: 상승 / 4,5: 하락 / 3: 보합)
                        diff_prefix = "▲" if sign in ['1', '2'] else "▼" if sign in ['4', '5'] else ""
                        
                        # 전일가 계산 (현재가와 대비 가격을 역산)
                        prev_price = current_price - diff if sign in ['1', '2'] else current_price + diff if sign in ['4', '5'] else current_price
                        
                        # 전역 저장소에 최신가 업데이트
                        shared_state["prices"][ticker] = {
                            "price": current_price,
                            "diff": f"{diff_prefix} {diff:,}",
                            "prev": prev_price
                        }
                        
    except Exception as e:
        shared_state["ws_status"] = f"⚠️ 웹소켓 종료/에러 발생: {e}"


# ==========================================
# [6. 비동기 루프 실행 래퍼 함수]
# ==========================================
def run_asyncio_loop(approval_key):
    """백그라운드 스레드에서 비동기 웹소켓 함수를 돌리기 위한 래퍼(Wrapper)"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(nxt_websocket_handler(approval_key))


# ==========================================
# [7. 메인 UI 렌더링 및 루프]
# ==========================================
approval_key = get_approval_key()

if approval_key:
    # 최초 1회만 백그라운드 웹소켓 스레드 실행
    if 'ws_thread_started' not in st.session_state:
        t = threading.Thread(target=run_asyncio_loop, args=(approval_key,), daemon=True)
        t.start()
        st.session_state.ws_thread_started = True

    # 1. 화면 상단에 현재 웹소켓 연결 상태 표시
    st.caption(f"**웹소켓 상태:** {shared_state['ws_status']}")
    st.markdown("<hr style='margin: 5px 0px; border: 1px solid #ddd;'>", unsafe_allow_html=True)
    
    base_total = 0
    current_total = 0
    display_list = []

    # 2. 전역 상태 저장소(shared_state)에서 최신 데이터를 읽어와서 UI 구성
    for s in valid_stocks:
        info = shared_state["prices"].get(s['ticker'], {"price": 0, "diff": "-", "prev": 0})
        p = info['price']
        prev_p = info['prev']
        m = s['marcap']
        
        # 지수 계산용 합산 로직
        if prev_p > 0:
            base_total += m
            current_total += m * (p / prev_p if p > 0 else 1)
        
        # 표(DataFrame)에 들어갈 데이터 구성
        display_list.append({
            "종목명": s['name'],
            "종목코드": s['ticker'],
            "현재가(NXT)": f"{p:,}" if p > 0 else "대기 중",
            "전일대비": info['diff']
        })

    # 3. NXT 지수 계산 및 출력
    if base_total > 0:
        nxt_index = (current_total / base_total) * 1000
        index_diff = nxt_index - 1000
        index_pct = (index_diff / 1000) * 100
    else:
        nxt_index, index_diff, index_pct = 1000, 0, 0

    st.metric(label="🚀 커스텀 NXT 지수 (Base: 1000 pt)", 
              value=f"{nxt_index:,.2f} pt", 
              delta=f"{index_diff:+,.2f} pt ({index_pct:+.2f}%)")

    # 4. 종목별 현재가 데이터프레임 출력
    st.dataframe(pd.DataFrame(display_list), width='stretch')

    # 5. 1초 대기 후 화면 자동 새로고침 (Rerun)
    time.sleep(1)
    st.rerun()

else:
    st.error("웹소켓 접속용 승인 키(Approval Key) 발급에 실패했습니다. API 키가 유효한지 확인해주세요.")
