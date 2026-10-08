import os
from fastapi import FastAPI, Request, Response, File, UploadFile
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import requests
import json
from datetime import datetime

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

# 클로바 API 설정
CLOVA_ENDPOINT = "https://api.naver.com/clova/studio/chat-completions"
API_KEY = os.getenv("CLOVA_API_KEY")

@app.post("/analyze")
async def analyze(request: Request):
    try:
        data = request.json.get("text")
        if not data:
            return Response({"error": "Text required"}, status_code=400)
            
        # 클로바 API 호출
        response = requests.post(
            CLOVA_ENDPOINT,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"prompt": data}
        )
        result = response.json().get("choices")[0].get("message")

        # history.txt 저장
        with open("history.txt", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "request": data,
                "response": result
            }) + "\n")

        return Response({"result": result})
        
    except Exception as e:
        return Response(str(e), status_code=500)

@app.post("/scan")
async def scan_image(file: UploadFile = File(...)):
    try:
        response = requests.post(
            CLOVA_ENDPOINT,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"prompt": f"영수증 분석: {file.filename} + 소비기한 예측"}
        )
        result = response.json().get("choices")[0].get("message")

        # 분석 결과 파싱
        data = json.loads(result)
        items_with_expiry = []
        for item in data.get("items", []):
            # 소비기한 추출
            matched = next(
                (i for i in data["choices"][0]["message"]["items"] if i["name"] == item["name"]),
                None
            )
            if matched:
                item["expiry_days"] = matched.get("expiry_days", 0)
            items_with_expiry.append(item)

        # history 저장
        with open("history.txt", "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "items": items_with_expiry
            }) + "\n")

        return Response({"items": items_with_expiry})

    except Exception as e:
        return Response(str(e), status_code=500)

@app.post("/recipe")
async def get_recipe(item_id: str):
    # 기존 레시피 추천 로직 유지
   ...

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)