#!/usr/bin/env python3

import base64
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# .env는 이 파일과 같은 폴더(app/.env)에서 읽는다 → 어느 폴더에서 실행해도 동일하게 동작
load_dotenv(Path(__file__).with_name(".env"))
load_dotenv()  # 현재 폴더의 .env도 보조로 읽음(이미 있는 값은 덮어쓰지 않음)


def env(*names, default=""):
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip().strip('"').strip("'")
    return default


OCR_URL = env("CLOVA_OCR_INVOKE_URL", "OCR_INVOKE_URL")
OCR_SECRET = env("CLOVA_OCR_SECRET_KEY", "OCR_SECRET_KEY", "CLOVA_OCR_SECRET")
# .env에 NAVER_API_KEY로 넣어둔 키도 인식
HCX_KEY = env("CLOVASTUDIO_API_KEY", "HYPERCLOVAX_API_KEY", "NCP_CLOVASTUDIO_API_KEY", "NAVER_API_KEY")
HCX_MODEL = env("CLOVASTUDIO_MODEL", default="HCX-005")
# 영수증 이미지 인식용(이미지 입력 가능한 모델: HCX-005)
HCX_VISION_MODEL = env("CLOVASTUDIO_VISION_MODEL", default="HCX-005")
HCX_BASE = env("CLOVASTUDIO_BASE_URL", default="https://clovastudio.stream.ntruss.com").rstrip("/")
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# 커뮤니티 첫 화면이 비어 있지 않도록 넣는 '릿톤 가이드'(공식) 팁
SEED_POSTS = [
    ("대파 한 단 끝까지 먹는 법", "냉동 보관", ["대파"],
     "대파는 씻어서 물기를 완전히 말린 뒤 송송 썰어 지퍼백에 냉동해 보세요. 필요한 만큼만 꺼내 쓸 수 있어서 한 단을 끝까지 다 먹을 수 있어요."),
    ("양파는 바람 잘 통하는 곳에", "보관 팁", ["양파", "감자"],
     "양파는 망이나 바구니에 담아 서늘하고 바람이 잘 통하는 곳에 두면 오래가요. 감자와 한곳에 두지 않는 것이 좋아요."),
    ("자투리 채소는 볶음밥으로", "남은 재료 활용", ["양파", "당근", "대파"],
     "조금씩 남은 채소는 한 통에 모아 두었다가 주말에 볶음밥이나 채소전으로 한 번에 써요. 버리던 자투리가 한 끼가 돼요."),
    ("장보기 전 냉장고 사진 한 장", "장보기", [],
     "장 보러 가기 전에 냉장고 안을 사진으로 찍어 가면 집에 있는 재료를 또 사는 일이 줄어요. 릿톤 재료 목록을 확인하고 가도 좋아요."),
]


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
        -- 처리(폐기) 안내 캐시: 같은 재료는 HyperCLOVA X를 다시 부르지 않음(토큰 절약)
        CREATE TABLE IF NOT EXISTS disposal_guides(
            name TEXT PRIMARY KEY, guide TEXT NOT NULL, source TEXT, created TEXT);
        -- 버린 재료 기록(통계용)
        CREATE TABLE IF NOT EXISTS disposed(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, qty INTEGER, storage TEXT,
            expiry TEXT, disposed_on TEXT, days_over INTEGER);
        -- 커뮤니티
        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, nickname TEXT NOT NULL, emoji TEXT DEFAULT '🥕', created TEXT);
        CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, title TEXT, content TEXT NOT NULL,
            category TEXT DEFAULT '기타', tags TEXT DEFAULT '[]', created TEXT, official INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS likes(post_id INTEGER, user_id TEXT, PRIMARY KEY(post_id, user_id));
        CREATE TABLE IF NOT EXISTS comments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id TEXT, content TEXT NOT NULL, created TEXT);
        """
    )
    if con.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0] == 0:
        for n, q, s, d in SEED_INGREDIENTS:
            con.execute("INSERT INTO ingredients(name,qty,storage,expiry) VALUES(?,?,?,?)",
                        (n, q, s, (date.today() + timedelta(days=d)).isoformat()))
    if con.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0:  # 커뮤니티 시작용 공식 팁
        con.execute("INSERT OR IGNORE INTO users(id,nickname,emoji,created) VALUES('official','릿톤 가이드','🌱',?)", (now_iso(),))
        for k, (title, cat, tags, content) in enumerate(SEED_POSTS):
            created = (datetime.now(timezone.utc) - timedelta(days=len(SEED_POSTS) - k, hours=k)).isoformat()
            con.execute("INSERT INTO posts(user_id,title,content,category,tags,created,official) VALUES('official',?,?,?,?,?,1)",
                        (title, content, cat, json.dumps(tags, ensure_ascii=False), created))
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
            "expiry": r["expiry"], "memo": r["memo"], "d_label": label, "d_class": cls,
            "expired": diff is not None and diff > 0, "days_over": diff if diff is not None and diff > 0 else 0}


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
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    for o, c in (("[", "]"), ("{", "}")):
        s, e = text.find(o), text.rfind(c)
        if s != -1 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except Exception:
                continue
    return None


class HCXError(Exception):
    """HyperCLOVA X 호출 실패(키 없음, 인증 오류, 한도 초과, 타임아웃 등)."""

    def __init__(self, msg: str, http_status: int = 0):
        super().__init__(msg)
        self.http_status = http_status


def _clip(v, n=120):
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


async def hcx_chat(messages: list, model: Optional[str] = None, max_tokens: int = 2048,
                   temperature: float = 0.3) -> str:
    """CLOVA Studio Chat Completions v3 호출 → 응답 텍스트. 실패하면 HCXError."""
    if not HCX_KEY:
        raise HCXError("API 키가 없어요. app/.env에 CLOVASTUDIO_API_KEY=nv-... 를 넣어 주세요.")
    model = model or HCX_MODEL
    body = {"messages": messages, "topP": 0.8, "topK": 0, "maxTokens": max_tokens,
            "temperature": temperature, "repetitionPenalty": 1.1}
    headers = {"Authorization": f"Bearer {HCX_KEY}", "Content-Type": "application/json",
               "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex, "Accept": "application/json"}
    url = f"{HCX_BASE}/v3/chat-completions/{model}"
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        raise HCXError("응답 시간 초과(90초)")
    except httpx.HTTPError as e:
        raise HCXError(f"네트워크 오류: {e!r}")

    try:
        data = r.json()
    except Exception:
        data = None
    status = (data or {}).get("status") or {}
    if r.status_code != 200 or str(status.get("code", "20000")) != "20000":
        detail = status.get("message") or r.text[:300]
        hint = {401: " (API 키가 틀렸거나 만료됨)", 403: " (이 키로 해당 모델 사용 권한 없음)",
                404: f" (모델명 '{model}' 확인 필요)", 429: " (호출 한도 초과 — 잠시 후 다시 시도)"}.get(r.status_code, "")
        raise HCXError(f"HTTP {r.status_code} / code {status.get('code', '-')}: {detail}{hint}", r.status_code)
    try:
        content = data["result"]["message"]["content"]
    except (KeyError, TypeError):
        raise HCXError(f"예상과 다른 응답 형식: {r.text[:300]}")
    if isinstance(content, list):  # 혹시 배열로 오면 텍스트만 이어붙임
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    print(f"[HyperCLOVA X] {model} OK  tokens={data['result'].get('usage', {}).get('totalTokens')}  "
          f"응답: {_clip(content)!r}")
    return content


async def hcx_openai_chat(messages: list, model: str, max_tokens: int = 1024, temperature: float = 0.1) -> str:
    """CLOVA Studio OpenAI 호환 엔드포인트(/v1/openai/chat/completions) 호출 → 응답 텍스트."""
    if not HCX_KEY:
        raise HCXError("API 키가 없어요.")
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    headers = {"Authorization": f"Bearer {HCX_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{HCX_BASE}/v1/openai/chat/completions", headers=headers, json=body)
    except httpx.TimeoutException:
        raise HCXError("응답 시간 초과(90초)")
    except httpx.HTTPError as e:
        raise HCXError(f"네트워크 오류: {e!r}")
    try:
        data = r.json()
    except Exception:
        data = None
    if r.status_code != 200 or not isinstance(data, dict) or not data.get("choices"):
        err = (data or {}).get("error") or (data or {}).get("status") or {}
        detail = (err.get("message") if isinstance(err, dict) else str(err)) or r.text[:300]
        raise HCXError(f"HTTP {r.status_code} (OpenAI 호환): {detail}", r.status_code)
    content = data["choices"][0]["message"]["content"] or ""
    print(f"[HyperCLOVA X/OpenAI호환] {model} OK  응답: {_clip(content)!r}")
    return content


def image_mime(data: bytes) -> Optional[str]:
    """HCX가 받는 형식(JPG/PNG/WEBP/BMP)인지 확인 → MIME, 아니면 None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return None


_VISION_MODE = {"ok": None}  # 성공한 이미지 전송 방식을 기억해서 다음부터 바로 사용


async def hcx_vision(system: str, prompt: str, data: bytes, mime: str) -> str:
    """이미지 + 텍스트 요청. v3(dataUri) 형식 2가지 → OpenAI 호환 형식 순서로 시도."""
    b64 = base64.b64encode(data).decode()
    durl = f"data:{mime};base64,{b64}"

    def v3(img_data):
        return lambda: hcx_chat([
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "dataUri": {"data": img_data}},
                {"type": "text", "text": prompt}]},
        ], model=HCX_VISION_MODEL, max_tokens=1024, temperature=0.1)

    modes = {
        "v3-base64": v3(b64),
        "v3-datauri": v3(durl),
        "openai": lambda: hcx_openai_chat([
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": durl}},
                {"type": "text", "text": prompt}]},
        ], model=HCX_VISION_MODEL),
    }
    order = list(modes)
    if _VISION_MODE["ok"] in modes:
        order.remove(_VISION_MODE["ok"])
        order.insert(0, _VISION_MODE["ok"])
    errors = []
    for name in order:
        try:
            out = await modes[name]()
            if _VISION_MODE["ok"] != name:
                print(f"[HyperCLOVA X 비전] '{name}' 방식으로 성공 — 이후 이 방식 사용")
            _VISION_MODE["ok"] = name
            return out
        except HCXError as e:
            print(f"[HyperCLOVA X 비전] '{name}' 방식 실패: {e}")
            errors.append(f"[{name}] {e}")
            if e.http_status in (401, 403, 429) or e.http_status >= 500:
                break  # 키/권한/한도/서버 문제는 형식을 바꿔도 소용없음
    raise HCXError(" / ".join(errors))


async def hcx(system: str, user: str, max_tokens: int = 2048, model: Optional[str] = None) -> str:
    """system + user 텍스트 한 번 호출."""
    return await hcx_chat([{"role": "system", "content": system},
                           {"role": "user", "content": user}], model=model, max_tokens=max_tokens)


def _to_int(v, default=1):
    m = re.search(r"\d+", str(v if v is not None else ""))
    return int(m.group()) if m else default


def normalize_candidates(parsed) -> list:
    """HCX가 돌려준 식재료 JSON 배열 → 화면용 후보 리스트."""
    if isinstance(parsed, dict):  # {"items":[...]} 처럼 감싸서 올 때
        parsed = next((v for v in parsed.values() if isinstance(v, list)), None)
    if not isinstance(parsed, list):
        return []
    out = []
    for p in parsed:
        if isinstance(p, str):
            p = {"name": p}
        if not isinstance(p, dict) or not str(p.get("name", "")).strip():
            continue
        st = p.get("storage")
        out.append({"name": str(p["name"]).strip(), "qty": max(_to_int(p.get("qty"), 1), 1),
                    "storage": st if st in ("실온", "냉장", "냉동") else "냉장",
                    "days": min(max(_to_int(p.get("shelf_life_days"), 7), 1), 365)})
    return out


RECEIPT_RULES = ("반드시 JSON 배열만 출력해(설명 문장, 마크다운 금지). "
                 "각 원소는 {\"name\": 일반적인 식재료명(브랜드/용량/수식어 제거, 예: '서울우유 1L'→'우유'), "
                 "\"qty\": 구매 개수 정수, \"storage\": \"실온\"|\"냉장\"|\"냉동\" 중 하나, "
                 "\"shelf_life_days\": 구매일부터 권장 소비기한까지 일수 정수} 형식이야. "
                 "상품명이 브랜드명/가공식품명이면 그 식품의 일반 이름으로 바꿔 "
                 "(예: '마늘빅프랑크'→'소시지', '신라면'→'라면', '예거라들러레몬'→'맥주', '마운틴듀'→'탄산음료'). "
                 "상품명에 재료 이름이 들어 있어도(예: 마늘빅프랑크의 '마늘') 그 재료를 산 것이 아니니 따로 뽑지 마. "
                 "봉투, 세제, 생활용품, 할인/합계/결제/포인트 줄처럼 식품이 아닌 항목은 제외해. "
                 "식품이 하나도 없으면 []를 출력해.")

# 1단계(비전): 영수증 사진에서 품목 줄만 글자 그대로 옮겨 적기
TRANSCRIBE_SYSTEM = "너는 영수증 사진의 글자를 정확하게 읽어 옮겨 적는 OCR 도우미야. 해석하거나 바꾸지 말고 보이는 그대로 적어."
TRANSCRIBE_PROMPT = (
    "이 영수증에서 '구매한 상품' 줄만 찾아 옮겨 적어 줘.\n"
    "- 상품 줄은 보통 '상품명 수량 금액' 형태로, 매장 정보 아래와 '합계/합계수량' 위에 있어.\n"
    "- 매장명, 주소, 전화번호, 날짜, 안내 문구, 합계, 할인, 과세/부가세, 결제, 카드, 포인트 줄은 적지 마.\n"
    "- 한 줄에 상품 하나씩 '상품명 | 수량 | 금액' 형식으로, 다른 설명 없이 출력해.\n"
    "- 상품명은 영수증에 인쇄된 글자 그대로 적어(고치거나 일반명으로 바꾸지 마).")


