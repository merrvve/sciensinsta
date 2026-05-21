"""
pubmed_tasks.py
───────────────
PubMed abstract search: core logic + Celery task.

Flow
────
1. POST /api/search-pubmed   → enqueues search_pubmed_task → returns task_id
2. GET  /api/task/{task_id}  → poll Celery state (shared with pptx)
3. GET  /api/pubmed-result/{task_id} → fetch JSON payload from Redis
4. POST /api/download-file   → stream the generated .xlsx to the client
"""

from __future__ import annotations

import io
import base64
import json
import os
import re
import urllib.request
from time import sleep

import matplotlib
matplotlib.use("Agg")          # must be set before importing pyplot
import matplotlib.pyplot as plt
import pandas as pd
import redis as redis_lib
from wordcloud import WordCloud, STOPWORDS

from celery_app import celery

# ── Redis client ──────────────────────────────────────────────────────────────
REDIS_URL          = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PUBMED_RESULT_TTL  = int(os.getenv("PUBMED_RESULT_TTL", 600))   # seconds (10 min)

# Absolute path so both the FastAPI container and the Celery worker container
# resolve to the same location when a shared Docker volume is mounted at /app/userDocs.
_HERE         = os.path.dirname(os.path.abspath(__file__))
USER_DOCS_DIR = os.getenv("USER_DOCS_DIR", os.path.join(_HERE, "userDocs"))

_redis = redis_lib.from_url(REDIS_URL, decode_responses=False)

# ── Private helpers ───────────────────────────────────────────────────────────

def _find_doi(row: pd.Series) -> str:
    """Return the first DOI / PMID column value, or a fallback string."""
    for col in row[4:]:
        if col is not None and (
            col.lstrip().startswith("DOI") or col.lstrip().startswith("PMID")
        ):
            return col
    return "no link found"


def _remove_index(title: str) -> str:
    """Strip the leading '1. ' numbering that NCBI efetch adds."""
    return re.sub(r"^\d+\.\s", "", title)


def _create_word_cloud(abstracts: list[str]) -> str:
    """
    Build a word-cloud from a list of abstract strings and return it
    as a base64-encoded PNG string.
    """
    comment_words = ""
    stopwords = set(STOPWORDS)

    for val in abstracts:
        tokens = str(val).split()
        comment_words += " ".join(t.lower() for t in tokens) + " "

    wordcloud = WordCloud(
        width=800,
        height=800,
        background_color="white",
        stopwords=stopwords,
        min_font_size=10,
    ).generate(comment_words)

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(wordcloud)
    ax.axis("off")
    fig.tight_layout(pad=0)

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    buf.seek(0)
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    buf.close()
    plt.close(fig)
    return encoded


# ── Core search logic ─────────────────────────────────────────────────────────

