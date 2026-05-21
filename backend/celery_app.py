from celery import Celery
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery = Celery(
    "sciensinsta",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks"],      # auto-import tasks module on worker start
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=300,     # Celery result entry TTL: 5 minutes
    worker_prefetch_multiplier=1,   # one task at a time per worker process
    task_acks_late=True,            # ack only after task finishes (safer on crash)
)
