import asyncio
import io
import json
import os
import traceback
import uuid
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import urlparse

import boto3
import cv2
import numpy as np
import requests
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .book.manifest_store import load_manifest
from .book.prompts import join_prompt_parts
from .book.stages import page_nums_for_stage, prepay_page_nums
from .config import settings
from .db import AsyncSessionLocal
from .logger import logger
from .models import BookPreview, Job, JobArtifact
from .workers import celery_app

s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)


async def _get_job(db: AsyncSession, job_id: str):
    res = await db.execute(select(Job).filter(Job.job_id == job_id))
    return res.scalar_one_or_none()


def _should_randomize_seed(job: Job, stage: str, explicit: bool) -> bool:
    if explicit:
        return True
    if stage != "prepay":
        return False
    data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    retry_data = data.get("generation_retry")
    if not isinstance(retry_data, dict):
        return False
    return bool(retry_data.get("randomize_seed"))


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _set_page_regeneration_state(job: Job, *, page_num: int, stage: str, status: str, error: Optional[str] = None) -> None:
    base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    data: Dict = dict(base_data)
    raw = base_data.get("page_regenerations")
    regen_map: Dict = dict(raw) if isinstance(raw, dict) else {}

    key = str(page_num)
    prev = regen_map.get(key)
    started_at = _utcnow_iso()
    if isinstance(prev, dict):
        prev_started = prev.get("startedAt")
        if isinstance(prev_started, str) and prev_started.strip():
            started_at = prev_started

    regen_map[key] = {
        "pageNum": int(page_num),
        "stage": stage,
        "status": status,
        "startedAt": started_at,
        "updatedAt": _utcnow_iso(),
        "error": error,
    }
    data["page_regenerations"] = regen_map
    job.analysis_json = data


def _run_face_transfer(
    child_pil: Image.Image,
    base_uri: str,
    prompt: str,
    negative: str,
    randomize_seed: bool = False,
) -> Image.Image:
    """
    Lazy wrapper to avoid importing ComfyUI/InsightFace stack for text-only pages.
    """
    from .inference.comfy_runner import run_face_transfer

    return run_face_transfer(child_pil, base_uri, prompt, negative, randomize_seed=randomize_seed)


def _build_stage_positive_prompt(manifest, spec, job: Job) -> str:
    page_or_job_prompt = (spec.prompt or job.common_prompt or "child portrait").strip()
    return join_prompt_parts([getattr(manifest, "positive_prompt", None), page_or_job_prompt])


def _has_face(pil_img: Image.Image) -> bool:
    """Fast face presence check using OpenCV Haar cascade."""
    try:
        img_np = np.array(pil_img.convert("RGB"))
        gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
        return len(faces) > 0
    except Exception as e:
        logger.warning(f"Face check failed, assuming face present: {e}")
        return True


def _s3_read_private_to_pil(s3_uri: str) -> Image.Image:
    """Read image from S3 and convert to PIL Image"""
    bucket = settings.S3_BUCKET_NAME
    key: Optional[str] = None

    if s3_uri.startswith("s3://"):
        parts = s3_uri.replace("s3://", "").split("/", 1)
        if len(parts) == 2:
            bucket, key = parts
        else:
            bucket = parts[0]
            key = ""
    elif s3_uri.startswith("http"):
        parsed = urlparse(s3_uri)
        path = parsed.path.lstrip("/")
        # Support path-style URLs: https://host/<bucket>/<key>
        # (VK Cloud style: https://hb.../magicloomio/child_photos/..jpg?... )
        parts = path.split("/", 1)
        if len(parts) == 2:
            bucket, key = parts[0], parts[1]
        else:
            key = parts[0] if parts else ""
    else:
        key = s3_uri

    if key is None:
        raise RuntimeError(f"Failed to parse S3 key from uri={s3_uri!r}")

    logger.debug(f"Reading S3 object: bucket={bucket}, key={key}")
    obj = s3.get_object(Bucket=bucket, Key=key)
    img = Image.open(io.BytesIO(obj["Body"].read())).convert("RGB")
    logger.debug(f"Successfully loaded image: size={img.size}")
    return img