def parse_item_lines(text: str) -> List[str]:
    """비전 모델이 옮겨 적은 '상품명 | 수량 | 금액' 줄 → 상품명(+수량) 리스트."""
    out = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line.strip().strip("`")).strip()
        if not line or "상품명" in line.replace(" ", "")[:6]:
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        if not re.search(r"[가-힣A-Za-z]", name) or re.search(r"합계|할인|부가세|과세|카드|포인트|결제", name):
            continue
        qty = parts[1] if len(parts) > 1 and re.fullmatch(r"\d{1,3}", parts[1]) else ""
        out.append(f"{name} (수량 {qty})" if qty else name)
    return out


async def names_to_candidates(names: List[str]) -> list:
    """상품명 목록 → HyperCLOVA X로 식재료 후보 정리."""
    out = await hcx("너는 영수증 상품명에서 식품만 추려 냉장고에 넣을 식재료로 정리하는 도우미야. " + RECEIPT_RULES,
                    "영수증 상품명 목록:\n" + "\n".join(names))
    return normalize_candidates(parse_json(out))


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
    n = 0
    for i in items:
        if i.name.strip():
            _insert(con, i)
            n += 1
    con.commit()
    con.close()
    return {"ok": True, "count": n}


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
    data = {k: v for k, v in p.model_dump().items() if v is not None}
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
    if not data:
        raise HTTPException(400, "빈 파일이에요.")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "이미지가 너무 커요(20MB 이하).")
    mock, engine, error = False, "", None
    names: List[str] = []
    candidates: Optional[list] = None

    if OCR_URL and OCR_SECRET:
        # 1) CLOVA OCR로 상품명 추출 → 2) HyperCLOVA X로 식재료 정리
        engine = "CLOVA OCR"
        try:
            names = await run_ocr(data, file.filename or "receipt.jpg") or []
        except Exception as e:
            print("[OCR 오류]", e)
            raise HTTPException(502, f"CLOVA OCR 호출 실패: {e}")
        if HCX_KEY and names:
            try:
                candidates = await names_to_candidates(names)
                engine += " + HyperCLOVA X"
            except HCXError as e:
                print("[HyperCLOVA X 오류]", e)
                error = str(e)
    elif HCX_KEY:
        # OCR 키가 없으면 HyperCLOVA X(HCX-005) 비전으로 영수증 이미지를 직접 읽음
        engine = f"HyperCLOVA X 비전({HCX_VISION_MODEL})"
        mime = image_mime(data)
        if not mime:
            raise HTTPException(415, "JPG/PNG/WEBP/BMP 이미지만 인식할 수 있어요. "
                                     "(아이폰 HEIC 사진이면 스크린샷을 찍거나 JPG로 바꿔서 올려 주세요)")
        try:  # 1단계: HCX-005가 품목 줄을 글자 그대로 옮겨 적음
            text = await hcx_vision(TRANSCRIBE_SYSTEM, TRANSCRIBE_PROMPT, data, mime)
        except HCXError as e:
            print("[HyperCLOVA X 비전 오류]", e)
            raise HTTPException(502, f"HyperCLOVA X 영수증 인식 실패: {e}")
        print("[영수증 1단계 - 읽은 품목]\n" + text)
        names = parse_item_lines(text)
        if names:
            try:  # 2단계: 읽은 상품명을 식재료로 정리
                candidates = await names_to_candidates(names)
                engine += f" + {HCX_MODEL}"
            except HCXError as e:
                print("[HyperCLOVA X 오류]", e)
                error = str(e)
        else:
            candidates = []
    else:
        mock, engine = True, "데모 데이터"
        names = ["양파 1.5kg", "대파 1단", "서울우유 1L", "국산 두부 300g", "삼겹살 500g"]

    if candidates is None:  # 폴백: 상품명 단순 정리
        candidates = []
        for n in names:
            clean = re.sub(r"\(수량 \d+\)", "", n)
            clean = re.sub(r"[\d.,]+\s*(kg|g|ml|l|입|개|봉|팩|단)?", "", clean, flags=re.I)
            clean = re.sub(r"[^\w가-힣 ]", "", clean).strip()
            if len(clean) >= 2:
                candidates.append({"name": clean, "qty": 1, "storage": "냉장", "days": 7})
    for c in candidates:
        c["expiry"] = (date.today() + timedelta(days=c.pop("days"))).isoformat()
    return {"mock": mock, "engine": engine, "error": error, "raw": names, "candidates": candidates}


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
    steps_json = json.dumps(r.steps, ensure_ascii=False)
    dup = con.execute("SELECT id FROM recipes WHERE name=? AND steps=?", (r.name, steps_json)).fetchone()
    if dup:  # 같은 레시피를 여러 번 '만들기' 눌러도 중복 저장 안 함
        con.close()
        return {"id": dup["id"]}
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

    error = None
    system = ("너는 냉장고 재료로 만들 수 있는 요리를 추천하는 요리사야. 소비기한이 임박한 재료(목록 앞쪽)를 우선 활용해. "
              "반드시 JSON 객체만 출력해(설명 문장, 마크다운 금지). 형식: {\"used\": [사용할 냉장고 재료 2~3개], "
              "\"recipe\": {\"name\": 요리명, \"minutes\": 정수, \"ingredients\": [재료명 문자열 배열], "
              "\"steps\": [조리 단계 문자열 배열(5~7단계, 간결하게)]}}")
    try:
        out = await hcx(system, "냉장고 재료(소비기한 임박순): " + ", ".join(names), max_tokens=2048)
        p = parse_json(out)
        if isinstance(p, dict) and isinstance(p.get("recipe"), dict) and p["recipe"].get("name"):
            rec = p["recipe"]
            used = [str(u) for u in (p.get("used") or []) if isinstance(u, str)][:3] or names[:2]
            rec = {"name": str(rec["name"]), "minutes": _to_int(rec.get("minutes"), 20),
                   "ingredients": [str(x) for x in (rec.get("ingredients") or [])],
                   "steps": [str(x) for x in (rec.get("steps") or [])]}
            msg = f"냉장고에 {', '.join(used)}이(가) 있어요. 이 재료로 만들 수 있는 {rec['name']} 어떠신가요?"
            return {"message": msg, "recipe": rec, "used": used, "source": "hcx", "error": None}
        error = f"응답을 JSON으로 해석하지 못했어요: {_clip(out, 200)}"
    except HCXError as e:
        error = str(e)
    print("[추천 폴백]", error)

    # 폴백(키 없음/실패): 보유율 가장 높은 기본 레시피
    con = db()
    rows = con.execute("SELECT * FROM recipes WHERE source='seed'").fetchall()
    con.close()
    best = max((recipe_row(r, names) for r in rows), key=lambda x: x["percent"])
    used = best["have"][:3] or names[:2]
    msg = f"냉장고에 {', '.join(used)}이(가) 있어요. 이 재료로 만들 수 있는 {best['name']} 어떠신가요?"
    return {"message": msg, "used": used, "source": "fallback", "error": error,
            "recipe": {"name": best["name"], "minutes": best["minutes"],
                       "ingredients": best["ingredients"], "steps": best["steps"]}}


# ---------------------------------------------------------------- API: 커뮤니티
# 로그인 대신 브라우저마다 만든 사용자 ID(uid)와 닉네임으로 구분 (해커톤용 간단 방식)
CATEGORIES = ("보관 팁", "남은 재료 활용", "냉동 보관", "장보기", "기타")
UID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def check_uid(uid: Optional[str]) -> str:
    if not uid or not UID_RE.match(uid):
        raise HTTPException(400, "사용자 정보가 없어요. 커뮤니티에서 닉네임을 먼저 정해 주세요.")
    con = db()
    ok = con.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    if not ok:
        raise HTTPException(401, "닉네임을 먼저 정해 주세요.")
    return uid


class UserIn(BaseModel):
    id: str
    nickname: str
    emoji: str = "🥕"


class PostIn(BaseModel):
    uid: str
    content: str
    category: Optional[str] = None


class CommentIn(BaseModel):
    uid: str
    content: str


class TextIn(BaseModel):
    content: str


class UidIn(BaseModel):
    uid: str


@app.post("/api/community/me")
def save_me(u: UserIn):
    if not UID_RE.match(u.id) or u.id == "official":
        raise HTTPException(400, "잘못된 사용자 ID")
    nick = re.sub(r"\s+", " ", u.nickname).strip()
    if not 2 <= len(nick) <= 12:
        raise HTTPException(400, "닉네임은 2~12자로 정해 주세요.")
    con = db()
    dup = con.execute("SELECT 1 FROM users WHERE nickname=? AND id<>?", (nick, u.id)).fetchone()
    if dup or nick == "릿톤 가이드":
        con.close()
        raise HTTPException(409, "이미 쓰고 있는 닉네임이에요.")
    con.execute("INSERT INTO users(id,nickname,emoji,created) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname, emoji=excluded.emoji",
                (u.id, nick, (u.emoji or "🥕")[:4], now_iso()))
    con.commit()
    con.close()
    return get_me(u.id)


@app.get("/api/community/me")
def get_me(uid: str):
    con = db()
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        con.close()
        return {"user": None}
    posts = con.execute("SELECT COUNT(*) FROM posts WHERE user_id=?", (uid,)).fetchone()[0]
    liked = con.execute("SELECT COUNT(*) FROM likes l JOIN posts p ON p.id=l.post_id WHERE p.user_id=?", (uid,)).fetchone()[0]
    con.close()
    return {"user": {"id": r["id"], "nickname": r["nickname"], "emoji": r["emoji"]}, "posts": posts, "likes_received": liked}


POST_SQL = """SELECT p.*, u.nickname, u.emoji,
    (SELECT COUNT(*) FROM likes l WHERE l.post_id=p.id) AS like_count,
    (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
    EXISTS(SELECT 1 FROM likes l WHERE l.post_id=p.id AND l.user_id=?) AS liked
    FROM posts p LEFT JOIN users u ON u.id=p.user_id"""


def post_row(r, uid):
    return {"id": r["id"], "title": r["title"], "content": r["content"], "category": r["category"],
            "tags": json.loads(r["tags"] or "[]"), "created": r["created"], "official": bool(r["official"]),
            "author": {"nickname": r["nickname"] or "(알 수 없음)", "emoji": r["emoji"] or "🥕"},
            "like_count": r["like_count"], "comment_count": r["comment_count"], "liked": bool(r["liked"]),
            "mine": bool(uid) and r["user_id"] == uid}


@app.get("/api/community/posts")
def list_posts(uid: str = "", category: str = "", sort: str = "latest", q: str = "", fridge: bool = False):
    where, args = [], [uid]
    if category in CATEGORIES:
        where.append("p.category=?")
        args.append(category)
    if q.strip():
        where.append("(p.title LIKE ? OR p.content LIKE ? OR p.tags LIKE ?)")
        args += [f"%{q.strip()}%"] * 3
    order = "like_count DESC, p.created DESC" if sort == "popular" else "p.created DESC"
    sql = POST_SQL + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order} LIMIT 100"
    con = db()
    rows = [post_row(r, uid) for r in con.execute(sql, args).fetchall()]
    con.close()
    if fridge:  # 내 냉장고에 있는 재료와 관련된 글만
        names = fridge_names()
        rows = [r for r in rows if any(has(names, t) for t in r["tags"])]
        for r in rows:
            r["match"] = [t for t in r["tags"] if has(names, t)]
    return rows


@app.get("/api/community/posts/{pid}")
def get_post(pid: int, uid: str = ""):
    con = db()
    r = con.execute(POST_SQL + " WHERE p.id=?", (uid, pid)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "없는 글이에요.")
    cs = con.execute("SELECT c.*, u.nickname, u.emoji FROM comments c LEFT JOIN users u ON u.id=c.user_id "
                     "WHERE c.post_id=? ORDER BY c.id ASC", (pid,)).fetchall()
    con.close()
    out = post_row(r, uid)
    out["comments"] = [{"id": c["id"], "content": c["content"], "created": c["created"],
                        "author": {"nickname": c["nickname"] or "(알 수 없음)", "emoji": c["emoji"] or "🥕"},
                        "mine": bool(uid) and c["user_id"] == uid} for c in cs]
    return out


TAG_SYSTEM = (
    "너는 식재료 낭비 줄이기 커뮤니티의 글 정리 도우미야. 반드시 JSON 객체만 출력해(설명 문장, 마크다운 금지). "
    "형식: {\"ok\": 욕설·혐오 표현·광고·홍보·전화번호 같은 개인정보가 없으면 true, 있으면 false, "
    "\"reason\": ok가 false일 때 이유 한 문장(아니면 빈 문자열), "
    "\"title\": 글 내용을 요약한 20자 이내 제목, "
    "\"category\": \"보관 팁\"|\"남은 재료 활용\"|\"냉동 보관\"|\"장보기\"|\"기타\" 중 하나, "
    "\"tags\": 글에 나온 식재료 이름 배열(최대 5개, 브랜드 없이 일반 이름으로, 없으면 [])}")


def simple_tags(text: str) -> List[str]:
    """HyperCLOVA X를 못 쓸 때: 냉장고/레시피에 있는 재료명이 글에 나오면 태그로."""
    con = db()
    names = set(fridge_names())
    for r in con.execute("SELECT ingredients FROM recipes").fetchall():
        names.update(json.loads(r["ingredients"] or "[]"))
    con.close()
    names.update(["양파", "마늘", "대파", "계란", "감자", "당근", "두부", "우유", "버섯", "밥", "김치", "고기", "빵", "바나나"])
    return [n for n in sorted(names, key=len, reverse=True) if len(n) >= 1 and n in text][:5]


