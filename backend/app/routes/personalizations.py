"""
Personalization routes - face transfer / book generation API.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import boto3
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import User, get_current_user_optional
from ..book.manifest_store import load_manifest
from ..book.stages import page_nums_for_front_preview, page_nums_for_stage, stage_has_face_swap
from ..config import settings
from ..db import get_db
from ..exceptions import InvalidJobStateError, JobNotFoundError, S3StorageError
from ..logger import logger
from ..models import Book, BookPreview, Job
from ..schemas import AvatarUploadResponse, Personalization, PreviewPage, PreviewResponse
from ..tasks import analyze_photo_task, build_stage_backgrounds_task, render_stage_pages_task

router = APIRouter(tags=["Personalizations"])

s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)


def _s3_put_uploadfile(file: UploadFile, key: str) -> str:
    try:
        s3.upload_fileobj(
            file.file,
            settings.S3_BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": file.content_type or "image/jpeg"},
        )
        return f"s3://{settings.S3_BUCKET_NAME}/{key}"
    except Exception as e:
        raise S3StorageError(f"Failed to upload file: {str(e)}") from e


def _presigned_get(uri: str, expires: int = 3600) -> str:
    """
    Generate a presigned GET URL for:
    - s3://bucket/key
    - relative key like templates/foo.jpg (uses configured bucket)
    - http(s) urls: try to parse bucket/key if it's our configured endpoint; otherwise return as-is
    """
    if not uri:
        return uri

    parsed_endpoint = urlparse(settings.AWS_ENDPOINT_URL) if settings.AWS_ENDPOINT_URL else None

    bucket: Optional[str] = None
    key: Optional[str] = None

    if uri.startswith("http"):
        p = urlparse(uri)
        if parsed_endpoint and p.netloc == parsed_endpoint.netloc:
            path = p.path.lstrip("/")
            parts = path.split("/", 1)
            if len(parts) == 2:
                bucket, key = parts
            elif parts:
                bucket = settings.S3_BUCKET_NAME
                key = parts[0]
        else:
            # Foreign URL - do not re-sign
            return uri

        if not bucket or key is None:
            return uri

        if bucket != settings.S3_BUCKET_NAME:
            return uri

        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )

    if uri.startswith("s3://"):
        p = urlparse(uri)
        bucket = p.netloc
        key = p.path.lstrip("/")
    else:
        bucket = settings.S3_BUCKET_NAME
        key = uri.lstrip("/")

    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def _layout_page_key(job_id: str, page_num: int) -> str:
    return f"layout/{job_id}/pages/page_{page_num:02d}.png"


def _is_thumbnail_uri(uri: Optional[str]) -> bool:
    return bool(uri and "/thumbnails/" in uri)


def _extract_ill_id_from_uri(uri: str) -> Optional[str]:
    try:
        base = os.path.basename(urlparse(uri).path)
        name, _ext = os.path.splitext(base)
        return name or None
    except Exception:
        return None


async def _get_preview_for_job(job: Job, db: AsyncSession) -> Optional[PreviewResponse]:
    """
    Prefer manifest-driven staged preview when available. Fallback to BookPreview if manifest missing.
    """
    if job.status not in ["preview_ready", "confirmed", "completed", "prepay_ready", "postpay_generating"]:
        return None

    try:
        stage = "prepay" if job.status in ["prepay_ready", "confirmed", "postpay_generating"] else "postpay"
        manifest = load_manifest(job.slug)
        page_nums = page_nums_for_front_preview(manifest, stage)
        pages = [
            PreviewPage(
                index=pn,
                imageUrl=_presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{_layout_page_key(job.job_id, pn)}"),
                locked=False,
                caption=None,
            )
            for pn in page_nums
        ]
        return PreviewResponse(pages=pages, unlockedCount=len(pages), totalCount=len(pages))
    except Exception:
        pass

    preview_result = await db.execute(
        select(BookPreview).filter(BookPreview.slug == job.slug).order_by(BookPreview.page_index)
    )
    preview_pages = preview_result.scalars().all()
    preview_pages = [p for p in preview_pages if not _is_thumbnail_uri(p.image_url)]

    pages = [
        PreviewPage(
            index=p.page_index,
            imageUrl=_presigned_get(p.image_url),
            locked=False,
            caption=p.caption,
        )
        for p in preview_pages
    ]

    return PreviewResponse(pages=pages, unlockedCount=len(pages), totalCount=len(pages))


def _job_to_personalization(job: Job, preview: Optional[PreviewResponse] = None) -> Personalization:
    def normalize_child_name(value: Optional[str]) -> str:
        if not value:
            return ""
        trimmed = value.strip()
        if not trimmed:
            return ""
        lowered = trimmed.lower()
        if lowered in ("unknown", "unknow"):
            return ""
        return trimmed

    avatar_url = job.avatar_url
    if not avatar_url and job.child_photo_uri:
        try:
            avatar_url = _presigned_get(job.child_photo_uri)
        except Exception:
            avatar_url = None

    return Personalization(
        id=job.job_id,
        slug=job.slug,
        childName=normalize_child_name(job.child_name),
        childAge=job.child_age,
        status=job.status,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
        previewReadyAt=job.preview_ready_at,
        avatarUrl=avatar_url,
        preview=preview,
        cartItemId=job.cart_item_id,
    )


@router.post("/upload_and_analyze/", response_model=Personalization, status_code=201)
async def upload_and_analyze(
    slug: str = Form(...),
    child_photo: UploadFile = File(...),
    illustration_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    book_result = await db.execute(select(Book).filter(Book.slug == slug))
    book = book_result.scalar_one_or_none()
    if not book:
        raise HTTPException(status_code=404, detail={"error": {"code": "BOOK_NOT_FOUND", "message": "Book not found"}})

    if child_photo.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(status_code=400, detail="Only jpg/png allowed")

    job_id = str(uuid.uuid4())
    photo_key = f"child_photos/{job_id}_{child_photo.filename}"
    child_photo_uri = _s3_put_uploadfile(child_photo, photo_key)

    job = Job(
        job_id=job_id,
        user_id=current_user.id if current_user else "anon",
        slug=slug,
        status="pending_analysis",
        child_photo_uri=child_photo_uri,
        child_name="",
        child_age=0,
        child_gender=None,
        caption_uri=None,
    )
    try:
        job.avatar_url = _presigned_get(child_photo_uri)
    except Exception:
        job.avatar_url = None

    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        analyze_photo_task.apply_async(args=(job_id, child_photo_uri, illustration_id, "unknown"), queue="gpu_prepay")
    except Exception:
        analyze_photo_task.delay(job_id, child_photo_uri, illustration_id, "unknown")

    return _job_to_personalization(job)


@router.get("/preview/{job_id}", response_model=PreviewResponse)
async def get_personalization_preview_stage(
    job_id: str,
    stage: str = Query("prepay", pattern="^(prepay|postpay)$"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        alt_result = await db.execute(select(Job).filter(Job.cart_item_id == job_id))
        job = alt_result.scalar_one_or_none()
        if not job:
            raise JobNotFoundError(job_id)

    if stage == "prepay":
        if job.status not in ["prepay_ready", "confirmed", "postpay_generating", "completed"]:
            raise InvalidJobStateError(job_id, job.status, "prepay_ready, confirmed (or later)")
    else:
        if job.status not in ["completed"]:
            raise InvalidJobStateError(job_id, job.status, "completed")

    manifest = load_manifest(job.slug)
    page_nums = page_nums_for_front_preview(manifest, stage)
    pages = [
        PreviewPage(
            index=pn,
            imageUrl=_presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{_layout_page_key(job.job_id, pn)}"),
            locked=False,
            caption=None,
        )
        for pn in page_nums
    ]
    return PreviewResponse(pages=pages, unlockedCount=len(pages), totalCount=len(pages))


@router.post("/avatar/{job_id}", response_model=AvatarUploadResponse, status_code=201)
async def upload_personalization_avatar(job_id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise JobNotFoundError(job_id)

    if file.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(status_code=400, detail="Only jpg/png allowed")

    avatar_key = f"avatars/{job_id}_{file.filename}"
    avatar_uri = _s3_put_uploadfile(file, avatar_key)

    job.child_photo_uri = avatar_uri
    try:
        job.avatar_url = _presigned_get(avatar_uri)
    except Exception:
        job.avatar_url = None
    job.status = "pending_analysis"
    await db.commit()

    try:
        analyze_photo_task.apply_async(args=(job_id, avatar_uri, None, job.child_gender or "unknown"), queue="gpu_prepay")
    except Exception:
        analyze_photo_task.delay(job_id, avatar_uri, None, job.child_gender or "unknown")

    return AvatarUploadResponse(uploadId=job_id, expiresAt=datetime.utcnow())


@router.post("/generate/")
async def confirm_personalization_generate(
    job_id: str = Form(...),
    child_name: str = Form(...),
    child_age: int = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise JobNotFoundError(job_id)

    if job.status not in ["preview_ready", "analyzing_completed", "prepay_ready"]:
        raise InvalidJobStateError(job_id, job.status, "preview_ready, analyzing_completed, or prepay_ready")

    if not current_user:
        raise HTTPException(status_code=401, detail={"error": {"code": "UNAUTHORIZED", "message": "Authentication required"}})

    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN", "message": "Personalization does not belong to user"}})

    job.child_name = child_name
    job.child_age = child_age
    job.status = "prepay_pending"
    await db.commit()
    await db.refresh(job)

    try:
        manifest = load_manifest(job.slug)
        if stage_has_face_swap(manifest, "prepay"):
            build_stage_backgrounds_task.apply_async(args=(job_id, "prepay"), queue="gpu_prepay")
            logger.info(f"Started PREPAY generation (GPU stage) for job after confirmation: {job_id}")
        else:
            render_stage_pages_task.apply_async(args=(job_id, "prepay"), queue="render")
            logger.info(f"Started PREPAY generation (render-only, no face swap) for job after confirmation: {job_id}")
    except Exception as e:
        logger.error(f"Failed to enqueue generation for job {job_id}: {e}")
        raise

    return {"status": "ok", "message": "Generation started"}

"""
Personalization routes - face transfer API
"""
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
from typing import Optional, List
from datetime import datetime
import boto3
from botocore.exceptions import ClientError
import os
import io
import zipfile
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import asyncio

from ..db import get_db
from ..models import Job, Book, BookPreview
from ..schemas import (
    Personalization,
    AvatarUploadResponse,
    PreviewResponse,
    PreviewPage,
    GenerationRetry,
    PageFaceSwapCheckResponse,
    PageRegeneration,
)
from ..auth import get_current_user, get_current_user_optional, get_current_user_header_or_query, User
from ..config import settings
from ..tasks import analyze_photo_task, build_stage_backgrounds_task, render_stage_pages_task
from ..logger import logger
from ..exceptions import JobNotFoundError, InvalidJobStateError, S3StorageError
from ..book.manifest_store import load_manifest
from ..book.stages import page_nums_for_front_preview, page_nums_for_stage, stage_has_face_swap
from PIL import Image

router = APIRouter(tags=["Personalizations"])

GENERATION_RETRY_LIMIT = 3

# Thread pool for CPU-intensive PDF generation
_pdf_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf_gen")

s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)

def _s3_put_uploadfile(file: UploadFile, key: str) -> str:
    try:
        s3.upload_fileobj(
            file.file,
            settings.S3_BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": file.content_type or "image/jpeg"}
        )
        logger.info(f"Uploaded file to S3: {key}")
        return f"s3://{settings.S3_BUCKET_NAME}/{key}"
    except Exception as e:
        logger.error(f"Failed to upload file to S3: {e}")
        raise S3StorageError(f"Failed to upload file: {str(e)}")

def _presigned_get(uri: str, expires=3600) -> str:
    """
    Generate a presigned GET URL for:
    - s3://bucket/key
    - http(s)://<endpoint>/<bucket>/<key> (path-style)
    - http(s)://<bucket>.<endpoint>/<key> (virtual-host style)
    - relative key like illustrations/foo.jpg (uses configured bucket)
    """
    if not uri:
        return uri

    parsed_endpoint = urlparse(settings.AWS_ENDPOINT_URL) if settings.AWS_ENDPOINT_URL else None

    bucket = None
    key = None

    if uri.startswith("http"):
        p = urlparse(uri)

        # If host matches configured endpoint, use path-style parsing
        if parsed_endpoint and p.netloc == parsed_endpoint.netloc:
            path = p.path.lstrip("/")
            parts = path.split("/", 1)
            if len(parts) == 2:
                bucket, key = parts
            elif parts:
                bucket = settings.S3_BUCKET_NAME
                key = parts[0]
        else:
            # Different host - try virtual-host style first: bucket.domain.tld/...
            host_parts = p.netloc.split(".")
            if len(host_parts) >= 3:
                bucket = host_parts[0]
                key = p.path.lstrip("/")

            # path-style fallback: domain.tld/bucket/key
            if not bucket:
                path = p.path.lstrip("/")
                parts = path.split("/", 1)
                if len(parts) == 2:
                    bucket, key = parts

            # If couldn't parse bucket/key from foreign URL, return as is
            if not bucket or not key:
                return uri

            # Foreign bucket (not our configured one) should not be re-signed via our S3 endpoint.
            # Otherwise we end up generating invalid links for 3rd-party public URLs.
            if bucket != settings.S3_BUCKET_NAME:
                return uri

        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )

    # Handle s3:// scheme
    if uri.startswith("s3://"):
        p = urlparse(uri)
        bucket = p.netloc
        key = p.path.lstrip("/")
    else:
        # Relative path - use configured bucket
        bucket = settings.S3_BUCKET_NAME
        key = uri.lstrip("/")

    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


async def _get_job_by_any_id(db: AsyncSession, job_id_or_cart_item_id: str) -> Job:
    result = await db.execute(select(Job).filter(Job.job_id == job_id_or_cart_item_id))
    job = result.scalar_one_or_none()
    if job:
        return job

    alt_result = await db.execute(select(Job).filter(Job.cart_item_id == job_id_or_cart_item_id))
    job = alt_result.scalar_one_or_none()
    if job:
        return job

    raise JobNotFoundError(job_id_or_cart_item_id)


def _read_generation_retry_used(job: Job) -> int:
    data = job.analysis_json
    if not isinstance(data, dict):
        return 0
    retry_data = data.get("generation_retry")
    if not isinstance(retry_data, dict):
        return 0
    used = retry_data.get("used")
    if isinstance(used, int) and used >= 0:
        return used
    if isinstance(used, float) and used.is_integer() and used >= 0:
        return int(used)
    return 0


def _build_generation_retry(job: Job) -> GenerationRetry:
    used = _read_generation_retry_used(job)
    remaining = max(0, GENERATION_RETRY_LIMIT - used)
    return GenerationRetry(
        used=used,
        limit=GENERATION_RETRY_LIMIT,
        remaining=remaining,
        allowed=remaining > 0,
    )


def _set_generation_retry_used(job: Job, used: int) -> None:
    base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    data = dict(base_data)
    retry_data = base_data.get("generation_retry")
    retry_data = dict(retry_data) if isinstance(retry_data, dict) else {}
    retry_data["used"] = max(0, used)
    retry_data["limit"] = GENERATION_RETRY_LIMIT
    data["generation_retry"] = retry_data
    job.analysis_json = data


def _set_generation_retry_randomize(job: Job, value: bool) -> None:
    base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    data = dict(base_data)
    retry_data = base_data.get("generation_retry")
    retry_data = dict(retry_data) if isinstance(retry_data, dict) else {}
    retry_data["randomize_seed"] = bool(value)
    data["generation_retry"] = retry_data
    job.analysis_json = data


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _set_page_regeneration_state(
    job: Job,
    *,
    page_num: int,
    stage: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    base_data = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    data = dict(base_data)
    raw = base_data.get("page_regenerations")
    regen_map = dict(raw) if isinstance(raw, dict) else {}

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


def _build_page_regenerations(job: Job) -> Optional[List[PageRegeneration]]:
    data = job.analysis_json
    if not isinstance(data, dict):
        return None
    raw = data.get("page_regenerations")
    if not isinstance(raw, dict):
        return None

    result: List[PageRegeneration] = []
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        page_num = value.get("pageNum")
        if not isinstance(page_num, int):
            try:
                page_num = int(key)
            except Exception:
                continue
        stage = value.get("stage")
        status = value.get("status")
        started_at = value.get("startedAt")
        updated_at = value.get("updatedAt")
        if not isinstance(stage, str) or not isinstance(status, str):
            continue
        if not isinstance(started_at, str) or not isinstance(updated_at, str):
            continue

        try:
            started_dt = datetime.fromisoformat(started_at)
            updated_dt = datetime.fromisoformat(updated_at)
        except Exception:
            continue

        err = value.get("error")
        error_str = err if isinstance(err, str) else None

        result.append(
            PageRegeneration(
                pageNum=page_num,
                stage=stage,
                status=status,
                startedAt=started_dt,
                updatedAt=updated_dt,
                error=error_str,
            )
        )

    return sorted(result, key=lambda item: item.pageNum)


def _s3_get_bytes(bucket: str, key: str) -> bytes:
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj.get("Body")
    if not body:
        return b""
    return body.read()


def _all_manifest_page_nums(manifest) -> List[int]:
    nums: List[int] = []
    for p in getattr(manifest, "pages", []):
        pn = getattr(p, "page_num", None)
        if isinstance(pn, int):
            nums.append(pn)
    # Ensure prepay pages are included too (first 3 pages from postpay list).
    nums.extend(page_nums_for_stage(manifest, "prepay"))
    return sorted(set(nums))


def _pdf_s3_key(job_id: str) -> str:
    return f"layout/{job_id}/book.pdf"


def _print_pdf_s3_key(job_id: str, kind: str) -> str:
    # Keep print artifacts separate from the legacy "book.pdf" cache key.
    if kind == "block":
        return f"layout/{job_id}/print/block.pdf"
    if kind == "cover":
        return f"layout/{job_id}/print/cover.pdf"
    raise ValueError(f"Unknown print PDF kind: {kind}")


def _is_s3_not_found_error(error: ClientError) -> bool:
    error_code = str(error.response.get("Error", {}).get("Code", ""))
    status_code = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return error_code in {"404", "NoSuchKey", "NotFound"} or status_code == 404


async def _wait_for_s3_object(bucket: str, key: str, attempts: int = 6, delay_seconds: float = 0.5) -> bool:
    for attempt in range(attempts):
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if _is_s3_not_found_error(e):
                if attempt < attempts - 1:
                    await asyncio.sleep(delay_seconds)
                    continue
                return False
            raise
    return False


def _build_pdf_bytes(job: Job, page_nums: List[int]) -> bytes:
    images: List[Image.Image] = []
    for pn in page_nums:
        key = f"layout/{job.job_id}/pages/page_{pn:02d}.png"
        try:
            content = _s3_get_bytes(settings.S3_BUCKET_NAME, key)
            if not content:
                logger.warning(f"Page {pn} not found in S3, skipping")
                continue
            with Image.open(io.BytesIO(content)) as img:
                images.append(img.convert("RGB"))
        except Exception as e:
            logger.error(f"Failed to load page {pn}: {e}")
            continue

    if len(images) == 0:
        logger.error(f"No images found for job {job.job_id}")
        raise HTTPException(status_code=404, detail="No pages found")

    try:
        first, *rest = images
        pdf_buffer = io.BytesIO()
        first.save(pdf_buffer, format="PDF", save_all=True, append_images=rest)
        return pdf_buffer.getvalue()
    finally:
        for img in images:
            try:
                img.close()
            except Exception:
                continue


async def _ensure_pdf_in_s3(job: Job, page_nums: List[int]) -> str:
    key = _pdf_s3_key(job.job_id)
    return await _ensure_pdf_in_s3_by_key(job, page_nums, key)


async def _ensure_pdf_in_s3_by_key(job: Job, page_nums: List[int], key: str) -> str:
    try:
        s3.head_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        return key
    except ClientError as e:
        if not _is_s3_not_found_error(e):
            raise

    pdf_bytes = await asyncio.get_event_loop().run_in_executor(
        _pdf_executor,
        _build_pdf_bytes,
        job,
        page_nums,
    )

    s3.put_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        CacheControl="no-store",
    )
    is_ready = await _wait_for_s3_object(settings.S3_BUCKET_NAME, key)
    if not is_ready:
        logger.warning(f"S3 PDF not visible yet for job {job.job_id}")
        raise HTTPException(
            status_code=503,
            detail="PDF is still being finalized. Please try again in a few seconds.",
        )
    return key


def _presigned_pdf_download_url(key: str, filename: str) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.S3_BUCKET_NAME,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
            "ResponseContentType": "application/pdf",
            "ResponseCacheControl": "no-store",
        },
        ExpiresIn=3600,
    )


def _template_cover_candidate_keys(slug: str, name: str) -> List[str]:
    """
    Resolve template cover keys by trying common extensions.

    We intentionally keep this simple and deterministic.
    """
    base = f"templates/{slug}/{name}"
    return [
        f"{base}.png",
        f"{base}.jpg",
        f"{base}.jpeg",
    ]


def _s3_get_first_existing_bytes(bucket: str, keys: List[str]) -> bytes:
    last_error: Optional[Exception] = None
    for key in keys:
        try:
            return _s3_get_bytes(bucket, key)
        except Exception as e:
            last_error = e
            continue
    raise HTTPException(status_code=404, detail=f"Template asset not found in S3 (tried: {keys}). Last error: {last_error}")


def _read_template_image(slug: str, name: str) -> Image.Image:
    content = _s3_get_first_existing_bytes(settings.S3_BUCKET_NAME, _template_cover_candidate_keys(slug, name))
    return Image.open(io.BytesIO(content)).convert("RGB")


def _read_child_photo(job: Job) -> Image.Image:
    analysis = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    crop_uri = analysis.get("face_crop_uri") if isinstance(analysis, dict) else None
    if not isinstance(crop_uri, str) or not crop_uri:
        raise HTTPException(status_code=400, detail="face crop is missing; cannot build cover")
    content = _s3_get_bytes(settings.S3_BUCKET_NAME, str(crop_uri).replace(f"s3://{settings.S3_BUCKET_NAME}/", ""))
    if not content:
        raise HTTPException(status_code=404, detail="Child photo not found")
    return Image.open(io.BytesIO(content)).convert("RGB")


def _run_face_transfer_for_cover(
    child_pil: Image.Image,
    illustration_uri: str,
    prompt: str,
    negative_prompt: str,
) -> Image.Image:
    """
    Lazy import to avoid pulling ComfyUI/InsightFace stack for unrelated requests.
    """
    from ..inference.comfy_runner import run_face_transfer  # noqa: WPS433

    return run_face_transfer(
        child_pil,
        illustration_uri,
        prompt,
        negative_prompt=negative_prompt,
        randomize_seed=False,
    )


def _s3_put_bytes(bucket: str, key: str, body: bytes, content_type: str) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        CacheControl="no-store",
    )


def _s3_put_png(img: Image.Image, key: str, *, dpi: int) -> None:
    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(dpi, dpi))
    _s3_put_bytes(settings.S3_BUCKET_NAME, key, buf.getvalue(), "image/png")


def _s3_put_pdf(pdf_bytes: bytes, key: str) -> None:
    _s3_put_bytes(settings.S3_BUCKET_NAME, key, pdf_bytes, "application/pdf")


def _build_single_page_pdf_bytes(img: Image.Image) -> bytes:
    pdf_buffer = io.BytesIO()
    img.convert("RGB").save(pdf_buffer, format="PDF")
    return pdf_buffer.getvalue()


def _crop_square(img: Image.Image, *, x: int, y: int, size: int) -> Image.Image:
    return img.crop((x, y, x + size, y + size)).convert("RGB")


async def _build_cover_page_at_size(
    job: Job,
    page_spec,
    target_w: int,
    target_h: int,
    dpi: int,
    *,
    book_positive_prompt: str,
) -> Image.Image:
    """
    Build a single cover page (page_00 or page_23) at print size 2800×3000.
    Applies face-swap and text layers as specified in manifest.
    """
    from ..rendering.html_text import render_text_layers_over_image  # noqa: WPS433

    # Load base image
    base_img = _s3_read_private_to_pil(page_spec.base_uri)

    # Apply face swap if needed
    if page_spec.needs_face_swap:
        from ..book.prompts import join_prompt_parts  # noqa: WPS433

        child_pil = _read_child_photo(job)
        page_prompt = page_spec.prompt or getattr(job, "common_prompt", None) or "child portrait"
        prompt = join_prompt_parts([book_positive_prompt, page_prompt])
        negative = page_spec.negative_prompt or "low quality, bad face, distorted"
        try:
            base_img = _run_face_transfer_for_cover(child_pil, page_spec.base_uri, prompt, negative)
        except Exception as e:
            logger.warning(f"Cover face swap failed for page {page_spec.page_num}, using base: {e}")

    # Resize to target print size
    if base_img.size != (target_w, target_h):
        base_img = base_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # Render text layers if any
    if page_spec.text_layers:
        template_vars = {
            "child_name": job.child_name or "",
            "child_age": str(job.child_age) if job.child_age else "",
        }
        # Current render_text_layers_over_image expects square output_px.
        # For rectangular 2800×3000, we render on the larger dimension and crop/pad.
        max_dim = max(target_w, target_h)
        # Render on square canvas
        text_rendered = await render_text_layers_over_image(
            base_img,
            page_spec.text_layers,
            template_vars=template_vars,
            output_px=max_dim,
        )
        # Crop/pad to exact target size
        if text_rendered.size != (target_w, target_h):
            # Center crop if larger, or pad if smaller
            if text_rendered.size[0] > target_w or text_rendered.size[1] > target_h:
                left = (text_rendered.size[0] - target_w) // 2
                top = (text_rendered.size[1] - target_h) // 2
                text_rendered = text_rendered.crop((left, top, left + target_w, top + target_h))
            else:
                # Pad (shouldn't happen, but defensive)
                padded = Image.new("RGB", (target_w, target_h), (255, 255, 255))
                paste_x = (target_w - text_rendered.size[0]) // 2
                paste_y = (target_h - text_rendered.size[1]) // 2
                padded.paste(text_rendered, (paste_x, paste_y))
                text_rendered = padded
        base_img = text_rendered

    return base_img


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
    content = _s3_get_bytes(bucket, key)
    if not content:
        raise HTTPException(status_code=404, detail=f"Image not found: {s3_uri}")
    return Image.open(io.BytesIO(content)).convert("RGB")


async def _ensure_cover_print_artifacts_in_s3(job: Job) -> str:
    """
    Build and cache print-ready cover artifacts from manifest pages 0 and 23:
    - cover spread PDF: layout/{job_id}/print/cover.pdf (back left, front right)
    - optional intermediates under layout/{job_id}/print/cover/
    - crops for block pages 00/23 (from the 2800×3000 versions)
    """
    cover_pdf_key = _print_pdf_s3_key(job.job_id, "cover")
    try:
        s3.head_object(Bucket=settings.S3_BUCKET_NAME, Key=cover_pdf_key)
        return cover_pdf_key
    except ClientError as e:
        if not _is_s3_not_found_error(e):
            raise

    manifest = load_manifest(job.slug)

    # Get page_00 (front) and page_23 (back) from manifest
    page_00_spec = manifest.page_by_num(0)
    page_23_spec = manifest.page_by_num(23)

    if not page_00_spec:
        raise HTTPException(status_code=404, detail="Page 0 (front cover) not found in manifest")
    if not page_23_spec:
        raise HTTPException(status_code=404, detail="Page 23 (back cover) not found in manifest")

    # Build cover pages at print size 2800×3000 @300dpi
    cover_w, cover_h, cover_dpi = 2800, 3000, 300
    back = await _build_cover_page_at_size(
        job,
        page_23_spec,
        cover_w,
        cover_h,
        cover_dpi,
        book_positive_prompt=manifest.positive_prompt,
    )
    front = await _build_cover_page_at_size(
        job,
        page_00_spec,
        cover_w,
        cover_h,
        cover_dpi,
        book_positive_prompt=manifest.positive_prompt,
    )

    # Build spread: back on the left, front on the right (per ts.txt line 18)
    spread = Image.new("RGB", (cover_w * 2, cover_h), (255, 255, 255))
    spread.paste(back, (0, 0))
    spread.paste(front, (cover_w, 0))

    # Upload intermediates (useful for debugging/preview)
    base_dir = f"layout/{job.job_id}/print/cover"
    _s3_put_png(back, f"{base_dir}/back.png", dpi=cover_dpi)
    _s3_put_png(front, f"{base_dir}/front.png", dpi=cover_dpi)
    _s3_put_png(spread, f"{base_dir}/spread.png", dpi=cover_dpi)

    # Crops for block pages (per ts.txt lines 16-17)
    crop_size = 2551
    back_crop = _crop_square(back, x=210, y=210, size=crop_size)
    front_crop = _crop_square(front, x=25, y=210, size=crop_size)

    # Always store print crops under print/pages/
    _s3_put_png(front_crop, f"layout/{job.job_id}/print/pages/page_00.png", dpi=cover_dpi)
    _s3_put_png(back_crop, f"layout/{job.job_id}/print/pages/page_23.png", dpi=cover_dpi)

    # Also ensure they exist under the main pages/ path (used by current PDF builder),
    # but do not overwrite if already generated.
    for page_num, crop_img in [(0, front_crop), (23, back_crop)]:
        page_key = f"layout/{job.job_id}/pages/page_{page_num:02d}.png"
        try:
            s3.head_object(Bucket=settings.S3_BUCKET_NAME, Key=page_key)
        except ClientError as e:
            if _is_s3_not_found_error(e):
                _s3_put_png(crop_img, page_key, dpi=cover_dpi)
            else:
                raise

    # Build and upload cover PDF (spread: back left, front right)
    pdf_bytes = await asyncio.get_event_loop().run_in_executor(_pdf_executor, _build_single_page_pdf_bytes, spread)
    _s3_put_pdf(pdf_bytes, cover_pdf_key)

    is_ready = await _wait_for_s3_object(settings.S3_BUCKET_NAME, cover_pdf_key)
    if not is_ready:
        raise HTTPException(status_code=503, detail="Cover PDF is still being finalized. Please try again in a few seconds.")
    return cover_pdf_key


async def _ensure_print_pdf_in_s3(job: Job, page_nums: List[int], kind: str) -> str:
    if kind == "cover":
        return await _ensure_cover_print_artifacts_in_s3(job)
    if kind != "block":
        raise HTTPException(status_code=400, detail=f"Unknown print PDF kind: {kind}")

    key = _print_pdf_s3_key(job.job_id, kind)
    return await _ensure_pdf_in_s3_by_key(job, page_nums, key)


@router.get("/preview/{job_id}/download/page/{page_num}")
async def download_personalization_page_png(
    job_id: str,
    page_num: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_header_or_query),
):
    """
    Download a single generated page as PNG.
    Available only for completed (final) personalizations and only to the job owner.
    """
    job = await _get_job_by_any_id(db, job_id)
    if job.user_id != current_user.id:
        raise JobNotFoundError(job_id)
    if job.status != "completed":
        raise InvalidJobStateError(job_id, job.status, "completed")

    key = f"layout/{job.job_id}/pages/page_{page_num:02d}.png"
    content = _s3_get_bytes(settings.S3_BUCKET_NAME, key)
    if not content:
        raise HTTPException(status_code=404, detail="Page not found")

    filename = f"page_{page_num:02d}.png"
    return Response(
        content=content,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/preview/{job_id}/download/zip")
async def download_personalization_book_zip(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_header_or_query),
):
    """
    Download the whole generated book as a ZIP of PNG pages.
    Available only for completed (final) personalizations and only to the job owner.
    """
    job = await _get_job_by_any_id(db, job_id)
    if job.user_id != current_user.id:
        raise JobNotFoundError(job_id)
    if job.status != "completed":
        raise InvalidJobStateError(job_id, job.status, "completed")

    manifest = load_manifest(job.slug)
    page_nums = _all_manifest_page_nums(manifest)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for pn in page_nums:
            key = f"layout/{job.job_id}/pages/page_{pn:02d}.png"
            content = _s3_get_bytes(settings.S3_BUCKET_NAME, key)
            if not content:
                continue
            zf.writestr(f"page_{pn:02d}.png", content)

    filename = f"book_{job.job_id}.zip"
    buffer.seek(0)

    def _iter_zip_chunks(chunk_size: int = 1024 * 1024):
        while True:
            chunk = buffer.read(chunk_size)
            if not chunk:
                break
            yield chunk

    logger.info(f"ZIP built for job {job_id}, bytes={buffer.getbuffer().nbytes}, pages={len(page_nums)}")
    return StreamingResponse(
        _iter_zip_chunks(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename=\"{filename}\"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/preview/{job_id}/download/pdf")
async def download_personalization_book_pdf(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_header_or_query),
):
    """
    Download the whole generated book as a single PDF.
    Available only for completed (final) personalizations and only to the job owner.
    """
    logger.info(f"Starting PDF download for job {job_id}")
    job = await _get_job_by_any_id(db, job_id)
    if job.user_id != current_user.id:
        raise JobNotFoundError(job_id)
    if job.status != "completed":
        raise InvalidJobStateError(job_id, job.status, "completed")

    manifest = load_manifest(job.slug)
    page_nums = _all_manifest_page_nums(manifest)
    logger.info(f"Found {len(page_nums)} pages for PDF")

    try:
        key = await _ensure_pdf_in_s3(job, page_nums)
        filename = f"book_{job.job_id}.pdf"
        presigned_url = _presigned_pdf_download_url(key, filename)
        logger.info(f"Redirecting PDF download for job {job_id} to S3")
        return RedirectResponse(url=presigned_url, status_code=302)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare PDF for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to prepare PDF: {str(e)}")


@router.get("/preview/{job_id}/download/pdf-url")
async def get_personalization_pdf_download_url(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_header_or_query),
):
    """
    Return a presigned S3 URL for the generated PDF.
    Available only for completed personalizations and only to the job owner.
    """
    logger.info(f"Preparing PDF URL for job {job_id}")
    job = await _get_job_by_any_id(db, job_id)
    if job.user_id != current_user.id:
        raise JobNotFoundError(job_id)
    if job.status != "completed":
        raise InvalidJobStateError(job_id, job.status, "completed")

    manifest = load_manifest(job.slug)
    page_nums = _all_manifest_page_nums(manifest)
    try:
        key = await _ensure_pdf_in_s3(job, page_nums)
        filename = f"book_{job.job_id}.pdf"
        presigned_url = _presigned_pdf_download_url(key, filename)
        return {"url": presigned_url, "expiresIn": 3600}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare PDF URL for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to prepare PDF URL: {str(e)}")


@router.get("/preview/{job_id}/download/print-pdf")
async def download_personalization_print_pdf(
    job_id: str,
    kind: str = "block",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_header_or_query),
):
    """
    Download a print-ready PDF.

    Currently supported:
    - kind=block: multi-page PDF assembled from generated page PNGs.

    Not implemented yet:
    - kind=cover: cover spread PDF.
    """
    logger.info(f"Starting print PDF download for job {job_id}, kind={kind}")
    job = await _get_job_by_any_id(db, job_id)
    if job.user_id != current_user.id:
        raise JobNotFoundError(job_id)
    if job.status != "completed":
        raise InvalidJobStateError(job_id, job.status, "completed")

    manifest = load_manifest(job.slug)
    page_nums = _all_manifest_page_nums(manifest)

    key = await _ensure_print_pdf_in_s3(job, page_nums, kind)
    filename = f"print_{kind}_{job.job_id}.pdf"
    presigned_url = _presigned_pdf_download_url(key, filename)
    return RedirectResponse(url=presigned_url, status_code=302)


@router.get("/preview/{job_id}/download/print-pdf-url")
async def get_personalization_print_pdf_download_url(
    job_id: str,
    kind: str = "block",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_header_or_query),
):
    """Return a presigned S3 URL for the print-ready PDF."""
    logger.info(f"Preparing print PDF URL for job {job_id}, kind={kind}")
    job = await _get_job_by_any_id(db, job_id)
    if job.user_id != current_user.id:
        raise JobNotFoundError(job_id)
    if job.status != "completed":
        raise InvalidJobStateError(job_id, job.status, "completed")

    manifest = load_manifest(job.slug)
    page_nums = _all_manifest_page_nums(manifest)

    key = await _ensure_print_pdf_in_s3(job, page_nums, kind)
    filename = f"print_{kind}_{job.job_id}.pdf"
    presigned_url = _presigned_pdf_download_url(key, filename)
    return {"url": presigned_url, "expiresIn": 3600}

def _is_thumbnail_uri(uri: Optional[str]) -> bool:
    return bool(uri and "/thumbnails/" in uri)


def _extract_ill_id_from_uri(uri: str) -> Optional[str]:
    try:
        base = os.path.basename(urlparse(uri).path)
        name, _ext = os.path.splitext(base)
        return name or None
    except Exception:
        return None

async def _get_preview_for_job(job: Job, db: AsyncSession) -> Optional[PreviewResponse]:
    """Get preview response for a personalization job"""
    # New staged flow: prepay_ready should also expose preview (first 3 postpay pages).
    if job.status not in ["preview_ready", "confirmed", "completed", "prepay_ready"]:
        return None

    # Prefer manifest-driven preview when available (new pipeline).
    # Fallback to legacy BookPreview-based response if manifest is missing.
    try:
        stage = "prepay" if job.status == "prepay_ready" else "postpay"
        manifest = load_manifest(job.slug)
        page_nums = page_nums_for_front_preview(manifest, stage)
        pages = [
            PreviewPage(
                index=pn,
                imageUrl=_presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{_layout_page_key(job.job_id, pn)}"),
                locked=False,
                caption=None,
            )
            for pn in page_nums
        ]
        return PreviewResponse(pages=pages, unlockedCount=len(pages), totalCount=len(pages))
    except Exception:
        # Legacy behavior below
        pass
    
    # Get book preview pages
    preview_result = await db.execute(
        select(BookPreview)
        .filter(BookPreview.slug == job.slug)
        .order_by(BookPreview.page_index)
    )
    preview_pages = preview_result.scalars().all()
    preview_pages = [p for p in preview_pages if not _is_thumbnail_uri(p.image_url)]
    
    # For personalized books, all pages are unlocked
    pages = [
        PreviewPage(
            index=p.page_index,
            imageUrl=_presigned_get(p.image_url),
            locked=False,
            caption=p.caption
        )
        for p in preview_pages
    ]
    
    return PreviewResponse(
        pages=pages,
        unlockedCount=len(pages),
        totalCount=len(pages)
    )

def _job_to_personalization(job: Job, preview: Optional[PreviewResponse] = None) -> Personalization:
    """Convert Job model to Personalization schema"""
    def normalize_child_name(value: Optional[str]) -> str:
        if not value:
            return ""
        trimmed = value.strip()
        if not trimmed:
            return ""
        lowered = trimmed.lower()
        if lowered in ("unknown", "unknow"):
            return ""
        return trimmed

    avatar_url = job.avatar_url
    if not avatar_url and job.child_photo_uri:
        try:
            avatar_url = _presigned_get(job.child_photo_uri)
        except Exception:
            avatar_url = None

    avatar_crop_url: Optional[str] = None
    analysis = job.analysis_json
    if isinstance(analysis, dict):
        crop_uri = analysis.get("face_crop_uri")
        if isinstance(crop_uri, str) and crop_uri:
            try:
                avatar_crop_url = _presigned_get(crop_uri)
            except Exception:
                avatar_crop_url = None

    return Personalization(
        id=job.job_id,
        slug=job.slug,
        childName=normalize_child_name(job.child_name),
        childAge=job.child_age,
        status=job.status,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
        previewReadyAt=job.preview_ready_at,
        avatarUrl=avatar_url,
        avatarCropUrl=avatar_crop_url,
        preview=preview,
        cartItemId=job.cart_item_id,
        generationRetry=_build_generation_retry(job),
        pageRegenerations=_build_page_regenerations(job),
    )

@router.post("/upload_and_analyze/", response_model=Personalization, status_code=201)
async def upload_and_analyze(
    slug: str = Form(...),
    child_photo: UploadFile = File(...),
    illustration_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Create personalization and start photo analysis
    """
    logger.info(
        f"Upload and analyze request",
        extra={
            "slug": slug,
            "illustration_id": illustration_id
        }
    )
    
    # Validate book exists
    book_result = await db.execute(select(Book).filter(Book.slug == slug))
    book = book_result.scalar_one_or_none()
    if not book:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "BOOK_NOT_FOUND", "message": "Book not found"}}
        )
    
    # Validate file type
    if child_photo.content_type not in ("image/jpeg", "image/png"):
        logger.warning(f"Invalid content type: {child_photo.content_type}")
        raise HTTPException(status_code=400, detail="Only jpg/png allowed")
    
    # Meta (name/age/gender) are provided later at /generate; store placeholders
    # child_name is required (nullable=False), so keep it as empty string instead of "unknown"
    # to avoid leaking placeholder into UI.
    norm_name = ""
    norm_age = 0
    norm_gender = None

    # Upload photo to S3
    job_id = str(uuid.uuid4())
    photo_key = f"child_photos/{job_id}_{child_photo.filename}"
    child_photo_uri = _s3_put_uploadfile(child_photo, photo_key)
    
    # Legacy "illustration_id" is no longer supported for production flows.
    # We keep the field for backward compatibility, but do not resolve any mock illustration assets.
    caption_uri = None
    
    # Create job
    job = Job(
        job_id=job_id,
        user_id=current_user.id if current_user else "anon",
        slug=slug,
        status="pending_analysis",
        child_photo_uri=child_photo_uri,
        child_name=norm_name,
        child_age=norm_age,
        child_gender=norm_gender,
        caption_uri=caption_uri,
    )
    try:
        job.avatar_url = _presigned_get(child_photo_uri)
    except Exception:
        job.avatar_url = None
    db.add(job)
    await db.commit()
    await db.refresh(job)
    
    logger.info(f"Job created: {job_id}")
    
    # Start analysis task
    try:
        analyze_photo_task.apply_async(
            args=(job_id, child_photo_uri, illustration_id, "unknown"),
            queue="gpu_prepay"
        )
    except Exception:
        analyze_photo_task.delay(job_id, child_photo_uri, illustration_id, "unknown")
    
    return _job_to_personalization(job)