def search_pubmed_core(query: str, work_id: str) -> dict:
    """
    Query PubMed via the NCBI E-utilities API, download up to 100 abstracts
    (5 rounds × 20), build a word-cloud, write an Excel file, and return
    a result dict.

    Parameters
    ----------
    query   : free-text PubMed query
    work_id : unique identifier (used as the Excel filename stem)

    Returns
    -------
    dict with keys:
        total_abstracts, downloaded_abstracts, searchQuery, work_id,
        dict (DataFrame.to_dict()), image (base64 PNG), filename (xlsx)
    """
    result: dict = {
        "total_abstracts": 0,
        "downloaded_abstracts": 0,
        "searchQuery": query,
        "work_id": work_id,
        "dict": "",
        "image": "",
        "filename": f"{work_id}.xlsx",
        "message": "",
    }

    encoded_query = query.replace(" ", "+")
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    db = "db=pubmed"

    # ── esearch ───────────────────────────────────────────────────────────────
    search_url = (
        f"{base_url}esearch.fcgi?{db}"
        f"&term={encoded_query}&usehistory=y&rettype=json"
    )
    with urllib.request.urlopen(search_url, timeout=15) as f:
        search_data = f.read().decode("utf-8")

    total = int(re.findall(r"<Count>(\d+?)</Count>", search_data)[0])
    result["total_abstracts"] = total
    if total < 1:
        result["message"] = "No abstracts found for this query."
        return result

    fetch_webenv   = "&WebEnv="    + re.findall(r"<WebEnv>(\S+)</WebEnv>", search_data)[0]
    fetch_querykey = "&query_key=" + re.findall(r"<QueryKey>(\d+?)</QueryKey>", search_data)[0]

    # ── efetch loop (max 5 rounds × 20 = 100 abstracts) ──────────────────────
    fetch_data = ""
    retmax     = 20
    retstart   = 0
    max_rounds = 5

    for _ in range(max_rounds):
        fetch_url = (
            f"{base_url}efetch.fcgi?{db}{fetch_querykey}{fetch_webenv}"
            f"&retstart={retstart}&retmax={retmax}&retmode=text&rettype=abstract"
        )
        with urllib.request.urlopen(fetch_url, timeout=15) as f:
            fetch_data += f.read().decode("utf-8") + "\n\n\n"

        sleep(0.34)          # ≤ 3 req/s — NCBI policy for unauthenticated access
        retstart += retmax
        if retstart >= total:
            break

    # ── parse into DataFrame ──────────────────────────────────────────────────
    raw_blocks   = [b for b in fetch_data.split("\n\n\n") if b.strip()]
    abstracts_list = [block.split("\n\n") for block in raw_blocks]
    result["downloaded_abstracts"] = len(abstracts_list)
    result["message"] = f"{len(abstracts_list)} abstract(s) downloaded."

    df = pd.DataFrame(abstracts_list)
    df[0] = df[0].apply(_remove_index)
    df["Link"] = df.apply(_find_doi, axis=1)

    # Fix abstract column (col 4)
    if df.shape[1] > 4:
        for i in range(df.shape[0]):
            cell = df.iloc[i, 4]
            if cell is not None:
                cell = cell.lstrip()
                if any(
                    cell.startswith(prefix)
                    for prefix in ("Comment", "Erratum", "Update", "Author")
                ):
                    cell = df.iloc[i, 5] if df.shape[1] > 5 else ""
                if cell.startswith("DOI"):
                    cell = df.iloc[i, 3]
                df.iloc[i, 4] = cell
            else:
                df.iloc[i, 4] = ""

    abstract_col = 4 if df.shape[1] > 4 else df.columns[-1]
    result["image"] = _create_word_cloud(df.iloc[:, abstract_col].tolist())

    # ── rename & export ───────────────────────────────────────────────────────
    rename_map = {0: "Journal", 1: "Title", 2: "Author", 3: "Author Info", 4: "Abstract"}
    keep_cols  = [c for c in [0, 1, 2, 3, 4, "Link"] if c in df.columns]
    new_df     = df[keep_cols].rename(columns=rename_map)

    os.makedirs(USER_DOCS_DIR, exist_ok=True)
    filepath = os.path.join(USER_DOCS_DIR, f"{work_id}.xlsx")
    new_df.to_excel(filepath, index=False)

    result["dict"] = new_df.to_dict()
    return result


# ── Celery task ───────────────────────────────────────────────────────────────

@celery.task(bind=True, name="tasks.search_pubmed")
def search_pubmed_task(self, query: str) -> dict:
    """
    Async Celery task.

    1. Runs the PubMed search (may take 10–60 s for 100 abstracts).
    2. Stores the full JSON payload in Redis under  pubmed:<task_id>
       with a configurable TTL (default 10 min).
    3. Returns a lightweight ``{"status": "done", "work_id": ...}`` dict
       that Celery stores in its own result backend.

    The FastAPI endpoint GET /api/pubmed-result/{task_id} fetches the
    heavy payload directly from Redis.
    """
    work_id = self.request.id   # reuse Celery UUID as the work_id
    try:
        data = search_pubmed_core(query, work_id)
        payload = {
            "total_abstracts":    data["total_abstracts"],
            "downloaded_abstracts": data["downloaded_abstracts"],
            "searchQuery":        data["searchQuery"],
            "work_id":            data["work_id"],
            "dict":               data["dict"],
            "image":              data["image"],
            "filename":           data["filename"],
            "message":            data.get("message", ""),
        }
        redis_key = f"pubmed:{work_id}"
        _redis.setex(redis_key, PUBMED_RESULT_TTL, json.dumps(payload))
        return {"status": "done", "work_id": work_id}
    except Exception as exc:
        # Re-raise so Celery marks the task FAILURE
        raise exc