@app.post("/api/community/posts")
async def create_post(p: PostIn):
    uid = check_uid(p.uid)
    content = p.content.strip()
    if not 5 <= len(content) <= 1000:
        raise HTTPException(400, "내용은 5~1000자로 써 주세요.")
    if re.search(r"01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}", content):  # HyperCLOVA X 없이도 막는 기본 필터
        raise HTTPException(422, "글을 올릴 수 없어요: 전화번호 같은 개인정보는 빼고 써 주세요.")
    meta, ai = {}, False
    try:
        meta = parse_json(await hcx(TAG_SYSTEM, content, max_tokens=300)) or {}
        meta = meta if isinstance(meta, dict) else {}
        ai = bool(meta)
    except HCXError as e:
        print("[커뮤니티 태그 폴백]", e)
    if meta.get("ok") is False:
        raise HTTPException(422, "글을 올릴 수 없어요: " + (str(meta.get("reason") or "부적절한 내용이 있어요.")))
    title = str(meta.get("title") or "").strip()[:30] or _clip(re.split(r"[.!?\n]", content)[0], 24)
    category = p.category if p.category in CATEGORIES else (meta.get("category") if meta.get("category") in CATEGORIES else "기타")
    tags = [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()][:5] if ai else simple_tags(content)
    con = db()
    cur = con.execute("INSERT INTO posts(user_id,title,content,category,tags,created) VALUES(?,?,?,?,?,?)",
                      (uid, title, content, category, json.dumps(tags, ensure_ascii=False), now_iso()))
    con.commit()
    pid = cur.lastrowid
    con.close()
    return {"id": pid, "title": title, "category": category, "tags": tags, "ai": ai}


@app.delete("/api/community/posts/{pid}")
def delete_post(pid: int, uid: str):
    con = db()
    r = con.execute("SELECT user_id FROM posts WHERE id=?", (pid,)).fetchone()
    if not r or r["user_id"] != uid:
        con.close()
        raise HTTPException(403, "내가 쓴 글만 지울 수 있어요.")
    for t in ("likes", "comments"):
        con.execute(f"DELETE FROM {t} WHERE post_id=?", (pid,))
    con.execute("DELETE FROM posts WHERE id=?", (pid,))
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/community/posts/{pid}/like")
def toggle_like(pid: int, body: UidIn):
    uid = check_uid(body.uid)
    con = db()
    if not con.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone():
        con.close()
        raise HTTPException(404, "없는 글이에요.")
    if con.execute("SELECT 1 FROM likes WHERE post_id=? AND user_id=?", (pid, uid)).fetchone():
        con.execute("DELETE FROM likes WHERE post_id=? AND user_id=?", (pid, uid))
        liked = False
    else:
        con.execute("INSERT INTO likes(post_id,user_id) VALUES(?,?)", (pid, uid))
        liked = True
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM likes WHERE post_id=?", (pid,)).fetchone()[0]
    con.close()
    return {"liked": liked, "like_count": n}


@app.post("/api/community/posts/{pid}/comments")
def add_comment(pid: int, c: CommentIn):
    uid = check_uid(c.uid)
    text = c.content.strip()
    if not 1 <= len(text) <= 300:
        raise HTTPException(400, "댓글은 1~300자로 써 주세요.")
    con = db()
    if not con.execute("SELECT 1 FROM posts WHERE id=?", (pid,)).fetchone():
        con.close()
        raise HTTPException(404, "없는 글이에요.")
    con.execute("INSERT INTO comments(post_id,user_id,content,created) VALUES(?,?,?,?)", (pid, uid, text, now_iso()))
    con.commit()
    con.close()
    return {"ok": True}


@app.delete("/api/community/comments/{cid}")
def delete_comment(cid: int, uid: str):
    con = db()
    r = con.execute("SELECT user_id FROM comments WHERE id=?", (cid,)).fetchone()
    if not r or r["user_id"] != uid:
        con.close()
        raise HTTPException(403, "내가 쓴 댓글만 지울 수 있어요.")
    con.execute("DELETE FROM comments WHERE id=?", (cid,))
    con.commit()
    con.close()
    return {"ok": True}


@app.post("/api/community/polish")
async def polish(t: TextIn):
    """대충 쓴 경험담 → HyperCLOVA X가 올리기 좋게 다듬기(내용은 그대로)."""
    text = t.content.strip()
    if not 5 <= len(text) <= 1000:
        raise HTTPException(400, "5자 이상 써 주세요.")
    try:
        out = await hcx("너는 글 다듬기 도우미야. 사용자가 쓴 '식재료 낭비를 줄인 경험'을 커뮤니티에 올리기 좋게 다듬어. "
                        "원래 내용과 사실(재료, 방법, 숫자)을 바꾸거나 새로운 정보를 지어내지 마. 맞춤법과 문장만 자연스럽게, "
                        "친근한 존댓말로, 300자 이내로. 다듬은 글만 출력해(따옴표, 제목, 설명 없이).", text, max_tokens=600)
    except HCXError as e:
        raise HTTPException(502, f"HyperCLOVA X 호출 실패: {e}")
    out = out.strip().strip('"').strip()
    return {"content": out or text}


# ---------------------------------------------------------------- API: 소비기한 지난 재료 처리 안내
BINS = ("음식물", "일반", "플라스틱", "비닐", "종이", "종이팩", "캔", "유리", "스티로폼")

DISPOSAL_SYSTEM = (
    "너는 한국의 생활폐기물 분리배출 기준을 잘 아는 주방 위생 도우미야. 소비기한이 지난 식재료를 안전하게 버리는 방법을 안내해. "
    "반드시 JSON 배열만 출력해(설명 문장, 마크다운 금지). 입력한 재료마다 원소 하나씩, 입력 순서대로. 각 원소 형식: "
    "{\"name\": 입력한 재료명 그대로, "
    "\"verdict\": 먹지 말고 버려야 하는 이유를 담은 한 문장, "
    "\"parts\": [{\"part\": 부위(예: 알맹이, 껍질·뿌리, 껍데기, 내용물, 뼈), \"bin\": 배출 종류, \"how\": 짧은 처리 방법}], "
    "\"packaging\": [{\"item\": 이 재료를 살 때 흔히 딸려오는 포장재, \"bin\": 배출 종류, \"how\": 짧은 처리 방법}], "
    "\"caution\": 위생·안전 주의사항 한 문장, \"tip\": 다음에 이 재료를 더 오래 두고 먹는 보관 팁 한 문장}. "
    "bin은 반드시 \"음식물\",\"일반\",\"플라스틱\",\"비닐\",\"종이\",\"종이팩\",\"캔\",\"유리\",\"스티로폼\" 중 하나. "
    "기준: 동물이 먹기 어려운 딱딱하거나 질긴 것(양파·마늘·대파의 마른 껍질과 뿌리, 달걀·조개·게 껍데기, 뼈, "
    "복숭아 등 딱딱한 씨, 옥수수 껍질과 속대, 견과류 껍데기, 티백)은 \"일반\". "
    "음식물은 물기와 이물질(비닐, 이쑤시개, 스티커 등)을 빼고 버린다. 포장재는 내용물을 비우고 헹궈서 재질별로 버린다. "
    "우유팩 같은 종이팩은 일반 종이와 따로 \"종이팩\". 확실하지 않은 내용은 지어내지 마.")

# HyperCLOVA X를 쓸 수 없을 때 쓰는 기본 규칙 (환경부 '음식물류 폐기물이 아닌 것' 기준)
HARD_PARTS = [
    (("양파", "마늘", "대파", "쪽파", "생강"), "마른 껍질·뿌리", "일반", "말라 있는 껍질과 뿌리는 종량제 봉투(일반쓰레기)로 버려요."),
    (("계란", "달걀", "메추리알"), "껍데기", "일반", "껍데기는 일반쓰레기, 내용물만 음식물로 버려요."),
    (("조개", "굴", "전복", "꽃게", "게", "소라", "홍합", "가리비", "바지락"), "껍데기", "일반", "껍데기는 일반쓰레기로 버려요."),
    (("닭", "갈비", "족발", "생선", "고등어"), "뼈·가시", "일반", "뼈와 가시는 골라내 일반쓰레기로 버려요."),
    (("옥수수",), "껍질·속대", "일반", "껍질과 속대는 일반쓰레기로 버려요."),
    (("복숭아", "자두", "살구", "망고", "아보카도"), "씨", "일반", "딱딱한 씨는 일반쓰레기로 버려요."),
    (("호두", "밤", "땅콩", "아몬드"), "껍데기", "일반", "딱딱한 껍데기는 일반쓰레기로 버려요."),
]


def fallback_guide(name: str) -> dict:
    if any(k in name for k in ("우유", "두유", "주스", "요거트", "요구르트")):
        parts = [{"part": "내용물(액체)", "bin": "기타", "how": "내용물을 비운 뒤 용기를 헹궈요. 액체 처리 방법은 사는 지역 안내를 따라 주세요."}]
    else:
        parts = [{"part": "먹는 부분", "bin": "음식물", "how": "물기를 꼭 빼고 비닐·스티커 같은 이물질을 골라낸 뒤 음식물 쓰레기로 버려요."}]
    for keys, part, b, how in HARD_PARTS:
        if any(k in name for k in keys):
            parts.append({"part": part, "bin": b, "how": how})
            break
    return {"name": name, "source": "basic",
            "verdict": "소비기한이 지난 식품은 겉으로 멀쩡해 보여도 먹지 말고 버리는 것이 안전해요.",
            "parts": parts,
            "packaging": ([{"item": "우유팩(종이팩)", "bin": "종이팩", "how": "헹궈서 펼쳐 말린 뒤 일반 종이와 따로 배출해요."}]
                          if any(k in name for k in ("우유", "두유", "주스")) else
                          [{"item": "비닐 포장(있다면)", "bin": "비닐", "how": "이물질을 털어내고 비닐류로 버려요."},
                           {"item": "플라스틱 용기(있다면)", "bin": "플라스틱", "how": "내용물을 비우고 헹군 뒤 버려요."}]),
            "caution": "곰팡이가 피었거나 냄새가 나면 다른 음식에 닿지 않게 봉투에 담아 바로 버리고 손을 씻어요.",
            "tip": ""}


def normalize_guide(g: dict, name: str) -> Optional[dict]:
    if not isinstance(g, dict):
        return None

    def rows(key, label):
        out = []
        for x in g.get(key) or []:
            if isinstance(x, dict) and (x.get(label) or x.get("how")):
                b = str(x.get("bin", "")).strip()
                out.append({label: str(x.get(label, "")).strip(), "how": str(x.get("how", "")).strip(),
                            "bin": b if b in BINS else ("일반" if not b else b)})
        return out

    parts = rows("parts", "part")
    if not parts:
        return None
    return {"name": name, "source": "hcx", "verdict": str(g.get("verdict", "")).strip(), "parts": parts,
            "packaging": rows("packaging", "item"), "caution": str(g.get("caution", "")).strip(),
            "tip": str(g.get("tip", "")).strip()}


async def disposal_guides(names: List[str]):
    """재료명 목록 → {이름: 안내}. 캐시에 없으면 HyperCLOVA X 한 번에 묶어서 호출."""
    names = list(dict.fromkeys(n for n in names if n))
    con = db()
    cache = {}
    for n in names:
        row = con.execute("SELECT guide FROM disposal_guides WHERE name=?", (n,)).fetchone()
        if row:
            cache[n] = json.loads(row["guide"])
    missing = [n for n in names if n not in cache][:8]  # 한 번에 최대 8개
    error = None
    if missing:
        try:
            out = await hcx(DISPOSAL_SYSTEM, "소비기한이 지난 재료:\n" + "\n".join(f"- {n}" for n in missing),
                            max_tokens=3500)
            parsed = parse_json(out)
            if isinstance(parsed, dict):
                parsed = next((v for v in parsed.values() if isinstance(v, list)), [parsed])
            parsed = parsed if isinstance(parsed, list) else []
            for k, n in enumerate(missing):
                g = next((x for x in parsed if isinstance(x, dict) and str(x.get("name", "")).strip() == n), None)
                if g is None and len(parsed) == len(missing):
                    g = parsed[k]
                g = normalize_guide(g, n)
                if g:
                    cache[n] = g
                    con.execute("INSERT OR REPLACE INTO disposal_guides(name,guide,source,created) VALUES(?,?,?,?)",
                                (n, json.dumps(g, ensure_ascii=False), "hcx", date.today().isoformat()))
            con.commit()
            if any(n not in cache for n in missing):
                error = "일부 재료는 HyperCLOVA X 응답을 해석하지 못해 기본 안내로 보여드려요."
        except HCXError as e:
            print("[처리 안내 폴백]", e)
            error = str(e)
    con.close()
    return {n: cache.get(n) or fallback_guide(n) for n in names}, error


@app.get("/api/disposal")
async def disposal(id: Optional[int] = None):
    con = db()
    rows = [ing_row(r) for r in con.execute("SELECT * FROM ingredients ORDER BY expiry ASC, id ASC").fetchall()]
    con.close()
    expired = [r for r in rows if r["expired"] and (id is None or r["id"] == id)]
    soon = [r for r in rows if not r["expired"] and r["d_class"] in ("red", "orange")]  # D-Day ~ D-3
    guides, error = await disposal_guides([r["name"] for r in expired])
    for r in expired:
        r["guide"] = guides[r["name"]]
    return {"expired": expired, "soon": soon, "error": error}


@app.post("/api/ingredients/{iid}/dispose")
def dispose_ingredient(iid: int):
    """'버렸어요' → 냉장고에서 빼고 버린 기록을 남김."""
    con = db()
    r = con.execute("SELECT * FROM ingredients WHERE id=?", (iid,)).fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "없는 재료")
    info = ing_row(r)
    con.execute("INSERT INTO disposed(name,qty,storage,expiry,disposed_on,days_over) VALUES(?,?,?,?,?,?)",
                (r["name"], r["qty"], r["storage"], r["expiry"], date.today().isoformat(), info["days_over"]))
    con.execute("DELETE FROM ingredients WHERE id=?", (iid,))
    con.commit()
    con.close()
    return {"ok": True}


