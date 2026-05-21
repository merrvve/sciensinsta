import os
import redis as redis_lib
from celery_app import celery
from pptx_service import create_pptx

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PPTX_TTL   = 300    # seconds — how long the generated file lives in Redis

# Separate Redis client for storing raw pptx bytes
# (Celery's result backend stores JSON; binary blobs need direct Redis access)
_redis = redis_lib.from_url(REDIS_URL, decode_responses=False)


@celery.task(bind=True, name="tasks.generate_pptx")
def generate_pptx(self, text: str, is_gpt: bool) -> dict:
    """
    Generates a .pptx file in memory, stores the bytes in Redis under
    key  pptx:<task_id>  with a TTL, and returns a status dict.

    The FastAPI /api/download-pptx/{task_id} endpoint fetches and
    deletes that key once the client downloads the file.
    """
    try:
        buf = create_pptx(text, is_gpt)
        key = f"pptx:{self.request.id}"
        _redis.setex(key, PPTX_TTL, buf.getvalue())
        return {"status": "done"}
    except ValueError as exc:
        # Re-raise so Celery marks the task FAILURE with the message
        raise exc
