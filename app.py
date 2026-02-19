import streamlit as st
import pandas as pd
import requests
import time

# 웹 페이지 설정
st.set_page_config(page_title="NXT 실시간 주가 대시보드", layout="wide")
st.title("📈 NXT 장중 실시간 주가 모니터링 (스마트폰 접속용)")

# --- [보안] 한국투자증권 API 키 (Streamlit Secrets에서 불러오기) ---
try:
    APP_KEY = st.secrets["kis"]["app_key"]
    APP_SECRET = st.secrets["kis"]["app_secret"]
except:
    st.error("API 키가 설정되지 않았습니다. Streamlit Secrets 설정을 확인해주세요.")
    st.stop()

URL_BASE = "https://openapi.koreainvestment.com:9443" # 실전투자 URL

# 1. KIS 접근 토큰 발급 (하루 1번만 발급받도록 캐싱, 유효기간 24시간)
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
    else:
        st.error("토큰 발급 실패. API 키를 확인하세요.")
        return None

# 2. 한국투자증권 실시간 현재가 조회 함수
def get_kis_current_price(ticker, token):
    url = f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100" # 주식현재가 시세 TR 코드
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", # J: 주식, ETF, ETN
        "FID_INPUT_ISCD": ticker       # 종목코드 (6자리)
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        data = res.json()
        if data['rt_cd'] == '0':
            # stck_prpr : 주식 현재가
            return int(data['output']['stck_prpr'])
    return 0 # 오류 시 0 반환

# --- 메인 웹 화면 로직 ---
# 3. 사용자로부터 엑셀 파일 직접 업로드 받기 (웹 호스팅 시 필수!)
uploaded_file = st.file_uploader("'지겹다_완성.xlsx' 파일을 업로드 해주세요.", type=["xlsx"])

if uploaded_file is not None:
    st.success("파일 업로드 완료! 데이터를 실시간으로 불러옵니다...")
    
    # 엑셀 데이터 읽기
    df = pd.read_excel(uploaded_file, sheet_name='sheet1')
    
    # C열(인덱스 2: 종목명), D열(인덱스 3: 티커) 추출 (NaN 제외)
    df = df[df.iloc[:, 3].notnull()]
    stock_list = df.iloc[:, [2, 3]].values.tolist()

    # 한국투자증권 토큰 발급
    access_token = get_access_token()
    
    if access_token:
        st.write("🔄 5초 단위로 실시간 체결가를 갱신 중입니다...")
        placeholder = st.empty()
        
        # 실시간 갱신 루프 (API 과부하를 막기 위해 5초 대기)
        while True:
            current_data = []
            for stock_name, ticker in stock_list:
                if str(ticker) != "검색불가":
                    clean_ticker = str(ticker).zfill(6)
                    # KIS API로 실시간 현재가 가져오기
                    current_price = get_kis_current_price(clean_ticker, access_token)
                    
                    current_data.append({
                        "종목명": stock_name,
                        "종목코드": clean_ticker,
                        "실시간 현재가(원)": f"{current_price:,}" # 보기 좋게 쉼표 추가
                    })
            
            # 화면 표출
            with placeholder.container():
                st.dataframe(pd.DataFrame(current_data), use_container_width=True)
            
            time.sleep(5) # 5초 대기