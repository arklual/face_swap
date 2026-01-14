"""
Cart routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
import uuid
from typing import List, Optional

from ..db import get_db
from ..models import Cart as CartModel, CartItem as CartItemModel, Job, Book
from ..services.cart import get_or_create_active_cart
from ..schemas import (
    Cart,
    CartItem,
    CartItemInput,
    CartItemUpdateRequest,
    CartPersonalizationSummary,
    CartTotals,
    Money,
    ShippingMethod,
    CheckoutQuoteRequest,
    CheckoutQuoteResponse,
    Address
)
from ..auth import get_current_user, User
from ..logger import logger

router = APIRouter(tags=["Cart"])

async def _calculate_cart_totals(cart_id: str, db: AsyncSession, shipping_amount: float = 0.0) -> CartTotals:
    """Calculate cart totals"""
    result = await db.execute(
        select(CartItemModel).filter(CartItemModel.cart_id == cart_id)
    )
    items = result.scalars().all()
    
    subtotal = sum(item.unit_price_amount * item.quantity for item in items)
    discount_total = 0.0
    tax_total = subtotal * 0.1  # 10% tax for demo
    grand_total = subtotal - discount_total + tax_total + shipping_amount
    
    # Get currency from cart
    cart_result = await db.execute(select(CartModel).filter(CartModel.id == cart_id))
    cart = cart_result.scalar_one()
    currency = cart.currency
    
    return CartTotals(
        subtotal=Money(amount=subtotal, currency=currency),
        discountTotal=Money(amount=discount_total, currency=currency),
        taxTotal=Money(amount=tax_total, currency=currency),
        shippingTotal=Money(amount=shipping_amount, currency=currency),
        grandTotal=Money(amount=grand_total, currency=currency)
    )

async def _get_or_create_cart(user_id: str, db: AsyncSession) -> CartModel:
    """Get or create cart for user"""
    # Delegate to a shared helper that also resolves/merges duplicates.
    return await get_or_create_active_cart(user_id=user_id, db=db)

async def _build_cart_response(cart: CartModel, db: AsyncSession) -> Cart:
    """Build cart response with items and totals"""
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

    # Get cart items
    items_result = await db.execute(
        select(CartItemModel).filter(CartItemModel.cart_id == cart.id)
    )
    cart_items = items_result.scalars().all()
    
    # Build items list
    items = []
    for item in cart_items:
        # Get book
        book_result = await db.execute(select(Book).filter(Book.slug == item.slug))
        book = book_result.scalar_one_or_none()
        
        # Get personalization
        pers_result = await db.execute(select(Job).filter(Job.job_id == item.personalization_id))
        personalization = pers_result.scalar_one_or_none()
        
        if book and personalization:
            items.append(CartItem(
                id=item.id,
                slug=item.slug,
                title=book.title,
                personalization=CartPersonalizationSummary(
                    childName=normalize_child_name(personalization.child_name),
                    childAge=personalization.child_age
                ),
                quantity=item.quantity,
                unitPrice=Money(amount=item.unit_price_amount, currency=item.unit_price_currency),
                lineTotal=Money(
                    amount=item.unit_price_amount * item.quantity,
                    currency=item.unit_price_currency
                ),
                previewImage=personalization.avatar_url
            ))
    
    # Calculate totals
    totals = await _calculate_cart_totals(cart.id, db)
    
    return Cart(
        id=cart.id,
        currency=cart.currency,
        items=items,
        totals=totals,
        updatedAt=cart.updated_at
    )

@router.get("/cart", response_model=Cart)
async def get_cart(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current cart"""
    cart = await _get_or_create_cart(current_user.id, db)
    return await _build_cart_response(cart, db)

