from celery import Celery
from .config import settings

def _route_task(name, args, kwargs, options, task=None):
    """
    Route tasks to dedicated queues.

    Important: some call sites use `.delay(...)` as a fallback when `.apply_async(..., queue=...)`
    fails. This router keeps behavior consistent in both cases.
    """
    if name == "app.tasks.analyze_photo_task":
        return {"queue": "gpu_prepay"}

    if name == "app.tasks.build_stage_backgrounds_task":
        stage = None
        if len(args) >= 2 and isinstance(args[1], str):
            stage = args[1]
        if isinstance(kwargs, dict) and isinstance(kwargs.get("stage"), str):
            stage = kwargs["stage"]
        if stage == "postpay":
            return {"queue": "gpu_postpay"}
        return {"queue": "gpu_prepay"}

    if name == "app.tasks.render_stage_pages_task":
        return {"queue": "render"}

    return None

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
    task_routes=(_route_task,),
)
