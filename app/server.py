import os
import httpx  
import json
from fastapi import FastAPI, Request, Response, File, UploadFile, HTTPException
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta

load_dotenv()
app = FastAPI()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],    
    allow_headers=["*"],
)

# ⚠️ [중요] 네이버 클로바 스튜디오 실제 엔드포인트 규격으로 수정해야 합니다.
# 예시: "https://ntruss.com"
CLOVA_ENDPOINT = os.getenv("CLOVA_ENDPOINT", "https://api.naver.com/clova/studio/chat-completions")
API_KEY = os.getenv("CLOVA_API_KEY")
GW_KEY = os.getenv("CLOVA_GW_KEY")  # APIGW Gateway Key가 필요한 경우 대비

timers = {}

@app.post("/scan")
async def scan_image(file: UploadFile = File(...)):
    # 클로바 스튜디오는 Bearer 대신 X-NCP-CLOVASTUDIO-API-KEY 등을 헤더로 요구하는 경우가 많습니다.
    headers = {
        "X-NCP-CLOVASTUDIO-API-KEY": f"{API_KEY}",
        "X-NCP-APIGW-API-KEY": f"{GW_KEY}" if GW_KEY else "",
        "Content-Type": "application/json"
    }
    
    # 모델이 구조화된 JSON 데이터만 추출하도록 프롬프트 뒤에 JSON 포맷 강제 유도 문구를 추가하는 것이 좋습니다.
    payload = {
        "messages": [
            {
                "role": "user", 
                "content": f"영수증 분석: {file.filename} 및 각 품목별 소비기한 예측을 진행해줘. 응답은 반드시 'items': [{{'id': '...', 'name': '...', 'expiry_days': 10}}] 구조를 가진 JSON 텍스트로만 해줘."
            }
        ],
        "temperature": 0.1  # 일관된 JSON 출력을 위해 온도를 낮춤
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(CLOVA_ENDPOINT, headers=headers, json=payload)
            response.raise_for_status()
            res_json = response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Clova API 호출 실패: {str(e)}")

    # 1. 오타 수정: IndexEror -> IndexError
    try:
        message_content = res_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise HTTPException(status_code=500, detail="API 응답 구조가 올바르지 않습니다.")
    
    # 2. 클로바가 텍스트(문자열) 형태로 마크다운이나 JSON을 주므로, 이를 딕셔너리로 파싱하는 로직 추가
    try:
        # 만약 ```json ... ``` 형태로 감싸져서 올 경우를 대비해 정제 후 파싱
        cleaned_content = message_content.replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned_content)
    except Exception:
        # 파싱 실패 시 프론트엔드가 인지할 수 있도록 원본 텍스트 구조로 대체 처리
        data = {"items": [], "raw_text": message_content}
    
    items = data.get("items", [])
    for item in items:
        expiry_days = item.get("expiry_days", 0)
        item_id = item.get("id")
        if item_id:
            timers[item_id] = datetime.now() + timedelta(days=expiry_days)
    
    return data

@app.post("/timer/{item_id}")
async def update_timer(item_id: str):
    return {"status": "Timer active" if item_id in timers else "Item not found"}

@app.post("/alert")
async def check_alert(item_id: str):
    if item_id in timers:
        remaining_days = (timers[item_id] - datetime.now()).days
        if remaining_days <= 10:
            return {"alert": "Low stock!", "recipe": await get_recipe(item_id)}
    return {"status": "OK"}

@app.post("/recipe")
async def get_recipe(item_id: str):
    headers = {
        "X-NCP-CLOVASTUDIO-API-KEY": f"{API_KEY}",
        "X-NCP-APIGW-API-KEY": f"{GW_KEY}" if GW_KEY else "",
        "Content-Type": "application/json"
    }
    payload = {
        "messages": [
            {"role": "user", "content": f"{item_id} 레시피 추천"}
        ]
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(CLOVA_ENDPOINT, headers=headers, json=payload)
            res_json = response.json()
            return res_json["choices"][0]["message"]
        except Exception as e:
            return {"error": f"레시피 추천 실패: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