@app.get("/api/stats")
def stats():
    con = db()
    month = date.today().strftime("%Y-%m")
    m = con.execute("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM disposed WHERE disposed_on LIKE ?", (month + "%",)).fetchone()
    total = con.execute("SELECT COUNT(*) FROM disposed").fetchone()[0]
    top = [dict(name=r[0], count=r[1]) for r in con.execute(
        "SELECT name, COUNT(*) c FROM disposed GROUP BY name ORDER BY c DESC, MAX(id) DESC LIMIT 3").fetchall()]
    con.close()
    return {"disposed_month": m[0], "disposed_month_qty": m[1], "disposed_total": total, "top": top}


@app.get("/api/status")
async def status(ping: bool = False):
    """연결 상태 확인. /api/status?ping=true 면 HyperCLOVA X에 실제로 짧게 호출해 봄."""
    res = {"hcx_key": bool(HCX_KEY), "hcx_key_hint": (HCX_KEY[:5] + "…") if HCX_KEY else "",
           "model": HCX_MODEL, "vision_model": HCX_VISION_MODEL,
           "ocr": bool(OCR_URL and OCR_SECRET), "env_file": str(Path(__file__).with_name(".env"))}
    if ping:
        try:
            res["ping"] = {"ok": True, "reply": await hcx("한 단어로만 답해.", "안녕?", max_tokens=20)}
        except HCXError as e:
            res["ping"] = {"ok": False, "error": str(e)}
    return res


