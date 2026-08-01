"""Compatibility consumer for retired ontology document-extraction messages."""

from app.tasks.celery_app import celery_app


@celery_app.task(bind=True)
def run_extraction(self, task_id: str) -> dict[str, str]:
    """Acknowledge legacy queued messages without running retired behavior."""
    return {
        "status": "retired",
        "task_id": task_id,
        "message": "Ontology document extraction has been retired",
    }
