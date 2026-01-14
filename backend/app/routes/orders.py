"""
Orders routes
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import User, get_current_user
from ..book.manifest_store import load_manifest
from ..book.stages import stage_has_face_swap
from ..db import get_db
from ..logger import logger
from ..models import (
    Book,
    Cart as CartModel,
    CartItem as CartItemModel,
    Job,
    Order as OrderModel,
    OrderItem as OrderItemModel,
    OrderStatus,
)
from ..schemas import (
    CartPersonalizationSummary,
    CartTotals,
    CreateOrderRequest,
    Money,
    Order,
    OrderItem,
    OrderListResponse,
    OrderSummary,
    PaginationMeta,
    ShippingMethod,
)
from ..services.cart import get_or_create_active_cart
from ..services.order_status import compute_order_status

router = APIRouter(tags=["Orders"])


def _generate_order_number() -> str:
    import random
    import string

    timestamp = datetime.now().strftime("%Y%m%d")
    random_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"WW-{timestamp}-{random_part}"


async def _calculate_order_totals(items: List[CartItemModel], shipping_amount: float, currency: str) -> CartTotals:
    subtotal = sum(item.unit_price_amount * item.quantity for item in items)
    discount_total = 0.0
    tax_total = subtotal * 0.1
    grand_total = subtotal - discount_total + tax_total + shipping_amount

    return CartTotals(
        subtotal=Money(amount=subtotal, currency=currency),
        discountTotal=Money(amount=discount_total, currency=currency),
        taxTotal=Money(amount=tax_total, currency=currency),
        shippingTotal=Money(amount=shipping_amount, currency=currency),
        grandTotal=Money(amount=grand_total, currency=currency),
    )


@router.post("/checkout/orders", response_model=Order, status_code=201)
async def create_order(
    order_request: CreateOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if order_request.cartId:
        cart_result = await db.execute(
            select(CartModel).filter(CartModel.id == order_request.cartId, CartModel.user_id == current_user.id)
        )
    else:
        cart_result = await db.execute(select(CartModel).filter(CartModel.user_id == current_user.id))
    cart = cart_result.scalar_one_or_none()

    if not cart:
        raise HTTPException(status_code=404, detail={"error": {"code": "CART_NOT_FOUND", "message": "Cart not found"}})

    async def load_cart_items(cart_id: str) -> List[CartItemModel]:
        items_result = await db.execute(select(CartItemModel).filter(CartItemModel.cart_id == cart_id))
        return list(items_result.scalars().all())

    cart_items = await load_cart_items(cart.id)
    if not cart_items:
        active_cart = await get_or_create_active_cart(user_id=current_user.id, db=db)
        if active_cart.id != cart.id:
            active_items = await load_cart_items(active_cart.id)
            if active_items:
                cart = active_cart
                cart_items = active_items

    if not cart_items:
        raise HTTPException(status_code=400, detail={"error": {"code": "CART_EMPTY", "message": "Cart is empty"}})

    if order_request.payment.provider not in ["stripe", "paypal", "test"]:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_PAYMENT_PROVIDER", "message": "Invalid payment provider"}},
        )

    from ..routes.cart import get_shipping_methods

    shipping_methods = await get_shipping_methods(current_user, db)
    shipping_method = next((m for m in shipping_methods if m.id == order_request.shippingMethodId), None)
    if not shipping_method:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "SHIPPING_METHOD_NOT_FOUND", "message": "Shipping method not found"}},
        )

    totals = await _calculate_order_totals(cart_items, shipping_method.amount.amount, cart.currency)

    order_id = str(uuid.uuid4())
    order = OrderModel(
        id=order_id,
        number=_generate_order_number(),
        user_id=current_user.id,
        status=OrderStatus.PENDING_PAYMENT,
        currency=cart.currency,
        subtotal_amount=totals.subtotal.amount,
        discount_amount=totals.discountTotal.amount,
        tax_amount=totals.taxTotal.amount,
        shipping_amount=totals.shippingTotal.amount,
        grand_total_amount=totals.grandTotal.amount,
        shipping_address=order_request.shippingAddress.dict(),
        billing_address=(order_request.billingAddress.dict() if order_request.billingAddress else order_request.shippingAddress.dict()),
        shipping_method={"id": shipping_method.id, "label": shipping_method.label, "amount": shipping_method.amount.amount},
        payment_provider=order_request.payment.provider,
        payment_token=order_request.payment.token,
        placed_at=datetime.utcnow(),
    )
    db.add(order)

    order_item_payloads: List[dict] = []
    for cart_item in cart_items:
        book_result = await db.execute(select(Book).filter(Book.slug == cart_item.slug))
        book = book_result.scalar_one()

        pers_result = await db.execute(select(Job).filter(Job.job_id == cart_item.personalization_id))
        personalization = pers_result.scalar_one()

        order_item = OrderItemModel(
            id=str(uuid.uuid4()),
            order_id=order_id,
            slug=cart_item.slug,
            title=book.title,
            personalization_id=cart_item.personalization_id,
            quantity=cart_item.quantity,
            unit_price_amount=cart_item.unit_price_amount,
            unit_price_currency=cart_item.unit_price_currency,
            line_total_amount=cart_item.unit_price_amount * cart_item.quantity,
            line_total_currency=cart_item.unit_price_currency,
            child_name=personalization.child_name,
            child_age=personalization.child_age,
        )
        db.add(order_item)
        order_item_payloads.append(
            {
                "id": order_item.id,
                "slug": order_item.slug,
                "title": order_item.title,
                "quantity": order_item.quantity,
                "unit_price_amount": order_item.unit_price_amount,
                "unit_price_currency": order_item.unit_price_currency,
                "line_total_amount": order_item.line_total_amount,
                "line_total_currency": order_item.line_total_currency,
                "personalization_id": order_item.personalization_id,
                "child_name": order_item.child_name,
                "child_age": order_item.child_age,
            }
        )

        await db.delete(cart_item)

    if order_request.payment.provider == "test":
        order.status = OrderStatus.PROCESSING

    await db.commit()
    await db.refresh(order)

    if order.status == OrderStatus.PROCESSING:
        try:
            from ..tasks import build_stage_backgrounds_task, render_stage_pages_task

            for payload in order_item_payloads:
                pers_id = payload["personalization_id"]
                manifest = load_manifest(payload["slug"])
                if stage_has_face_swap(manifest, "postpay"):
                    build_stage_backgrounds_task.apply_async(args=(pers_id, "postpay"), queue="gpu")
                else:
                    render_stage_pages_task.apply_async(args=(pers_id, "postpay"), queue="render")
        except Exception as e:
            logger.error(f"Failed to trigger postpay generation for order {order.id}: {e}")

    items: List[OrderItem] = []

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

    for payload in order_item_payloads:
        items.append(
            OrderItem(
                id=payload["id"],
                personalizationId=payload["personalization_id"],
                slug=payload["slug"],
                title=payload["title"],
                quantity=payload["quantity"],
                unitPrice=Money(amount=payload["unit_price_amount"], currency=payload["unit_price_currency"]),
                lineTotal=Money(amount=payload["line_total_amount"], currency=payload["line_total_currency"]),
                personalization=CartPersonalizationSummary(
                    childName=normalize_child_name(payload["child_name"]),
                    childAge=payload["child_age"],
                ),
            )
        )

    return Order(
        id=order.id,
        number=order.number,
        status=order.status.value,
        placedAt=order.placed_at,
        currency=order.currency,
        totals=totals,
        items=items,
        shippingAddress=order_request.shippingAddress,
        billingAddress=order_request.billingAddress or order_request.shippingAddress,
        shippingMethod=shipping_method,
        personalizationPreviews=[],
    )


@router.post("/orders/{orderId}/mark_paid", response_model=Order)
async def mark_order_paid(
    orderId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order_result = await db.execute(select(OrderModel).filter(OrderModel.id == orderId, OrderModel.user_id == current_user.id))
    order = order_result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail={"error": {"code": "ORDER_NOT_FOUND", "message": "Order not found"}})

    order.status = OrderStatus.PROCESSING
    await db.commit()
    await db.refresh(order)

    items_result = await db.execute(select(OrderItemModel).filter(OrderItemModel.order_id == order.id))
    order_items = items_result.scalars().all()

    from ..tasks import build_stage_backgrounds_task, render_stage_pages_task

    for oi in order_items:
        manifest = load_manifest(oi.slug)
        if stage_has_face_swap(manifest, "postpay"):
            build_stage_backgrounds_task.apply_async(args=(oi.personalization_id, "postpay"), queue="gpu")
        else:
            render_stage_pages_task.apply_async(args=(oi.personalization_id, "postpay"), queue="render")

    return await get_order(orderId=orderId, current_user=current_user, db=db)


@router.get("/orders", response_model=OrderListResponse)
async def get_orders(
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(OrderModel).filter(OrderModel.user_id == current_user.id)
    if cursor:
        query = query.filter(OrderModel.id > cursor)
    query = query.order_by(desc(OrderModel.placed_at)).limit(limit + 1)

    result = await db.execute(query)
    orders = result.scalars().all()

    has_more = len(orders) > limit
    if has_more:
        orders = orders[:limit]

    next_cursor = orders[-1].id if has_more and orders else None

    # Derive virtual status "delivary" when all personalizations are generated (job.status == "completed").
    order_ids = [o.id for o in orders]
    job_statuses_by_order_id: dict[str, list[str]] = {}
    if order_ids:
        items_result = await db.execute(select(OrderItemModel).filter(OrderItemModel.order_id.in_(order_ids)))
        order_items = list(items_result.scalars().all())
        personalization_ids = list({oi.personalization_id for oi in order_items if oi.personalization_id})

        jobs_by_id: dict[str, Job] = {}
        if personalization_ids:
            jobs_result = await db.execute(select(Job).filter(Job.job_id.in_(personalization_ids)))
            jobs = list(jobs_result.scalars().all())
            jobs_by_id = {j.job_id: j for j in jobs}

        for oi in order_items:
            job = jobs_by_id.get(oi.personalization_id)
            if not job:
                continue
            job_statuses_by_order_id.setdefault(oi.order_id, []).append(job.status)

    summaries = [
        OrderSummary(
            id=o.id,
            number=o.number,
            status=compute_order_status(
                base_status=o.status.value,
                item_job_statuses=job_statuses_by_order_id.get(o.id, []),
            ),
            placedAt=o.placed_at,
            total=Money(amount=o.grand_total_amount, currency=o.currency),
        )
        for o in orders
    ]

    return OrderListResponse(
        data=summaries,
        meta=PaginationMeta(total=len(summaries), limit=limit, nextCursor=next_cursor),
    )


@router.get("/orders/{orderId}", response_model=Order)
async def get_order(
    orderId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    order_result = await db.execute(select(OrderModel).filter(OrderModel.id == orderId, OrderModel.user_id == current_user.id))
    order = order_result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail={"error": {"code": "ORDER_NOT_FOUND", "message": "Order not found"}})

    items_result = await db.execute(select(OrderItemModel).filter(OrderItemModel.order_id == order.id))
    order_items = items_result.scalars().all()

    items: List[OrderItem] = []
    item_job_statuses: list[str] = []
    for oi in order_items:
        pers_result = await db.execute(select(Job).filter(Job.job_id == oi.personalization_id))
        pers = pers_result.scalar_one()
        item_job_statuses.append(pers.status)
        items.append(
            OrderItem(
                id=oi.id,
                personalizationId=oi.personalization_id,
                slug=oi.slug,
                title=oi.title,
                quantity=oi.quantity,
                unitPrice=Money(amount=oi.unit_price_amount, currency=oi.unit_price_currency),
                lineTotal=Money(amount=oi.line_total_amount, currency=oi.line_total_currency),
                personalization=CartPersonalizationSummary(childName=pers.child_name, childAge=pers.child_age),
            )
        )

    totals = CartTotals(
        subtotal=Money(amount=order.subtotal_amount, currency=order.currency),
        discountTotal=Money(amount=order.discount_amount, currency=order.currency),
        taxTotal=Money(amount=order.tax_amount, currency=order.currency),
        shippingTotal=Money(amount=order.shipping_amount, currency=order.currency),
        grandTotal=Money(amount=order.grand_total_amount, currency=order.currency),
    )

    from ..schemas import Address

    shipping_method = ShippingMethod(
        id=order.shipping_method.get("id", "standard"),
        label=order.shipping_method.get("label", "Standard Shipping"),
        amount=Money(amount=order.shipping_method.get("amount", 0), currency=order.currency),
        estimatedDaysMin=5,
        estimatedDaysMax=7,
    )

    return Order(
        id=order.id,
        number=order.number,
        status=compute_order_status(base_status=order.status.value, item_job_statuses=item_job_statuses),
        placedAt=order.placed_at,
        currency=order.currency,
        totals=totals,
        items=items,
        shippingAddress=Address(**order.shipping_address),
        billingAddress=Address(**order.billing_address) if order.billing_address else None,
        shippingMethod=shipping_method,
        personalizationPreviews=[],
    )

