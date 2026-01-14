from __future__ import annotations

import uuid
from typing import Dict, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import logger
from ..models import Cart as CartModel
from ..models import CartItem as CartItemModel


async def get_or_create_active_cart(user_id: str, db: AsyncSession) -> CartModel:
    """
    Returns a single "active" cart for the user.

    The database schema does not enforce uniqueness for carts.user_id, so duplicates may exist
    (e.g., race conditions). We pick the most recently updated cart and merge items from older
    carts into it.
    """
    result = await db.execute(
        select(CartModel)
        .where(CartModel.user_id == user_id)
        .order_by(desc(CartModel.updated_at), desc(CartModel.created_at), desc(CartModel.id))
    )
    carts = result.scalars().all()

    if not carts:
        cart = CartModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            currency="USD",
        )
        db.add(cart)
        await db.commit()
        await db.refresh(cart)
        return cart

    primary = carts[0]
    duplicates = carts[1:]
    if duplicates:
        await _merge_duplicate_carts(primary=primary, duplicates=duplicates, db=db)
        await db.refresh(primary)

    return primary


async def _merge_duplicate_carts(primary: CartModel, duplicates: Sequence[CartModel], db: AsyncSession) -> None:
    primary_items_result = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == primary.id)
    )
    primary_items = primary_items_result.scalars().all()
    primary_by_personalization_id: Dict[str, CartItemModel] = {
        item.personalization_id: item for item in primary_items
    }

    moved_items = 0
    merged_quantities = 0

    for dup in duplicates:
        dup_items_result = await db.execute(
            select(CartItemModel).where(CartItemModel.cart_id == dup.id)
        )
        dup_items = dup_items_result.scalars().all()

        for item in dup_items:
            existing = primary_by_personalization_id.get(item.personalization_id)
            if existing:
                existing.quantity += item.quantity
                merged_quantities += item.quantity
                await db.delete(item)
                continue

            item.cart_id = primary.id
            primary_by_personalization_id[item.personalization_id] = item
            moved_items += 1

        await db.delete(dup)

    await db.commit()
    logger.info(
        "Merged duplicate carts",
        extra={
            "user_id": primary.user_id,
            "primary_cart_id": primary.id,
            "duplicates": [c.id for c in duplicates],
            "moved_items": moved_items,
            "merged_quantities": merged_quantities,
        },
    )

