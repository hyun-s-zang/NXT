import streamlit as st
import pandas as pd
import requests
import time
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

# 2. 실시간 현재가/종가 조회 함수
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
            return int(data['output']['stck_prpr'])
    return 0

# --- 메인 웹 화면 로직 ---
uploaded_file = st.file_uploader("'지겹다_완성.xlsx' 파일을 업로드 해주세요.", type=["xlsx"])

if uploaded_file is not None:
    st.success("파일 업로드 완료! 데이터를 분석합니다...")
    
    # 엑셀 데이터 안전하게 읽기
    try:
        df = pd.read_excel(uploaded_file, sheet_name=0)
        # C열(인덱스 2), D열(인덱스 3) 추출 및 빈칸 제거
        stock_data = df.iloc[:, [2, 3]].dropna()
        stock_list = stock_data.values.tolist()
    except Exception as e:
        st.error(f"엑셀 데이터를 읽는 중 문제가 발생했습니다: {e}")
        st.stop()

    if len(stock_list) == 0:
        st.warning("엑셀에서 종목명과 티커를 찾을 수 없습니다. 파일 양식을 확인해 주세요.")
        st.stop()

    access_token = get_access_token()
    
    if access_token:
        # 한국 시간(KST) 기준 현재 시간 확인
        KST = timezone(timedelta(hours=9))
        now = datetime.now(KST)
        
        # 주식시장 개장 여부 판단 (아침 9시 ~ 저녁 8시)
        is_market_open = (9 <= now.hour < 20)
        
        placeholder = st.empty()
        
        if is_market_open:
            st.info(f"🟢 현재 장 중입니다. 총 {len(stock_list)}개 종목의 체결가를 5초 단위로 갱신합니다.")
            while True:
                current_data = []
                for stock_name, ticker in stock_list:
                    if str(ticker) != "검색불가":
                        clean_ticker = str(ticker).zfill(6)
                        current_price = get_kis_current_price(clean_ticker, access_token)
                        current_data.append({
                            "종목명": stock_name,
                            "종목코드": clean_ticker,
                            "현재가(원)": f"{current_price:,}"
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
                    current_price = get_kis_current_price(clean_ticker, access_token)
                    current_data.append({
                        "종목명": stock_name,
                        "종목코드": clean_ticker,
                        "종가(원)": f"{current_price:,}"
                    })
            
            # 장 마감일 때는 무한 루프(while) 없이 표를 딱 한 번만 그려줍니다.
            with placeholder.container():
                st.dataframe(pd.DataFrame(current_data), use_container_width=True)
