from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from io import BytesIO
import json
import redis as redis_lib
import os

from tasks import generate_pptx
from pubmed_tasks import search_pubmed_task, USER_DOCS_DIR

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis    = redis_lib.from_url(REDIS_URL, decode_responses=False)

load_dotenv()

# ── Rate limiter (keyed by client IP) ────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Sciensinsta API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ──────────────────────────────────────────────────────────────────────
raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:4321,http://localhost:3000")
origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request models ─────────────────────────────────────────────────────────────
class ContactRequest(BaseModel):
    email: str
    message: str
    timezone: str = ""

class NotifyRequest(BaseModel):
    email: str
    timezone: str = ""

class SlidesRequest(BaseModel):
    text: str
    tool: int = 1           # 1 = direct text→pptx, 2 = ChatGPT JSON→pptx

class PubmedRequest(BaseModel):
    query: str

class DownloadRequest(BaseModel):
    filename: str

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "message": "hello"}

# ── Contact ───────────────────────────────────────────────────────────────────
@app.post("/api/add-contact")
def add_contact(body: ContactRequest):
    """Receives a contact/feedback form submission."""
    print(f"[contact] email={body.email!r}  tz={body.timezone!r}")
    return {"message": "hello", "received": {"email": body.email}}

# ── Newsletter ────────────────────────────────────────────────────────────────
@app.post("/api/add-notify-list")
def add_notify(body: NotifyRequest):
    """Adds an email to the release-notification list."""
    print(f"[notify] email={body.email!r}  tz={body.timezone!r}")
    return {"message": "hello", "received": {"email": body.email}}

# ── Text → .pptx  (submit) ────────────────────────────────────────────────────
@app.post("/api/create-slides")
@limiter.limit("10/minute")
def create_slides(request: Request, body: SlidesRequest):
    """
    Enqueues a pptx generation task and returns the task_id immediately.
    The client should poll GET /api/task/{task_id} until status == 'done',
    then download via GET /api/download-pptx/{task_id}.
    Rate-limited to 10 requests per minute per IP.
    """
    print(f"[slides] queuing  tool={body.tool}  text_len={len(body.text)}")
    task = generate_pptx.delay(body.text, body.tool == 2)
    return {"task_id": task.id, "status": "queued"}


# ── Task status (poll) ────────────────────────────────────────────────────────
@app.get("/api/task/{task_id}")
def task_status(task_id: str):
    """
    Returns the current state of a Celery task.
    Possible statuses: queued | processing | done | failed
    """
    result = generate_pptx.AsyncResult(task_id)
    state  = result.state

    if state == "PENDING":
        return {"status": "queued"}
    if state == "STARTED":
        return {"status": "processing"}
    if state == "SUCCESS":
        return {"status": "done"}
    if state == "FAILURE":
        return {"status": "failed", "detail": str(result.result)}
    return {"status": state.lower()}


# ── Download generated file (fetch + delete) ──────────────────────────────────
@app.get("/api/download-pptx/{task_id}")
def download_pptx(task_id: str):
    """
    Fetches the generated .pptx bytes from Redis, streams them to the client,
    then deletes the Redis key so nothing lingers on the server.
    """
    key  = f"pptx:{task_id}"
    data = _redis.get(key)

    if not data:
        raise HTTPException(
            status_code=404,
            detail="File not found — it may have expired (5 min TTL) or already been downloaded.",
        )

    _redis.delete(key)   # delete immediately; one download per task

    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": 'attachment; filename="presentation.pptx"'},
    )

# ── PubMed abstract search (submit) ──────────────────────────────────────────
@app.post("/api/search-pubmed")
@limiter.limit("5/minute")
def search_pubmed(request: Request, body: PubmedRequest):
    """
    Enqueues a PubMed search task and returns the task_id immediately.

    Rate-limited to 5 requests per minute per IP (searches take ~10–60 s
    and hit NCBI servers; be a good citizen).

    Poll GET /api/task/{task_id} until status == 'done', then call
    GET /api/pubmed-result/{task_id} to retrieve the full payload.
    """
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=422, detail="query must not be empty")
    print(f"[pubmed] queuing query={query!r}")
    task = search_pubmed_task.delay(query)
    return {"task_id": task.id, "status": "queued"}


# ── PubMed result (fetch from Redis) ─────────────────────────────────────────
@app.get("/api/pubmed-result/{task_id}")
def pubmed_result(task_id: str):
    """
    Retrieves the full PubMed search payload from Redis.

    Returns 404 if the result has expired (TTL = 10 min) or doesn't exist yet
    (task still processing — check /api/task/{task_id} first).
    """
    raw = _redis.get(f"pubmed:{task_id}")
    if not raw:
        raise HTTPException(
            status_code=404,
            detail="Result not found — the task may still be running, "
                   "or the result has expired (10-min TTL).",
        )
    return json.loads(raw)


# ── Download generated file ───────────────────────────────────────────────────
@app.post("/api/download-file")
def download_file(body: DownloadRequest):
    """
    Streams a previously generated .xlsx file from the userDocs directory.
    Expects ``filename`` to be a bare filename like ``<task_id>.xlsx``.
    """
    # Strip any path components — only allow a bare filename
    safe_name = os.path.basename(body.filename)
    filepath  = os.path.join(USER_DOCS_DIR, safe_name)

    if not os.path.isfile(filepath):
        raise HTTPException(
            status_code=404,
            detail="File not found — it may have expired or the task is still running.",
        )

    print(f"[download] serving {filepath!r}")
    return FileResponse(
        filepath,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=safe_name,
    )
