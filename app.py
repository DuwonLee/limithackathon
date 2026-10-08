#!/usr/bin/env python3

import base64
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()


def env(*names, default=""):
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip().strip('"').strip("'")
    return default


OCR_URL = env("CLOVA_OCR_INVOKE_URL", "OCR_INVOKE_URL")
OCR_SECRET = env("CLOVA_OCR_SECRET_KEY", "OCR_SECRET_KEY", "CLOVA_OCR_SECRET")
HCX_KEY = env("CLOVASTUDIO_API_KEY", "HYPERCLOVAX_API_KEY", "NCP_CLOVASTUDIO_API_KEY")
HCX_MODEL = env("CLOVASTUDIO_MODEL", default="HCX-005")
HCX_BASE = env("CLOVASTUDIO_BASE_URL", default="https://clovastudio.stream.ntruss.com")
DB_PATH = Path(__file__).with_name("littone.db")

app = FastAPI(title="릿톤")

# ---------------------------------------------------------------- DB
SEED_RECIPES = [
    ("떡볶이", 20, ["떡", "고추장", "고춧가루", "어묵", "대파", "설탕"],
     ["떡을 물에 10분 불린다.", "물에 고추장, 고춧가루, 설탕을 풀어 끓인다.",
      "떡과 어묵, 대파를 넣고 소스가 걸쭉해질 때까지 졸인다."]),
    ("보쌈", 50, ["삼겹살", "된장", "양파", "대파", "마늘", "생강"],
     ["냄비에 물, 된장, 양파, 대파, 마늘, 생강을 넣고 끓인다.", "삼겹살을 넣고 40분 정도 삶는다.",
      "한 김 식힌 뒤 먹기 좋게 썰어 낸다."]),
    ("장조림", 50, ["소고기", "계란", "간장", "마늘", "대파", "설탕"],
     ["소고기는 찬물에 담가 핏물을 뺀다.", "물, 간장, 설탕, 마늘, 대파와 함께 30분 삶는다.",
      "삶은 계란을 넣고 10분 더 졸인다."]),
    ("산채비빔밥", 25, ["밥", "산나물", "고추장", "참기름", "계란"],
     ["산나물은 데쳐서 물기를 짜고 참기름에 무친다.", "그릇에 밥과 나물을 담는다.",
      "계란 프라이와 고추장을 올려 비빈다."]),
    ("가지볶음", 25, ["가지", "마늘", "간장", "대파", "참기름"],
     ["가지를 길게 썰어 소금에 5분 절인다.", "팬에 마늘, 대파를 볶다가 가지를 넣고 볶는다.",
      "간장과 참기름으로 간을 맞춘다."]),
    ("봉골레 파스타", 20, ["마늘", "링귀네면", "화이트와인", "모시조개", "올리브유", "고추", "파슬리"],
     ["2~3시간 전 조개를 소금물에 해감한다.",
      "소금 간을 한 끓는 물에 링귀니를 뭉치지 않게 흩트려 펴 넣어 6~7분 정도 삶아 알덴테로 익힌다.",
      "면이 익을 동안 달구어진 팬에 올리브유를 두르고 썰어놓은 마늘, 고추를 먼저 넣고 살짝 볶다가 조개를 넣고 볶는다. 그리고 화이트와인을 100mL 정도 넣고 뚜껑을 덮은 후 조개가 입을 벌릴 때까지 익힌다.",
      "알덴테로 살짝 덜 익은 면을 넣어 마저 익힌다. 조개 육수가 면에 배어들며 간을 겸해서 익힌다.",
      "조개 육수가 졸아들어 가면 면수를 넣어준다. 면수의 전분기로 기름과 나머지 겉도는 조개 육수를 서로 어우르도록 잡아주며 마저 익힌다.",
      "졸아들 때까지 볶다가 후추 등 향신료로 추가 양념을 치고 마무리로 살짝 버무리고 올린다. 접시에 담은 후 잘게 썬 파슬리를 위에 뿌려주면 완성이다."]),
]

