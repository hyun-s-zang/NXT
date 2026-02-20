# --- 1. 전역 로그 저장소 추가 ---
@st.cache_resource
def get_log_store():
    return []

ws_logs = get_log_store()

# --- 2. 웹소켓 핸들러 수정 ---
async def nxt_websocket_handler(approval_key):
    try:
        ws_logs.insert(0, "🔄 웹소켓 연결 시도 중...")
        async with websockets.connect(WS_URL, ping_interval=60) as ws:
            ws_logs.insert(0, "✅ 웹소켓 서버 연결 성공!")
            
            # (구독 요청 코드는 기존과 동일)
            for stock in valid_stocks:
                # ... send_data 구성 ...
                await ws.send(json.dumps(send_data))
                await asyncio.sleep(0.1)
                
            ws_logs.insert(0, "📡 구독 요청 전송 완료. 응답 대기 중...")

            while True:
                data = await ws.recv()
                
                # 서버에서 오는 JSON 형태의 응답을 화면 로그에 저장!
                if data.startswith('{'):
                    parsed_data = json.loads(data)
                    # KIS API는 구독 성공 시 msg1 값으로 상태를 알려줍니다.
                    ws_logs.insert(0, f"📩 서버 메시지: {parsed_data.get('body', {}).get('msg1', data)}")
                    # 로그가 너무 길어지지 않게 10개만 유지
                    if len(ws_logs) > 10:
                        ws_logs.pop()
                    continue
                
                # (실제 체결가 파싱 코드는 기존과 동일)
                # ...
                
    except Exception as e:
        ws_logs.insert(0, f"❌ 웹소켓 에러 발생: {e}")
