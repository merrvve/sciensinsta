import os
from celery_app import celery
from pptx_service import create_pptx
from r2_service import upload_bytes

PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


@celery.task(bind=True, name="tasks.generate_pptx")
def generate_pptx(self, text: str, is_gpt: bool) -> dict:
    """
    Generates a .pptx file in memory and uploads it to Cloudflare R2 under
    ``user-docs/<task_id>.pptx``.

    The FastAPI ``GET /api/download-pptx/{task_id}`` endpoint generates a
    presigned R2 URL once the task reaches SUCCESS state.
    """
    try:
        buf = create_pptx(text, is_gpt)
        filename = f"{self.request.id}.pptx"
        upload_bytes(buf.getvalue(), filename, PPTX_CONTENT_TYPE)
        return {"status": "done", "filename": filename}
    except ValueError as exc:
        # Re-raise so Celery marks the task FAILURE with the message
        raise exc