@router.get("/status/{job_id}", response_model=Personalization)
async def get_personalization_status(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get personalization status"""
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise JobNotFoundError(job_id)
    
    preview = await _get_preview_for_job(job, db)
    return _job_to_personalization(job, preview)

@router.get("/result/{job_id}", response_model=PreviewResponse)
async def get_personalization_preview(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get personalized preview for completed job.

    Returns book preview pages (without thumbnails) with generated images substituted when available.
    """
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise JobNotFoundError(job_id)
    
    # New staged flow:
    # - prepay: first 3 pages from the postpay list (manifest-driven)
    # - postpay: full set (manifest-driven)
    if job.status not in ["preview_ready", "confirmed", "completed", "prepay_ready"]:
        raise InvalidJobStateError(job_id, job.status, "prepay_ready, preview_ready, confirmed, or completed")

    # Try manifest-driven preview first (new pipeline).
    try:
        stage = "prepay" if job.status == "prepay_ready" else "postpay"
        manifest = load_manifest(job.slug)
        page_nums = page_nums_for_front_preview(manifest, stage)
        pages = [
            PreviewPage(
                index=pn,
                imageUrl=_presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{_layout_page_key(job.job_id, pn)}"),
                locked=False,
                caption=None,
            )
            for pn in page_nums
        ]
        return PreviewResponse(
            pages=pages,
            unlockedCount=len(pages),
            totalCount=len(pages),
        )
    except Exception:
        # Fallback to legacy BookPreview + results/{job_id}/ mapping
        pass
    
    # Load book preview pages (exclude thumbnails)
    preview_result = await db.execute(
        select(BookPreview)
        .filter(BookPreview.slug == job.slug)
        .order_by(BookPreview.page_index)
    )
    preview_pages_all = preview_result.scalars().all()
    preview_pages = [p for p in preview_pages_all if not _is_thumbnail_uri(p.image_url)]

    if not preview_pages:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PREVIEW_NOT_FOUND", "message": "No preview pages for this book"}}
        )

    # List generated results from S3 for this job
    generated_map = {}
    try:
        prefix = f"results/{job_id}/"
        resp = s3.list_objects_v2(Bucket=settings.S3_BUCKET_NAME, Prefix=prefix)
        for obj in (resp.get("Contents") or []):
            key = obj.get("Key") or ""
            base = os.path.basename(key)
            ill_id, _ext = os.path.splitext(base)
            if not ill_id:
                continue
            generated_map[ill_id] = _presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{key}")
    except Exception as e:
        logger.warning(f"Failed to list generated results for job {job_id}: {e}")
        generated_map = {}

    pages: List[PreviewPage] = []
    for p in preview_pages:
        ill_id = _extract_ill_id_from_uri(p.image_url)
        if ill_id and ill_id in generated_map:
            img_url = generated_map[ill_id]
        else:
            img_url = _presigned_get(p.image_url)

        pages.append(
            PreviewPage(
                index=p.page_index,
                imageUrl=img_url,
                locked=False,
                caption=p.caption,
            )
        )

    return PreviewResponse(
        pages=pages,
        unlockedCount=len(pages),
        totalCount=len(pages),
    )


