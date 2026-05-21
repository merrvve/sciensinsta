from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Sciensinsta API", version="0.1.0")

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

# ── Text → .pptx ──────────────────────────────────────────────────────────────
@app.post("/api/create-slides")
def create_slides(body: SlidesRequest):
    """
    Converts text (or a ChatGPT JSON list) to a .pptx file.
    tool=1 → plain text, tool=2 → JSON slide list from ChatGPT.
    Returns a .pptx blob in production; returns hello stub for now.
    """
    print(f"[slides] tool={body.tool}  text_len={len(body.text)}")
    # TODO: generate real pptx and return FileResponse
    return {"message": "hello", "received": {"tool": body.tool, "text_length": len(body.text)}}

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
