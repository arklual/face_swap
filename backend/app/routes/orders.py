"""
Orders routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
import uuid
from datetime import datetime
from typing import Optional, List

from ..db import get_db
from ..models import (
    Order as OrderModel,
    OrderItem as OrderItemModel,
    OrderStatus,
    Cart as CartModel,
    CartItem as CartItemModel,
    Job,
    Book
)
from ..schemas import (
    CreateOrderRequest,
    Order,
    OrderItem,
    OrderSummary,
    OrderListResponse,
    CartTotals,
    CartPersonalizationSummary,
    ShippingMethod,
    Money,
    PaginationMeta,
    PreviewPage
)
from ..auth import get_current_user, User
from ..logger import logger

router = APIRouter(tags=["Orders"])

def _generate_order_number() -> str:
    """Generate unique order number"""
    import random
    import string
    timestamp = datetime.now().strftime("%Y%m%d")
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"WW-{timestamp}-{random_part}"

async def _calculate_order_totals(items: List[CartItemModel], shipping_amount: float, currency: str) -> CartTotals:
    """Calculate order totals"""
    subtotal = sum(item.unit_price_amount * item.quantity for item in items)
    discount_total = 0.0
    tax_total = subtotal * 0.1  # 10% tax for demo
    grand_total = subtotal - discount_total + tax_total + shipping_amount
    
    return CartTotals(
        subtotal=Money(amount=subtotal, currency=currency),
        discountTotal=Money(amount=discount_total, currency=currency),
        taxTotal=Money(amount=tax_total, currency=currency),
        shippingTotal=Money(amount=shipping_amount, currency=currency),
        grandTotal=Money(amount=grand_total, currency=currency)
    )

@router.post("/checkout/orders", response_model=Order, status_code=201)
async def create_order(
    order_request: CreateOrderRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create order from cart"""
    # Get cart
    cart_result = await db.execute(
        select(CartModel).filter(CartModel.user_id == current_user.id)
    )
    cart = cart_result.scalar_one_or_none()
    
    if not cart:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "CART_NOT_FOUND", "message": "Cart not found"}}
        )
    
    # Get cart items
    items_result = await db.execute(
        select(CartItemModel).filter(CartItemModel.cart_id == cart.id)
    )
    cart_items = items_result.scalars().all()
    
    if not cart_items:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "CART_EMPTY", "message": "Cart is empty"}}
        )
    
    # Validate payment (simplified for demo)
    if order_request.payment.provider not in ["stripe", "paypal", "test"]:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_PAYMENT_PROVIDER", "message": "Invalid payment provider"}}
        )
    
    # Get shipping method details (simplified)
    from ..routes.cart import get_shipping_methods
    shipping_methods = await get_shipping_methods(current_user, db)
    shipping_method = next(
        (m for m in shipping_methods if m.id == order_request.shippingMethodId),
        None
    )
    
    if not shipping_method:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "SHIPPING_METHOD_NOT_FOUND", "message": "Shipping method not found"}}
        )
    
    # Calculate totals
    totals = await _calculate_order_totals(cart_items, shipping_method.amount.amount, cart.currency)
    
    # Create order
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
        billing_address=order_request.billingAddress.dict() if order_request.billingAddress else order_request.shippingAddress.dict(),
        shipping_method={
            "id": shipping_method.id,
            "label": shipping_method.label,
            "amount": shipping_method.amount.amount
        },
        payment_provider=order_request.payment.provider,
        payment_token=order_request.payment.token,
        placed_at=datetime.utcnow()
    )
    db.add(order)
    
    # Create order items from cart items
    order_items = []
    for cart_item in cart_items:
        # Get book
        book_result = await db.execute(select(Book).filter(Book.slug == cart_item.slug))
        book = book_result.scalar_one()
        
        # Get personalization
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
            child_age=personalization.child_age
        )
        db.add(order_item)
        order_items.append(order_item)
        
        # Delete cart item
        await db.delete(cart_item)
    
    # Process payment (simplified for demo)
    if order_request.payment.provider == "test":
        order.status = OrderStatus.PROCESSING
    
    await db.commit()
    await db.refresh(order)
    
    logger.info(f"Order created: {order.number}")
    
    # Build response
    items = []
    for oi in order_items:
        pers_result = await db.execute(select(Job).filter(Job.job_id == oi.personalization_id))
        pers = pers_result.scalar_one()
        
        items.append(OrderItem(
            id=oi.id,
            slug=oi.slug,
            title=oi.title,
            quantity=oi.quantity,
            unitPrice=Money(amount=oi.unit_price_amount, currency=oi.unit_price_currency),
            lineTotal=Money(amount=oi.line_total_amount, currency=oi.line_total_currency),
            personalization=CartPersonalizationSummary(
                childName=pers.child_name,
                childAge=pers.child_age
            )
        ))
    
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
        personalizationPreviews=[]
    )

