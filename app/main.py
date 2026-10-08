from fastapi import FastAPI, File, UploadFile, HTTPException
import os
import json
import requests
from datetime import datetime
import schedule
import time

# Load.env file
with open('.env', 'r') as f:
    for line in f:
        key, value = line.strip().split('=', 1)
        os.environ[key] = value.replace('"', '')

app = FastAPI()

CLOVA_API_KEY = os.getenv("CLOVA_STUDIO_API_KEY")
CLOVA_ENDPOINT = "https://api.ncloud-docs.com/v1/recipes"

class Product(BaseModel):
    purchase_date: str
    expiry_date: str
    product: str

products = []

def get_recipe(product_name):
    headers = {
        "Authorization": f"Bearer {os.getenv('CLOVA_STUDIO_API_KEY')}",
        "Content-Type": "application/json"
    }
    params = {"query": product_name}
    
    try:
        response = requests.get(CLOVA_ENDPOINT, headers=headers, json=params)
        response.raise_for_status()
        return response.json().get("result")[0] if response.json().get("result") else {}
    except Exception as e:
        return {"error": str(e)}

@app.post("/upload-products/")
async def upload_products(json_file: UploadFile = File(...)):
    try:
        data = json.loads(json_file.file.read().decode())
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="잘못된 JSON 형식")
    
    for item in data:
        products.append(Product(**item))
    schedule.every(1).minute.do(check_expiry)
    return {"상태": "성공"}

@app.get("/products/")
async def list_products():
    return [dict(purchase_date=p.purchase_date, expiry_date=p.expiry_date, product=p.product) for p in products]

@app.post("/get-recipe/")
async def get_recipe_endpoint(product_name: str):
    result = get_recipe(product_name)
    return {"레시피": result.get("title"), "보관방법": result.get("storage")} if 'title' in result else {"오류": "레시피 정보를 찾을 수 없습니다."}

def check_expiry():
    today = datetime.now()
    for product in products:
        diff = datetime.strptime(product.expiry_date, "%Y-%m-%d") - today
        if diff.days <= 3:
            print(f"[ALERT] {product.product} ({product.expiry_date}): {diff.days}일 남음")

if __name__ == "__main__":
    schedule.run_pending()
    app.run(host="0.0.0.0", port=8000)