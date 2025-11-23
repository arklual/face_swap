from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import boto3
import json
import os
import time
from typing import List, Dict, Any, Optional
from .config import settings
from .db import get_db, engine
from .models import Base, Job
from .workers import celery_app
from .tasks import analyze_photo_task, generate_image_task
from sqlalchemy import select
from urllib.parse import urlparse
from .logger import logger
from .exceptions import (
    FaceAppBaseException,
    JobNotFoundError,
    InvalidJobStateError,
    S3StorageError,
    faceapp_exception_handler,
    http_exception_handler,
    generic_exception_handler,
)

app = FastAPI(
    title="Face Transfer API",
    version="1.0.0",
    description="API для переноса лица ребёнка на иллюстрации"
)

app.add_exception_handler(FaceAppBaseException, faceapp_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    logger.info(
        f"Request: {request.method} {request.url.path}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
        }
    )
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    logger.info(
        f"Response: {response.status_code}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000, 2),
        }
    )
    
    return response

s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)

@app.on_event("startup")
async def startup():
    logger.info("Starting Face Transfer API")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down Face Transfer API")

def _s3_put_uploadfile(file: UploadFile, key: str) -> str:
    try:
        s3.upload_fileobj(file.file, settings.S3_BUCKET_NAME, key, ExtraArgs={"ContentType": file.content_type or "image/jpeg"})
        logger.info(f"Uploaded file to S3: {key}")
        return f"s3://{settings.S3_BUCKET_NAME}/{key}"
    except Exception as e:
        logger.error(f"Failed to upload file to S3: {e}")
        raise S3StorageError(f"Failed to upload file: {str(e)}")

def _presigned_get(s3_uri: str, expires=3600) -> str:
    p = urlparse(s3_uri)
    if p.scheme == "s3":
        bucket = p.netloc
        key = p.path.lstrip("/")
    else:
        parts = p.path.lstrip("/").split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Unexpected S3 URI: {s3_uri}")
        bucket, key = parts
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )

@app.post("/upload_and_analyze/")
async def upload_and_analyze(
    child_photo: UploadFile = File(...),
    illustration_id: Optional[str] = Form(None),
    child_name: str = Form(...),
    child_age: int = Form(...),
    child_gender: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload child photo and start analysis.
    Returns job_id for tracking.
    """
    logger.info(
        f"Upload request received",
        extra={
            "illustration_id": illustration_id,
            "child_age": child_age,
            "child_gender": child_gender,
            "uploaded_file": child_photo.filename,
        }
    )
    
    if child_photo.content_type not in ("image/jpeg", "image/png"):
        logger.warning(f"Invalid content type: {child_photo.content_type}")
        raise HTTPException(status_code=400, detail="Only jpg/png allowed")
    
    if child_age < 0 or child_age > 18:
        logger.warning(f"Invalid age: {child_age}")
        raise HTTPException(status_code=400, detail="Age must be between 0 and 18")
    
    if child_gender not in ("boy", "girl"):
        logger.warning(f"Invalid gender: {child_gender}")
        raise HTTPException(status_code=400, detail="Gender must be 'boy' or 'girl'")

    job_id = str(uuid.uuid4())
    photo_key = f"child_photos/{job_id}_{child_photo.filename}"
    child_photo_uri = _s3_put_uploadfile(child_photo, photo_key)

    caption_uri = None
    if illustration_id:
        try:
            illustrations_path = os.path.join(os.path.dirname(__file__), "illustrations.json")
            with open(illustrations_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ill = next((i for i in data.get("illustrations", []) if i.get("id") == illustration_id), None)
                if ill and ill.get("full_uri"):
                    caption_uri = ill["full_uri"]
        except Exception as e:
            logger.warning(f"Failed to resolve illustration full_uri for {illustration_id}: {e}")

        if not caption_uri:
            caption_uri = f"illustrations/{illustration_id}.jpg"

    job = Job(
        job_id=job_id,
        user_id="anon",
        status="pending_analysis",
        child_photo_uri=child_photo_uri,
        caption_uri=caption_uri,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    
    logger.info(f"Job created: {job_id}")

    try:
        analyze_photo_task.apply_async(
            args=(job_id, child_photo_uri, illustration_id, child_name, child_age, child_gender),
            queue="gpu"
        )
    except Exception:
        analyze_photo_task.delay(job_id, child_photo_uri, illustration_id, child_name, child_age, child_gender)

    return {"job_id": job_id, "status": job.status}

@app.post("/generate/")
async def generate_image(job_id: str = Form(...), db: AsyncSession = Depends(get_db)):
    """
    Start face transfer generation for analyzed job.
    """
    logger.info(f"Generate request for job: {job_id}")
    
    res = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = res.scalar_one_or_none()
    
    if not job:
        logger.warning(f"Job not found: {job_id}")
        raise JobNotFoundError(job_id)
    
    if job.status not in ("analyzing_completed", "pending_generation"):
        logger.warning(f"Job {job_id} not ready: {job.status}")
        raise InvalidJobStateError(job_id, job.status, "analyzing_completed or pending_generation")

    job.status = "pending_generation"
    await db.commit()
    await db.refresh(job)
    
    logger.info(f"Job {job_id} queued for generation")

    try:
        generate_image_task.apply_async(args=(job_id,), queue="gpu")
    except Exception:
        generate_image_task.delay(job_id)
    return {"job_id": job.job_id, "status": job.status}

@app.get("/status/{job_id}")
async def get_job_status(job_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get current status of a job.
    """
    res = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = res.scalar_one_or_none()
    
    if not job:
        logger.warning(f"Job not found: {job_id}")
        raise JobNotFoundError(job_id)
    
    analysis = job.analysis_json or {}
    return {
        "job_id": job.job_id,
        "status": job.status,
        "face_detected": bool(analysis.get("face_detected")) if isinstance(analysis, dict) else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }

@app.get("/result/{job_id}")
async def get_job_result(job_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get result URLs for completed job. Supports multi-illustration outputs.
    """
    res = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = res.scalar_one_or_none()
    
    if not job:
        logger.warning(f"Job not found: {job_id}")
        raise JobNotFoundError(job_id)
    
    if job.status != "completed" or not job.result_uri:
        logger.warning(f"Job {job_id} not completed: {job.status}")
        raise InvalidJobStateError(job_id, job.status, "completed")
    
    try:
        prefix = f"results/{job_id}/"
        keys = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.S3_BUCKET_NAME, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".png"):
                    keys.append(key)

        if not keys:
            url = _presigned_get(job.result_uri)
            return {"job_id": job.job_id, "results": [{"id": "default", "url": url}]}

        items = []
        for key in keys:
            ill_id = os.path.splitext(os.path.basename(key))[0]
            url = _presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{key}")
            items.append({"id": ill_id, "url": url})

        return {"job_id": job.job_id, "results": items}
    except Exception as e:
        logger.error(f"Failed to list/generate result URLs: {e}")
        try:
            result_url = _presigned_get(job.result_uri)
            return {"job_id": job.job_id, "results": [{"id": "default", "url": result_url}]}
        except Exception as inner:
            logger.error(f"Failed to generate presigned URL: {inner}")
            raise S3StorageError("Failed to generate download URL")

@app.get("/illustrations/")
async def get_illustrations(gender: str = None, age: int = None) -> Dict[str, Any]:
    """
    Get list of available illustrations with filters.
    """
    illustrations_path = os.path.join(os.path.dirname(__file__), "illustrations.json")
    try:
        with open(illustrations_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            illustrations = data["illustrations"]
    except FileNotFoundError:
        return {"illustrations": []}
    
    filtered = illustrations
    if gender:
        filtered = [ill for ill in filtered if ill.get("gender") == gender or ill.get("gender") is None]
    if age:
        filtered = [
            ill for ill in filtered 
            if ill.get("age_range") is None or (ill["age_range"][0] <= age <= ill["age_range"][1])
        ]
    
    for ill in filtered:
        try:
            ill["thumbnail_url"] = _presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{ill['thumbnail_uri']}")
        except:
            ill["thumbnail_url"] = None
    
    return {"illustrations": filtered}

@app.get("/illustrations/{illustration_id}")
async def get_illustration(illustration_id: str) -> Dict[str, Any]:
    """
    Get specific illustration by ID.
    """
    illustrations_path = os.path.join(os.path.dirname(__file__), "illustrations.json")
    try:
        with open(illustrations_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            illustrations = data["illustrations"]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Illustrations database not found")
    
    illustration = next((ill for ill in illustrations if ill["id"] == illustration_id), None)
    if not illustration:
        raise HTTPException(status_code=404, detail="Illustration not found")
    
    try:
        illustration["thumbnail_url"] = _presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{illustration['thumbnail_uri']}")
        illustration["full_url"] = _presigned_get(f"s3://{settings.S3_BUCKET_NAME}/{illustration['full_uri']}")
    except:
        illustration["thumbnail_url"] = None
        illustration["full_url"] = None
    
    return illustration

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "service": "faceapp-backend"}