def _try_avatar_url_to_s3_uri(avatar_url: str) -> Optional[str]:
    if not avatar_url or not avatar_url.startswith("http"):
        return None
    parsed = urlparse(avatar_url)
    path = parsed.path.lstrip("/")
    if not path:
        return None
    parts = path.split("/", 1)
    if len(parts) != 2:
        return None
    bucket, key = parts
    if not bucket or not key:
        return None
    return f"s3://{bucket}/{key}"


def _s3_write_pil(img: Image.Image, key: str, dpi: Optional[int] = None) -> str:
    """Write PIL Image to S3"""
    buf = io.BytesIO()
    save_kwargs: Dict[str, object] = {}
    if dpi:
        save_kwargs["dpi"] = (dpi, dpi)
    img.save(buf, format="PNG", **save_kwargs)
    buf.seek(0)

    logger.debug(
        f"Writing image to S3: bucket={settings.S3_BUCKET_NAME}, key={key}, size={len(buf.getvalue())} bytes"
    )
    s3.put_object(Bucket=settings.S3_BUCKET_NAME, Key=key, Body=buf.getvalue(), ContentType="image/png")

    s3_uri = f"s3://{settings.S3_BUCKET_NAME}/{key}"
    logger.info(f"Successfully wrote image to S3: {s3_uri}")
    return s3_uri


def _page_key(page_num: int) -> str:
    return f"page_{page_num:02d}"


def _layout_bg_key(job_id: str, page_num: int) -> str:
    return f"layout/{job_id}/pages/{_page_key(page_num)}_bg.png"


def _layout_final_key(job_id: str, page_num: int) -> str:
    return f"layout/{job_id}/pages/{_page_key(page_num)}.png"


async def _upsert_artifact(
    db: AsyncSession,
    *,
    job_id: str,
    stage: str,
    kind: str,
    s3_uri: str,
    page_num: Optional[int] = None,
    meta: Optional[Dict] = None,
) -> None:
    """
    Insert an artifact record. (We don't enforce uniqueness yet; S3 keys are deterministic anyway.)
    """
    art = JobArtifact(
        id=str(uuid.uuid4()),
        job_id=job_id,
        stage=stage,
        kind=kind,
        page_num=page_num,
        s3_uri=s3_uri,
        meta=meta,
    )
    db.add(art)


