"""
Personalization routes - face transfer API
"""
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
from typing import Optional
from datetime import datetime
import boto3
import os
import json
from urllib.parse import urlparse

from ..db import get_db
from ..models import Job, Book, BookPreview
from ..schemas import (
    Personalization,
    AvatarUploadResponse,
    PreviewResponse,
    PreviewPage
)
from ..auth import get_current_user_optional, User
from ..config import settings
from ..tasks import analyze_photo_task, generate_image_task
from ..logger import logger
from ..exceptions import JobNotFoundError, InvalidJobStateError, S3StorageError

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
            ExtraArgs={"ContentType": file.content_type or "image/jpeg"}
        )
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

async def _get_preview_for_job(job: Job, db: AsyncSession) -> Optional[PreviewResponse]:
    """Get preview response for a personalization job"""
    if job.status not in ["preview_ready", "confirmed", "completed"]:
        return None
    
    # Get book preview pages
    preview_result = await db.execute(
        select(BookPreview)
        .filter(BookPreview.slug == job.slug)
        .order_by(BookPreview.page_index)
    )
    preview_pages = preview_result.scalars().all()
    
    # For personalized books, all pages are unlocked
    pages = [
        PreviewPage(
            index=p.page_index,
            imageUrl=p.image_url,
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
    return Personalization(
        id=job.job_id,
        slug=job.slug,
        childName=job.child_name,
        childAge=job.child_age,
        status=job.status,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
        previewReadyAt=job.preview_ready_at,
        avatarUrl=job.avatar_url,
        preview=preview,
        cartItemId=job.cart_item_id
    )

@router.post("/upload_and_analyze/", response_model=Personalization, status_code=201)
async def upload_and_analyze(
    slug: str = Form(...),
    child_photo: UploadFile = File(...),
    illustration_id: Optional[str] = Form(None),
    child_name: str = Form(...),
    child_age: int = Form(...),
    child_gender: Optional[str] = Form(None),
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
            "child_age": child_age,
            "child_gender": child_gender,
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
    
    # Validate age
    if child_age < 0 or child_age > 18:
        logger.warning(f"Invalid age: {child_age}")
        raise HTTPException(status_code=400, detail="Age must be between 0 and 18")
    
    # Validate gender
    if child_gender and child_gender not in ("boy", "girl"):
        logger.warning(f"Invalid gender: {child_gender}")
        raise HTTPException(status_code=400, detail="Gender must be 'boy' or 'girl'")
    
    # Upload photo to S3
    job_id = str(uuid.uuid4())
    photo_key = f"child_photos/{job_id}_{child_photo.filename}"
    child_photo_uri = _s3_put_uploadfile(child_photo, photo_key)
    
    # Resolve illustration URI
    caption_uri = None
    if illustration_id:
        try:
            illustrations_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "illustrations.json")
            with open(illustrations_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ill = next((i for i in data.get("illustrations", []) if i.get("id") == illustration_id), None)
                if ill and ill.get("full_uri"):
                    caption_uri = ill["full_uri"]
        except Exception as e:
            logger.warning(f"Failed to resolve illustration full_uri for {illustration_id}: {e}")
        
        if not caption_uri:
            caption_uri = f"illustrations/{illustration_id}.jpg"
    
    # Create job
    job = Job(
        job_id=job_id,
        user_id=current_user.id if current_user else "anon",
        slug=slug,
        status="pending_analysis",
        child_photo_uri=child_photo_uri,
        child_name=child_name,
        child_age=child_age,
        child_gender=child_gender,
        caption_uri=caption_uri,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    
    logger.info(f"Job created: {job_id}")
    
    # Start analysis task
    try:
        analyze_photo_task.apply_async(
            args=(job_id, child_photo_uri, illustration_id, child_name, child_age, child_gender or "unknown"),
            queue="gpu"
        )
    except Exception:
        analyze_photo_task.delay(job_id, child_photo_uri, illustration_id, child_name, child_age, child_gender or "unknown")
    
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
    """Get personalized preview for completed job"""
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise JobNotFoundError(job_id)
    
    if job.status not in ["preview_ready", "confirmed", "completed"]:
        raise InvalidJobStateError(job_id, job.status, "preview_ready, confirmed, or completed")
    
    preview = await _get_preview_for_job(job, db)
    if not preview:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "PREVIEW_NOT_READY", "message": "Preview not ready"}}
        )
    
    return preview

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
            args=(job_id, avatar_uri, None, job.child_name, job.child_age, job.child_gender or "unknown"),
            queue="gpu"
        )
    except Exception:
        analyze_photo_task.delay(job_id, avatar_uri, None, job.child_name, job.child_age, job.child_gender or "unknown")
    
    return AvatarUploadResponse(
        uploadId=job_id,
        expiresAt=datetime.utcnow()
    )

@router.post("/generate/")
async def confirm_personalization_generate(
    job_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """Confirm personalization and add to cart"""
    from ..models import Cart as CartModel, CartItem as CartItemModel
    from ..schemas import Cart, CartItem as CartItemSchema, CartPersonalizationSummary, CartTotals, Money
    
    # Get personalization
    result = await db.execute(select(Job).filter(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    
    if not job:
        raise JobNotFoundError(job_id)
    
    if job.status not in ["preview_ready", "analyzing_completed"]:
        raise InvalidJobStateError(job_id, job.status, "preview_ready or analyzing_completed")
    
    # Get or create cart
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Authentication required"}}
        )
    
    cart_result = await db.execute(
        select(CartModel).filter(CartModel.user_id == current_user.id)
    )
    cart = cart_result.scalar_one_or_none()
    
    if not cart:
        import uuid as uuid_lib
        cart = CartModel(
            id=str(uuid_lib.uuid4()),
            user_id=current_user.id,
            currency="USD"
        )
        db.add(cart)
        await db.commit()
        await db.refresh(cart)
    
    # Get book
    book_result = await db.execute(select(Book).filter(Book.slug == job.slug))
    book = book_result.scalar_one_or_none()
    
    if not book:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "BOOK_NOT_FOUND", "message": "Book not found"}}
        )
    
    # Check if already in cart
    existing_result = await db.execute(
        select(CartItemModel).filter(
            CartItemModel.cart_id == cart.id,
            CartItemModel.personalization_id == job_id
        )
    )
    existing_item = existing_result.scalar_one_or_none()
    
    if not existing_item:
        # Create cart item
        cart_item = CartItemModel(
            id=str(uuid.uuid4()),
            cart_id=cart.id,
            slug=book.slug,
            personalization_id=job_id,
            quantity=1,
            unit_price_amount=book.price_amount,
            unit_price_currency=book.price_currency
        )
        db.add(cart_item)
        
        # Update job
        job.cart_item_id = cart_item.id
    
    job.status = "confirmed"
    await db.commit()
    
    logger.info(f"Personalization confirmed and added to cart: {job_id}")
    
    # Build cart response
    from ..routes.cart import _build_cart_response
    return await _build_cart_response(cart, db)

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

@router.get("/illustrations/")
async def get_illustrations(
    gender: Optional[str] = Query(None),
    age: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Get list of available illustrations"""
    illustrations_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "illustrations.json")
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

@router.get("/illustrations/{illustration_id}")
async def get_illustration(
    illustration_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get specific illustration"""
    illustrations_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "illustrations.json")
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

