from celery import Celery
from .config import settings

celery_app = Celery(
    "faceapp",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks"]
)

celery_app.conf.update(
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_routes={
        "app.tasks.analyze_photo_task": {"queue": "gpu"},
        "app.tasks.build_stage_backgrounds_task": {"queue": "gpu"},
        "app.tasks.render_stage_pages_task": {"queue": "render"},
    },
)
