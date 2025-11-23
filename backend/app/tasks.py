import io, asyncio, json, uuid, traceback, os
from PIL import Image
import boto3
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from .workers import celery_app
from .config import settings
from .db import AsyncSessionLocal
from .models import Job
from .inference.vision_qwen import analyze_image_pil
from .inference.comfy_runner import run_face_transfer
from .logger import logger

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

def _s3_read_private_to_pil(s3_uri: str) -> Image.Image:
    """Read image from S3 and convert to PIL Image"""
    try:
        if s3_uri.startswith("s3://"):
            key = s3_uri.replace("s3://", "").split("/", 1)[1]
        else:
            key = s3_uri.split('/', 4)[-1] if s3_uri.startswith("http") else s3_uri
            
        logger.debug(f"Reading S3 object: bucket={settings.S3_BUCKET_NAME}, key={key}")
        obj = s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        img = Image.open(io.BytesIO(obj["Body"].read())).convert("RGB")
        logger.debug(f"Successfully loaded image: size={img.size}")
        return img
    except Exception as e:
        logger.error(f"Failed to read image from S3: {s3_uri}, error: {e}")
        raise

def _s3_write_pil(img: Image.Image, key: str) -> str:
    """Write PIL Image to S3"""
    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        
        logger.debug(f"Writing image to S3: bucket={settings.S3_BUCKET_NAME}, key={key}, size={len(buf.getvalue())} bytes")
        s3.put_object(Bucket=settings.S3_BUCKET_NAME, Key=key, Body=buf.getvalue(), ContentType="image/png")
        
        s3_uri = f"s3://{settings.S3_BUCKET_NAME}/{key}"
        logger.info(f"Successfully wrote image to S3: {s3_uri}")
        return s3_uri
    except Exception as e:
        logger.error(f"Failed to write image to S3: {key}, error: {e}")
        raise

@celery_app.task(bind=True, acks_late=True, max_retries=3)
def analyze_photo_task(self, job_id: str, child_photo_uri: str, illustration_id: str, child_name: str, child_age: int, child_gender: str):
    """
    Celery task to analyze child photo using Qwen2-VL model.
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
            logger.info(f"Job {job_id} status updated to 'analyzing'")

            try:
                logger.info(f"Loading image from S3: {child_photo_uri}")
                pil = _s3_read_private_to_pil(job.child_photo_uri)
                
                logger.info(f"Running vision analysis with model: {settings.QWEN_MODEL_ID}")
                data = analyze_image_pil(pil, settings.QWEN_MODEL_ID)
                
                logger.info(
                    f"Analysis completed for job {job_id}",
                    extra={
                        "job_id": job_id,
                        "face_detected": data.get("face_detected", False),
                        "analysis_data": data,
                    }
                )
                
                job.analysis_json = data
                
                if data.get("face_detected"):
                    job.common_prompt = f"{data.get('gender','child')}, {data.get('hair_length','')}, {data.get('hair_style','')}, {data.get('hair_color','')}, {data.get('eyes_color','')} eyes"
                    logger.info(f"Generated prompt from Qwen analysis for job {job_id}: {job.common_prompt}")
                else:
                    logger.warning(f"No face detected in photo for job: {job_id}")
                    job.common_prompt = "child portrait, neutral, high quality"
                    logger.info(f"Using default prompt for job {job_id}: {job.common_prompt}")
                
                job.status = "analyzing_completed"
                await db.commit()
                await db.refresh(job)
                
                logger.info(f"Job {job_id} analysis completed successfully")
                if data.get("face_detected", False):
                    job.status = "pending_generation"
                    await db.commit()
                    await db.refresh(job)
                    generate_image_task.apply_async(args=(job_id,), queue="gpu")
                    logger.info(f"Started image generation for job: {job_id}")
                
            except Exception as e:
                logger.error(
                    f"Analysis failed for job {job_id}: {str(e)}",
                    extra={
                        "job_id": job_id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )
                job.status = "analysis_failed"
                await db.commit()
                raise
                
    return asyncio.run(_run())

@celery_app.task(bind=True, acks_late=True, max_retries=2)
def generate_image_task(self, job_id: str):
    """
    Celery task to generate face-swapped image using ComfyUI or InsightFace.
    """
    async def _run():
        logger.info(f"Starting image generation for job: {job_id}")
        
        async with AsyncSessionLocal() as db:
            job = await _get_job(db, job_id)
            if not job:
                logger.error(f"Job not found in database: {job_id}")
                return
                
            if job.status not in ("pending_generation", "generating"):
                logger.warning(
                    f"Job {job_id} not in pending_generation/generating state: {job.status}"
                )
                return

            if job.status != "generating":
                job.status = "generating"
                await db.commit()
                await db.refresh(job)
                logger.info(f"Job {job_id} status updated to 'generating'")

            try:
                logger.info(f"Loading child photo from S3: {job.child_photo_uri}")
                child_pil = _s3_read_private_to_pil(job.child_photo_uri)

                illustrations_path = os.path.join(os.path.dirname(__file__), "illustrations.json")
                try:
                    with open(illustrations_path, "r", encoding="utf-8") as f:
                        ill_data = json.load(f)
                        illustrations = ill_data.get("illustrations", [])
                except Exception as e:
                    logger.error(f"Failed to load illustrations list: {e}")
                    raise

                common_prompt = (job.common_prompt or "child portrait").strip(", ")
                base_negative = "low quality, bad face, distorted"

                saved_results = []
                for ill in illustrations:
                    ill_id = ill.get("id") or "unknown"
                    illustration_uri = ill.get("full_uri") or ill.get("thumbnail_uri")
                    if not illustration_uri:
                        logger.warning(f"Illustration {ill_id} has no URI, skipping")
                        continue

                    ill_prompt = ill.get("prompt")
                    ill_negative = ill.get("negative_prompt")
                    prompt = f"{ill_prompt}, {common_prompt}" if ill_prompt else common_prompt
                    negative = f"{ill_negative}, {base_negative}" if ill_negative else base_negative

                    logger.info(
                        f"Running face transfer for job {job_id} on illustration {ill_id}",
                        extra={
                            "job_id": job_id,
                            "illustration_id": ill_id,
                            "illustration_uri": illustration_uri,
                        }
                    )

                    try:
                        out_img = run_face_transfer(child_pil, illustration_uri, prompt, negative)
                        result_key = f"results/{job_id}/{ill_id}.png"
                        logger.info(f"Uploading result to S3: {result_key}")
                        s3_uri = _s3_write_pil(out_img, result_key)
                        saved_results.append((ill_id, s3_uri))
                    except Exception as gen_err:
                        logger.error(
                            f"Generation failed for illustration {ill_id}: {gen_err}",
                            extra={"job_id": job_id, "illustration_id": ill_id}
                        )

                if not saved_results:
                    logger.error(f"No results generated for job {job_id}")
                    job.status = "generation_failed"
                    await db.commit()
                    await db.refresh(job)
                    raise RuntimeError("All generations failed")

                job.result_uri = saved_results[0][1]
                job.status = "completed"
                await db.commit()
                await db.refresh(job)
                logger.info(
                    f"Job {job_id} completed successfully with {len(saved_results)} images",
                    extra={"job_id": job_id}
                )
                
            except Exception as e:
                logger.error(
                    f"Generation failed for job {job_id}: {str(e)}",
                    extra={
                        "job_id": job_id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
                )
                job.status = "generation_failed"
                await db.commit()
                raise
                
    return asyncio.run(_run())