@celery_app.task(bind=True, acks_late=True, max_retries=2)
def build_stage_backgrounds_task(
    self,
    job_id: str,
    stage: str,
    randomize_seed: bool = False,
    page_num: Optional[int] = None,
    update_status: bool = True,
    override_child_photo_uri: Optional[str] = None,
):
    """
    GPU-stage task:
    - loads manifest from S3
    - for pages in the given stage:
      - runs face swap if needed
      - otherwise loads base image
      - normalizes to output.page_size_px
      - writes background image to S3 (layout/..._bg.png)
    - enqueues CPU render task (text overlay / finalization)

    If override_child_photo_uri is provided, it is used instead of job.child_photo_uri
    for the current task. The override is only applied when regenerating a single page.
    """

    async def _run():
        requested_page_num = page_num
        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            manifest = load_manifest(job.slug)
            if requested_page_num is not None:
                if stage == "prepay":
                    allowed_page_nums = prepay_page_nums(manifest)
                else:
                    allowed_page_nums = page_nums_for_stage(manifest, stage) or []
                if requested_page_num not in allowed_page_nums:
                    raise RuntimeError(f"Page {requested_page_num} is not available for stage={stage}")
                page_nums = [requested_page_num]
            elif stage == "prepay":
                page_nums = prepay_page_nums(manifest)
            else:
                page_nums = page_nums_for_stage(manifest, stage) or []
            randomize_seed_flag = _should_randomize_seed(job, stage, randomize_seed)

            # Persist per-page regeneration state (for cross-device UI)
            if requested_page_num is not None and not update_status:
                _set_page_regeneration_state(
                    job,
                    page_num=requested_page_num,
                    stage=stage,
                    status="bg_generating",
                    error=None,
                )
                await db.commit()
                await db.refresh(job)

            if update_status:
                if stage == "prepay":
                    job.status = "prepay_generating"
                else:
                    job.status = "postpay_generating"
                await db.commit()
                await db.refresh(job)

            child_pil: Optional[Image.Image] = None
            resolved_child_photo_uri = override_child_photo_uri if requested_page_num is not None else None
            if resolved_child_photo_uri:
                # Page regeneration with a custom photo: crop it on-the-fly so the generation workflow
                # (which no longer crops) receives the same kind of input as the main flow.
                from .inference.comfy_runner import run_face_crop_comfy_api  # noqa: WPS433

                override_pil = _s3_read_private_to_pil(resolved_child_photo_uri)
                if not _has_face(override_pil):
                    raise RuntimeError("No face detected in override photo; cannot regenerate page")
                child_pil = run_face_crop_comfy_api(override_pil)
            else:
                analysis = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                crop_uri = analysis.get("face_crop_uri") if isinstance(analysis, dict) else None
                if isinstance(crop_uri, str) and crop_uri:
                    child_pil = _s3_read_private_to_pil(crop_uri)
                else:
                    # Backward compatibility: some legacy jobs may miss child_photo_uri/face_crop_uri.
                    # Fall back to avatar_url (presigned) and crop it on-the-fly, then persist crop_uri.
                    source_uri = job.child_photo_uri
                    if not source_uri and isinstance(job.avatar_url, str) and job.avatar_url:
                        source_uri = _try_avatar_url_to_s3_uri(job.avatar_url)
                    if isinstance(source_uri, str) and source_uri:
                        from .inference.comfy_runner import run_face_crop_comfy_api  # noqa: WPS433

                        raw_pil = _s3_read_private_to_pil(source_uri)
                        if not _has_face(raw_pil):
                            raise RuntimeError("No face detected in photo; cannot regenerate page")
                        child_pil = run_face_crop_comfy_api(raw_pil)
                        # Persist crop for next runs
                        crop_key = f"avatars/{job_id}_crop.png"
                        crop_uri = _s3_write_pil(child_pil, crop_key)
                        base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                        analysis_data = dict(base_data)
                        analysis_data["face_crop_uri"] = crop_uri
                        job.analysis_json = analysis_data
                        await db.commit()
                        await db.refresh(job)

            for current_page_num in page_nums:
                spec = manifest.page_by_num(current_page_num)
                if not spec:
                    raise RuntimeError(f"Manifest has no page spec for page_num={current_page_num}")

                if spec.needs_face_swap:
                    if child_pil is None:
                        raise RuntimeError("child_photo_uri is missing; cannot run face swap")
                    prompt = _build_stage_positive_prompt(manifest, spec, job)
                    negative = (spec.negative_prompt or "low quality, bad face, distorted").strip()
                    out_img = _run_face_transfer(
                        child_pil,
                        spec.base_uri,
                        prompt,
                        negative,
                        randomize_seed=randomize_seed_flag,
                    )
                else:
                    out_img = _s3_read_private_to_pil(spec.base_uri)

                target = manifest.output.page_size_px
                if out_img.size != (target, target):
                    out_img = out_img.resize((target, target), Image.Resampling.LANCZOS)

                bg_key = _layout_bg_key(job_id, current_page_num)
                bg_uri = _s3_write_pil(out_img, bg_key, dpi=manifest.output.dpi)
                await _upsert_artifact(
                    db,
                    job_id=job_id,
                    stage=stage,
                    kind="page_bg_png",
                    s3_uri=bg_uri,
                    page_num=current_page_num,
                )

            if randomize_seed_flag and stage == "prepay":
                base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                retry_data = base_data.get("generation_retry")
                if isinstance(retry_data, dict):
                    data = dict(base_data)
                    retry_copy = dict(retry_data)
                    retry_copy["randomize_seed"] = False
                    data["generation_retry"] = retry_copy
                    job.analysis_json = data

            await db.commit()

            render_kwargs = {
                "job_id": job_id,
                "stage": stage,
                "page_num": requested_page_num,
                "update_status": update_status,
            }
            if requested_page_num is None and update_status:
                render_kwargs = {"job_id": job_id, "stage": stage}

            if requested_page_num is not None and not update_status:
                _set_page_regeneration_state(
                    job,
                    page_num=requested_page_num,
                    stage=stage,
                    status="render_queued",
                    error=None,
                )
                await db.commit()
                await db.refresh(job)

            try:
                render_stage_pages_task.apply_async(kwargs=render_kwargs, queue="render")
            except Exception:
                render_stage_pages_task.delay(**render_kwargs)

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(
            f"Stage background build failed for job {job_id}: {e}",
            extra={"job_id": job_id, "stage": stage, "traceback": traceback.format_exc()},
        )
        if update_status:
            try:
                async def _mark_failed():
                    async with AsyncSessionLocal() as db:
                        job = await _get_job(db, job_id)
                        if job:
                            job.status = "generation_failed"
                            await db.commit()

                asyncio.run(_mark_failed())
            except Exception:
                pass
        else:
            if page_num is not None:
                try:
                    async def _mark_regen_failed():
                        async with AsyncSessionLocal() as db:
                            job = await _get_job(db, job_id)
                            if not job:
                                return
                            _set_page_regeneration_state(
                                job,
                                page_num=page_num,
                                stage=stage,
                                status="failed",
                                error=str(e),
                            )
                            await db.commit()

                    asyncio.run(_mark_regen_failed())
                except Exception:
                    pass
        raise


