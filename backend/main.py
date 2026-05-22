from dotenv import load_dotenv
load_dotenv()   # must run before any local import that reads os.getenv at module level

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import fcntl
import json
import redis as redis_lib
import os

from tasks import generate_pptx
from pubmed_tasks import search_pubmed_task
from r2_service import presigned_download_url

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_redis    = redis_lib.from_url(REDIS_URL, decode_responses=False)

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

# ── Subscribers file ──────────────────────────────────────────────────────────
# Resolve once at startup — relative paths are anchored to this file's directory
# so they work the same whether running locally or inside the container.
_HERE = os.path.dirname(os.path.abspath(__file__))
_raw  = os.getenv("SUBSCRIBERS_FILE", "subscribers.txt")
SUBSCRIBERS_FILE = _raw if os.path.isabs(_raw) else os.path.join(_HERE, _raw)

# ── Request models ─────────────────────────────────────────────────────────────
class NotifyRequest(BaseModel):
    email: str

class SlidesRequest(BaseModel):
    text: str
    tool: int = 1           # 1 = direct text→pptx, 2 = ChatGPT JSON→pptx

class PubmedRequest(BaseModel):
    query: str

class DownloadRequest(BaseModel):
    filename: str

# ── Newsletter subscribe ──────────────────────────────────────────────────────
@app.post("/api/add-notify-list")
@limiter.limit("5/minute")
def add_notify(request: Request, body: NotifyRequest):
    """
    Appends the subscriber's email address to SUBSCRIBERS_FILE (one per line).
    Uses an exclusive file lock so concurrent requests don't corrupt the file.
    """
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="email must not be empty")

    os.makedirs(os.path.dirname(os.path.abspath(SUBSCRIBERS_FILE)), exist_ok=True)
    with open(SUBSCRIBERS_FILE, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(email + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    print(f"[newsletter] subscribed {email!r}")
    return {"message": "subscribed"}


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "message": "hello"}

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


# ── Download generated .pptx (redirect to R2 presigned URL) ──────────────────
@app.get("/api/download-pptx/{task_id}")
def download_pptx(task_id: str):
    """
    Generates a presigned Cloudflare R2 URL for the .pptx file created by
    *task_id* and redirects the client to it (307 Temporary Redirect).

    Returns 404 if the task has not completed yet or the file does not exist
    in R2.
    """
    result = generate_pptx.AsyncResult(task_id)
    if result.state != "SUCCESS":
        raise HTTPException(
            status_code=404,
            detail="File not ready — the task may still be running or has failed.",
        )
    try:
        url = presigned_download_url(f"{task_id}.pptx")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found in storage.")
    return RedirectResponse(url=url, status_code=307)

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


# ── Download generated .xlsx (redirect to R2 presigned URL) ──────────────────
@app.post("/api/download-file")
def download_file(body: DownloadRequest):
    """
    Generates a presigned Cloudflare R2 URL for the requested .xlsx file
    and redirects the client to it (307 Temporary Redirect).

    Expects ``filename`` to be a bare filename like ``<task_id>.xlsx``.
    Any path components are stripped for safety.
    """
    safe_name = os.path.basename(body.filename)
    print(f"[download] generating R2 presigned URL for {safe_name!r}")
    try:
        url = presigned_download_url(safe_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="File not found — it may have expired or the task is still running.",
        )
    return RedirectResponse(url=url, status_code=307)
