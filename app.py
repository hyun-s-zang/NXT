import streamlit as st
import pandas as pd
import requests
import time
import os
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="NXT 실시간 주가 대시보드", layout="wide")
st.title("📈 초고속 NXT 실시간 & 종가 주가 모니터링")

# --- [보안] 한국투자증권 API 키 ---
try:
    APP_KEY = st.secrets["kis"]["app_key"]
    APP_SECRET = st.secrets["kis"]["app_secret"]
except:
    st.error("API 키가 설정되지 않았습니다. Streamlit Secrets 설정을 확인해주세요.")
    st.stop()

URL_BASE = "https://openapi.koreainvestment.com:9443"

# 1. KIS 접근 토큰 발급
@st.cache_data(ttl=3600*20) 
def get_access_token():
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    url = f"{URL_BASE}/oauth2/tokenP"
    res = requests.post(url, headers=headers, json=body)
    if res.status_code == 200:
        return res.json()["access_token"]
    return None

# 2. [핵심] 비동기 초고속 데이터 조회 함수 (동시에 여러 종목 조회)
async def fetch_price_async(session, ticker, token, sem):
    async with sem:  # API 호출 제한 방지용 세마포어
        url = f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "FHKST01010100" 
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker       
        }
        
        try:
            async with session.get(url, headers=headers, params=params) as res:
                if res.status == 200:
                    data = await res.json()
                    if data['rt_cd'] == '0':
                        price = int(data['output']['stck_prpr'])
                        diff = int(data['output']['prdy_vrss'])
                        sign = data['output']['prdy_vrss_sign']
                        
                        if sign in ['1', '2']: diff_str = f"▲ {diff:,}"
                        elif sign in ['4', '5']: diff_str = f"▼ {diff:,}"
                        else: diff_str = "-"
                        return ticker, f"{price:,}", diff_str
        except Exception:
            pass
        return ticker, "0", "-"

async def get_all_prices_async(tickers, token):
    # 한투 API 초당 호출 제한(초당 20건)을 고려하여 동시 접속량 조절
    sem = asyncio.Semaphore(15) 
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_price_async(session, ticker, token, sem) for ticker in tickers]
        results = await asyncio.gather(*tasks)
        return {res[0]: {"price": res[1], "diff": res[2]} for res in results}

# --- 메인 웹 화면 로직 ---
default_excel_file = "지겹다_완성.xlsx"
uploaded_file = st.file_uploader("새로운 종목 리스트로 갱신하려면 엑셀 파일을 업로드하세요.", type=["xlsx"])
file_to_read = uploaded_file if uploaded_file is not None else default_excel_file

if not os.path.exists(default_excel_file) and uploaded_file is None:
    st.error("기본 엑셀 파일('지겹다_완성.xlsx')을 찾을 수 없습니다. GitHub에 업로드해 주세요.")
    st.stop()

try:
    df = pd.read_excel(file_to_read, sheet_name=0)
    stock_data = df.iloc[:, [2, 3]].dropna()
    stock_list = stock_data.values.tolist()
except Exception as e:
    st.error(f"엑셀 데이터를 읽는 중 문제가 발생했습니다: {e}")
    st.stop()

if len(stock_list) == 0:
    st.warning("엑셀에서 종목명과 티커를 찾을 수 없습니다.")
    st.stop()

access_token = get_access_token()

if access_token:
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    is_market_open = (9 <= now.hour < 20)
    
    placeholder = st.empty()
    
    # 조회할 티커 리스트만 따로 추출 (검색불가 제외 및 6자리 맞춤)
    valid_stocks = [(name, str(t).zfill(6)) for name, t in stock_list if str(t) != "검색불가"]
    tickers_to_fetch = [t[1] for t in valid_stocks]
    
    if is_market_open:
        st.info(f"🟢 장 중입니다. 총 {len(valid_stocks)}개 종목을 초고속으로 갱신합니다.")
        while True:
            # 비동기로 모든 종목 가격을 한 번에 가져옴
            price_dict = asyncio.run(get_all_prices_async(tickers_to_fetch, access_token))
            
            current_data = []
            for stock_name, ticker in valid_stocks:
                info = price_dict.get(ticker, {"price": "0", "diff": "-"})
                current_data.append({
                    "종목명": stock_name,
                    "종목코드": ticker,
                    "현재가(원)": info["price"],
                    "전일대비": info["diff"]
                })
                
            with placeholder.container():
                st.dataframe(pd.DataFrame(current_data), use_container_width=True)
            time.sleep(5)
            
    else:
        st.error(f"🔴 장 마감 시간입니다. (현재 시각: {now.strftime('%H:%M')})")
        with st.spinner('데이터를 초고속으로 불러오는 중입니다...'):
            price_dict = asyncio.run(get_all_prices_async(tickers_to_fetch, access_token))
            
            current_data = []
            for stock_name, ticker in valid_stocks:
                info = price_dict.get(ticker, {"price": "0", "diff": "-"})
                current_data.append({
                    "종목명": stock_name,
                    "종목코드": ticker,
                    "종가(원)": info["price"],
                    "전일대비": info["diff"]
                })
        
        with placeholder.container():
            st.dataframe(pd.DataFrame(current_data), use_container_width=True)
