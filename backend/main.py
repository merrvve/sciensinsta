from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from io import BytesIO
import redis as redis_lib
import os

from tasks import generate_pptx

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

# ── PubMed abstract search ────────────────────────────────────────────────────
@app.post("/api/search-pubmed")
def search_pubmed(body: PubmedRequest):
    """
    Searches PubMed for the given query, downloads up to 100 abstracts,
    and returns a word-cloud image + metadata.
    Returns a hello stub for now.
    """
    print(f"[pubmed] query={body.query!r}")
    # TODO: real PubMed NCBI API call
    return {
        "message": "hello",
        "total_abstracts": 0,
        "downloaded_abstracts": 0,
        "work_id": "stub-work-id",
        "dict": "",
        "image": "",
    }

# ── Download generated file ───────────────────────────────────────────────────
@app.post("/api/download-file")
def download_file(body: DownloadRequest):
    """
    Returns a previously generated file (e.g. abstracts.xlsx) as a download.
    Returns a hello stub for now.
    """
    print(f"[download] filename={body.filename!r}")
    # TODO: return FileResponse from a temp/work directory
    return {"message": "hello", "received": {"filename": body.filename}}