@celery_app.task(bind=True, acks_late=True, max_retries=2)
def render_stage_pages_task(
    self,
    job_id: str,
    stage: str,
    page_num: Optional[int] = None,
    update_status: bool = True,
):
    """
    CPU-stage task:
    - loads manifest
    - for pages in stage:
      - loads background image from S3 (layout/..._bg.png) OR derives it directly from base_uri for non-face pages
      - applies text layers if configured
      - writes final page image to S3 (layout/...page_XX.png)
    """

    async def _run():
        from .rendering.html_text import render_text_layers_over_image

        requested_page_num = page_num
        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            manifest = load_manifest(job.slug)
            if requested_page_num is not None:
                if stage == "prepay":
                    allowed_page_nums = prepay_page_nums(manifest)
                else:
                    allowed_page_nums = page_nums_for_stage(manifest, stage) or []
                if requested_page_num not in allowed_page_nums:
                    raise RuntimeError(f"Page {requested_page_num} is not available for stage={stage}")
                page_nums = [requested_page_num]
            elif stage == "prepay":
                page_nums = prepay_page_nums(manifest)
            else:
                page_nums = page_nums_for_stage(manifest, stage) or []

            # Persist per-page regeneration state (for cross-device UI)
            if requested_page_num is not None and not update_status:
                _set_page_regeneration_state(
                    job,
                    page_num=requested_page_num,
                    stage=stage,
                    status="rendering",
                    error=None,
                )
                await db.commit()
                await db.refresh(job)

            if update_status:
                if stage == "prepay":
                    job.status = "prepay_generating"
                else:
                    job.status = "postpay_generating"
                await db.commit()
                await db.refresh(job)

            for current_page_num in page_nums:
                spec = manifest.page_by_num(current_page_num)
                if not spec:
                    raise RuntimeError(f"Manifest has no page spec for page_num={current_page_num}")

                target = manifest.output.page_size_px
                bg_key = _layout_bg_key(job_id, current_page_num)

                if spec.needs_face_swap:
                    bg_uri = f"s3://{settings.S3_BUCKET_NAME}/{bg_key}"
                    bg_img = _s3_read_private_to_pil(bg_uri)
                else:
                    bg_img = _s3_read_private_to_pil(spec.base_uri)
                    if bg_img.size != (target, target):
                        bg_img = bg_img.resize((target, target), Image.Resampling.LANCZOS)

                    bg_uri = _s3_write_pil(bg_img, bg_key, dpi=manifest.output.dpi)
                    await _upsert_artifact(
                        db,
                        job_id=job_id,
                        stage=stage,
                        kind="page_bg_png",
                        s3_uri=bg_uri,
                        page_num=current_page_num,
                    )

                if spec.text_layers:
                    final_img = await render_text_layers_over_image(
                        bg_img,
                        spec.text_layers,
                        template_vars={
                            "child_name": job.child_name,
                            "child_age": job.child_age,
                            "child_gender": job.child_gender,
                        },
                        output_px=manifest.output.page_size_px,
                    )
                else:
                    final_img = bg_img

                final_key = _layout_final_key(job_id, current_page_num)
                final_uri = _s3_write_pil(final_img, final_key, dpi=manifest.output.dpi)
                await _upsert_artifact(
                    db,
                    job_id=job_id,
                    stage=stage,
                    kind="page_png",
                    s3_uri=final_uri,
                    page_num=current_page_num,
                )

            if update_status:
                if stage == "prepay":
                    job.status = "prepay_ready"
                else:
                    job.status = "completed"
            else:
                # Mark regeneration as completed for requested page
                if requested_page_num is not None:
                    _set_page_regeneration_state(
                        job,
                        page_num=requested_page_num,
                        stage=stage,
                        status="completed",
                        error=None,
                    )
            await db.commit()

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(
            f"Stage render failed for job {job_id}: {e}",
            extra={"job_id": job_id, "stage": stage, "traceback": traceback.format_exc()},
        )
        if update_status:
            try:
                async def _mark_failed():
                    async with AsyncSessionLocal() as db:
                        job = await _get_job(db, job_id)
                        if job:
                            job.status = "generation_failed"
                            await db.commit()

                asyncio.run(_mark_failed())
            except Exception:
                pass
        else:
            if page_num is not None:
                try:
                    async def _mark_regen_failed():
                        async with AsyncSessionLocal() as db:
                            job = await _get_job(db, job_id)
                            if not job:
                                return
                            _set_page_regeneration_state(
                                job,
                                page_num=page_num,
                                stage=stage,
                                status="failed",
                                error=str(e),
                            )
                            await db.commit()

                    asyncio.run(_mark_regen_failed())
                except Exception:
                    pass
        raise


