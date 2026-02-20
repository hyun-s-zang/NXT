import streamlit as st
import pandas as pd
import requests
import asyncio
import websockets
import json
import time
import threading

# --- 페이지 기본 설정 ---
st.set_page_config(page_title="NXT 실시간 주가 대시보드", layout="wide")

# (CSS 스타일링, API 키 설정, 엑셀 데이터 로드 부분은 기존 코드와 동일하게 유지)
# ...

# --- 상태 관리를 위한 전역 저장소 고도화 ---
@st.cache_resource
def get_shared_state():
    return {
        "ws_status": "연결 대기 중...",  # 웹소켓 상태를 UI로 전달할 변수
        "prices": {s['ticker']: {"price": 0, "diff": "-", "prev": 0} for s in valid_stocks}
    }

shared_state = get_shared_state()

# --- 웹소켓 수신 함수 개선 ---
async def nxt_websocket_handler(approval_key):
    try:
        shared_state["ws_status"] = "🔄 서버 연결 시도 중..."
        async with websockets.connect(WS_URL, ping_interval=60) as ws:
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
                
                # 1. JSON 형태의 시스템 메시지 처리 (PINGPONG 및 에러)
                if data.startswith('{'):
                    parsed = json.loads(data)
                    tr_id = parsed.get("header", {}).get("tr_id", "")
                    
                    if tr_id == "PINGPONG":
                        continue # 핑퐁은 그냥 무시
                        
                    msg = parsed.get("body", {}).get("msg1", "")
                    if "ALREADY IN USE" in msg:
                        shared_state["ws_status"] = "❌ 중복 접속 에러 (모든 창을 닫고 5분 뒤 다시 실행하세요)"
                        break # 루프 종료
                    elif "SUBSCRIBE SUCCESS" in msg:
                        continue
                    continue
                    
                # 2. 실제 체결가 데이터 파싱
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
                        
                        # 전역 저장소 업데이트
                        shared_state["prices"][ticker] = {
                            "price": current_price,
                            "diff": f"{diff_prefix} {diff:,}",
                            "prev": prev_price
                        }
    except Exception as e:
        shared_state["ws_status"] = f"⚠️ 웹소켓 종료/에러: {e}"

def run_asyncio_loop(approval_key):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(nxt_websocket_handler(approval_key))


# --- 메인 대시보드 렌더링 루프 ---
approval_key = get_approval_key()

if approval_key:
    if 'ws_thread_started' not in st.session_state:
        t = threading.Thread(target=run_asyncio_loop, args=(approval_key,), daemon=True)
        t.start()
        st.session_state.ws_thread_started = True

    # 현재 웹소켓의 상태를 UI 상단에 배지로 표시
    st.caption(f"상태: {shared_state['ws_status']}")
    st.markdown("<hr style='margin: 5px 0px; border: 1px solid #ddd;'>", unsafe_allow_html=True)
    
    base_total = 0
    current_total = 0
    display_list = []

    # UI 구성 시 shared_state["prices"]를 참조
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

    # 지수 계산 (동일)
    # ...

    st.metric(label="🚀 커스텀 NXT 지수 (Base: 1000 pt)", value=f"{nxt_index:,.2f} pt", delta=f"{index_diff:+,.2f} pt ({index_pct:+.2f}%)")
    st.dataframe(pd.DataFrame(display_list), width='stretch')

    time.sleep(1)
    st.rerun()

else:
    st.error("승인 키 발급 실패")
