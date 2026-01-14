"""
Authentication routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..db import get_db
from ..models import User, PasswordResetToken, UserDeliveryAddress
from ..schemas import (
    SignupRequest,
    LoginRequest,
    AuthResponse,
    UserProfile,
    ForgotPasswordRequest,
    ResetPasswordRequest
)
from ..auth import hash_password, verify_password, create_access_token, get_current_user
from ..logger import logger

router = APIRouter(prefix="/auth", tags=["Auth"])

async def _get_user_delivery(db: AsyncSession, user_id: str) -> Optional[UserDeliveryAddress]:
    result = await db.execute(select(UserDeliveryAddress).filter(UserDeliveryAddress.user_id == user_id))
    return result.scalar_one_or_none()

def _to_user_profile(user: User, delivery: Optional[UserDeliveryAddress]) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        firstName=user.first_name,
        lastName=user.last_name,
        phone=user.phone,
        deliveryRecipient=delivery.recipient if delivery else None,
        deliveryCity=delivery.city if delivery else None,
        deliveryStreet=delivery.street if delivery else None,
        deliveryHouse=delivery.house if delivery else None,
        deliveryApartment=delivery.apartment if delivery else None,
        deliveryPostalCode=delivery.postal_code if delivery else None,
        deliveryComment=delivery.comment if delivery else None,
    )

@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(request: SignupRequest, db: AsyncSession = Depends(get_db)):
    """Register a new customer"""
    # Check if email already exists
    result = await db.execute(select(User).filter(User.email == request.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "EMAIL_EXISTS", "message": "Email already registered"}}
        )
    
    # Create new user
    user = User(
        id=str(uuid.uuid4()),
        email=request.email,
        password_hash=hash_password(request.password),
        first_name=request.firstName,
        last_name=request.lastName
    )
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    logger.info(f"New user registered: {user.email}")
    
    # Generate token
    token = create_access_token(user.id)
    delivery = await _get_user_delivery(db, user.id)
    
    return AuthResponse(
        token=token,
        user=_to_user_profile(user, delivery)
    )

@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login to existing account"""
    # Find user
    result = await db.execute(select(User).filter(User.email == request.email))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid email or password"}}
        )
    
    logger.info(f"User logged in: {user.email}")
    
    # Generate token
    token = create_access_token(user.id)
    delivery = await _get_user_delivery(db, user.id)
    
    return AuthResponse(
        token=token,
        user=_to_user_profile(user, delivery)
    )

@router.post("/logout", status_code=204)
async def logout(current_user: User = Depends(get_current_user)):
    """Logout from account"""
    # In a real implementation with refresh tokens, you would invalidate the token here
    # For now, the client will simply discard the token
    logger.info(f"User logged out: {current_user.email}")
    return

@router.post("/forgot-password", status_code=202)
async def forgot_password(request: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Request password reset"""
    # Find user
    result = await db.execute(select(User).filter(User.email == request.email))
    user = result.scalar_one_or_none()
    
    # Always return 202 even if user doesn't exist (security best practice)
    if user:
        # Create reset token
        token = str(uuid.uuid4())
        reset_token = PasswordResetToken(
            token=token,
            user_id=user.id,
            expires_at=datetime.utcnow() + timedelta(hours=1)
        )
        db.add(reset_token)
        await db.commit()
        
        logger.info(f"Password reset requested for: {user.email}")
        # TODO: Send email with reset link
        # In production, you would send an email here with the reset link
    
    return

@router.post("/reset-password", status_code=204)
async def reset_password(request: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Reset password using token"""
    # Find token
    result = await db.execute(
        select(PasswordResetToken).filter(
            PasswordResetToken.token == request.token,
            PasswordResetToken.used == False
        )
    )
    reset_token = result.scalar_one_or_none()
    
    if not reset_token or reset_token.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_TOKEN", "message": "Invalid or expired reset token"}}
        )
    
    # Update user password
    user_result = await db.execute(select(User).filter(User.id == reset_token.user_id))
    user = user_result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "USER_NOT_FOUND", "message": "User not found"}}
        )
    
    user.password_hash = hash_password(request.password)
    reset_token.used = True
    
    await db.commit()
    
    logger.info(f"Password reset completed for: {user.email}")
    return