@router.get("/preview/{job_id}", response_model=PreviewResponse)
async def get_personalization_preview_stage(
    job_id: str,
    stage: str = Query("prepay", pattern="^(prepay|postpay)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    Manifest-driven preview endpoint.

    - stage=prepay: returns first 3 pages from the postpay list (manifest-driven)
    - stage=postpay: returns all pages allowed by manifest (requires job completed)
    """
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        # Backward/defensive compatibility: sometimes UI can pass cart_item_id here.
        alt_result = await db.execute(select(Job).filter(Job.cart_item_id == job_id))
        job = alt_result.scalar_one_or_none()
        if not job:
            raise JobNotFoundError(job_id)

    if stage == "prepay":
        # Allow viewing even while postpay is running; but require at least prepay_ready.
        #
        # Note: `confirmed` is set when a personalization is added to cart
        # and can overwrite `prepay_ready`, so it must be treated as "prepay_ready or later"
        # for preview access.
        if job.status not in ["prepay_ready", "confirmed", "postpay_generating", "completed"]:
            raise InvalidJobStateError(job_id, job.status, "prepay_ready, confirmed (or later)")
    else:
        if job.status not in ["completed"]:
            raise InvalidJobStateError(job_id, job.status, "completed")

    manifest = load_manifest(job.slug)
    page_nums = page_nums_for_front_preview(manifest, stage)
    pages = [
        PreviewPage(
            index=pn,
            imageUrl=_presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{_layout_page_key(job.job_id, pn)}"),
            locked=False,
            caption=None,
        )
        for pn in page_nums
    ]
    return PreviewResponse(pages=pages, unlockedCount=len(pages), totalCount=len(pages))

@router.get("/jobs", response_model=List[Personalization])
async def list_personalization_jobs(
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """List all personalization jobs for the authenticated user"""
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Authentication required"}}
        )
    
    result = await db.execute(select(Job).filter(Job.user_id == current_user.id))
    jobs = result.scalars().all()
    
    personalizations = []
    for job in jobs:
        preview = await _get_preview_for_job(job, db)
        personalizations.append(_job_to_personalization(job, preview))
    
    return personalizations

@router.post("/avatar/{job_id}", response_model=AvatarUploadResponse, status_code=201)
async def upload_personalization_avatar(
    job_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Upload or replace avatar for personalization"""
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise JobNotFoundError(job_id)
    
    # Validate file type
    if file.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(status_code=400, detail="Only jpg/png allowed")
    
    # Upload new avatar
    avatar_key = f"avatars/{job_id}_{file.filename}"
    avatar_uri = _s3_put_uploadfile(file, avatar_key)
    
    # Update job
    job.child_photo_uri = avatar_uri
    job.avatar_url = _presigned_get(avatar_uri)
    job.status = "pending_analysis"
    await db.commit()
    
    logger.info(f"Avatar updated for job: {job_id}")
    
    # Restart analysis
    try:
        analyze_photo_task.apply_async(
            args=(job_id, avatar_uri, None, job.child_gender or "unknown"),
            queue="gpu_prepay"
        )
    except Exception:
        analyze_photo_task.delay(job_id, avatar_uri, None, job.child_gender or "unknown")
    
    return AvatarUploadResponse(
        uploadId=job_id,
        expiresAt=datetime.utcnow()
    )

@router.post("/generate/")
async def confirm_personalization_generate(
    job_id: str = Form(...),
    child_name: str = Form(...),
    child_age: int = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """Confirm personalization and start generation"""
    # Get personalization
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise JobNotFoundError(job_id)
    
    # In the new flow, /generate triggers PREPAY generation (first 3 postpay pages).
    if job.status not in ["preview_ready", "analyzing_completed", "prepay_ready"]:
        raise InvalidJobStateError(job_id, job.status, "preview_ready, analyzing_completed, or prepay_ready")

    analysis = job.analysis_json if isinstance(job.analysis_json, dict) else {}
    crop_uri = analysis.get("face_crop_uri") if isinstance(analysis, dict) else None
    if not isinstance(crop_uri, str) or not crop_uri:
        raise HTTPException(status_code=400, detail="Face crop is not ready. Please upload another photo.")
    
    # Verify user authentication
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Authentication required"}}
        )
    
    # Verify job belongs to user
    if job.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "FORBIDDEN", "message": "Personalization does not belong to user"}}
        )
    
    # Update stored name/age from request body before generation
    job.child_name = child_name
    job.child_age = child_age

    # Kick off PREPAY generation (first 3 postpay pages) after user confirmed
    job.status = "prepay_pending"
    await db.commit()
    await db.refresh(job)
    
    try:
        manifest = load_manifest(job.slug)
        if stage_has_face_swap(manifest, "prepay"):
            build_stage_backgrounds_task.apply_async(args=(job_id, "prepay"), queue="gpu_prepay")
            logger.info(f"Started PREPAY generation (GPU stage) for job after confirmation: {job_id}")
        else:
            render_stage_pages_task.apply_async(args=(job_id, "prepay"), queue="render")
            logger.info(f"Started PREPAY generation (render-only, no face swap) for job after confirmation: {job_id}")
    except Exception as e:
        logger.error(f"Failed to enqueue generation for job {job_id}: {e}")
        raise
    
    logger.info(f"Personalization confirmed and generation started: {job_id}")
    
    return {"status": "ok", "message": "Generation started"}

