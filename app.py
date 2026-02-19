import streamlit as st
import pandas as pd
import requests
import time
import os
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="NXT 실시간 주가 대시보드", layout="wide")
st.markdown("""
    <style>
    /* 모바일(화면 너비 768px 이하) 환경에만 적용되는 디자인 */
    @media (max-width: 768px) {
        /* 1. 기본 제목(h1) 크기 대폭 축소 및 여백 제거 */
        h1 {
            font-size: 20px !important;
            padding-top: 0rem !important;
            padding-bottom: 0rem !important;
        }
        /* 2. 앱 최상단 여백(빈 공간) 축소 */
        .block-container {
            padding-top: 1.5rem !important; 
        }
        /* 3. 지수(Metric)와 표(Table) 사이의 기본 간격(gap) 축소 */
        [data-testid="stVerticalBlock"] {
            gap: 0.2rem !important;
        }
        /* 4. 지수 하단 여백 완벽 제거 */
        [data-testid="stMetric"] {
            margin-bottom: -15px !important;
        }
    }
    </style>
""", unsafe_allow_html=True)

# 안전한 Streamlit 기본 제목 사용 (위의 CSS가 모바일에서만 크기를 줄여줍니다)
st.title("📈 초고속 NXT 실시간 대시보드 & 커스텀 지수")
""", unsafe_allow_html=True)

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

# 2. 비동기 초고속 데이터 조회 (현재가, 전일종가, 시가총액 모두 반환)
async def fetch_price_async(session, ticker, excel_marcap, token, sem):
    async with sem:
        url = f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "FHKST01010100" 
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        
        try:
            async with session.get(url, headers=headers, params=params) as res:
                if res.status == 200:
                    data = await res.json()
                    if data['rt_cd'] == '0':
                        price = int(data['output']['stck_prpr'])
                        diff = int(data['output']['prdy_vrss'])
                        sign = data['output']['prdy_vrss_sign']
                        
                        # 전일 종가 역산 로직
                        if sign in ['1', '2']: 
                            diff_str = f"▲ {diff:,}"
                            prev_price = price - diff
                        elif sign in ['4', '5']: 
                            diff_str = f"▼ {diff:,}"
                            prev_price = price + diff
                        else: 
                            diff_str = "-"
                            prev_price = price
                            
                        return ticker, price, prev_price, diff_str, excel_marcap
        except Exception:
            pass
        return ticker, 0, 0, "-", excel_marcap

async def get_all_prices_async(stock_info_list, token):
    sem = asyncio.Semaphore(15) 
    async with aiohttp.ClientSession() as session:
        # stock_info_list는 (ticker, marcap) 형태
        tasks = [fetch_price_async(session, t, m, token, sem) for t, m in stock_info_list]
        results = await asyncio.gather(*tasks)
        return {res[0]: {"price": res[1], "prev_price": res[2], "diff": res[3], "marcap": res[4]} for res in results}

# --- 메인 웹 화면 로직 ---
default_excel_file = "지겹다_완성.xlsx"
uploaded_file = st.file_uploader("새로운 종목 리스트로 갱신하려면 엑셀 파일을 업로드하세요.", type=["xlsx"])
file_to_read = uploaded_file if uploaded_file is not None else default_excel_file

if not os.path.exists(default_excel_file) and uploaded_file is None:
    st.error("기본 엑셀 파일('지겹다_완성.xlsx')을 찾을 수 없습니다.")
    st.stop()

try:
    df = pd.read_excel(file_to_read, sheet_name=0)
    valid_stocks = []
    # 엑셀 데이터 파싱 (C열: 종목명, D열: 티커, E열: 시가총액)
    for idx, row in df.iterrows():
        if pd.notna(row.iloc[3]): # 티커가 비어있지 않은 경우
            name = str(row.iloc[2])
            ticker = str(row.iloc[3])
            # E열에 시가총액이 있다면 가져오고, 없으면 0으로 처리
            marcap = float(row.iloc[4]) if df.shape[1] > 4 and pd.notna(row.iloc[4]) else 0
            
            if ticker != "검색불가":
                valid_stocks.append((name, ticker.zfill(6), marcap))
except Exception as e:
    st.error(f"엑셀 파싱 에러: {e}")
    st.stop()

if len(valid_stocks) == 0:
    st.warning("엑셀에서 유효한 종목명과 티커를 찾을 수 없습니다.")
    st.stop()

access_token = get_access_token()

if access_token:
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    is_market_open = (9 <= now.hour < 20)
    
    # 지수와 표를 그릴 화면 공간 할당
    index_placeholder = st.empty()
    st.markdown("<hr style='margin: 5px 0px; border: 1px solid #ddd;'>", unsafe_allow_html=True)
    table_placeholder = st.empty()
    
    tickers_to_fetch = [(t, m) for n, t, m in valid_stocks]
    
    if is_market_open:
        st.info(f"🟢 장 중입니다. 실시간 가격과 지수를 5초 단위로 갱신합니다.")
        while True:
            price_dict = asyncio.run(get_all_prices_async(tickers_to_fetch, access_token))
            
            current_data = []
            base_total_value = 0
            current_total_value = 0
            
            for stock_name, ticker, _ in valid_stocks:
                info = price_dict.get(ticker, {"price": 0, "prev_price": 0, "diff": "-", "marcap": 0})
                p = info["price"]
                prev_p = info["prev_price"]
                m = info["marcap"]
                
                # 지수 산출 로직 (시가총액 또는 동일가중)
                weight = m if m > 0 else 1 
                if prev_p > 0:
                    base_total_value += weight
                    current_total_value += weight * (p / prev_p)
                
                current_data.append({
                    "종목명": stock_name,
                    "종목코드": ticker,
                    "현재가(원)": f"{p:,}" if p > 0 else "0",
                    "전일대비": info["diff"]
                })
            
            # 지수 계산 (기준=1000)
            if base_total_value > 0:
                nxt_index = (current_total_value / base_total_value) * 1000
                index_diff = nxt_index - 1000
                index_pct = (index_diff / 1000) * 100
            else:
                nxt_index, index_diff, index_pct = 1000, 0, 0
                
            with index_placeholder.container():
                st.metric(label="🚀 커스텀 NXT 지수 (Base: 전일종가 = 1000 pt)", 
                          value=f"{nxt_index:,.2f} pt", 
                          delta=f"{index_diff:+,.2f} pt ({index_pct:+.2f}%)")
                
            with table_placeholder.container():
                st.dataframe(pd.DataFrame(current_data), use_container_width=True)
            time.sleep(5)
            
    else:
        st.error(f"🔴 장 마감 시간입니다. 최종 종가 기준으로 지수와 데이터를 불러옵니다.")
        with st.spinner('데이터를 초고속으로 불러오는 중입니다...'):
            price_dict = asyncio.run(get_all_prices_async(tickers_to_fetch, access_token))
            
            current_data = []
            base_total_value = 0
            current_total_value = 0
            
            for stock_name, ticker, _ in valid_stocks:
                info = price_dict.get(ticker, {"price": 0, "prev_price": 0, "diff": "-", "marcap": 0})
                p = info["price"]
                prev_p = info["prev_price"]
                m = info["marcap"]
                
                weight = m if m > 0 else 1 
                if prev_p > 0:
                    base_total_value += weight
                    current_total_value += weight * (p / prev_p)
                    
                current_data.append({
                    "종목명": stock_name,
                    "종목코드": ticker,
                    "종가(원)": f"{p:,}" if p > 0 else "0",
                    "전일대비": info["diff"]
                })
                
            if base_total_value > 0:
                nxt_index = (current_total_value / base_total_value) * 1000
                index_diff = nxt_index - 1000
                index_pct = (index_diff / 1000) * 100
            else:
                nxt_index, index_diff, index_pct = 1000, 0, 0
                
            with index_placeholder.container():
                st.metric(label="🚀 커스텀 NXT 지수 (Base: 전일종가 = 1000 pt)", 
                          value=f"{nxt_index:,.2f} pt", 
                          delta=f"{index_diff:+,.2f} pt ({index_pct:+.2f}%)")
        
        with table_placeholder.container():
            st.dataframe(pd.DataFrame(current_data), use_container_width=True)