# (이름, 개수, 보관, 오늘 기준 만료까지 일수)
SEED_INGREDIENTS = [("양파", 2, "냉장", -3), ("마늘", 10, "냉장", 2), ("계란", 6, "냉장", 11), ("베이컨", 1, "냉장", 21)]


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS ingredients(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, qty INTEGER DEFAULT 1,
            storage TEXT DEFAULT '냉장', expiry TEXT, memo TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS recipes(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, minutes INTEGER DEFAULT 20,
            ingredients TEXT, steps TEXT, source TEXT DEFAULT 'seed');
        """
    )
    if con.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0] == 0:
        for n, q, s, d in SEED_INGREDIENTS:
            con.execute("INSERT INTO ingredients(name,qty,storage,expiry) VALUES(?,?,?,?)",
                        (n, q, s, (date.today() + timedelta(days=d)).isoformat()))
    if con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0:
        for n, m, ing, steps in SEED_RECIPES:
            con.execute("INSERT INTO recipes(name,minutes,ingredients,steps) VALUES(?,?,?,?)",
                        (n, m, json.dumps(ing, ensure_ascii=False), json.dumps(steps, ensure_ascii=False)))
    con.commit()
    con.close()


init_db()


# ---------------------------------------------------------------- helpers
def d_info(expiry: Optional[str]):
    if not expiry:
        return "D-?", "", None
    diff = (date.today() - date.fromisoformat(expiry)).days  # >0 : 지남
    if diff > 0:
        return f"D+{diff}", "red", diff
    if diff == 0:
        return "D-Day", "red", diff
    return f"D-{-diff}", ("orange" if diff >= -3 else ""), diff


def ing_row(r):
    label, cls, diff = d_info(r["expiry"])
    return {"id": r["id"], "name": r["name"], "qty": r["qty"], "storage": r["storage"],
            "expiry": r["expiry"], "memo": r["memo"], "d_label": label, "d_class": cls}


def fridge_names():
    con = db()
    names = [r["name"] for r in con.execute("SELECT name FROM ingredients").fetchall()]
    con.close()
    return names


def has(fridge, ing):
    return any(ing in f or f in ing for f in fridge if f and ing)


def recipe_row(r, fridge):
    ings = json.loads(r["ingredients"] or "[]")
    have = [i for i in ings if has(fridge, i)]
    pct = round(len(have) / len(ings) * 100) if ings else 0
    return {"id": r["id"], "name": r["name"], "minutes": r["minutes"], "ingredients": ings,
            "have": have, "percent": pct, "steps": json.loads(r["steps"] or "[]")}


def parse_json(text: str):
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    for o, c in (("{", "}"), ("[", "]")):
        s, e = text.find(o), text.rfind(c)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except Exception:
                continue
    return None


async def hcx(system: str, user: str, max_tokens: int = 2048):
    """HyperCLOVA X 호출. 실패/키 없음이면 None."""
    if not HCX_KEY:
        return None
    body = {
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": system}]},
            {"role": "user", "content": [{"type": "text", "text": user}]},
        ],
        "topP": 0.8, "topK": 0, "maxTokens": max_tokens, "temperature": 0.3, "repetitionPenalty": 1.1,
    }
    headers = {"Authorization": f"Bearer {HCX_KEY}", "Content-Type": "application/json",
               "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{HCX_BASE}/v3/chat-completions/{HCX_MODEL}", headers=headers, json=body)
        r.raise_for_status()
        return r.json()["result"]["message"]["content"]
    except Exception as e:
        print("[HyperCLOVA X 오류]", e)
        return None


async def run_ocr(data: bytes, filename: str):
    """CLOVA OCR -> 상품명 리스트. 키 없으면 None."""
    if not (OCR_URL and OCR_SECRET):
        return None
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "jpg").lower()
    ext = {"jpeg": "jpg"}.get(ext, ext)
    if ext not in ("jpg", "png", "pdf", "tiff"):
        ext = "jpg"
    payload = {"version": "V2", "requestId": str(uuid.uuid4()), "timestamp": int(time.time() * 1000),
               "lang": "ko",
               "images": [{"format": ext, "name": "receipt", "data": base64.b64encode(data).decode()}]}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(OCR_URL, headers={"X-OCR-SECRET": OCR_SECRET, "Content-Type": "application/json"},
                         json=payload)
    r.raise_for_status()
    img = r.json()["images"][0]
    names = []
    # 영수증 특화 도메인
    for sub in img.get("receipt", {}).get("result", {}).get("subResults", []):
        for it in sub.get("items", []):
            t = (it.get("name") or {}).get("text")
            if t:
                names.append(t)
    # General OCR 폴백: 줄 단위 텍스트
    if not names:
        line = ""
        for f in img.get("fields", []):
            line += f.get("inferText", "") + " "
            if f.get("lineBreak"):
                names.append(line.strip())
                line = ""
        if line.strip():
            names.append(line.strip())
    return names


# ---------------------------------------------------------------- models
class IngIn(BaseModel):
    name: str
    qty: int = 1
    storage: str = "냉장"
    expiry: Optional[str] = None
    memo: str = ""


class IngPatch(BaseModel):
    name: Optional[str] = None
    qty: Optional[int] = None
    storage: Optional[str] = None
    expiry: Optional[str] = None
    memo: Optional[str] = None


class RecipeIn(BaseModel):
    name: str
    minutes: int = 20
    ingredients: List[str] = []
    steps: List[str] = []


# ---------------------------------------------------------------- API: 재료
@app.get("/api/ingredients")
def list_ingredients():
    con = db()
    rows = con.execute("SELECT * FROM ingredients ORDER BY expiry ASC, id ASC").fetchall()
    con.close()
    return [ing_row(r) for r in rows]


def _insert(con, i: IngIn):
    exp = i.expiry or (date.today() + timedelta(days=7)).isoformat()
    cur = con.execute("INSERT INTO ingredients(name,qty,storage,expiry,memo) VALUES(?,?,?,?,?)",
                      (i.name.strip(), max(i.qty, 1), i.storage, exp, i.memo))
    return cur.lastrowid


@app.post("/api/ingredients")
def add_ingredient(i: IngIn):
    con = db()
    _insert(con, i)
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/ingredients/bulk")
def add_bulk(items: List[IngIn]):
    con = db()
    for i in items:
        if i.name.strip():
            _insert(con, i)
    con.commit()
    con.close()
    return {"ok": True, "count": len(items)}


@app.get("/api/ingredients/{iid}")
def get_ingredient(iid: int):
    con = db()
    r = con.execute("SELECT * FROM ingredients WHERE id=?", (iid,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404, "없는 재료")
    return ing_row(r)


@app.patch("/api/ingredients/{iid}")
def patch_ingredient(iid: int, p: IngPatch):
    data = {k: v for k, v in p.dict().items() if v is not None}
    if data:
        con = db()
        sets = ",".join(f"{k}=?" for k in data)
        con.execute(f"UPDATE ingredients SET {sets} WHERE id=?", (*data.values(), iid))
        con.commit()
        con.close()
    return {"ok": True}


@app.delete("/api/ingredients/{iid}")
def del_ingredient(iid: int):
    con = db()
    con.execute("DELETE FROM ingredients WHERE id=?", (iid,))
    con.commit()
    con.close()
    return {"ok": True}


# ---------------------------------------------------------------- API: 영수증 스캔
@app.post("/api/receipt")
async def scan_receipt(file: UploadFile = File(...)):
    data = await file.read()
    mock = False
    try:
        names = await run_ocr(data, file.filename or "receipt.jpg")
    except Exception as e:
        print("[OCR 오류]", e)
        raise HTTPException(502, f"CLOVA OCR 호출 실패: {e}")
    if names is None:
        mock = True
        names = ["양파 1.5kg", "대파 1단", "서울우유 1L", "국산 두부 300g", "삼겹살 500g"]

    candidates = None
    if HCX_KEY and names:
        system = ("너는 영수증 상품명에서 식재료만 추려 정리하는 도우미야. 반드시 JSON 배열만 출력해. "
                  "각 원소는 {\"name\": 일반적인 식재료명(브랜드/용량 제거), \"qty\": 정수 개수, "
                  "\"storage\": \"실온|냉장|냉동\" 중 하나, \"shelf_life_days\": 권장 소비기한 일수 정수} 형식이야. "
                  "봉투, 세제 등 식재료가 아닌 항목은 제외해.")
        out = await hcx(system, "영수증 상품명 목록:\n" + "\n".join(names))
        parsed = parse_json(out)
        if isinstance(parsed, list):
            candidates = []
            for p in parsed:
                try:
                    candidates.append({"name": str(p["name"]), "qty": int(p.get("qty", 1) or 1),
                                       "storage": p.get("storage") if p.get("storage") in ("실온", "냉장", "냉동") else "냉장",
                                       "days": int(p.get("shelf_life_days", 7) or 7)})
                except Exception:
                    continue
    if not candidates:  # 폴백: 단순 정리
        candidates = []
        for n in names:
            clean = re.sub(r"[\d.,]+\s*(kg|g|ml|l|입|개|봉|팩|단)?", "", n, flags=re.I)
            clean = re.sub(r"[^\w가-힣 ]", "", clean).strip()
            if len(clean) >= 2:
                candidates.append({"name": clean, "qty": 1, "storage": "냉장", "days": 7})
    for c in candidates:
        c["expiry"] = (date.today() + timedelta(days=c.pop("days"))).isoformat()
    return {"mock": mock, "raw": names, "candidates": candidates}


# ---------------------------------------------------------------- API: 레시피
@app.get("/api/recipes")
def list_recipes():
    fridge = fridge_names()
    con = db()
    rows = con.execute("SELECT * FROM recipes ORDER BY id DESC").fetchall()
    con.close()
    out = [recipe_row(r, fridge) for r in rows]
    return out


@app.get("/api/recipes/{rid}")
def get_recipe(rid: int):
    con = db()
    r = con.execute("SELECT * FROM recipes WHERE id=?", (rid,)).fetchone()
    con.close()
    if not r:
        raise HTTPException(404, "없는 레시피")
    return recipe_row(r, fridge_names())


@app.post("/api/recipes")
def add_recipe(r: RecipeIn):
    con = db()
    cur = con.execute("INSERT INTO recipes(name,minutes,ingredients,steps,source) VALUES(?,?,?,?,'hcx')",
                      (r.name, r.minutes, json.dumps(r.ingredients, ensure_ascii=False),
                       json.dumps(r.steps, ensure_ascii=False)))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return {"id": rid}


@app.get("/api/recommend")
async def recommend():
    con = db()
    rows = con.execute("SELECT name FROM ingredients ORDER BY expiry ASC").fetchall()
    con.close()
    names = [r["name"] for r in rows]
    if not names:
        return {"message": "냉장고에 재료를 먼저 등록해 주세요.", "recipe": None, "used": []}

    if HCX_KEY:
        system = ("너는 냉장고 재료로 만들 수 있는 요리를 추천하는 요리사야. 소비기한이 임박한 재료(목록 앞쪽)를 우선 활용해. "
                  "반드시 JSON 객체만 출력해. 형식: {\"used\": [사용할 냉장고 재료 2~3개], \"recipe\": {\"name\": 요리명, "
                  "\"minutes\": 정수, \"ingredients\": [재료명 문자열 배열], \"steps\": [조리 단계 문자열 배열(5~7단계, 간결하게)]}}")
        out = await hcx(system, "냉장고 재료(소비기한 임박순): " + ", ".join(names), max_tokens=2048)
        p = parse_json(out)
        if isinstance(p, dict) and isinstance(p.get("recipe"), dict) and p["recipe"].get("name"):
            rec = p["recipe"]
            used = [u for u in p.get("used", []) if isinstance(u, str)][:3] or names[:2]
            rec = {"name": str(rec["name"]), "minutes": int(rec.get("minutes", 20) or 20),
                   "ingredients": [str(x) for x in rec.get("ingredients", [])],
                   "steps": [str(x) for x in rec.get("steps", [])]}
            msg = f"냉장고에 {'과 '.join(used) if len(used) == 2 else ', '.join(used)} 있는 것을 추천드려요. 해당 메뉴로 만들 수 있는 {rec['name']} 어떠신가요?"
            return {"message": msg, "recipe": rec, "used": used}

    # 폴백(키 없음/실패): 보유율 가장 높은 기본 레시피
    con = db()
    rows = con.execute("SELECT * FROM recipes").fetchall()
    con.close()
    best = max((recipe_row(r, names) for r in rows), key=lambda x: x["percent"])
    used = best["have"][:3] or names[:2]
    msg = f"냉장고에 {', '.join(used)} 있는 것을 추천드려요. 해당 메뉴로 만들 수 있는 {best['name']} 어떠신가요?"
    return {"message": msg, "used": used,
            "recipe": {"name": best["name"], "minutes": best["minutes"],
                       "ingredients": best["ingredients"], "steps": best["steps"]}}


# ---------------------------------------------------------------- 프론트 (내장)
@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>릿톤</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#e9e9e9;font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;color:#222}
#app{max-width:430px;min-height:100vh;margin:0 auto;background:#f8f8f8;position:relative;padding-bottom:96px}
#screen{padding:22px 18px}
h1{font-size:26px;margin:6px 4px 16px}h2{font-size:17px;margin:20px 4px 10px}
.card{background:#fff;border-radius:16px;padding:12px 14px;margin-bottom:10px;box-shadow:0 1px 3px #0001}
.row{display:flex;align-items:center;gap:12px;cursor:pointer}.grow{flex:1}
.em{width:54px;height:54px;border-radius:50%;background:#f3f3f3;display:flex;align-items:center;justify-content:center;font-size:30px;flex:none}
.tags{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
.tag{font-size:11px;padding:2px 8px;border-radius:10px;background:#f1f1f1;color:#666;border:1px solid #e4e4e4}
.tag.red{background:#ffe9e9;color:#e33;border-color:#f9c9c9}.tag.orange{background:#fff2e0;color:#d77a00;border-color:#f6d9ad}
.tag.pct{background:#ffe9e9;color:#e33;border-color:#f9c9c9}
.chev{color:#999;font-size:20px;align-self:flex-start}
.add{text-align:center;color:#888;padding:22px;cursor:pointer}
.btn{display:block;width:100%;border:0;border-radius:14px;padding:14px;font-size:15px;background:#7fdcf0;color:#06323c;font-weight:600;cursor:pointer;margin-top:10px}
.btn.del{background:#f4d3d6;color:#c33}.btn.gray{background:#e8e8e8;color:#444}
input,select,textarea{width:100%;padding:12px;border:1px solid #e3e3e3;border-radius:12px;font-size:15px;background:#fff;font-family:inherit}
label{font-size:13px;color:#666;margin:14px 4px 6px;display:block}
.seg{display:flex;background:#eee;border-radius:14px;padding:4px;margin-bottom:14px}
.seg div{flex:1;text-align:center;padding:9px;border-radius:11px;font-size:14px;color:#777;cursor:pointer}
.seg div.on{background:#fff;color:#111;font-weight:600;box-shadow:0 1px 3px #0002}
.pills{display:flex;gap:8px}.pills div{flex:1;text-align:center;padding:10px;border-radius:12px;background:#eee;font-size:13px;cursor:pointer}
.pills div.on{background:#c9dcff;color:#1a3d8f;font-weight:600}
.qty{display:flex;align-items:center;justify-content:center;gap:22px;background:#f1f1f1;border-radius:14px;padding:10px;font-size:17px}
.qty button{border:0;background:#fff;width:34px;height:34px;border-radius:50%;font-size:18px}
nav{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:#fff;border-radius:26px;box-shadow:0 4px 18px #0002;display:flex;gap:6px;padding:8px 14px;z-index:5}
nav div{width:78px;text-align:center;font-size:11px;color:#999;padding:4px;cursor:pointer}
nav div span{display:block;font-size:22px}nav div.on{color:#111}
.back{font-size:26px;cursor:pointer;margin-bottom:6px;display:inline-block;padding:4px 8px 4px 0}
.hero{text-align:center}.hero .big{font-size:110px;line-height:1.1}
.chips{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px}
.chip{flex:none;width:76px;text-align:center;background:#fff;border-radius:14px;padding:10px 4px;font-size:12px;border:1px solid #eee}
.chip.miss{opacity:.45}.chip .e{font-size:28px}
.step{display:flex;gap:12px;background:#fff;border-radius:14px;padding:14px;margin-bottom:10px;font-size:13.5px;line-height:1.6}
.step b{width:20px;text-align:center;border-right:1px solid #eee;padding-right:12px;margin-right:0;flex:none}
.hcx{background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 1px 6px #0002;margin-bottom:10px}
.hcx .t{padding:16px}.hcx h3{margin:0 0 8px;color:#5b87b8;font-size:20px}.hcx p{margin:0;font-size:13.5px;line-height:1.6}
.hcx button{width:100%;border:0;background:#78dbee;padding:12px;font-size:15px;cursor:pointer}
.ring{position:relative;width:300px;height:300px;margin:10px auto}
.ring div{position:absolute;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;box-shadow:0 1px 5px #0003;filter:blur(1.5px);opacity:.8}
.ring div.c{filter:none;opacity:1;border:3px solid #78dbee;box-shadow:0 0 0 4px #78dbee44}
.note{font-size:12px;color:#c77;background:#fff4f4;border-radius:10px;padding:8px 10px;margin-bottom:10px}
.scanbox{border:2px dashed #78dbee;border-radius:16px;padding:40px 10px;text-align:center;color:#3a7d8c;background:#eefbff}
.center{text-align:center;color:#888;padding:40px 0}
.cand{display:flex;gap:8px;align-items:center;margin-bottom:8px}.cand input[type=checkbox]{width:22px;height:22px;flex:none}
.cand input,.cand select{padding:8px;font-size:13px}
</style></head><body>
<div id="app"><div id="screen"></div>
<nav><div id="n-ingredients" onclick="setTab('ingredients')"><span>🍎</span>재료</div>
<div id="n-recipes" onclick="setTab('recipes')"><span>🍴</span>레시피</div>
<div id="n-me" onclick="setTab('me')"><span>👤</span>내 정보</div></nav></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,opt={}){const r=await fetch(url,opt);if(!r.ok){let t=await r.text();throw new Error(t)}return r.json()}
const jpost=(u,b,m='POST')=>api(u,{method:m,headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const EM=[['양파','🧅'],['마늘','🧄'],['계란','🥚'],['달걀','🥚'],['베이컨','🥓'],['대파','🌱'],['파슬리','🌿'],['두부','⬜'],['우유','🥛'],
['삼겹','🥩'],['소고기','🥩'],['돼지','🥩'],['고기','🥩'],['닭','🍗'],['가지','🍆'],['당근','🥕'],['감자','🥔'],['토마토','🍅'],['고추','🌶️'],
['조개','🦪'],['새우','🦐'],['생선','🐟'],['떡','🍡'],['면','🍝'],['파스타','🍝'],['밥','🍚'],['비빔','🍚'],['와인','🍷'],['버섯','🍄'],
['치즈','🧀'],['사과','🍎'],['배추','🥬'],['상추','🥬'],['오이','🥒'],['옥수수','🌽'],['빵','🍞'],['보쌈','🥬'],['장조림','🥚'],['떡볶이','🌶️'],['볶음','🍆']];
const emo=(n,d='🥬')=>{for(const [k,v] of EM) if(String(n).includes(k)) return v;return d}
const state={tab:'ingredients',rtab:'mine'};
function screen(h){$('#screen').innerHTML=h;window.scrollTo(0,0)}
function setTab(t){state.tab=t;['ingredients','recipes','me'].forEach(x=>$('#n-'+x).classList.toggle('on',x===t));
 ({ingredients:showIngredients,recipes:showRecipes,me:showMe})[t]()}

/* ---------- 재료 ---------- */
async function showIngredients(){
 const items=await api('/api/ingredients');
 screen(`<h1>재료</h1>`+items.map(i=>`<div class="card row" onclick="showDetail(${i.id})">
 <div class="em">${emo(i.name)}</div><div class="grow"><b>${esc(i.name)}</b>
 <div class="tags"><span class="tag ${i.d_class}">${i.d_label}</span><span class="tag">${i.storage}보관</span></div></div><span class="chev">›</span></div>`).join('')
 +`<div class="card add" onclick="showAdd()">추가 +</div>`)}

function showAdd(){
 screen(`<span class="back" onclick="showIngredients()">‹</span><h1>재료 추가</h1>
 <div class="card row" onclick="showScan()"><div class="em">🧾</div><div class="grow"><b>영수증으로 스캔</b><div class="tags"><span class="tag">CLOVA OCR</span></div></div><span class="chev">›</span></div>
 <h2>직접 입력</h2>
 <label>재료명</label><input id="a-name" placeholder="예) 대파">
 <label>개수</label><input id="a-qty" type="number" value="1" min="1">
 <label>보관방법</label><select id="a-st"><option>냉장</option><option>실온</option><option>냉동</option></select>
 <label>소비기한</label><input id="a-exp" type="date" value="${new Date(Date.now()+7*864e5).toISOString().slice(0,10)}">
 <button class="btn" onclick="saveAdd()">추가하기</button>`)}
async function saveAdd(){
 const name=$('#a-name').value.trim();if(!name)return alert('재료명을 입력해 주세요');
 await jpost('/api/ingredients',{name,qty:+$('#a-qty').value||1,storage:$('#a-st').value,expiry:$('#a-exp').value});showIngredients()}

async function showDetail(id){
 const i=await api('/api/ingredients/'+id);window._ing=i;
 screen(`<span class="back" onclick="showIngredients()">‹</span>
 <div class="card hero"><div class="big">${emo(i.name)}</div><b style="font-size:18px">${esc(i.name)}</b></div>
 <div class="qty"><button onclick="chgQty(-1)">−</button><span id="d-qty">${i.qty}개</span><button onclick="chgQty(1)">+</button></div>
 <label>보관방법</label><div class="pills" id="d-st">${['실온','냉장','냉동'].map(s=>`<div class="${s===i.storage?'on':''}" onclick="pickSt('${s}')">${s}보관</div>`).join('')}</div>
 <label>소비기한 수정</label><input id="d-exp" type="date" value="${i.expiry||''}">
 <label>메모</label><textarea id="d-memo" rows="2" placeholder="입력하세요....">${esc(i.memo)}</textarea>
 <button class="btn" onclick="saveDetail()">저장</button>
 <button class="btn del" onclick="delIng()">삭제</button>`)}
function chgQty(d){window._ing.qty=Math.max(1,window._ing.qty+d);$('#d-qty').textContent=window._ing.qty+'개'}
function pickSt(s){window._ing.storage=s;[...$('#d-st').children].forEach(c=>c.classList.toggle('on',c.textContent.startsWith(s)))}
async function saveDetail(){const i=window._ing;
 await jpost('/api/ingredients/'+i.id,{qty:i.qty,storage:i.storage,expiry:$('#d-exp').value,memo:$('#d-memo').value},'PATCH');showIngredients()}
async function delIng(){if(!confirm('삭제할까요?'))return;await api('/api/ingredients/'+window._ing.id,{method:'DELETE'});showIngredients()}

/* ---------- 영수증 스캔 ---------- */
function showScan(){
 screen(`<span class="back" onclick="showAdd()">‹</span><h1>영수증 스캔</h1>
 <div class="scanbox" onclick="$('#f').click()">📷<br><br>식재료 영수증을 촬영하거나<br>사진을 선택해 주세요</div>
 <input id="f" type="file" accept="image/*" capture="environment" style="display:none" onchange="doScan(this.files[0])"><div id="scan-out"></div>`)}
async function doScan(file){
 if(!file)return;$('#scan-out').innerHTML='<div class="center">영수증을 분석하는 중…</div>';
 const fd=new FormData();fd.append('file',file);
 try{const r=await api('/api/receipt',{method:'POST',body:fd});window._cands=r.candidates;
  $('#scan-out').innerHTML=(r.mock?'<div class="note">OCR 키가 없어 데모 데이터로 표시 중이에요. .env를 확인해 주세요.</div>':'')
  +'<h2>인식된 재료</h2>'+r.candidates.map((c,k)=>`<div class="cand"><input type="checkbox" id="c-ck${k}" checked>
   <input id="c-n${k}" value="${esc(c.name)}" style="flex:2"><select id="c-s${k}" style="flex:1">${['냉장','실온','냉동'].map(s=>`<option ${s===c.storage?'selected':''}>${s}</option>`).join('')}</select>
   <input id="c-e${k}" type="date" value="${c.expiry}" style="flex:2"></div>`).join('')
  +`<button class="btn" onclick="regCands()">선택한 재료 등록</button>`}
 catch(e){$('#scan-out').innerHTML='<div class="note">스캔 실패: '+esc(e.message).slice(0,300)+'</div>'}}
async function regCands(){const items=[];
 window._cands.forEach((c,k)=>{if($('#c-ck'+k).checked)items.push({name:$('#c-n'+k).value,qty:c.qty||1,storage:$('#c-s'+k).value,expiry:$('#c-e'+k).value})});
 if(!items.length)return alert('선택된 재료가 없어요');await jpost('/api/ingredients/bulk',items);setTab('ingredients')}

/* ---------- 레시피 ---------- */
function rtabs(){return `<h1>레시피</h1><div class="seg"><div class="${state.rtab==='mine'?'on':''}" onclick="state.rtab='mine';showRecipes()">내 레시피</div>
 <div class="${state.rtab==='ai'?'on':''}" onclick="state.rtab='ai';showRecipes()">HyperCLOVA X 추천</div></div>`}
async function showRecipes(){
 if(state.rtab==='ai')return showRecommend();
 const rs=await api('/api/recipes');
 screen(rtabs()+rs.map(r=>`<div class="card row" onclick="showRecipe(${r.id})"><div class="em">${emo(r.name,'🍽️')}</div>
 <div class="grow"><b>${esc(r.name)}</b><div class="tags"><span class="tag">⏰ ${r.minutes}분</span><span class="tag pct">재료 ${r.percent}% 보유</span></div></div><span class="chev">›</span></div>`).join(''))}

async function showRecipe(id){const r=await api('/api/recipes/'+id);
 screen(`<span class="back" onclick="showRecipes()">‹ <b style="font-size:22px">레시피</b></span>
 <div class="hero"><div class="big">${emo(r.name,'🍽️')}</div><h2 style="font-size:20px;margin:6px 0">${esc(r.name)}</h2>
 <div class="tags" style="justify-content:center"><span class="tag pct">재료 ${r.percent}% 보유</span><span class="tag">⏰ ${r.minutes}분</span></div></div>
 <h2>재료</h2><div class="chips">${r.ingredients.map(i=>`<div class="chip ${r.have.includes(i)?'':'miss'}"><div class="e">${emo(i,'🥣')}</div>${esc(i)}</div>`).join('')}</div>
 <h2>조리 방법</h2>${r.steps.map((s,k)=>`<div class="step"><b>${k+1}</b><div>${esc(s)}</div></div>`).join('')}`)}

async function showRecommend(){
 screen(rtabs()+'<div class="center">HyperCLOVA X가 냉장고를 살펴보는 중…</div>');
 try{const r=await api('/api/recommend');window._rec=r;
  const pos=[...Array(8)].map((_,k)=>{const a=k/8*2*Math.PI,R=118,s=k%2?34:50;return `left:${150+R*Math.cos(a)-s/2}px;top:${150+R*Math.sin(a)-s/2}px;width:${s}px;height:${s}px;font-size:${s*0.55}px`});
  const dec=['🍲','🍛','🥘','🍝','🍆','🥗','🍳','🍜'];
  screen(rtabs()+`<div class="hcx"><div class="t"><h3>HyperCLOVA X 추천</h3><p>${esc(r.message)}</p></div>
  ${r.recipe?'<button onclick="makeRec()">만들기</button>':''}</div>
  <div class="ring">${pos.map((p,k)=>`<div style="${p}">${dec[k]}</div>`).join('')}
  <div class="c" style="left:95px;top:95px;width:110px;height:110px;font-size:58px">${r.recipe?emo(r.recipe.name,'🍽️'):'🍽️'}</div></div>`)}
 catch(e){screen(rtabs()+'<div class="note">추천 실패: '+esc(e.message).slice(0,300)+'</div>')}}
async function makeRec(){const r=await jpost('/api/recipes',window._rec.recipe);state.rtab='mine';showRecipe(r.id)}

/* ---------- 내 정보 ---------- */
async function showMe(){const it=await api('/api/ingredients');
 const exp=it.filter(i=>i.d_class==='red').length,soon=it.filter(i=>i.d_class==='orange').length;
 screen(`<h1>내 정보</h1><div class="card"><b>냉장고 현황</b><div class="tags" style="margin-top:10px">
 <span class="tag">전체 ${it.length}개</span><span class="tag orange">임박 ${soon}개</span><span class="tag red">지남 ${exp}개</span></div></div>
 <div class="card" style="font-size:13px;color:#777">릿톤 · 냉장고 재료 관리 & HyperCLOVA X 레시피 추천</div>`)}

setTab('ingredients');
</script></body></html>
"""

if __name__ == "__main__":
    import uvicorn

    print(f"OCR: {'ON' if OCR_URL and OCR_SECRET else 'mock'} | HyperCLOVA X: {'ON' if HCX_KEY else 'mock'}")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
