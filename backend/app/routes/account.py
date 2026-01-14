"""
Account routes - profile management
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..db import get_db
from ..models import User, UserDeliveryAddress
from ..schemas import UserProfile, UserProfileUpdate
from ..auth import get_current_user
from ..logger import logger
from typing import Optional

router = APIRouter(prefix="/account", tags=["Account"])

@router.get("/profile", response_model=UserProfile)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get customer profile"""
    result = await db.execute(select(UserDeliveryAddress).filter(UserDeliveryAddress.user_id == current_user.id))
    delivery = result.scalar_one_or_none()
    return UserProfile(
        id=current_user.id,
        email=current_user.email,
        firstName=current_user.first_name,
        lastName=current_user.last_name,
        phone=current_user.phone,
        deliveryRecipient=delivery.recipient if delivery else None,
        deliveryCity=delivery.city if delivery else None,
        deliveryStreet=delivery.street if delivery else None,
        deliveryHouse=delivery.house if delivery else None,
        deliveryApartment=delivery.apartment if delivery else None,
        deliveryPostalCode=delivery.postal_code if delivery else None,
        deliveryComment=delivery.comment if delivery else None,
    )

@router.put("/profile", response_model=UserProfile)
async def update_profile(
    profile_update: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update customer profile"""
    fields_set = getattr(profile_update, "__fields_set__", set())

    if "firstName" in fields_set:
        if profile_update.firstName and profile_update.firstName.strip():
            current_user.first_name = profile_update.firstName.strip()

    if "lastName" in fields_set:
        if profile_update.lastName and profile_update.lastName.strip():
            current_user.last_name = profile_update.lastName.strip()

    if "phone" in fields_set:
        current_user.phone = profile_update.phone

    delivery_fields = {
        "deliveryRecipient",
        "deliveryCity",
        "deliveryStreet",
        "deliveryHouse",
        "deliveryApartment",
        "deliveryPostalCode",
        "deliveryComment",
    }
    should_update_delivery = any(field in fields_set for field in delivery_fields)
    delivery: Optional[UserDeliveryAddress] = None
    if should_update_delivery:
        delivery_result = await db.execute(
            select(UserDeliveryAddress).filter(UserDeliveryAddress.user_id == current_user.id)
        )
        delivery = delivery_result.scalar_one_or_none()
        if not delivery:
            delivery = UserDeliveryAddress(user_id=current_user.id)
            db.add(delivery)

        if "deliveryRecipient" in fields_set:
            delivery.recipient = profile_update.deliveryRecipient
        if "deliveryCity" in fields_set:
            delivery.city = profile_update.deliveryCity
        if "deliveryStreet" in fields_set:
            delivery.street = profile_update.deliveryStreet
        if "deliveryHouse" in fields_set:
            delivery.house = profile_update.deliveryHouse
        if "deliveryApartment" in fields_set:
            delivery.apartment = profile_update.deliveryApartment
        if "deliveryPostalCode" in fields_set:
            delivery.postal_code = profile_update.deliveryPostalCode
        if "deliveryComment" in fields_set:
            delivery.comment = profile_update.deliveryComment
    
    await db.commit()
    await db.refresh(current_user)

    delivery_result = await db.execute(select(UserDeliveryAddress).filter(UserDeliveryAddress.user_id == current_user.id))
    delivery = delivery_result.scalar_one_or_none()
    
    logger.info(f"Profile updated for user: {current_user.email}")
    
    return UserProfile(
        id=current_user.id,
        email=current_user.email,
        firstName=current_user.first_name,
        lastName=current_user.last_name,
        phone=current_user.phone,
        deliveryRecipient=delivery.recipient if delivery else None,
        deliveryCity=delivery.city if delivery else None,
        deliveryStreet=delivery.street if delivery else None,
        deliveryHouse=delivery.house if delivery else None,
        deliveryApartment=delivery.apartment if delivery else None,
        deliveryPostalCode=delivery.postal_code if delivery else None,
        deliveryComment=delivery.comment if delivery else None,
    )

