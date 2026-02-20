import streamlit as st
import pandas as pd
import requests
import os
import asyncio
import aiohttp
import websockets
import json
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="NXT 실시간 주가 대시보드", layout="wide")

# --- 기존 CSS 유지 ---
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
    st.error("API 키가 설정되지 않았습니다.")
    st.stop()

# --- [수정] 웹소켓 및 토큰 설정 ---
URL_BASE = "https://openapi.koreainvestment.com:9443"
WS_URL = "ws://ops.koreainvestment.com:21000" # 실시간 웹소켓 주소

@st.cache_data(ttl=3600*20)
def get_access_token():
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, json=body)
    return res.json().get("access_token") if res.status_code == 200 else None

@st.cache_data(ttl=3600*20)
def get_approval_key(): # 웹소켓 접속용 승인 키 발급
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "secretkey": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/Approval", headers=headers, json=body)
    return res.json().get("approval_key") if res.status_code == 200 else None

# --- 데이터 로드 ---
file_to_read = "지겹다_완성.xlsx"
df = pd.read_excel(file_to_read, sheet_name=0)
valid_stocks = []
for idx, row in df.iterrows():
    if pd.notna(row.iloc[3]) and str(row.iloc[3]) != "검색불가":
        valid_stocks.append({
            "name": str(row.iloc[2]),
            "ticker": str(row.iloc[3]).zfill(6),
            "marcap": float(row.iloc[4]) if df.shape[1] > 4 and pd.notna(row.iloc[4]) else 1
        })

# 실시간 데이터를 저장할 전역 변수 역할의 딕셔너리
if 'price_data' not in st.session_state:
    st.session_state.price_data = {s['ticker']: {"price": 0, "diff": "-", "prev": 0} for s in valid_stocks}

# --- [수정] 웹소켓 수신 함수 (NXT 실시간 체결가) ---
async def nxt_websocket_handler(approval_key):
    async with websockets.connect(WS_URL) as ws:
        for stock in valid_stocks:
            # H0NXSTC0: 넥스트트레이드 실시간 체결가 TR
            # (만약 정규장+NXT 통합을 원할 시 H0STCNT0 등 KIS 가이드에 따른 TR 변경 가능)
            send_data = {
                "header": {
                    "approval_key": approval_key,
                    "custtype": "P",
                    "tr_type": "1", # 등록
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

        while True:
            data = await ws.recv()
            if data[0] in ['0', '1']: # 데이터 패킷인 경우
                parts = data.split('|')
                content = parts[-1].split('^')
                ticker = parts[3]
                current_price = int(content[2])
                diff = int(content[4])
                sign = content[3]
                
                # 기호에 따른 처리
                diff_prefix = "▲" if sign in ['1', '2'] else "▼" if sign in ['4', '5'] else ""
                prev_price = current_price - diff if sign in ['1', '2'] else current_price + diff if sign in ['4', '5'] else current_price
                
                st.session_state.price_data[ticker] = {
                    "price": current_price,
                    "diff": f"{diff_prefix} {diff:,}",
                    "prev": prev_price
                }

# --- 메인 대시보드 루프 ---
index_placeholder = st.empty()
st.markdown("<hr style='margin: 5px 0px; border: 1px solid #ddd;'>", unsafe_allow_html=True)
table_placeholder = st.empty()

# 앱 실행 시 웹소켓을 백그라운드에서 실행 (Streamlit 구조상 비동기 처리가 까다로우므로 여기서는 요약된 로직 제공)
# 실제 운영 시에는 별도의 스레드나 멀티프로세싱이 필요할 수 있으나, Streamlit의 실험적 기능을 활용합니다.

approval_key = get_approval_key()

if approval_key:
    # 화면 갱신을 위한 루프
    # 주의: Streamlit에서 웹소켓의 실시간 데이터를 화면에 뿌리기 위해 무한루프를 사용합니다.
    while True:
        base_total = 0
        current_total = 0
        display_list = []

        for s in valid_stocks:
            info = st.session_state.price_data[s['ticker']]
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

        # 지수 계산
        if base_total > 0:
            nxt_index = (current_total / base_total) * 1000
            index_diff = nxt_index - 1000
            index_pct = (index_diff / 1000) * 100
        else:
            nxt_index, index_diff, index_pct = 1000, 0, 0

        with index_placeholder.container():
            st.metric(label="🚀 커스텀 NXT 지수 (Base: 1000 pt)", 
                      value=f"{nxt_index:,.2f} pt", 
                      delta=f"{index_diff:+,.2f} pt ({index_pct:+.2f}%)")

        with table_placeholder.container():
            st.dataframe(pd.DataFrame(display_list), use_container_width=True)
        
        time.sleep(1) # 지수 계산 및 화면 갱신 주기