# ---------------------------------------------------------------- 프론트 (내장)
@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>릿톤</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
:root{--bg:#fafafa;--card:#fff;--soft:#f3f3f4;--line:#ececec;--text:#1c1c1e;--sub:#8e8e93;
 --cyan:#7fd8ea;--cyan-ink:#0d4652;--blue:#4f86c6;--cal:#2f7cf6;--sel:#d6e4f8;--sel-ink:#2c4f86;
 --red:#e5484d;--red-bg:#fde6e6;--red-line:#f7caca;--del:#f6d8da;
 --shadow:0 1px 2px rgba(0,0,0,.03),0 4px 14px rgba(0,0,0,.04)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:#ececec}
body{font-family:Pretendard,-apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif;color:var(--text);letter-spacing:-.01em}
button,input,select,textarea{font-family:inherit;color:inherit}
#app{max-width:430px;min-height:100vh;margin:0 auto;background:var(--bg);position:relative;padding-bottom:110px;overflow-x:hidden}
#screen{padding:28px 18px 0}
h1{font-size:28px;font-weight:800;margin:14px 6px 20px;letter-spacing:-.02em}
h2{font-size:18px;font-weight:700;margin:26px 4px 12px}
.card{background:var(--card);border-radius:18px;box-shadow:var(--shadow)}
.list .card{display:flex;align-items:center;gap:14px;padding:14px 14px 14px 12px;margin-bottom:10px;cursor:pointer;position:relative}
.list .card:active{transform:scale(.99)}
.grow{flex:1;min-width:0}
.title{font-size:17px;font-weight:600}
.ingimg{width:64px;height:58px;display:flex;align-items:center;justify-content:center;font-size:46px;flex:none;filter:drop-shadow(0 2px 2px rgba(0,0,0,.12))}
.chev{position:absolute;right:14px;top:14px;width:18px;height:18px;color:#9a9a9f}
.tags{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;align-items:center}
.tag{font-size:11px;line-height:1;padding:5px 9px;border-radius:999px;background:#fff;color:#8a8a8e;border:1px solid var(--line);white-space:nowrap}
.tag.red,.tag.pct{background:var(--red-bg);color:var(--red);border-color:var(--red-bg)}
.tag.red{border-color:var(--red-line)}
.time{display:inline-flex;align-items:center;gap:3px;font-size:12px;color:#333}
.time svg{width:13px;height:13px}
.addcard{display:flex!important;justify-content:center;padding:34px!important;color:#666;font-size:15px}
.back{display:inline-flex;align-items:center;gap:6px;cursor:pointer;padding:6px 6px 6px 0;color:#8e8e93}
.back svg{width:22px;height:22px}
.back.big{color:var(--text);margin:8px 0 6px}.back.big b{font-size:28px;font-weight:800;letter-spacing:-.02em}
/* nav */
nav{position:fixed;bottom:22px;left:50%;transform:translateX(-50%);display:flex;gap:4px;padding:8px 18px;z-index:20;
 background:rgba(255,255,255,.92);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-radius:999px;box-shadow:0 8px 28px rgba(0,0,0,.12)}
nav div{width:64px;text-align:center;font-size:10px;color:#9a9a9f;cursor:pointer;padding:3px 0}
nav svg{display:block;margin:0 auto 2px;width:24px;height:24px}
nav div.on{color:#1c1c1e}
body.dark nav,body.sub nav{display:none}
body.sub #app{padding-bottom:40px}
/* detail */
.hero{background:var(--soft);border-radius:24px;text-align:center;padding:30px 10px 22px;margin-top:6px}
.hero .big{font-size:112px;line-height:1.1;filter:drop-shadow(0 6px 8px rgba(0,0,0,.15))}
.hero b{display:block;font-size:18px;font-weight:600;margin-top:12px}
.qty{display:flex;align-items:center;justify-content:space-between;background:var(--soft);border-radius:999px;padding:4px 6px;margin-top:10px;font-size:16px}
.qty button{border:0;background:transparent;width:40px;height:36px;font-size:22px;cursor:pointer;color:#333}
label.lb{display:block;font-size:15px;font-weight:600;margin:24px 4px 10px}
.pills{display:flex;gap:8px}
.pills div{flex:1;text-align:center;padding:9px 0;border-radius:999px;background:var(--soft);font-size:13px;color:#6b6b70;cursor:pointer;transition:.15s}
.pills div.on{background:var(--sel);color:var(--sel-ink);font-weight:600}
.daterow{display:flex;align-items:center;justify-content:space-between;background:var(--soft);border-radius:999px;padding:13px 16px 13px 20px;font-size:15px;cursor:pointer}
.daterow svg{width:18px;height:18px;color:#9a9a9f}
.field{width:100%;padding:14px 16px;border:1px solid var(--line);border-radius:16px;font-size:15px;background:#fff;outline:none}
.field:focus{border-color:#cfd8e6}
textarea.field{resize:none;min-height:84px}
.btn{display:block;width:100%;border:0;border-radius:999px;padding:16px;font-size:15px;font-weight:600;cursor:pointer;margin-top:22px;
 background:var(--cyan);color:var(--cyan-ink);box-shadow:0 6px 16px rgba(127,216,234,.35)}
.btn.del{background:var(--del);color:#d64545;box-shadow:0 6px 16px rgba(214,69,69,.12)}
.btn.ghost{background:var(--soft);color:#444;box-shadow:none}
.saved{font-size:12px;color:#9a9a9f;text-align:right;height:16px;margin:6px 6px 0;transition:opacity .3s}
/* recipe */
.seg{display:flex;background:#efeff0;border-radius:999px;padding:4px;margin:0 0 22px}
.seg div{flex:1;text-align:center;padding:11px 4px;border-radius:999px;font-size:13.5px;color:#6b6b70;cursor:pointer}
.seg div.on{background:#fff;color:var(--text);font-weight:600;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.seg div.on.ai{color:var(--blue)}
.plate{border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none;
 background:radial-gradient(circle at 50% 45%,#fff 0 52%,#efe8df 53%,#f8f4ef 64%,#e9e1d6 100%);box-shadow:0 2px 6px rgba(0,0,0,.10)}
.plate span{filter:drop-shadow(0 2px 2px rgba(0,0,0,.15))}
.hcx{background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 4px 22px rgba(127,216,234,.30),0 1px 2px rgba(0,0,0,.04);margin-bottom:6px}
.hcx .t{padding:20px 20px 18px}
.hcx h3{margin:0 0 10px;color:var(--blue);font-size:23px;font-weight:800;letter-spacing:-.02em}
.hcx p{margin:0;font-size:14px;line-height:1.65;color:#444}.hcx p b{color:#1c1c1e}
.hcx button{width:100%;border:0;background:var(--cyan);color:var(--cyan-ink);padding:14px;font-size:15px;font-weight:600;cursor:pointer}
.ring{position:relative;width:330px;height:330px;margin:4px auto 0}
.ring .plate{position:absolute}
.ring .far{filter:blur(2.5px);opacity:.75}
.ring .c{box-shadow:0 0 0 5px #a6e6f3,0 0 0 11px rgba(127,216,234,.28),0 6px 18px rgba(0,0,0,.15)}
.rhero{text-align:center}
.rhero .plate{margin:6px auto 0}
.rhero h2{font-size:21px;margin:18px 0 8px}
.chips{display:flex;gap:10px;overflow-x:auto;padding:2px 2px 10px;margin:0 -18px;padding-left:18px;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:none;width:92px;height:108px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;background:#fff;border-radius:16px;box-shadow:var(--shadow);font-size:12px;color:#444;text-align:center;padding:6px}
.chip .e{font-size:40px;filter:drop-shadow(0 2px 2px rgba(0,0,0,.12))}
.chip.miss{opacity:.5}.chip.miss .e{filter:grayscale(.9) blur(.6px)}
.step{display:flex;background:#fff;border-radius:16px;box-shadow:var(--shadow);margin-bottom:10px;font-size:13.5px;line-height:1.7;color:#333}
.step b{flex:none;width:44px;display:flex;justify-content:center;padding-top:14px;font-weight:500;border-right:1px solid var(--line);margin:8px 0}
.step div{padding:14px 16px}
.note{font-size:12.5px;line-height:1.5;color:#b45353;background:#fff3f3;border-radius:12px;padding:10px 12px;margin-bottom:12px}
.center{text-align:center;color:#8e8e93;padding:44px 0;font-size:14px}
/* bottom sheet + calendar */
.ov{position:fixed;inset:0;z-index:40;display:flex;align-items:flex-end;justify-content:center;
 background:rgba(250,250,250,.35);backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px);animation:fade .2s}
.sheet{width:100%;max-width:430px;background:#fff;border-radius:30px 30px 0 0;padding:24px 18px 30px;box-shadow:0 -8px 30px rgba(0,0,0,.08);animation:up .25s ease-out;max-height:88vh;overflow:auto}
@keyframes up{from{transform:translateY(40px);opacity:.4}}@keyframes fade{from{opacity:0}}
.sh-h{display:flex;align-items:center;justify-content:space-between;margin:0 4px 18px}
.sh-h b{font-size:19px;font-weight:700}
.okbtn{width:36px;height:36px;border-radius:50%;border:0;background:#b9b9bc;color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer}
.okbtn svg{width:18px;height:18px}
.cal{border:1px solid var(--line);border-radius:18px;padding:14px 12px 10px}
.cal-h{display:flex;align-items:center;justify-content:space-between;padding:0 6px 10px}
.cal-h b{font-size:15px;font-weight:700;display:flex;align-items:center;gap:4px}
.cal-h b i{color:var(--cal);font-style:normal}
.cal-h span{display:flex;gap:18px}.cal-h button{border:0;background:none;color:var(--cal);cursor:pointer;padding:2px}
.cal-h svg{width:20px;height:20px}
.cal-g{display:grid;grid-template-columns:repeat(7,1fr);text-align:center}
.cal-g .w{font-size:10.5px;color:#c4c4c8;font-weight:600;padding-bottom:6px}
.cal-g .d{height:44px;display:flex;align-items:center;justify-content:center;font-size:18px;cursor:pointer}
.cal-g .d span{width:42px;height:42px;display:flex;align-items:center;justify-content:center;border-radius:50%}
.cal-g .d.today span{color:var(--cal)}
.cal-g .d.sel span{background:#dbe8fd;color:var(--cal);font-weight:700;font-size:21px}
/* scan (camera) */
body.dark,body.dark #app{background:#17110e}
.scan{min-height:100vh;margin:-28px -18px 0;padding:22px 18px 30px;color:#fff;position:relative}
.scan .back{color:#fff}
.banner{background:var(--cyan);color:var(--cyan-ink);text-align:center;font-size:13.5px;font-weight:600;padding:13px;margin:12px 0 16px}
.frame{position:relative;aspect-ratio:3/4.3;border:3px dashed #74d6ec;border-radius:16px;overflow:hidden;background:#241a15;display:flex;align-items:center;justify-content:center;cursor:pointer}
.frame video,.frame img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.frame img{object-fit:contain;background:#241a15}
.frame .hint{text-align:center;color:#c9bdb6;font-size:14px;line-height:1.7;padding:20px}
.frame .loading{position:absolute;inset:0;background:rgba(0,0,0,.55);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;font-size:14px}
.spin{width:34px;height:34px;border:3px solid rgba(255,255,255,.25);border-top-color:var(--cyan);border-radius:50%;animation:sp 1s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.shutter{display:flex;align-items:center;justify-content:center;gap:56px;margin-top:22px}
.shutter .shot{width:70px;height:70px;border-radius:50%;background:#fff;border:5px solid #17110e;box-shadow:0 0 0 3px #fff;cursor:pointer}
.shutter .gal{width:46px;height:46px;border-radius:12px;background:rgba(255,255,255,.14);display:flex;align-items:center;justify-content:center;cursor:pointer}
.shutter .gal svg{width:24px;height:24px;color:#fff}.shutter .ph{width:46px}
.cand{display:flex;align-items:center;gap:10px;padding:10px 4px;border-bottom:1px solid #f2f2f2}
.cand:last-of-type{border-bottom:0}
.ck{width:24px;height:24px;border-radius:50%;border:1.5px solid #cfcfd3;flex:none;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#fff}
.ck.on{background:var(--cyan);border-color:var(--cyan);color:var(--cyan-ink)}.ck svg{width:14px;height:14px}
.cand .em{font-size:28px;width:34px;text-align:center;flex:none}
.cand input.n{border:0;font-size:15px;font-weight:600;width:100%;outline:none;background:transparent;padding:2px 0}
.cand .sub{display:flex;gap:6px;margin-top:4px}
.cand select,.cand input[type=date]{border:1px solid var(--line);border-radius:999px;font-size:12px;padding:4px 8px;background:#fff;color:#555}
details.raw{font-size:12.5px;color:#666;background:var(--soft);border-radius:12px;padding:10px 12px;margin:0 0 12px;line-height:1.6}
.me-row{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;font-size:15px}
.me-row+.me-row{border-top:1px solid #f2f2f2}
/* 소비기한 지난 재료 처리 */
.alert{display:flex;align-items:center;gap:12px;background:#fff1f1;border:1px solid #f8d2d2;border-radius:18px;padding:14px 16px;margin-bottom:14px;cursor:pointer}
.alert .ic{width:40px;height:40px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;flex:none;box-shadow:0 1px 3px rgba(229,72,77,.15)}
.alert b{display:block;font-size:15px;color:#c93a3f}.alert small{font-size:12.5px;color:#9a6b6d}
.alert svg{width:18px;height:18px;color:#d89a9c;flex:none}
.intro{background:#fff1f1;border-radius:20px;padding:18px 18px 16px;margin-bottom:16px}
.intro>b{font-size:19px;color:#c93a3f;display:block;margin-bottom:6px}.intro p{margin:0;font-size:13.5px;line-height:1.6;color:#7a5a5c}.intro p b{color:#a8383c}
.intro.ok{background:#eefaf3}.intro.ok>b{color:#23885a}.intro.ok p{color:#4f7a62}
.dcard{background:#fff;border-radius:20px;box-shadow:var(--shadow);padding:16px 16px 14px;margin-bottom:14px;transition:opacity .3s,transform .3s}
.dcard.gone{opacity:0;transform:translateX(30px)}
.dhead{display:flex;align-items:center;gap:12px}
.dhead .ingimg{width:52px;height:50px;font-size:40px}
.verdict{font-size:13.5px;line-height:1.6;color:#555;margin:12px 2px 4px}
.dsub{font-size:12.5px;font-weight:700;color:#8e8e93;margin:14px 2px 8px}
.brow{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-top:1px solid #f3f3f3}
.brow:first-of-type{border-top:0}
.bin{flex:none;min-width:58px;text-align:center;font-size:11.5px;font-weight:700;padding:5px 8px;border-radius:8px;background:#ececee;color:#555}
.bin.b-음식물{background:#fff0dc;color:#b8650a}.bin.b-플라스틱{background:#e3effd;color:#2f6fd6}.bin.b-비닐{background:#efe7fb;color:#7a4bc7}
.bin.b-종이{background:#f3ece2;color:#8a6a3e}.bin.b-종이팩{background:#e2f5ea;color:#23885a}.bin.b-캔{background:#e9edf1;color:#4d5d6d}
.bin.b-유리{background:#e0f5f7;color:#1f8a99}
.brow div{font-size:13.5px;line-height:1.55;color:#444}.brow div b{color:#1c1c1e;margin-right:4px}
.hint2{display:flex;gap:8px;font-size:13px;line-height:1.55;color:#555;background:var(--soft);border-radius:12px;padding:10px 12px;margin-top:8px}
.dbtns{display:flex;gap:8px;margin-top:14px}
.dbtns button{flex:1;border:0;border-radius:999px;padding:12px;font-size:14px;font-weight:600;cursor:pointer}
.dbtns .b1{background:var(--soft);color:#444}.dbtns .b2{background:var(--cyan);color:var(--cyan-ink)}
.soon{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.soon span{display:inline-flex;align-items:center;gap:6px;background:#fff;border-radius:999px;padding:7px 12px 7px 9px;font-size:13px;box-shadow:var(--shadow)}
.soon i{font-style:normal;font-size:11px;color:#d77a00;font-weight:700}
.rules{background:#fff;border-radius:20px;box-shadow:var(--shadow);padding:16px 18px;font-size:13.5px;line-height:1.75;color:#444}
.rules ul{margin:6px 0 0;padding-left:18px}.rules small{display:block;color:#9a9a9f;font-size:12px;margin-top:10px;line-height:1.5}
.dlink{display:block;text-align:center;font-size:13px;color:var(--blue);margin-top:12px;cursor:pointer}
/* 커뮤니티 */
.sub-t{font-size:14px;color:#8e8e93;margin:-12px 6px 16px}
.search{display:flex;align-items:center;gap:8px;background:#efeff0;border-radius:999px;padding:11px 16px;margin-bottom:12px}
.search svg{width:18px;height:18px;color:#9a9a9f;flex:none}
.search input{border:0;background:transparent;outline:none;font-size:15px;width:100%}
.cchips{display:flex;gap:8px;overflow-x:auto;margin:0 -18px 10px;padding:2px 18px 4px;scrollbar-width:none}.cchips::-webkit-scrollbar{display:none}
.cchips div{flex:none;padding:8px 14px;border-radius:999px;background:#fff;font-size:13px;color:#555;cursor:pointer;box-shadow:var(--shadow)}
.cchips div.on{background:#1c1c1e;color:#fff}
.cchips div.fr.on{background:var(--cyan);color:var(--cyan-ink)}
.sortbar{display:flex;justify-content:flex-end;gap:12px;font-size:12.5px;color:#a0a0a5;margin:2px 6px 10px}
.sortbar span{cursor:pointer}.sortbar span.on{color:#1c1c1e;font-weight:600}
.post{padding:16px 16px 12px;margin-bottom:12px;cursor:pointer}
.ph{display:flex;align-items:center;gap:10px}
.av{width:38px;height:38px;border-radius:50%;background:var(--soft);display:flex;align-items:center;justify-content:center;font-size:21px;flex:none}
.ph b{font-size:14px;font-weight:600}.ph small{display:block;font-size:12px;color:#a0a0a5;margin-top:1px}
.off{display:inline-block;font-size:10.5px;font-weight:700;color:#23885a;background:#e2f5ea;border-radius:6px;padding:2px 6px;margin-left:6px;vertical-align:1px}
.pt{font-size:16.5px;font-weight:700;margin:12px 0 6px;letter-spacing:-.01em}
.pc{font-size:14px;line-height:1.65;color:#444;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;white-space:pre-wrap}
.pc.full{display:block;-webkit-line-clamp:unset;font-size:15px;line-height:1.75;color:#333}
.ptags{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.ptags span{font-size:12px;color:#5b7fa8;background:#eef4fb;border-radius:999px;padding:4px 10px}
.ptags span.m{background:#e3f7fb;color:#0f6c80;font-weight:600}
.pf{display:flex;gap:16px;margin-top:12px;padding-top:10px;border-top:1px solid #f3f3f3;font-size:13px;color:#8e8e93}
.pf span{display:inline-flex;align-items:center;gap:5px;cursor:pointer}.pf svg{width:17px;height:17px}
.pf .lk.on{color:var(--red)}.pf .lk.on svg{fill:var(--red)}
.fab{position:fixed;bottom:98px;right:max(18px,calc(50% - 197px));z-index:15;border:0;border-radius:999px;background:var(--cyan);color:var(--cyan-ink);
 font-size:15px;font-weight:700;padding:14px 20px;box-shadow:0 8px 22px rgba(127,216,234,.55);cursor:pointer;display:flex;align-items:center;gap:6px}
.fab svg{width:18px;height:18px}
.cmt{display:flex;gap:10px;padding:12px 2px;border-top:1px solid #f1f1f1}
.cmt .av{width:32px;height:32px;font-size:17px}
.cmt b{font-size:13px;font-weight:600}.cmt small{font-size:11.5px;color:#a0a0a5;margin-left:6px}
.cmt p{margin:3px 0 0;font-size:14px;line-height:1.6;color:#333;white-space:pre-wrap}
.cmt .x{font-size:11.5px;color:#b0b0b5;cursor:pointer;margin-left:6px}
.cbar{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:100%;max-width:430px;background:rgba(255,255,255,.96);
 backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-top:1px solid #eee;padding:10px 14px calc(10px + env(safe-area-inset-bottom));display:flex;gap:8px;z-index:25}
.cbar input{flex:1;border:0;background:var(--soft);border-radius:999px;padding:12px 16px;font-size:14.5px;outline:none}
.cbar button{border:0;background:var(--cyan);color:var(--cyan-ink);border-radius:999px;padding:0 18px;font-weight:700;cursor:pointer}
.bigheart{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:9px 16px;font-size:14px;cursor:pointer;margin-top:16px}
.bigheart svg{width:18px;height:18px}.bigheart.on{border-color:#f7caca;background:var(--red-bg);color:var(--red)}.bigheart.on svg{fill:var(--red)}
.cpills{display:flex;flex-wrap:wrap;gap:8px}
.cpills div{padding:9px 14px;border-radius:999px;background:var(--soft);font-size:13px;color:#6b6b70;cursor:pointer}
.cpills div.on{background:var(--sel);color:var(--sel-ink);font-weight:600}
textarea.write{min-height:210px;font-size:15px;line-height:1.7}
.wfoot{display:flex;justify-content:space-between;align-items:center;margin:8px 4px 0;font-size:12.5px;color:#a0a0a5}
.aibtn{border:1px solid #cfe0f6;background:#f2f7fe;color:#3a6fb0;border-radius:999px;padding:9px 14px;font-size:13px;font-weight:600;cursor:pointer}
.emos{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px}
.emos div{width:44px;height:44px;border-radius:50%;background:var(--soft);display:flex;align-items:center;justify-content:center;font-size:23px;cursor:pointer;border:2px solid transparent}
.emos div.on{border-color:var(--cyan);background:#effbfd}
.toast{position:fixed;left:50%;bottom:110px;transform:translateX(-50%);background:rgba(28,28,30,.92);color:#fff;font-size:13.5px;padding:11px 16px;border-radius:999px;z-index:60;
 max-width:min(92vw,400px);text-align:center;animation:fade .2s}
.prof{display:flex;align-items:center;gap:14px;padding:16px 18px}
.prof .av{width:52px;height:52px;font-size:28px}
.prof b{font-size:17px}.prof small{display:block;font-size:12.5px;color:#8e8e93;margin-top:3px}
.prof button{border:0;background:var(--soft);border-radius:999px;padding:8px 12px;font-size:12.5px;cursor:pointer;color:#555}
</style></head><body>
<div id="app"><div id="screen"></div>
<nav>
 <div id="n-ingredients" onclick="setTab('ingredients')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 7.5c-1.6-1.3-4.3-1.5-6 .3-1.9 2-1.6 5.8 0 8.7 1.2 2.2 2.7 4 4.2 4 .9 0 1.1-.5 1.8-.5s.9.5 1.8.5c1.5 0 3-1.8 4.2-4 1.6-2.9 1.9-6.7 0-8.7-1.7-1.8-4.4-1.6-6-.3z"/><path d="M12 7.5c0-1.8.7-3.4 2.3-4.3"/></svg>재료</div>
 <div id="n-recipes" onclick="setTab('recipes')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 3v6.5a2.5 2.5 0 0 0 5 0V3M9 3v18"/><path d="M17.5 21V3c-2 1-3 4.2-3 8.5h3"/></svg>레시피</div>
 <div id="n-community" onclick="setTab('community')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4.5h11a1.5 1.5 0 0 1 1.5 1.5v6.5A1.5 1.5 0 0 1 15 14H9.2L6 17v-3H4a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 4 4.5z"/><path d="M19.5 8.5h.5A1.5 1.5 0 0 1 21.5 10v6.5A1.5 1.5 0 0 1 20 18h-1.5v3l-3.3-3H11"/></svg>커뮤니티</div>
 <div id="n-me" onclick="setTab('me')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="8" r="3.6"/><path d="M4.8 20.2c1-3.6 3.8-5.4 7.2-5.4s6.2 1.8 7.2 5.4z"/></svg>내 정보</div>
</nav></div>
<input id="f" type="file" accept="image/*" style="display:none" onchange="this.files[0]&&doScan(this.files[0]);this.value=''">
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url,opt={}){const r=await fetch(url,opt);if(!r.ok){let t=await r.text();try{t=JSON.parse(t).detail??t}catch(_){}throw new Error(typeof t==='string'?t:JSON.stringify(t))}return r.json()}
const jpost=(u,b,m='POST')=>api(u,{method:m,headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const I={
 chev:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>',
 back:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
 clock:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="13" r="7.5"/><path d="M12 9.5V13l2.5 1.5M5 4.5 3 6.5M19 4.5l2 2"/></svg>',
 check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
 left:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
 right:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>',
 photo:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.8"/><path d="M21 16l-5-5-9 9"/></svg>',
 cam:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>'};
/* 이모지(사진 대신) */
const RM=[['떡볶이','🍢'],['보쌈','🥩'],['장조림','🥚'],['비빔밥','🥗'],['가지','🍆'],['파스타','🍝'],['스파게티','🍝'],['카레','🍛'],['김치찌개','🍲'],['찌개','🍲'],['국','🍲'],['볶음밥','🍛'],
 ['오믈렛','🍳'],['계란','🍳'],['샐러드','🥗'],['라면','🍜'],['국수','🍜'],['덮밥','🍛'],['샌드위치','🥪'],['토스트','🍞'],['전','🥞'],['구이','🍖'],['볶음','🍳']];
const EM=[['고춧가루','🌶️'],['고추장','🌶️'],['양파','🧅'],['마늘','🧄'],['계란','🥚'],['달걀','🥚'],['베이컨','🥓'],['대파','🌿'],['파슬리','🌿'],['산나물','🌿'],['두부','🧈'],['우유','🥛'],
 ['삼겹','🥩'],['소고기','🥩'],['돼지','🥩'],['고기','🥩'],['소시지','🌭'],['프랑크','🌭'],['햄','🍖'],['닭','🍗'],['가지','🍆'],['당근','🥕'],['감자','🥔'],['토마토','🍅'],['고추','🌶️'],
 ['조개','🦪'],['새우','🦐'],['생선','🐟'],['어묵','🍢'],['떡','🍡'],['링귀','🍝'],['면','🍝'],['파스타','🍝'],['밥','🍚'],['쌀','🍚'],['와인','🍷'],['맥주','🍺'],['음료','🥤'],['콜라','🥤'],['사이다','🥤'],
 ['버섯','🍄'],['치즈','🧀'],['버터','🧈'],['사과','🍎'],['레몬','🍋'],['바나나','🍌'],['배추','🥬'],['김치','🥬'],['상추','🥬'],['오이','🥒'],['옥수수','🌽'],['브로콜리','🥦'],['빵','🍞'],
 ['올리브','🫒'],['생강','🫚'],['간장','🫙'],['된장','🫙'],['참기름','🫗'],['기름','🫗'],['설탕','🧂'],['소금','🧂'],['라면','🍜']];
const emo=(n,d='🥬')=>{for(const [k,v] of EM) if(String(n).includes(k)) return v;return d};
const remo=n=>{for(const [k,v] of RM) if(String(n).includes(k)) return v;return emo(n,'🍽️')};
const plate=(n,s,cls='',st='')=>`<div class="plate ${cls}" style="width:${s}px;height:${s}px;${st}"><span style="font-size:${Math.round(s*.5)}px">${remo(n)}</span></div>`;
/* 받침에 따른 조사 */
const jong=w=>{const c=String(w).trim().slice(-1).charCodeAt(0);return c>=0xAC00&&c<=0xD7A3&&(c-0xAC00)%28>0};
const fmt=iso=>iso?iso.replaceAll('-','.'):'날짜 선택';
const isoOf=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const plus=n=>isoOf(new Date(Date.now()+n*864e5));

const state={tab:'ingredients',rtab:'mine',stream:null};
function screen(h,dark=false,sub=false){stopCam();document.querySelectorAll('.fab,.cbar,.toast').forEach(e=>e.remove());document.body.classList.toggle('dark',dark);document.body.classList.toggle('sub',sub||dark);$('#screen').innerHTML=h;window.scrollTo(0,0)}
function setTab(t){state.tab=t;['ingredients','recipes','community','me'].forEach(x=>$('#n-'+x).classList.toggle('on',x===t));
 ({ingredients:showIngredients,recipes:showRecipes,community:showCommunity,me:showMe})[t]()}

/* ---------- 재료 목록 ---------- */
async function showIngredients(){
 const items=await api('/api/ingredients');
 const ex=items.filter(i=>i.expired);
 screen(`<h1>재료</h1>`+(ex.length?`<div class="alert" onclick="showDisposal()"><div class="ic">🗑️</div><div class="grow"><b>소비기한이 지난 재료 ${ex.length}개</b>
  <small>${ex.slice(0,3).map(i=>esc(i.name)).join(', ')}${ex.length>3?' 외':''} · 처리 방법을 확인해 주세요</small></div>${I.chev}</div>`:'')+`<div class="list">`+items.map(i=>`<div class="card" onclick="showDetail(${i.id})">
  <div class="ingimg">${emo(i.name)}</div><div class="grow"><div class="title">${esc(i.name)}</div>
  <div class="tags"><span class="tag ${i.d_class==='red'?'red':''}">${i.d_label}</span><span class="tag">${esc(i.storage)}보관</span></div></div>
  <span class="chev">${I.chev}</span></div>`).join('')
  +`<div class="card addcard" onclick="showAdd()">추가 +</div></div>`)}

/* ---------- 재료 추가 ---------- */
function showAdd(){
 window._new={qty:1,storage:'냉장',expiry:plus(7)};
 screen(`<span class="back" onclick="showIngredients()">${I.back}</span><h1 style="margin-top:4px">재료 추가</h1>
 <div class="list"><div class="card" onclick="showScan()"><div class="ingimg" style="font-size:40px">🧾</div>
  <div class="grow"><div class="title">영수증으로 추가</div><div class="tags"><span class="tag">HyperCLOVA X가 자동으로 정리해요</span></div></div><span class="chev">${I.chev}</span></div></div>
 <h2>직접 입력</h2>
 <input id="a-name" class="field" placeholder="재료명 (예: 대파)">
 <div class="qty"><button onclick="nq(-1)">−</button><span id="a-qty">1개</span><button onclick="nq(1)">+</button></div>
 <label class="lb">보관방법</label><div class="pills" id="a-st">${stPills('냉장','pickNewSt')}</div>
 <label class="lb">소비기한</label><div class="daterow" onclick="openCal(window._new.expiry,v=>{window._new.expiry=v;$('#a-exp').textContent=fmt(v)})"><span id="a-exp">${fmt(window._new.expiry)}</span>${I.chev}</div>
 <button class="btn" onclick="saveAdd()">추가하기</button>`,false,true)}
const stPills=(cur,fn)=>['실온','냉장','냉동'].map(s=>`<div class="${s===cur?'on':''}" onclick="${fn}('${s}',this)">${s}보관</div>`).join('');
function nq(d){const n=window._new;n.qty=Math.max(1,n.qty+d);$('#a-qty').textContent=n.qty+'개'}
function pickNewSt(s,el){window._new.storage=s;[...el.parentNode.children].forEach(c=>c.classList.toggle('on',c===el))}
async function saveAdd(){const name=$('#a-name').value.trim();if(!name){$('#a-name').focus();return}
 await jpost('/api/ingredients',{name,...window._new});showIngredients()}

/* ---------- 재료 상세 (변경 즉시 저장) ---------- */
async function showDetail(id){
 const i=await api('/api/ingredients/'+id);window._ing=i;
 screen(`<span class="back" onclick="showIngredients()">${I.back}</span>
 <div class="hero"><div class="big">${emo(i.name)}</div><b>${esc(i.name)}</b></div>
 ${i.expired?`<div class="alert" style="margin:10px 0 0" onclick="showDisposal(${i.id})"><div class="ic">🗑️</div><div class="grow"><b>소비기한이 ${i.days_over}일 지났어요</b><small>드시지 말고 처리 방법을 확인해 주세요</small></div>${I.chev}</div>`:''}
 <div class="qty"><button onclick="chgQty(-1)">−</button><span id="d-qty">${i.qty}개</span><button onclick="chgQty(1)">+</button></div>
 <label class="lb">보관방법</label><div class="pills">${stPills(i.storage,'pickSt')}</div>
 <label class="lb">소비기한</label><div class="daterow" onclick="openCal(window._ing.expiry,v=>{window._ing.expiry=v;$('#d-exp').textContent=fmt(v);saveIng({expiry:v})})"><span id="d-exp">${fmt(i.expiry)}</span>${I.chev}</div>
 <label class="lb">메모</label><textarea id="d-memo" class="field" placeholder="입력하세요...." oninput="memoLater()">${esc(i.memo)}</textarea>
 <div class="saved" id="saved"></div>
 <button class="btn del" onclick="delIng()">삭제</button>`,false,true)}
let _st,_mt;
async function saveIng(patch){await jpost('/api/ingredients/'+window._ing.id,patch,'PATCH');const s=$('#saved');if(s){s.textContent='저장됨';s.style.opacity=1;clearTimeout(_st);_st=setTimeout(()=>s.style.opacity=0,1200)}}
function chgQty(d){const i=window._ing;i.qty=Math.max(1,i.qty+d);$('#d-qty').textContent=i.qty+'개';saveIng({qty:i.qty})}
function pickSt(s,el){window._ing.storage=s;[...el.parentNode.children].forEach(c=>c.classList.toggle('on',c===el));saveIng({storage:s})}
function memoLater(){clearTimeout(_mt);_mt=setTimeout(()=>saveIng({memo:$('#d-memo').value}),600)}
async function delIng(){if(!confirm(window._ing.name+'을(를) 삭제할까요?'))return;await api('/api/ingredients/'+window._ing.id,{method:'DELETE'});showIngredients()}

/* ---------- 소비기한 캘린더 (바텀시트) ---------- */
const MON=['January','February','March','April','May','June','July','August','September','October','November','December'];
function openCal(iso,onOk){const d=iso?new Date(iso+'T00:00'):new Date();window._cal={y:d.getFullYear(),m:d.getMonth(),sel:iso||isoOf(new Date()),onOk};
 const ov=document.createElement('div');ov.className='ov';ov.id='cal-ov';ov.onclick=e=>{if(e.target===ov)ov.remove()};
 ov.innerHTML=`<div class="sheet"><div class="sh-h"><b>소비기한 수정</b><button class="okbtn" onclick="calOk()">${I.check}</button></div><div class="cal" id="cal"></div></div>`;
 document.body.appendChild(ov);drawCal()}
function drawCal(){const c=window._cal,first=new Date(c.y,c.m,1).getDay(),days=new Date(c.y,c.m+1,0).getDate(),today=isoOf(new Date());
 let g=['SUN','MON','TUE','WED','THU','FRI','SAT'].map(w=>`<div class="w">${w}</div>`).join('')+'<div></div>'.repeat(first);
 for(let k=1;k<=days;k++){const iso=`${c.y}-${String(c.m+1).padStart(2,'0')}-${String(k).padStart(2,'0')}`;
  g+=`<div class="d ${iso===c.sel?'sel':''} ${iso===today?'today':''}" onclick="window._cal.sel='${iso}';drawCal()"><span>${k}</span></div>`}
 $('#cal').innerHTML=`<div class="cal-h"><b>${MON[c.m]} ${c.y} <i>›</i></b><span><button onclick="mv(-1)">${I.left}</button><button onclick="mv(1)">${I.right}</button></span></div><div class="cal-g">${g}</div>`}
function mv(d){const c=window._cal;c.m+=d;if(c.m<0){c.m=11;c.y--}if(c.m>11){c.m=0;c.y++}drawCal()}
function calOk(){const c=window._cal;$('#cal-ov').remove();c.onOk(c.sel)}

/* ---------- 영수증 스캔 (카메라) ---------- */
function showScan(){
 screen(`<div class="scan"><span class="back" onclick="showAdd()">${I.back}</span>
 <div class="banner">식재료 영수증을 가이드 라인에 맞춰주세요.</div>
 <div class="frame" id="frame" onclick="shoot()"><video id="cam" playsinline muted></video>
  <div class="hint" id="hint">${I.cam.replace('<svg','<svg style="width:34px;height:34px;display:block;margin:0 auto 10px"')}화면을 눌러 영수증을 촬영하거나<br>아래 앨범 버튼으로 사진을 선택해 주세요</div></div>
 <div class="shutter"><div class="gal" onclick="$('#f').click()" title="앨범에서 선택">${I.photo}</div><div class="shot" onclick="shoot()" title="촬영"></div><div class="ph"></div></div></div>`,true);
 startCam()}
async function startCam(){try{if(!navigator.mediaDevices?.getUserMedia)return;
 const s=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});
 const v=$('#cam');if(!v){s.getTracks().forEach(t=>t.stop());return}state.stream=s;v.srcObject=s;await v.play();$('#hint')?.remove()}catch(e){console.log('camera off:',e.name)}}
function stopCam(){state.stream?.getTracks().forEach(t=>t.stop());state.stream=null}
function shoot(){const v=$('#cam');if(!state.stream||!v?.videoWidth)return $('#f').click();
 const c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;c.getContext('2d').drawImage(v,0,0);
 c.toBlob(b=>doScan(new File([b],'receipt.jpg',{type:'image/jpeg'})),'image/jpeg',.92)}
/* HCX 이미지 제한(긴 변 2240px 이하) 맞추기 + 작은 사진은 확대(글씨 인식률↑) + 가로:세로 1:5 이내(긴 영수증은 흰 여백) + JPEG 변환 */
function shrink(file,max=2000){return new Promise(res=>{const u=URL.createObjectURL(file),im=new Image();
 im.onload=()=>{URL.revokeObjectURL(u);const L=Math.max(im.width,im.height),k=L>max?max/L:Math.min(3,Math.max(1,1600/L));
  const w=Math.max(4,Math.round(im.width*k)),h=Math.max(4,Math.round(im.height*k));
  const cw=Math.max(w,Math.ceil(h/4.8)),ch=Math.max(h,Math.ceil(w/4.8));
  const c=document.createElement('canvas');c.width=cw;c.height=ch;const g=c.getContext('2d');
  g.fillStyle='#fff';g.fillRect(0,0,cw,ch);g.drawImage(im,(cw-w)/2,(ch-h)/2,w,h);c.toBlob(b=>res(b?new File([b],'receipt.jpg',{type:'image/jpeg'}):file),'image/jpeg',0.9)};
 im.onerror=()=>{URL.revokeObjectURL(u);res(file)};im.src=u})}
async function doScan(file){
 if(!$('#frame'))showScan();stopCam();
 const fr=$('#frame');fr.onclick=null;fr.innerHTML=`<img src="${URL.createObjectURL(file)}"><div class="loading"><div class="spin"></div>HyperCLOVA X가 영수증을 읽는 중…</div>`;
 const fd=new FormData();fd.append('file',await shrink(file));
 try{const r=await api('/api/receipt',{method:'POST',body:fd});window._cands=r.candidates.map(c=>({...c,on:true}));
  fr.querySelector('.loading')?.remove();showCands(r)}
 catch(e){fr.querySelector('.loading')?.remove();sheet(`<div class="sh-h"><b>스캔 실패</b></div><div class="note">${esc(e.message).slice(0,500)}</div><button class="btn ghost" onclick="closeSheet();showScan()">다시 찍기</button>`)}}
function sheet(h){closeSheet();const ov=document.createElement('div');ov.className='ov';ov.id='sh-ov';ov.style.background='rgba(0,0,0,.25)';ov.innerHTML=`<div class="sheet">${h}</div>`;document.body.appendChild(ov)}
function closeSheet(){$('#sh-ov')?.remove()}
function showCands(r){const cs=window._cands;
 sheet(`<div class="sh-h" style="margin-bottom:6px"><b>인식된 재료 ${cs.length}개</b></div><div class="tags" style="margin:0 4px 14px"><span class="tag">${esc(r.engine||'')}</span></div>
 ${r.mock?'<div class="note">API 키가 없어 데모 데이터로 표시 중이에요. app/.env를 확인해 주세요.</div>':''}
 ${r.error?'<div class="note">HyperCLOVA X 정리 실패 → 단순 정리로 대체: '+esc(r.error)+'</div>':''}
 ${r.raw&&r.raw.length&&!r.mock?'<details class="raw"><summary>영수증에서 읽은 상품명 '+r.raw.length+'개</summary>'+r.raw.map(esc).join('<br>')+'</details>':''}
 ${cs.length?'':'<div class="center">영수증에서 식재료를 찾지 못했어요.</div>'}
 ${cs.map((c,k)=>`<div class="cand"><div class="ck ${c.on?'on':''}" onclick="tg(${k},this)">${I.check}</div><div class="em">${emo(c.name)}</div>
  <div class="grow"><input class="n" value="${esc(c.name)}" oninput="window._cands[${k}].name=this.value">
  <div class="sub"><select onchange="window._cands[${k}].storage=this.value">${['냉장','실온','냉동'].map(s=>`<option ${s===c.storage?'selected':''}>${s}</option>`).join('')}</select>
  <input type="date" value="${c.expiry}" onchange="window._cands[${k}].expiry=this.value"><span class="tag">${c.qty||1}개</span></div></div></div>`).join('')}
 ${cs.length?'<button class="btn" onclick="regCands()">선택한 재료 등록</button>':''}
 <button class="btn ghost" style="margin-top:10px" onclick="closeSheet();showScan()">다시 찍기</button>`)}
function tg(k,el){const c=window._cands[k];c.on=!c.on;el.classList.toggle('on',c.on)}
async function regCands(){const items=window._cands.filter(c=>c.on&&c.name.trim()).map(c=>({name:c.name,qty:c.qty||1,storage:c.storage,expiry:c.expiry}));
 if(!items.length)return alert('선택된 재료가 없어요');await jpost('/api/ingredients/bulk',items);closeSheet();setTab('ingredients')}

/* ---------- 소비기한 지난 재료 처리 안내 ---------- */
const binRow=(x,label)=>`<div class="brow"><span class="bin b-${esc(x.bin)}">${esc(x.bin)}</span><div><b>${esc(x[label])}</b>${esc(x.how)}</div></div>`;
async function showDisposal(id){window._dispId=id;
 const head=`<span class="back big" onclick="${id?`showDetail(${id})`:'setTab(\'ingredients\')'}">${I.back}<b>처리 안내</b></span>`;
 screen(head+'<div class="center"><div class="spin" style="margin:0 auto 14px;border-color:#e3f6fa;border-top-color:var(--cyan)"></div>HyperCLOVA X가 처리 방법을 정리하는 중…</div>',false,true);
 let d;try{d=await api('/api/disposal'+(id?'?id='+id:''))}catch(e){return screen(head+'<div class="note">불러오기 실패: '+esc(e.message)+'</div>',false,true)}
 window._disp=d;const ex=d.expired;
 const intro=ex.length?`<div class="intro"><b>소비기한이 지난 재료 ${ex.length}개</b><p>소비기한은 보관 방법을 지켰을 때 <b>먹어도 안전한 마지막 날</b>이에요. 지난 재료는 드시지 말고 아래 방법대로 버려 주세요.</p></div>`
  :`<div class="intro ok"><b>🎉 소비기한이 지난 재료가 없어요</b><p>지금처럼 소비기한 전에 다 먹으면 버리는 음식이 줄어요.</p></div>`;
 const cards=ex.map(i=>{const g=i.guide||{};return `<div class="dcard" id="dc-${i.id}">
  <div class="dhead"><div class="ingimg">${emo(i.name)}</div><div class="grow"><div class="title">${esc(i.name)} <span style="font-size:13px;color:#8e8e93;font-weight:400">${i.qty}개</span></div>
   <div class="tags"><span class="tag red">${i.d_label}</span><span class="tag">${esc(i.storage)}보관</span><span class="tag">${g.source==='hcx'?'HyperCLOVA X 안내':'기본 안내'}</span></div></div></div>
  ${g.verdict?`<div class="verdict">${esc(g.verdict)}</div>`:''}
  <div class="dsub">분리배출</div>${(g.parts||[]).map(x=>binRow(x,'part')).join('')}
  ${(g.packaging||[]).length?`<div class="dsub">포장재</div>${g.packaging.map(x=>binRow(x,'item')).join('')}`:''}
  ${g.caution?`<div class="hint2"><span>⚠️</span><span>${esc(g.caution)}</span></div>`:''}
  ${g.tip?`<div class="hint2"><span>💡</span><span><b>다음엔</b> ${esc(g.tip)}</span></div>`:''}
  <div class="dbtns"><button class="b1" onclick="fixExp(${i.id},'${i.expiry}')">기한 수정</button><button class="b2" onclick="disposeIng(${i.id})">버렸어요</button></div>
  <span class="dlink" data-n="${esc(i.name)}" onclick="openCommunity(this.dataset.n)">커뮤니티에서 ${esc(i.name)} 낭비를 줄인 방법 보기 ›</span></div>`}).join('');
 const soon=d.soon.length&&!id?`<h2>곧 지나는 재료</h2><div class="soon">${d.soon.map(i=>`<span>${emo(i.name)} ${esc(i.name)} <i>${i.d_label}</i></span>`).join('')}</div>
  <button class="btn" style="margin-top:4px" onclick="state.rtab='ai';setTab('recipes')">이 재료로 레시피 추천 받기</button>`:'';
 const rules=id?'':`<h2>헷갈리기 쉬운 분리배출</h2><div class="rules"><b>음식물 쓰레기가 아니에요 → 일반쓰레기</b><ul>
  <li>양파·마늘·대파의 마른 껍질과 뿌리</li><li>달걀·조개·게 껍데기</li><li>뼈, 생선 가시</li><li>복숭아·자두 등 딱딱한 씨, 옥수수 껍질과 속대</li><li>티백, 한약재 찌꺼기</li></ul>
  <ul style="margin-top:10px"><li>음식물은 물기를 꼭 짜고 비닐·이쑤시개 같은 이물질을 빼고 버려요.</li><li>용기는 내용물을 비우고 헹군 뒤 재질별로, 우유팩은 일반 종이와 따로 모아요.</li></ul>
  <small>지역마다 세부 기준이 다를 수 있어요. 정확한 기준은 사는 곳의 구청·시청 안내를 확인해 주세요.</small></div>`;
 screen(head+(d.error?`<div class="note">${esc(d.error)}</div>`:'')+intro+cards+soon+rules,false,true)}
async function disposeIng(id){const c=$('#dc-'+id);await api('/api/ingredients/'+id+'/dispose',{method:'POST'});
 if(c){c.classList.add('gone');await new Promise(r=>setTimeout(r,300))}
 if(window._dispId)return setTab('ingredients');showDisposal()}
function fixExp(id,iso){openCal(iso,async v=>{await jpost('/api/ingredients/'+id,{expiry:v},'PATCH');window._dispId?showDetail(id):showDisposal()})}

/* ---------- 레시피 ---------- */
function rtabs(){return `<h1>레시피</h1><div class="seg"><div class="${state.rtab==='mine'?'on':''}" onclick="state.rtab='mine';showRecipes()">내 레시피</div>
 <div class="ai ${state.rtab==='ai'?'on':''}" onclick="state.rtab='ai';showRecipes()">HyperCLOVA X 추천</div></div>`}
async function showRecipes(){
 if(state.rtab==='ai')return showRecommend();
 const rs=await api('/api/recipes');
 screen(rtabs()+'<div class="list">'+rs.map(r=>`<div class="card" onclick="showRecipe(${r.id})">${plate(r.name,62)}
 <div class="grow"><div class="title">${esc(r.name)}</div><div class="tags"><span class="time">${I.clock}${r.minutes}분</span><span class="tag pct">재료 ${r.percent}% 보유</span></div></div>
 <span class="chev">${I.chev}</span></div>`).join('')+'</div>')}

async function showRecipe(id){const r=await api('/api/recipes/'+id);
 screen(`<span class="back big" onclick="showRecipes()">${I.back}<b>레시피</b></span>
 <div class="rhero">${plate(r.name,196)}<h2>${esc(r.name)}</h2>
 <div class="tags" style="justify-content:center"><span class="tag pct">재료 ${r.percent}% 보유</span><span class="tag time" style="color:#333">${I.clock}${r.minutes}분</span></div></div>
 <h2>재료</h2><div class="chips">${r.ingredients.map(i=>`<div class="chip ${r.have.includes(i)?'':'miss'}"><div class="e">${emo(i,'🥣')}</div>${esc(i)}</div>`).join('')}</div>
 <h2>조리 방법</h2>${r.steps.map((s,k)=>`<div class="step"><b>${k+1}</b><div>${esc(s)}</div></div>`).join('')}`,false,true)}

function recMsg(r){const u=(r.used||[]).filter(Boolean),name=r.recipe?.name;if(!u.length||!name)return esc(r.message);
 const b=x=>`<b>${esc(x)}</b>`;let list;
 if(u.length===1)list=b(u[0]);else if(u.length===2)list=b(u[0])+(jong(u[0])?'과 ':'와 ')+b(u[1]);else list=u.slice(0,-1).map(b).join(', ')+', '+b(u.at(-1));
 return `냉장고에 ${list}${jong(u.at(-1))?'을':'를'} 사용하시는 것을 추천드려요. 해당 메뉴로 만들 수 있는 ${b(name)} 어떠신가요?`}
async function showRecommend(){
 screen(rtabs()+'<div class="center"><div class="spin" style="margin:0 auto 14px;border-color:#e3f6fa;border-top-color:var(--cyan)"></div>HyperCLOVA X가 냉장고를 살펴보는 중…</div>');
 try{const [r,rs]=await Promise.all([api('/api/recommend'),api('/api/recipes').catch(()=>[])]);window._rec=r;
  const others=[...new Set(rs.map(x=>x.name).filter(n=>n!==r.recipe?.name))],pool=[...others,'김치찌개','카레','샐러드','오믈렛','라면','덮밥','볶음밥','토스트'];
  const C=165,ring=[];
  for(let k=0;k<6;k++){const a=k*Math.PI/3,R=104,s=70;ring.push(plate(pool[k],s,'',`left:${C+R*Math.cos(a)-s/2}px;top:${C+R*Math.sin(a)-s/2}px`))}
  for(let k=0;k<6;k++){const a=Math.PI/6+k*Math.PI/3,R=150,s=32;ring.push(plate(pool[k+6]||pool[k],s,'far',`left:${C+R*Math.cos(a)-s/2}px;top:${C+R*Math.sin(a)-s/2}px`))}
  screen(rtabs()+(r.source==='fallback'&&r.error?`<div class="note">HyperCLOVA X 호출 실패 → 기본 레시피로 대체했어요.<br>${esc(r.error)}</div>`:'')
  +`<div class="hcx"><div class="t"><h3>HyperCLOVA X 추천</h3><p>${recMsg(r)}</p></div>${r.recipe?'<button onclick="makeRec()">만들기</button>':''}</div>
  <div class="ring">${ring.join('')}${plate(r.recipe?.name||'',124,'c',`left:${C-62}px;top:${C-62}px`)}</div>`)}
 catch(e){screen(rtabs()+'<div class="note">추천 실패: '+esc(e.message).slice(0,300)+'</div>')}}
async function makeRec(){const r=await jpost('/api/recipes',window._rec.recipe);state.rtab='mine';showRecipe(r.id)}

/* ---------- 커뮤니티 ---------- */
const ME={uid:null,user:null,info:null};
const CATS=['보관 팁','남은 재료 활용','냉동 보관','장보기','기타'];
const AV=['🧅','🥕','🌿','🥔','🍅','🥚','🍄','🧄','🥬','🍎','🧈','🌽'];
const HEART='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.3a4.3 4.3 0 0 1 7.5 2.5C19.5 15.4 12 20 12 20z"/></svg>';
const BUBBLE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4.5 5h15a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4 3.5V17H4.5A1.5 1.5 0 0 1 3 15.5v-9A1.5 1.5 0 0 1 4.5 5z"/></svg>';
const SEARCH='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>';
const PEN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>';
state.cq={category:'',fridge:false,sort:'latest',q:''};
function newId(){return 'u'+Date.now().toString(36)+Math.random().toString(36).slice(2,12)}
function uidGet(){if(ME.uid)return ME.uid;try{let u=localStorage.getItem('littone_uid');if(!u){u=newId();localStorage.setItem('littone_uid',u)}return ME.uid=u}catch(e){return ME.uid=newId()}}
async function loadMe(){const r=await api('/api/community/me?uid='+encodeURIComponent(uidGet()));ME.user=r.user;ME.info=r;return r.user}
async function needMe(then){if(ME.user||await loadMe())return then();nickSheet(then)}
function ago(iso){const s=(Date.now()-new Date(iso))/1000;if(s<60)return '방금';if(s<3600)return Math.floor(s/60)+'분 전';
 if(s<86400)return Math.floor(s/3600)+'시간 전';if(s<604800)return Math.floor(s/86400)+'일 전';const d=new Date(iso);return `${d.getFullYear()}.${d.getMonth()+1}.${d.getDate()}`}
function toast(msg){document.querySelectorAll('.toast').forEach(e=>e.remove());const t=document.createElement('div');t.className='toast';t.textContent=msg;document.body.appendChild(t);setTimeout(()=>t.remove(),2600)}
function nickSheet(then,edit=false){
 const A=['알뜰한','부지런한','싱싱한','든든한','야무진','상큼한'],N=['양파','당근','대파','감자','두부','버섯'];
 const sug=ME.user?.nickname||A[Math.random()*A.length|0]+' '+N[Math.random()*N.length|0];window._emo=ME.user?.emoji||AV[Math.random()*AV.length|0];
 sheet(`<div class="sh-h"><b>${edit?'닉네임 변경':'커뮤니티에서 쓸 닉네임'}</b></div>
 <div style="font-size:13px;color:#8e8e93;margin:-8px 4px 14px">다른 사용자에게 보이는 이름과 프로필 이모지예요.</div>
 <div class="emos">${AV.map(e=>`<div class="${e===window._emo?'on':''}" onclick="window._emo='${e}';[...this.parentNode.children].forEach(c=>c.classList.toggle('on',c===this))">${e}</div>`).join('')}</div>
 <input id="nick" class="field" maxlength="12" value="${esc(sug)}" placeholder="2~12자">
 <div class="note" id="nick-err" style="display:none;margin-top:10px"></div>
 <button class="btn" onclick="saveNick()">${edit?'저장':'시작하기'}</button>`);window._nickThen=then}
async function saveNick(){try{const r=await jpost('/api/community/me',{id:uidGet(),nickname:$('#nick').value,emoji:window._emo});ME.user=r.user;ME.info=r;closeSheet();window._nickThen&&window._nickThen()}
 catch(e){const el=$('#nick-err');el.style.display='block';el.textContent=e.message}}
function openCommunity(q){state.cq={category:'',fridge:false,sort:'latest',q:q||''};setTab('community')}

async function showCommunity(){
 const c=state.cq;
 screen(`<h1>커뮤니티</h1><div class="sub-t">식재료 낭비를 줄인 방법을 서로 나눠요</div>
 <div class="search">${SEARCH}<input id="cq" placeholder="재료나 방법으로 검색 (예: 대파, 냉동)" value="${esc(c.q)}" oninput="clearTimeout(window._qt);window._qt=setTimeout(()=>{state.cq.q=this.value;loadFeed()},300)"></div>
 <div class="cchips"><div class="${!c.category&&!c.fridge?'on':''}" onclick="setCq('',false)">전체</div><div class="fr ${c.fridge?'on':''}" onclick="setCq('',true)">🧊 내 냉장고 재료</div>
  ${CATS.map(k=>`<div class="${c.category===k?'on':''}" onclick="setCq('${k}',false)">${k}</div>`).join('')}</div>
 <div class="sortbar"><span class="${c.sort==='latest'?'on':''}" onclick="state.cq.sort='latest';showCommunity()">최신순</span><span class="${c.sort==='popular'?'on':''}" onclick="state.cq.sort='popular';showCommunity()">공감순</span></div>
 <div id="feed"><div class="center">불러오는 중…</div></div>`);
 const fab=document.createElement('button');fab.className='fab';fab.innerHTML=PEN+'글쓰기';fab.onclick=()=>needMe(showWrite);document.body.appendChild(fab);
 loadFeed()}
function setCq(cat,fr){state.cq.category=cat;state.cq.fridge=fr;showCommunity()}
async function loadFeed(){const c=state.cq,qs=new URLSearchParams({uid:uidGet(),category:c.category,sort:c.sort,q:c.q,fridge:c.fridge});
 let ps;try{ps=await api('/api/community/posts?'+qs)}catch(e){$('#feed').innerHTML='<div class="note">불러오기 실패: '+esc(e.message)+'</div>';return}
 if(!$('#feed'))return;
 $('#feed').innerHTML=ps.length?ps.map(postCard).join(''):`<div class="center">${c.q?`'${esc(c.q)}' 관련 글이 아직 없어요.`:c.fridge?'내 냉장고 재료와 관련된 글이 아직 없어요.':'아직 글이 없어요.'}<br>첫 번째로 나만의 방법을 공유해 보세요!</div>`}
const postHead=p=>`<div class="ph"><div class="av">${esc(p.author.emoji)}</div><div class="grow"><b>${esc(p.author.nickname)}</b>${p.official?'<span class="off">공식</span>':''}${p.mine?'<span class="off" style="color:#3a6fb0;background:#eaf2fd">나</span>':''}
 <small>${ago(p.created)} · ${esc(p.category)}</small></div></div>`;
const tagsHtml=p=>p.tags.length?`<div class="ptags">${p.tags.map(t=>`<span class="${(p.match||[]).includes(t)?'m':''}">#${esc(t)}</span>`).join('')}</div>`:'';
const postCard=p=>`<div class="card post" onclick="showPost(${p.id})">${postHead(p)}<div class="pt">${esc(p.title)}</div><div class="pc">${esc(p.content)}</div>${tagsHtml(p)}
 <div class="pf"><span class="lk ${p.liked?'on':''}" onclick="event.stopPropagation();like(${p.id},this)">${HEART}<i style="font-style:normal">${p.like_count}</i></span><span>${BUBBLE}${p.comment_count}</span></div></div>`;
async function like(id,el){needMe(async()=>{try{const r=await jpost(`/api/community/posts/${id}/like`,{uid:uidGet()});
 el.classList.toggle('on',r.liked);const n=el.querySelector('i');if(n)n.textContent=r.like_count;else el.lastChild.textContent=' 공감 '+r.like_count}catch(e){toast(e.message)}})}

async function showPost(id){
 let p;try{p=await api(`/api/community/posts/${id}?uid=${encodeURIComponent(uidGet())}`)}catch(e){return toast(e.message)}
 screen(`<span class="back big" onclick="setTab('community')">${I.back}<b>커뮤니티</b></span>
 <div class="card post" style="cursor:default;margin-top:10px">${postHead(p)}<div class="pt" style="font-size:20px">${esc(p.title)}</div><div class="pc full">${esc(p.content)}</div>${tagsHtml(p)}
  <div style="display:flex;align-items:center;justify-content:space-between"><span class="bigheart ${p.liked?'on':''}" onclick="like(${p.id},this)">${HEART} 공감 ${p.like_count}</span>
  ${p.mine?`<span style="font-size:13px;color:#b0b0b5;cursor:pointer;margin-top:16px" onclick="delPost(${p.id})">삭제</span>`:''}</div></div>
 <h2 style="margin-top:20px">댓글 ${p.comments.length}</h2>
 <div class="card" style="padding:2px 14px">${p.comments.length?p.comments.map(c=>`<div class="cmt"><div class="av">${esc(c.author.emoji)}</div><div class="grow">
  <b>${esc(c.author.nickname)}</b><small>${ago(c.created)}</small>${c.mine?`<span class="x" onclick="delCmt(${c.id},${p.id})">삭제</span>`:''}<p>${esc(c.content)}</p></div></div>`).join('')
  :'<div class="center" style="padding:24px 0">첫 댓글을 남겨 보세요</div>'}</div><div style="height:70px"></div>`,false,true);
 const bar=document.createElement('div');bar.className='cbar';
 bar.innerHTML=`<input id="cin" maxlength="300" placeholder="댓글을 입력하세요" onkeydown="if(event.key==='Enter'&&!event.isComposing)sendCmt(${p.id})"><button onclick="sendCmt(${p.id})">등록</button>`;
 document.body.appendChild(bar)}
async function sendCmt(pid){const v=$('#cin').value.trim();if(!v)return;
 needMe(async()=>{try{await jpost(`/api/community/posts/${pid}/comments`,{uid:uidGet(),content:v});showPost(pid)}catch(e){toast(e.message)}})}
async function delCmt(cid,pid){if(!confirm('댓글을 삭제할까요?'))return;await api(`/api/community/comments/${cid}?uid=${encodeURIComponent(uidGet())}`,{method:'DELETE'});showPost(pid)}
async function delPost(id){if(!confirm('글을 삭제할까요?'))return;await api(`/api/community/posts/${id}?uid=${encodeURIComponent(uidGet())}`,{method:'DELETE'});await showCommunity();toast('삭제했어요')}

function showWrite(){window._wcat='';window._undo=null;
 screen(`<span class="back big" onclick="setTab('community')">${I.back}<b>글쓰기</b></span>
 <label class="lb">분류</label><div class="cpills" id="wcat"><div class="on" onclick="pickCat('',this)">✨ 자동 분류</div>${CATS.map(k=>`<div onclick="pickCat('${k}',this)">${k}</div>`).join('')}</div>
 <label class="lb">내용</label>
 <textarea id="wtxt" class="field write" maxlength="1000" placeholder="식재료 낭비를 줄인 나만의 방법을 알려 주세요.&#10;예) 대파를 썰어서 냉동했더니 한 단을 끝까지 다 먹었어요!" oninput="$('#wcnt').textContent=this.value.length"></textarea>
 <div class="wfoot"><span><span id="wcnt">0</span>/1000</span><span id="wundo"></span></div>
 <div style="margin-top:12px"><button class="aibtn" id="wai" onclick="polishTxt()">✨ HyperCLOVA X로 다듬기</button></div>
 <div style="font-size:12.5px;color:#a0a0a5;margin:12px 4px 0;line-height:1.6">올리면 HyperCLOVA X가 제목과 식재료 태그를 자동으로 붙여요.</div>
 <button class="btn" id="wpost" onclick="submitPost()">올리기</button>`,false,true)}
function pickCat(k,el){window._wcat=k;[...el.parentNode.children].forEach(c=>c.classList.toggle('on',c===el))}
async function polishTxt(){const t=$('#wtxt'),b=$('#wai');if(t.value.trim().length<5)return toast('5자 이상 써 주세요');
 b.disabled=true;b.textContent='다듬는 중…';try{const r=await jpost('/api/community/polish',{content:t.value});window._undo=t.value;t.value=r.content;$('#wcnt').textContent=t.value.length;
 $('#wundo').innerHTML='<span style="color:var(--blue);cursor:pointer" onclick="undoPolish()">되돌리기</span>'}catch(e){toast(e.message)}b.disabled=false;b.textContent='✨ HyperCLOVA X로 다듬기'}
function undoPolish(){if(window._undo==null)return;$('#wtxt').value=window._undo;$('#wcnt').textContent=window._undo.length;window._undo=null;$('#wundo').innerHTML=''}
async function submitPost(){const v=$('#wtxt').value.trim();if(v.length<5)return toast('5자 이상 써 주세요');
 const b=$('#wpost');b.disabled=true;b.textContent='올리는 중…';
 try{const r=await jpost('/api/community/posts',{uid:uidGet(),content:v,category:window._wcat||null});
  await showPost(r.id);toast(r.ai&&r.tags.length?`HyperCLOVA X가 ${r.tags.map(t=>'#'+t).join(' ')} 태그를 붙였어요`:'글을 올렸어요')}
 catch(e){toast(e.message);b.disabled=false;b.textContent='올리기'}}

/* ---------- 내 정보 ---------- */
async function showMe(){const it=await api('/api/ingredients');
 const exp=it.filter(i=>i.d_class==='red').length,soon=it.filter(i=>i.d_class==='orange').length;
 screen(`<h1>내 정보</h1>
 <div class="card" style="margin-bottom:12px" id="prof"></div>
 <div class="card" style="margin-bottom:12px"><div class="me-row"><span>등록된 재료</span><b>${it.length}개</b></div>
 <div class="me-row"><span>소비기한 임박 (3일 이내)</span><b style="color:#d77a00">${soon}개</b></div>
 <div class="me-row" style="cursor:pointer" onclick="showDisposal()"><span>소비기한 지남 · 처리 안내 ›</span><b style="color:var(--red)">${exp}개</b></div></div>
 <div class="card" style="margin-bottom:12px" id="st-card"></div>
 <div class="card" style="padding:18px"><div class="title" style="font-size:16px">HyperCLOVA X 연결</div>
 <div id="hcx-st" style="font-size:13px;color:#555;margin-top:8px;line-height:1.7">확인 중…</div>
 <button class="btn ghost" style="margin-top:14px" onclick="pingHcx()">연결 테스트</button></div>
 <div style="font-size:12px;color:#aaa;text-align:center;margin-top:20px">릿톤 · 냉장고 재료 관리 & HyperCLOVA X 레시피 추천</div>`);
 loadMe().then(u=>{$('#prof').innerHTML=u?`<div class="prof"><div class="av">${esc(u.emoji)}</div><div class="grow"><b>${esc(u.nickname)}</b>
  <small>내가 쓴 글 ${ME.info.posts}개 · 받은 공감 ${ME.info.likes_received}개</small></div><button onclick="nickSheet(showMe,true)">변경</button></div>`
  :`<div class="prof"><div class="av">👋</div><div class="grow"><b>커뮤니티 닉네임</b><small>아직 정하지 않았어요</small></div><button onclick="nickSheet(showMe)">정하기</button></div>`}).catch(()=>{});
 api('/api/stats').then(t=>{$('#st-card').innerHTML=`<div class="me-row"><span>이번 달 버린 재료</span><b>${t.disposed_month}개</b></div>
  <div class="me-row"><span>지금까지 버린 재료</span><b>${t.disposed_total}개</b></div>`
  +(t.top.length?`<div class="me-row"><span>자주 버리는 재료</span><b style="font-weight:500;font-size:14px">${t.top.map(x=>esc(x.name)+' '+x.count+'회').join(' · ')}</b></div>`:'')}).catch(()=>{});
 const s=await api('/api/status');$('#hcx-st').innerHTML=hcxLine(s)}
function hcxLine(s){return `API 키: ${s.hcx_key?'✅ '+esc(s.hcx_key_hint):'❌ 없음 (app/.env 확인)'}<br>모델: ${esc(s.model)} · 영수증: ${s.ocr?'CLOVA OCR':esc(s.vision_model)+' 비전'}`
 +(s.ping?`<br>${s.ping.ok?'✅ 응답: '+esc(s.ping.reply):'❌ '+esc(s.ping.error)}`:'')}
async function pingHcx(){$('#hcx-st').innerHTML='호출 중…';try{$('#hcx-st').innerHTML=hcxLine(await api('/api/status?ping=true'))}catch(e){$('#hcx-st').textContent='오류: '+e.message}}

setTab('ingredients');
</script></body></html>
"""

if __name__ == "__main__":
    import uvicorn

    print(f"OCR: {'ON' if OCR_URL and OCR_SECRET else 'OFF(영수증은 HCX 비전 사용)'} | "
          f"HyperCLOVA X: {'ON (' + HCX_MODEL + ')' if HCX_KEY else 'OFF — app/.env에 CLOVASTUDIO_API_KEY 필요'}")
    print(f"브라우저에서 http://localhost:{os.getenv('PORT', '8000')} 접속")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
