from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, String, DateTime, func, JSON, Integer, Float, Boolean, ForeignKey, Text, Enum as SQLEnum
import enum
Base = declarative_base()

class Job(Base):
    """Personalization job table"""
    __tablename__ = "jobs"
    job_id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    slug = Column(String, nullable=False, index=True)  # Book slug
    status = Column(String, default="pending")
    child_photo_uri = Column(String, nullable=True)
    child_name = Column(String, nullable=False)
    child_age = Column(Integer, nullable=False)
    child_gender = Column(String, nullable=True)
    caption_uri = Column(String, nullable=True)
    common_prompt = Column(String, nullable=True)
    analysis_json = Column(JSON, nullable=True)
    result_uri = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    preview_ready_at = Column(DateTime, nullable=True)
    cart_item_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class JobArtifact(Base):
    """
    Stores generated artifacts for a personalization job.

    We intentionally keep this in a separate table to avoid altering the `jobs` table
    without migrations (create_all will create missing tables automatically).
    """

    __tablename__ = "job_artifacts"

    id = Column(String, primary_key=True, index=True)
    job_id = Column(String, ForeignKey("jobs.job_id"), nullable=False, index=True)

    # prepay|postpay
    stage = Column(String, nullable=False, index=True)

    # page_png|spread_png|print_pdf|debug_json|...
    kind = Column(String, nullable=False, index=True)

    page_num = Column(Integer, nullable=True, index=True)
    s3_uri = Column(String, nullable=False)
    meta = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

class User(Base):
    """User/Customer table"""
    __tablename__ = "users"
    id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class UserDeliveryAddress(Base):
    """
    Stores user's default delivery address.

    Kept as a separate table so it can be created automatically without migrations.
    """

    __tablename__ = "user_delivery_addresses"

    user_id = Column(String, ForeignKey("users.id"), primary_key=True, index=True)

    recipient = Column(String, nullable=True)
    city = Column(String, nullable=True)
    street = Column(String, nullable=True)
    house = Column(String, nullable=True)
    apartment = Column(String, nullable=True)
    postal_code = Column(String, nullable=True)
    comment = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class PasswordResetToken(Base):
    """Password reset tokens"""
    __tablename__ = "password_reset_tokens"
    token = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

class Book(Base):
    """Book catalog"""
    __tablename__ = "books"
    slug = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    subtitle = Column(String, nullable=True)
    description = Column(Text, nullable=False)
    description_secondary = Column(Text, nullable=True)
    hero_image = Column(String, nullable=False)
    gallery_images = Column(JSON, nullable=True)  # List of URLs
    bullets = Column(JSON, nullable=True)  # List of strings
    age_range = Column(String, nullable=False)  # e.g., "2-4", "4-6"
    category = Column(String, nullable=False)  # boy, girl, holiday, bestseller
    price_amount = Column(Float, nullable=False)
    price_currency = Column(String, nullable=False, default="USD")
    compare_at_price_amount = Column(Float, nullable=True)
    compare_at_price_currency = Column(String, nullable=True)
    discount_percent = Column(Float, nullable=True)
    specs = Column(JSON, nullable=True)  # Book specs object
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class BookPreview(Base):
    """Book preview pages"""
    __tablename__ = "book_previews"
    id = Column(String, primary_key=True, index=True)
    slug = Column(String, ForeignKey("books.slug"), nullable=False, index=True)
    page_index = Column(Integer, nullable=False)
    image_url = Column(String, nullable=False)
    locked = Column(Boolean, default=False)
    caption = Column(String, nullable=True)

class Cart(Base):
    """Shopping cart"""
    __tablename__ = "carts"
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    currency = Column(String, nullable=False, default="USD")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class CartItem(Base):
    """Cart items"""
    __tablename__ = "cart_items"
    id = Column(String, primary_key=True, index=True)
    cart_id = Column(String, ForeignKey("carts.id"), nullable=False, index=True)
    slug = Column(String, ForeignKey("books.slug"), nullable=False)
    personalization_id = Column(String, ForeignKey("jobs.job_id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price_amount = Column(Float, nullable=False)
    unit_price_currency = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class OrderStatus(enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PROCESSING = "processing"
    DELIVERY = "delivery"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

class Order(Base):
    """Orders"""
    __tablename__ = "orders"
    id = Column(String, primary_key=True, index=True)
    number = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(SQLEnum(OrderStatus), nullable=False, default=OrderStatus.PENDING_PAYMENT)
    currency = Column(String, nullable=False)
    subtotal_amount = Column(Float, nullable=False)
    discount_amount = Column(Float, nullable=False, default=0)
    tax_amount = Column(Float, nullable=False, default=0)
    shipping_amount = Column(Float, nullable=False, default=0)
    grand_total_amount = Column(Float, nullable=False)
    shipping_address = Column(JSON, nullable=False)
    billing_address = Column(JSON, nullable=True)
    shipping_method = Column(JSON, nullable=False)
    payment_provider = Column(String, nullable=True)
    payment_token = Column(String, nullable=True)
    placed_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class OrderItem(Base):
    """Order items"""
    __tablename__ = "order_items"
    id = Column(String, primary_key=True, index=True)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False, index=True)
    slug = Column(String, nullable=False)
    title = Column(String, nullable=False)
    personalization_id = Column(String, ForeignKey("jobs.job_id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price_amount = Column(Float, nullable=False)
    unit_price_currency = Column(String, nullable=False)
    line_total_amount = Column(Float, nullable=False)
    line_total_currency = Column(String, nullable=False)
    child_name = Column(String, nullable=False)
    child_age = Column(Integer, nullable=False)
    created_at = Column(DateTime, server_default=func.now())