@router.get("/orders", response_model=OrderListResponse)
async def get_orders(
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get list of customer orders"""
    query = select(OrderModel).filter(OrderModel.user_id == current_user.id)
    
    if cursor:
        query = query.filter(OrderModel.id > cursor)
    
    query = query.order_by(desc(OrderModel.placed_at)).limit(limit + 1)
    
    result = await db.execute(query)
    orders = result.scalars().all()
    
    # Check if there are more results
    has_more = len(orders) > limit
    if has_more:
        orders = orders[:limit]
    
    next_cursor = orders[-1].id if has_more and orders else None
    
    summaries = [
        OrderSummary(
            id=order.id,
            number=order.number,
            status=order.status.value,
            placedAt=order.placed_at,
            total=Money(amount=order.grand_total_amount, currency=order.currency)
        )
        for order in orders
    ]
    
    return OrderListResponse(
        data=summaries,
        meta=PaginationMeta(
            total=len(summaries),
            limit=limit,
            nextCursor=next_cursor
        )
    )

@router.get("/orders/{orderId}", response_model=Order)
async def get_order(
    orderId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get order details"""
    # Get order
    order_result = await db.execute(
        select(OrderModel).filter(
            OrderModel.id == orderId,
            OrderModel.user_id == current_user.id
        )
    )
    order = order_result.scalar_one_or_none()
    
    if not order:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "ORDER_NOT_FOUND", "message": "Order not found"}}
        )
    
    # Get order items
    items_result = await db.execute(
        select(OrderItemModel).filter(OrderItemModel.order_id == order.id)
    )
    order_items = items_result.scalars().all()
    
    # Build items list
    items = []
    for oi in order_items:
        pers_result = await db.execute(select(Job).filter(Job.job_id == oi.personalization_id))
        pers = pers_result.scalar_one()
        
        items.append(OrderItem(
            id=oi.id,
            slug=oi.slug,
            title=oi.title,
            quantity=oi.quantity,
            unitPrice=Money(amount=oi.unit_price_amount, currency=oi.unit_price_currency),
            lineTotal=Money(amount=oi.line_total_amount, currency=oi.line_total_currency),
            personalization=CartPersonalizationSummary(
                childName=pers.child_name,
                childAge=pers.child_age
            )
        ))
    
    # Calculate totals
    totals = CartTotals(
        subtotal=Money(amount=order.subtotal_amount, currency=order.currency),
        discountTotal=Money(amount=order.discount_amount, currency=order.currency),
        taxTotal=Money(amount=order.tax_amount, currency=order.currency),
        shippingTotal=Money(amount=order.shipping_amount, currency=order.currency),
        grandTotal=Money(amount=order.grand_total_amount, currency=order.currency)
    )
    
    # Build shipping method from stored data
    shipping_method = ShippingMethod(
        id=order.shipping_method.get("id", "standard"),
        label=order.shipping_method.get("label", "Standard Shipping"),
        amount=Money(amount=order.shipping_method.get("amount", 0), currency=order.currency),
        estimatedDaysMin=5,
        estimatedDaysMax=7
    )
    
    from ..schemas import Address
    return Order(
        id=order.id,
        number=order.number,
        status=order.status.value,
        placedAt=order.placed_at,
        currency=order.currency,
        totals=totals,
        items=items,
        shippingAddress=Address(**order.shipping_address),
        billingAddress=Address(**order.billing_address) if order.billing_address else None,
        shippingMethod=shipping_method,
        personalizationPreviews=[]
    )