@router.get("/preview/{job_id}/page/{page_num}/needs-face-swap", response_model=PageFaceSwapCheckResponse)
async def get_page_face_swap_requirement(
    job_id: str,
    page_num: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    job = await _get_job_by_any_id(db, job_id)

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Personalization does not belong to user")

    manifest = load_manifest(job.slug)
    spec = manifest.page_by_num(page_num)
    if not spec:
        raise HTTPException(status_code=404, detail="Page not found in manifest")

    return PageFaceSwapCheckResponse(pageNum=page_num, needsFaceSwap=bool(spec.needs_face_swap))


def _resolve_page_regeneration_stage(job: Job) -> tuple[str, List[str]]:
    if job.status == "completed":
        return "postpay", ["completed"]
    return "prepay", ["generation_failed", "prepay_ready", "confirmed", "preview_ready"]


def _validate_page_regeneration(job: Job, page_num: int) -> str:
    stage, allowed_statuses = _resolve_page_regeneration_stage(job)
    if job.status not in allowed_statuses:
        raise InvalidJobStateError(job.job_id, job.status, ", ".join(allowed_statuses))

    manifest = load_manifest(job.slug)
    spec = manifest.page_by_num(page_num)
    if not spec:
        raise HTTPException(status_code=404, detail="Page not found in manifest")

    stage_page_nums = page_nums_for_stage(manifest, stage)
    if page_num not in stage_page_nums:
        raise HTTPException(status_code=400, detail="Page is not available for requested stage")

    if not spec.needs_face_swap:
        raise HTTPException(status_code=400, detail="Page does not require face swap")

    return stage


def _enqueue_page_regeneration(
    job_id: str,
    stage: str,
    page_num: int,
    *,
    override_child_photo_uri: Optional[str] = None,
) -> None:
    try:
        kwargs = {
            "job_id": job_id,
            "stage": stage,
            "randomize_seed": True,
            "page_num": page_num,
            "update_status": False,
        }
        if override_child_photo_uri:
            kwargs["override_child_photo_uri"] = override_child_photo_uri

        # Route to appropriate queue based on stage: prepay → gpu_prepay, postpay → gpu_postpay
        queue = "gpu_prepay" if stage == "prepay" else "gpu_postpay"
        try:
            build_stage_backgrounds_task.apply_async(kwargs=kwargs, queue=queue)
        except Exception:
            build_stage_backgrounds_task.delay(**kwargs)
        logger.info(f"Started {stage.upper()} page regeneration for job {job_id}, page {page_num}")
    except Exception as e:
        logger.error(f"Failed to enqueue page regeneration for job {job_id}: {e}")
        raise


@router.post("/regenerate/{job_id}/page/{page_num}")
async def regenerate_personalization_page(
    job_id: str,
    page_num: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Retry page regeneration with a randomized seed."""
    job = await _get_job_by_any_id(db, job_id)

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Personalization does not belong to user")

    stage = _validate_page_regeneration(job, page_num)

    used = _read_generation_retry_used(job)
    if used >= GENERATION_RETRY_LIMIT:
        raise HTTPException(status_code=400, detail="Regeneration limit reached")

    _set_generation_retry_used(job, used + 1)
    await db.commit()
    await db.refresh(job)

    # Persist regeneration state for cross-device visibility
    _set_page_regeneration_state(job, page_num=page_num, stage=stage, status="queued", error=None)
    await db.commit()
    await db.refresh(job)

    _enqueue_page_regeneration(job_id, stage, page_num)

    return {"status": "ok", "message": "Page regeneration started"}


@router.post("/regenerate/{job_id}/page/{page_num}/with-photo")
async def regenerate_personalization_page_with_photo(
    job_id: str,
    page_num: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Retry page regeneration with a custom photo for this page."""
    job = await _get_job_by_any_id(db, job_id)

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Personalization does not belong to user")

    if file.content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(status_code=400, detail="Only jpg/png allowed")

    stage = _validate_page_regeneration(job, page_num)

    used = _read_generation_retry_used(job)
    if used >= GENERATION_RETRY_LIMIT:
        raise HTTPException(status_code=400, detail="Regeneration limit reached")

    page_photo_key = f"page_photos/{job_id}/page_{page_num:02d}_{uuid.uuid4().hex}_{file.filename}"
    override_photo_uri = _s3_put_uploadfile(file, page_photo_key)

    _set_generation_retry_used(job, used + 1)
    await db.commit()
    await db.refresh(job)

    # Persist regeneration state for cross-device visibility
    _set_page_regeneration_state(job, page_num=page_num, stage=stage, status="queued", error=None)
    await db.commit()
    await db.refresh(job)

    _enqueue_page_regeneration(job_id, stage, page_num, override_child_photo_uri=override_photo_uri)

    return {"status": "ok", "message": "Page regeneration started"}

@router.post("/cancel/{job_id}", status_code=204)
async def cancel_personalization(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Cancel personalization"""
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise JobNotFoundError(job_id)
    
    job.status = "cancelled"
    await db.commit()
    
    logger.info(f"Job cancelled: {job_id}")
    return

