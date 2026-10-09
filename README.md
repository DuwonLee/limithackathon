# limithackathon — 릿톤

냉장고 재료 관리 + HyperCLOVA X 레시피 추천 / 영수증 인식 (FastAPI 단일 파일: `app/main.py`)

## 실행

```bash
cd limithackathon
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app/main.py                 # → http://localhost:8000
```

## 환경변수 (`app/.env`)

```
CLOVASTUDIO_API_KEY=nv-xxxxxxxxxxxxxxxx   # CLOVA Studio API 키 (NAVER_API_KEY 이름도 인식)
# CLOVASTUDIO_MODEL=HCX-005               # 레시피 추천/정리용 모델 (기본 HCX-005)
# CLOVASTUDIO_VISION_MODEL=HCX-005        # 영수증 이미지 인식용 (이미지 입력 가능한 모델)
# CLOVA_OCR_INVOKE_URL=...                # (선택) CLOVA OCR을 쓰려면 둘 다 입력
# CLOVA_OCR_SECRET_KEY=...
```

## HyperCLOVA X 연결 확인

- 앱의 **내 정보 → 연결 테스트** 버튼, 또는 `http://localhost:8000/api/status?ping=true`
- 실패하면 원인(키 오류 401, 모델 권한 403, 모델명 404, 한도 초과 429 등)이 화면과 터미널에 표시됩니다.

## 기능별 HyperCLOVA X 사용

| 기능 | API |
|---|---|
| 레시피 탭 → HyperCLOVA X 추천 | `GET /api/recommend` — 소비기한 임박 재료로 레시피 생성 |
| 재료 추가 → 영수증 스캔 | `POST /api/receipt` — OCR 키가 없으면 HCX-005 비전으로 영수증 사진을 직접 읽음 |