@celery_app.task(bind=True, acks_late=True, max_retries=3)
def analyze_photo_task(self, job_id: str, child_photo_uri: str, illustration_id: str, child_gender: str):
    """
    Celery task to analyze child photo (lightweight placeholder).
    """

    async def _run():
        logger.info(f"Starting photo analysis for job: {job_id}")

        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            job.status = "analyzing"
            await db.commit()
            await db.refresh(job)

            try:
                if not job.child_photo_uri:
                    raise RuntimeError("child_photo_uri is missing; cannot analyze photo")

                pil = _s3_read_private_to_pil(job.child_photo_uri)

                # Step 1: face detection gate.
                # Do not attempt to crop if no face is detected (Comfy crop node can still return something).
                has_face = _has_face(pil)
                if not has_face:
                    base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                    analysis = dict(base_data)
                    analysis["face_detected"] = False
                    analysis["analysis_error"] = "no_face_detected"
                    job.analysis_json = analysis
                    job.status = "analysis_failed"
                    await db.commit()
                    return

                # Use ComfyUI's face crop node (part of generation pipeline) in analysis,
                # so the user sees exactly what will be used in generation.
                from .inference.comfy_runner import run_face_crop_comfy_api  # noqa: WPS433

                crop = run_face_crop_comfy_api(pil)
                crop_key = f"avatars/{job_id}_crop.png"
                crop_uri = _s3_write_pil(crop, crop_key)

                base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                analysis = dict(base_data)
                analysis["face_detected"] = True
                analysis["face_crop_uri"] = crop_uri
                analysis["note"] = "face crop via comfy"
                job.analysis_json = analysis

                await _upsert_artifact(
                    db,
                    job_id=job_id,
                    stage="analysis",
                    kind="avatar_crop_png",
                    s3_uri=crop_uri,
                    page_num=None,
                    meta=None,
                )

                job.common_prompt = "child portrait, neutral, high quality"
                job.status = "analyzing_completed"
                await db.commit()
                await db.refresh(job)
            except Exception as e:
                # ComfyUI may still be starting up after a full container restart.
                # Treat connection errors as transient and let Celery retry a few times
                # instead of immediately marking the job as failed.
                if isinstance(e, requests.exceptions.ConnectionError) and self.request.retries < self.max_retries:
                    logger.warning(
                        f"Analysis transient failure for job {job_id}, will retry: {str(e)}",
                        extra={
                            "job_id": job_id,
                            "error": str(e),
                            "retry": int(self.request.retries) + 1,
                            "max_retries": int(self.max_retries),
                        },
                    )
                    base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                    analysis = dict(base_data)
                    analysis["analysis_retrying"] = True
                    analysis["analysis_retry_error"] = str(e)
                    analysis["analysis_retry_count"] = int(self.request.retries) + 1
                    job.analysis_json = analysis
                    job.status = "analyzing"
                    await db.commit()
                    raise self.retry(exc=e, countdown=5 * (int(self.request.retries) + 1))

                logger.error(
                    f"Analysis failed for job {job_id}: {str(e)}",
                    extra={
                        "job_id": job_id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
                base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
                analysis = dict(base_data)
                analysis["face_detected"] = False
                analysis["analysis_error"] = str(e)
                job.analysis_json = analysis
                job.status = "analysis_failed"
                await db.commit()
                return

    return asyncio.run(_run())


@celery_app.task(bind=True, acks_late=True, max_retries=2)
def generate_image_task(self, job_id: str, child_name: str = None, child_age: int = None):
    """
    Legacy task (kept for backward compatibility).
    """

    async def _run():
        logger.info(f"Starting image generation for job: {job_id}")

        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return

            if job.status not in ("pending_generation", "generating"):
                logger.warning(f"Job {job_id} not in pending_generation/generating state: {job.status}")
                return

            if job.status != "generating":
                job.status = "generating"
                await db.commit()
                await db.refresh(job)

            try:
                child_pil = _s3_read_private_to_pil(job.child_photo_uri)

                illustrations_path = os.path.join(os.path.dirname(__file__), "illustrations.json")
                with open(illustrations_path, "r", encoding="utf-8") as f:
                    ill_data = json.load(f)
                    illustrations = ill_data.get("illustrations", [])

                preview_res = await db.execute(
                    select(BookPreview).filter(BookPreview.slug == job.slug).order_by(BookPreview.page_index)
                )
                preview_pages_all = preview_res.scalars().all()
                preview_pages = [p for p in preview_pages_all if p.image_url and "/thumbnails/" not in p.image_url]

                required_ill_ids = []
                for p in preview_pages:
                    try:
                        base = os.path.basename(urlparse(p.image_url).path)
                        ill_id, _ext = os.path.splitext(base)
                        if ill_id and ill_id not in required_ill_ids:
                            required_ill_ids.append(ill_id)
                    except Exception:
                        continue

                if not required_ill_ids:
                    required_ill_ids = [i.get("id") for i in illustrations if i.get("id")]

                ill_by_id = {i.get("id"): i for i in illustrations if i.get("id")}

                resolved_child_name = child_name or job.child_name
                resolved_child_age = child_age if child_age is not None else job.child_age
                common_prompt_base = (job.common_prompt or "child portrait").strip(", ")
                personal_bits = []
                if resolved_child_name:
                    personal_bits.append(str(resolved_child_name))
                if resolved_child_age is not None:
                    personal_bits.append(f"{resolved_child_age} years old")
                if job.child_gender:
                    personal_bits.append(job.child_gender)
                if personal_bits:
                    common_prompt = f"{common_prompt_base}, " + ", ".join(personal_bits)
                    common_prompt = common_prompt.strip(", ")
                else:
                    common_prompt = common_prompt_base
                base_negative = "low quality, bad face, distorted"

                saved_results = []
                failed_ids = []
                for ill_id in required_ill_ids:
                    ill = ill_by_id.get(ill_id)
                    if not ill:
                        failed_ids.append(ill_id)
                        continue

                    illustration_uri = ill.get("full_uri") or ill.get("thumbnail_uri")
                    if not illustration_uri:
                        failed_ids.append(ill_id)
                        continue

                    ill_prompt = ill.get("prompt")
                    ill_negative = ill.get("negative_prompt")
                    prompt = f"{ill_prompt}, {common_prompt}" if ill_prompt else common_prompt
                    negative = f"{ill_negative}, {base_negative}" if ill_negative else base_negative

                    try:
                        out_img = _run_face_transfer(child_pil, illustration_uri, prompt, negative)
                        result_key = f"results/{job_id}/{ill_id}.png"
                        s3_uri = _s3_write_pil(out_img, result_key)
                        saved_results.append((ill_id, s3_uri))
                    except Exception:
                        failed_ids.append(ill_id)

                if not saved_results or failed_ids:
                    job.status = "generation_failed"
                    analysis = job.analysis_json or {}
                    analysis["generation_failed_ids"] = failed_ids
                    job.analysis_json = analysis
                    await db.commit()
                    raise RuntimeError(f"Generation incomplete: {failed_ids}")

                job.result_uri = saved_results[0][1]
                job.status = "completed"
                await db.commit()
                await db.refresh(job)

            except Exception as e:
                logger.error(
                    f"Generation failed for job {job_id}: {str(e)}",
                    extra={
                        "job_id": job_id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
                job.status = "generation_failed"
                await db.commit()
                raise

    return asyncio.run(_run())

