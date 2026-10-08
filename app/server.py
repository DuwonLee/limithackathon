import os
from fastapi import FastAPI, Request, Response, File, UploadFile
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import requests
import json
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

# 클로바 스튜디오 API 설정
CLOVA_ENDPOINT = "https://api.naver.com/clova/studio/chat-completions"
API_KEY = os.getenv("CLOVA_API_KEY")  # 사용자 입력 필요

@app.post("/scan")
async def scan_image(file: UploadFile = File(...)):
    try:
        # 클로바 API 호출 (영수증 분석)
        response = requests.post(
            CLOVA_ENDPOINT,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"prompt": f"영수증 분석: {file.filename}"}
        )
        result = response.json().get("choices")[0].get("message")

        # JSON 파싱 및 저장
        data = json.loads(result)
        with open("history.txt", "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "items": data.get("items", [])
            }) + "\n")

        return Response(data)

    except Exception as e:
        return Response(str(e), status_code=500)

@app.post("/recipe")
async def get_recipe(item_id: str):
    # 클로바 API 레시피 추천 호출
    response = requests.post(
        CLOVA_ENDPOINT,
        headers={"Authorization": API_KEY},
        json={"prompt": f"{item_id} 레시피 추천"}
    )
    return Response(response.json().get("choices")[0].get("message"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)