@router.post("/cart/items", response_model=Cart)
async def add_to_cart(
    item_input: CartItemInput,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Add personalization to cart"""
    try:
        # Avoid touching ORM objects after commit (AsyncSession may expire instances, triggering MissingGreenlet).
        user_id = str(current_user.id)

        # Get or create cart
        cart = await _get_or_create_cart(current_user.id, db)

        # Verify personalization exists and belongs to user
        pers_result = await db.execute(
            select(Job).filter(
                Job.job_id == item_input.personalizationId,
                Job.user_id == current_user.id
            )
        )
        personalization = pers_result.scalar_one_or_none()

        if not personalization:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "PERSONALIZATION_NOT_FOUND", "message": "Personalization not found"}}
            )

        allowed_statuses = {
            "preview_ready",
            "prepay_ready",
            "confirmed",
            # Allow re-purchasing an already completed personalization (buy another copy).
            "completed",
        }
        if personalization.status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "PERSONALIZATION_NOT_READY",
                        "message": f"Personalization not ready for cart (status={personalization.status})"
                    }
                }
            )

        # Get book
        book_result = await db.execute(select(Book).filter(Book.slug == personalization.slug))
        book = book_result.scalar_one_or_none()

        if not book:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "BOOK_NOT_FOUND", "message": "Book not found"}}
            )

        # Check if item already in cart
        existing_result = await db.execute(
            select(CartItemModel).filter(
                CartItemModel.cart_id == cart.id,
                CartItemModel.personalization_id == item_input.personalizationId
            )
        )
        existing_item = existing_result.scalar_one_or_none()

        if existing_item:
            # Update quantity
            existing_item.quantity += item_input.quantity
            # Touch cart to update updated_at timestamp
            cart.currency = cart.currency
            await db.commit()
            # AsyncSession expires attributes on commit; refresh to avoid lazy-load IO in response building
            await db.refresh(cart)
        else:
            # Create new cart item
            cart_item = CartItemModel(
                id=str(uuid.uuid4()),
                cart_id=cart.id,
                slug=book.slug,
                personalization_id=item_input.personalizationId,
                quantity=item_input.quantity,
                unit_price_amount=book.price_amount,
                unit_price_currency=book.price_currency
            )
            db.add(cart_item)

            # Update personalization
            personalization.cart_item_id = cart_item.id
            # Don't downgrade a fully generated/purchased job.
            if personalization.status != "completed":
                personalization.status = "confirmed"

            await db.commit()
            await db.refresh(cart)

        logger.info(
            "Added to cart",
            extra={
                "user_id": user_id,
                "cart_id": cart.id,
                "personalization_id": item_input.personalizationId,
                "slug": getattr(personalization, "slug", None),
                "status": getattr(personalization, "status", None),
            },
        )

        return await _build_cart_response(cart, db)
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        logger.error(
            "Integrity error while adding to cart",
            extra={"user_id": str(getattr(current_user, "id", "")), "personalization_id": item_input.personalizationId},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "ADD_TO_CART_FAILED", "message": "Failed to add item to cart"}}
        ) from e
    except Exception as e:
        await db.rollback()
        logger.error(
            "Unexpected error while adding to cart",
            extra={"user_id": str(getattr(current_user, "id", "")), "personalization_id": item_input.personalizationId},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "ADD_TO_CART_FAILED", "message": "Failed to add item to cart"}}
        ) from e

@router.patch("/cart/items/{itemId}", response_model=Cart)
async def update_cart_item(
    itemId: str,
    update_request: CartItemUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update cart item quantity"""
    try:
        # Get user's cart
        cart = await _get_or_create_cart(current_user.id, db)
        
        # Get cart item
        item_result = await db.execute(
            select(CartItemModel).filter(
                CartItemModel.id == itemId,
                CartItemModel.cart_id == cart.id
            )
        )
        item = item_result.scalar_one_or_none()
        
        if not item:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "ITEM_NOT_FOUND", "message": "Cart item not found"}}
            )
        
        # Update quantity
        item.quantity = update_request.quantity
        
        # Touch cart to update updated_at timestamp
        # Update cart currency to trigger onupdate for updated_at
        cart.currency = cart.currency
        
        await db.commit()
        
        # Refresh objects to get updated timestamps
        await db.refresh(cart)
        await db.refresh(item)
        
        logger.info(f"Updated cart item: {itemId}, new quantity: {update_request.quantity}")
        
        return await _build_cart_response(cart, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating cart item {itemId}: {str(e)}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "UPDATE_FAILED", "message": "Failed to update cart item"}}
        )

@router.delete("/cart/items/{itemId}", status_code=204)
async def remove_from_cart(
    itemId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove item from cart"""
    try:
        # Get user's cart
        cart = await _get_or_create_cart(current_user.id, db)

        # Get cart item
        item_result = await db.execute(
            select(CartItemModel).filter(
                CartItemModel.id == itemId,
                CartItemModel.cart_id == cart.id
            )
        )
        item = item_result.scalar_one_or_none()

        # Idempotent delete: if it's already gone (or was merged away), treat as success.
        if not item:
            return

        # Delete item
        await db.execute(
            delete(CartItemModel).where(
                CartItemModel.id == itemId,
                CartItemModel.cart_id == cart.id
            )
        )
        await db.commit()

        logger.info(f"Removed from cart: {itemId}")
        return
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error while removing from cart {itemId}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "REMOVE_FROM_CART_FAILED", "message": "Failed to remove item from cart"}}
        ) from e

@router.get("/checkout/shipping-methods", response_model=List[ShippingMethod])
async def get_shipping_methods(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get available shipping methods"""
    # In a real implementation, this would be dynamic based on cart and location
    return [
        ShippingMethod(
            id="standard",
            label="Standard Shipping",
            description="5-7 business days",
            amount=Money(amount=5.99, currency="USD"),
            estimatedDaysMin=5,
            estimatedDaysMax=7
        ),
        ShippingMethod(
            id="express",
            label="Express Shipping",
            description="2-3 business days",
            amount=Money(amount=15.99, currency="USD"),
            estimatedDaysMin=2,
            estimatedDaysMax=3
        ),
        ShippingMethod(
            id="overnight",
            label="Overnight Shipping",
            description="Next business day",
            amount=Money(amount=29.99, currency="USD"),
            estimatedDaysMin=1,
            estimatedDaysMax=1
        )
    ]

@router.post("/checkout/quote", response_model=CheckoutQuoteResponse)
async def get_checkout_quote(
    quote_request: CheckoutQuoteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Calculate checkout totals"""
    # Get cart
    if quote_request.cartId:
        cart_result = await db.execute(
            select(CartModel).filter(
                CartModel.id == quote_request.cartId,
                CartModel.user_id == current_user.id
            )
        )
        cart = cart_result.scalar_one_or_none()
        if not cart:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "CART_NOT_FOUND", "message": "Cart not found"}}
            )
    else:
        cart = await _get_or_create_cart(current_user.id, db)
    
    # Get shipping method
    shipping_methods = await get_shipping_methods(current_user, db)
    shipping_method = next(
        (m for m in shipping_methods if m.id == quote_request.shippingMethodId),
        None
    )
    
    if not shipping_method:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "SHIPPING_METHOD_NOT_FOUND", "message": "Shipping method not found"}}
        )
    
    # Calculate totals with shipping
    totals = await _calculate_cart_totals(cart.id, db, shipping_method.amount.amount)
    
    return CheckoutQuoteResponse(
        cartId=cart.id,
        totals=totals,
        shippingMethod=shipping_method
    )

