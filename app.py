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

# ------------------------------------------
# [2-1. REST API용 Access Token 발급 추가]
# ------------------------------------------
@st.cache_data(ttl=3600*20)
def get_access_token():
    """초기 종가 데이터를 불러오기 위한 REST API용 토큰 발급"""
    headers = {"content-type": "application/json"}
    # 주의: 토큰 발급은 secretkey가 아니라 appsecret이라는 파라미터명을 사용합니다.
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, json=body)
    return res.json().get("access_token") if res.status_code == 200 else None


# ==========================================
# [3. 데이터 로드 (엑셀 파일)]
# ==========================================
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
    return {
        "ws_status": "연결 대기 중...",  
        "prices": {s['ticker']: {"price": 0, "diff": "-", "prev": 0} for s in valid_stocks}
    }

shared_state = get_shared_state()


# ------------------------------------------
# [4-1. 초기 종가 세팅 함수 추가 (REST API)]
# ------------------------------------------
def fetch_initial_prices(token):
    """앱 시작 시 등록된 종목들의 마지막 종가를 REST API로 1회 조회하여 채워 넣습니다."""
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100" # 주식현재가(시세) 조회 TR 코드
    }
    
    for stock in valid_stocks:
        # 이미 웹소켓이나 이전 캐시로 가격이 들어왔다면 건너뜀
        if shared_state["prices"][stock['ticker']]['price'] > 0:
            continue
            
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock['ticker']
        }
        res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-price", headers=headers, params=params)
        
        if res.status_code == 200:
            data = res.json().get("output", {})
            if data:
                current_price = int(data.get("stck_prpr", 0))
                diff = int(data.get("prdy_vrss", 0))
                sign = data.get("prdy_vrss_sign", "3")
                
                diff_prefix = "▲" if sign in ['1', '2'] else "▼" if sign in ['4', '5'] else ""
                prev_price = current_price - diff if sign in ['1', '2'] else current_price + diff if sign in ['4', '5'] else current_price
                
                shared_state["prices"][stock['ticker']] = {
                    "price": current_price,
                    "diff": f"{diff_prefix} {diff:,}" if diff != 0 else "0",
                    "prev": prev_price
                }
        # KIS API 초당 요청 제한(TPS)을 피하기 위해 약간의 딜레이 추가
        time.sleep(0.05)


# ==========================================
# [5. 웹소켓 비동기 처리 함수] (수정: ping_interval 해제 및 자동 재연결)
# ==========================================
async def nxt_websocket_handler(approval_key):
    # 🔁 서버가 끊어버려도 다시 연결을 시도하는 무한 루프를 겉에 씌웁니다.
    while True: 
        try:
            shared_state["ws_status"] = "🔄 서버 연결 시도 중..."
            
            # ⭐ 핵심: ping_interval=None 으로 설정하여 라이브러리 충돌을 막습니다.
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                shared_state["ws_status"] = "✅ 서버 연결 성공, 구독 요청 중..."
                
                for stock in valid_stocks:
                    send_data = {
                        "header": {"approval_key": approval_key, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
                        "body": {"input": {"tr_id": "H0NXSTC0", "tr_key": stock['ticker']}}
                    }
                    await ws.send(json.dumps(send_data))
                    await asyncio.sleep(0.1) 
                    
                shared_state["ws_status"] = "🟢 데이터 수신 중..."

                while True:
                    data = await ws.recv()
                    
                    if data.startswith('{'):
                        parsed = json.loads(data)
                        tr_id = parsed.get("header", {}).get("tr_id", "")
                        if tr_id == "PINGPONG": continue 
                        msg = parsed.get("body", {}).get("msg1", "")
                        
                        # 중복 접속일 때만 아예 무한 루프를 탈출(완전 종료)합니다.
                        if "ALREADY IN USE" in msg:
                            shared_state["ws_status"] = "❌ 중복 접속 에러 (모든 창 닫고 5분 대기)"
                            return 
                        continue
                        
                    if data[0] in ['0', '1']: 
                        parts = data.split('|')
                        content = parts[-1].split('^')
                        if len(content) > 4:
                            ticker = parts[3]
                            current_price = int(content[2])
                            diff = int(content[4])
                            sign = content[3]
                            
                            diff_prefix = "▲" if sign in ['1', '2'] else "▼" if sign in ['4', '5'] else ""
                            prev_price = current_price - diff if sign in ['1', '2'] else current_price + diff if sign in ['4', '5'] else current_price
                            
                            shared_state["prices"][ticker] = {
                                "price": current_price,
                                "diff": f"{diff_prefix} {diff:,}",
                                "prev": prev_price
                            }
                            
        # ⚠️ 전화가 뚝 끊기면 당황하지 않고 3초 쉬었다가 윗부분(while True)으로 돌아가 재연결!
        except websockets.exceptions.ConnectionClosedError:
            shared_state["ws_status"] = "⚠️ 서버 끊김 (3초 뒤 자동 재연결...)"
            await asyncio.sleep(3)
        except Exception as e:
            shared_state["ws_status"] = f"⚠️ 웹소켓 에러 발생 (3초 뒤 재연결...): {e}"
            await asyncio.sleep(3)

# ==========================================
# [6. 비동기 루프 실행 래퍼 함수]
# ==========================================
def run_asyncio_loop(approval_key):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(nxt_websocket_handler(approval_key))


# ==========================================
# [7. 메인 UI 렌더링 및 루프]
# ==========================================
approval_key = get_approval_key()
access_token = get_access_token() # REST API용 토큰 추가 발급

if approval_key and access_token:
    
    # ⭐ [핵심] 웹소켓을 켜기 전, 최초 1회만 REST API로 전체 종가를 불러옵니다.
    if 'initial_fetch_done' not in st.session_state:
        with st.spinner("최근 종가 데이터를 불러오는 중입니다... 잠시만 기다려주세요."):
            fetch_initial_prices(access_token)
        st.session_state.initial_fetch_done = True

    # 최초 1회만 백그라운드 웹소켓 스레드 실행
    if 'ws_thread_started' not in st.session_state:
        t = threading.Thread(target=run_asyncio_loop, args=(approval_key,), daemon=True)
        t.start()
        st.session_state.ws_thread_started = True

    st.caption(f"**웹소켓 상태:** {shared_state['ws_status']}")
    st.markdown("<hr style='margin: 5px 0px; border: 1px solid #ddd;'>", unsafe_allow_html=True)
    
    base_total = 0
    current_total = 0
    display_list = []

    for s in valid_stocks:
        info = shared_state["prices"].get(s['ticker'], {"price": 0, "diff": "-", "prev": 0})
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

    st.dataframe(pd.DataFrame(display_list), width='stretch')

    time.sleep(1)
    st.rerun()

else:
    st.error("API 키 인증에 실패했습니다. (승인키 또는 접근 토큰 발급 오류)")

