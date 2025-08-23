# backend/main.py
import os, json, time, pathlib, asyncio, warnings
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
# Google GenAI SDK (Gemini) : pip install google-genai
from google import genai

# ---------- App setup ----------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 開発中は * でOK。運用ではオリジンを絞る
    allow_methods=["*"],
    allow_headers=["*"]
)

BASE = pathlib.Path(__file__).parent               # backend/
PROJECT_ROOT = BASE.parent                         # プロジェクトルート
FRONTEND_DIR = PROJECT_ROOT / "frontend"           # frontend/
SUBMIT_DIR = BASE / "submissions"                  # backend/submissions
SUBMIT_DIR.mkdir(exist_ok=True)

load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ---------- Helpers ----------
def read_text(p: pathlib.Path) -> str:
  try:
    return p.read_text(encoding="utf-8")
  except Exception:
    return ""

def recent_submissions(n=3):
  files = sorted(SUBMIT_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
  return [read_text(p) for p in files[:n]]

def build_system_context():
  # ★ フロント資産から読み込むように修正
  manual_path = FRONTEND_DIR / "manual.md"
  steps_path  = FRONTEND_DIR / "steps.json"

  manual = read_text(manual_path)
  steps_json = read_text(steps_path)
  try:
    steps = json.loads(steps_json) if steps_json.strip() else []
  except Exception:
    steps = []

  subs  = recent_submissions(3)

  ctx = f"""# App Context
## Manual
{manual}

## Steps
{json.dumps(steps, ensure_ascii=False, indent=2)}

## Recent Submissions (latest 3)
{json.dumps(subs, ensure_ascii=False, indent=2)}
"""
  return ctx

# ---------- Endpoints ----------
@app.post("/api/submit")
async def api_submit(payload: dict):
  ts = int(time.time())
  step = int(payload.get("step", 0))
  content = (payload.get("content") or "").strip()
  name = f"{ts:010d}-step{step}.txt"
  (SUBMIT_DIR / name).write_text(content, encoding="utf-8")
  return {"ok": True, "file": name}

@app.post("/api/chat/stream")
async def api_chat_stream(req: Request):
  body = await req.json()
  user_q = (body.get("query") or "").strip()
  meta = body.get("meta") or {}

  # --- Compose prompt (高速・簡易RAG) ---
  system_ctx = build_system_context()
  user_prompt = f"""あなたは現場支援エージェントです。
以下のコンテキスト（マニュアル／手順／最新提出）を参照しつつ、日本語で簡潔に回答してください。

[進捗ステップ] {meta.get('step')}
[質問] {user_q}
"""

  # --- Gemini streaming ---
  if not GOOGLE_API_KEY:
    # APIキーが未設定でもUIを壊さない
    async def gen_err():
      yield "data: " + "（開発用メッセージ）GOOGLE_API_KEY が未設定です。\n\n"
      yield "data: [DONE]\n\n"
    return StreamingResponse(
      gen_err(),
      media_type="text/event-stream",
      headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # 一部リバプロでのバッファ抑止
      }
    )

  client = genai.Client(api_key=GOOGLE_API_KEY)

  async def event_stream():
    # generate_content_stream: 逐次トークンを取得
    stream = client.models.generate_content_stream(
      model="gemini-2.5-flash",
      contents=[
        {"role":"user", "parts":[
          {"text": system_ctx},
          {"text": user_prompt}
        ]}
      ]
    )
    for chunk in stream:
      text = getattr(chunk, "text", None)
      if text:
        # \n は SSE の1イベント内で明示的に扱うためエスケープ
        yield "data: " + text.replace("\n", "\\n") + "\n\n"
      await asyncio.sleep(0)  # イベントループに制御を返してUIカクつきを防止

    yield "data: [DONE]\n\n"

  return StreamingResponse(
    event_stream(),
    media_type="text/event-stream",
    headers={
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    }
  )

@app.get("/healthz")
def healthz():
  return {"ok": True}

@app.get("/api/healthz")
def api_healthz():
  return {"ok": True}
