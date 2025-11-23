"""
Account routes - profile management
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..schemas import UserProfile, UserProfileUpdate
from ..auth import get_current_user
from ..logger import logger

router = APIRouter(prefix="/account", tags=["Account"])

@router.get("/profile", response_model=UserProfile)
async def get_profile(
    current_user: User = Depends(get_current_user)
):
    """Get customer profile"""
    return UserProfile(
        id=current_user.id,
        email=current_user.email,
        firstName=current_user.first_name,
        lastName=current_user.last_name,
        phone=current_user.phone
    )

@router.put("/profile", response_model=UserProfile)
async def update_profile(
    profile_update: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update customer profile"""
    # Update fields if provided
    if profile_update.firstName is not None:
        current_user.first_name = profile_update.firstName
    
    if profile_update.lastName is not None:
        current_user.last_name = profile_update.lastName
    
    if profile_update.phone is not None:
        current_user.phone = profile_update.phone
    
    await db.commit()
    await db.refresh(current_user)
    
    logger.info(f"Profile updated for user: {current_user.email}")
    
    return UserProfile(
        id=current_user.id,
        email=current_user.email,
        firstName=current_user.first_name,
        lastName=current_user.last_name,
        phone=current_user.phone
    )

