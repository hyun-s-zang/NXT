import streamlit as st
import pandas as pd
import requests
import time
import os
from datetime import datetime, timedelta, timezone

# 웹 페이지 설정
st.set_page_config(page_title="NXT 실시간 주가 대시보드", layout="wide")
st.title("📈 NXT 실시간 & 종가 주가 모니터링")

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

# 2. 실시간 현재가/종가 및 전일대비 조회 함수
def get_kis_current_price(ticker, token):
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
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        if data['rt_cd'] == '0':
            price = int(data['output']['stck_prpr'])       # 현재가
            diff = int(data['output']['prdy_vrss'])        # 전일 대비 절대값
            sign = data['output']['prdy_vrss_sign']        # 전일 대비 부호
            
            # 부호에 따른 기호 설정 (1,2: 상승 / 4,5: 하락 / 3: 보합)
            if sign in ['1', '2']:
                diff_str = f"▲ {diff:,}"
            elif sign in ['4', '5']:
                diff_str = f"▼ {diff:,}"
            else:
                diff_str = "-"
                
            return price, diff_str
    return 0, "-"

# --- 메인 웹 화면 로직 ---
default_excel_file = "지겹다_완성.xlsx"

# 사용자가 새 파일을 올리면 그걸 쓰고, 안 올리면 GitHub에 있는 기본 파일을 사용합니다.
uploaded_file = st.file_uploader("새로운 종목 리스트로 갱신하려면 엑셀 파일을 업로드하세요. (기본 파일 사용 시 무시)", type=["xlsx"])
file_to_read = uploaded_file if uploaded_file is not None else default_excel_file

# GitHub에 엑셀 파일이 잘 올라가 있는지 확인
if not os.path.exists(default_excel_file) and uploaded_file is None:
    st.error("기본 엑셀 파일('지겹다_완성.xlsx')을 찾을 수 없습니다. GitHub 저장소에 파일을 업로드해 주세요.")
    st.stop()

# 엑셀 데이터 안전하게 읽기
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
    
    if is_market_open:
        st.info(f"🟢 현재 장 중입니다. 총 {len(stock_list)}개 종목을 5초 단위로 갱신합니다.")
        while True:
            current_data = []
            for stock_name, ticker in stock_list:
                if str(ticker) != "검색불가":
                    clean_ticker = str(ticker).zfill(6)
                    current_price, diff_str = get_kis_current_price(clean_ticker, access_token)
                    current_data.append({
                        "종목명": stock_name,
                        "종목코드": clean_ticker,
                        "현재가(원)": f"{current_price:,}",
                        "전일대비": diff_str
                    })
            with placeholder.container():
                st.dataframe(pd.DataFrame(current_data), use_container_width=True)
            time.sleep(5)
            
    else:
        st.error(f"🔴 현재는 장 마감 시간입니다. (현재 시각: {now.strftime('%H:%M')})")
        st.write(f"총 {len(stock_list)}개 종목의 **최종 종가** 기준으로 데이터를 1회 불러옵니다.")
        
        current_data = []
        for stock_name, ticker in stock_list:
            if str(ticker) != "검색불가":
                clean_ticker = str(ticker).zfill(6)
                current_price, diff_str = get_kis_current_price(clean_ticker, access_token)
                current_data.append({
                    "종목명": stock_name,
                    "종목코드": clean_ticker,
                    "종가(원)": f"{current_price:,}",
                    "전일대비": diff_str
                })
        
        with placeholder.container():
            st.dataframe(pd.DataFrame(current_data), use_container_width=True)
