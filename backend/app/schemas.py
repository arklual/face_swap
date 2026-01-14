"""
Pydantic schemas for request/response validation
"""
from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

# ===== Common Schemas =====

class ApiError(BaseModel):
    error: Dict[str, Any]

class Money(BaseModel):
    amount: float
    currency: str

class HealthResponse(BaseModel):
    status: str

class VersionResponse(BaseModel):
    version: str

# ===== Auth Schemas =====

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    firstName: str
    lastName: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserProfile(BaseModel):
    id: str
    email: EmailStr
    firstName: str
    lastName: str
    phone: Optional[str] = None
    deliveryRecipient: Optional[str] = None
    deliveryCity: Optional[str] = None
    deliveryStreet: Optional[str] = None
    deliveryHouse: Optional[str] = None
    deliveryApartment: Optional[str] = None
    deliveryPostalCode: Optional[str] = None
    deliveryComment: Optional[str] = None

class AuthResponse(BaseModel):
    token: str
    user: UserProfile

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)

class UserProfileUpdate(BaseModel):
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    phone: Optional[str] = None
    deliveryRecipient: Optional[str] = None
    deliveryCity: Optional[str] = None
    deliveryStreet: Optional[str] = None
    deliveryHouse: Optional[str] = None
    deliveryApartment: Optional[str] = None
    deliveryPostalCode: Optional[str] = None
    deliveryComment: Optional[str] = None

# ===== Book Schemas =====

class BookTag(BaseModel):
    label: str
    tone: Optional[str] = None

class BookSummary(BaseModel):
    slug: str
    title: str
    subtitle: Optional[str] = None
    heroImage: str
    ageRange: str
    category: str
    price: Money
    compareAtPrice: Optional[Money] = None
    discountPercent: Optional[float] = None
    tags: List[BookTag] = []

class BookSpecs(BaseModel):
    idealFor: str
    ageRange: str
    characters: str
    genre: str
    pages: str
    shipping: str

class BookDetail(BookSummary):
    description: str
    descriptionSecondary: Optional[str] = None
    bullets: List[str]
    galleryImages: List[str]
    specs: BookSpecs

class PaginationMeta(BaseModel):
    total: int
    limit: int
    nextCursor: Optional[str] = None

class BookListResponse(BaseModel):
    data: List[BookSummary]
    meta: PaginationMeta

class HighlightSection(BaseModel):
    key: str
    title: str
    ctaLabel: Optional[str] = None
    items: List[BookSummary]

class BookHighlightsResponse(BaseModel):
    sections: List[HighlightSection]

class RelatedBooksResponse(BaseModel):
    data: List[BookSummary]

class FilterCategory(BaseModel):
    slug: str
    label: str

class FilterAgeRange(BaseModel):
    id: str
    label: str

class BookFiltersResponse(BaseModel):
    categories: List[FilterCategory]
    ageRanges: List[FilterAgeRange]
    tags: List[BookTag] = []
    years: List[int] = []

class PreviewPage(BaseModel):
    index: int
    imageUrl: str
    locked: bool
    caption: Optional[str] = None

class PreviewResponse(BaseModel):
    pages: List[PreviewPage]
    unlockedCount: int
    totalCount: int

# ===== Personalization Schemas =====

class PersonalizationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PREVIEW_READY = "preview_ready"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FAILED = "failed"

class GenerationRetry(BaseModel):
    used: int
    limit: int
    remaining: int
    allowed: bool

class Personalization(BaseModel):
    id: str
    slug: str
    childName: str
    childAge: int
    status: str
    createdAt: datetime
    updatedAt: datetime
    previewReadyAt: Optional[datetime] = None
    avatarUrl: Optional[str] = None
    preview: Optional[PreviewResponse] = None
    cartItemId: Optional[str] = None
    generationRetry: Optional[GenerationRetry] = None

class AvatarUploadResponse(BaseModel):
    uploadId: str
    expiresAt: datetime

# ===== Cart Schemas =====

class CartPersonalizationSummary(BaseModel):
    childName: str
    childAge: int

class CartItemInput(BaseModel):
    personalizationId: str
    quantity: int = Field(ge=1)

class CartItemUpdateRequest(BaseModel):
    quantity: int = Field(ge=1)

class CartItem(BaseModel):
    id: str
    slug: str
    title: str
    personalization: CartPersonalizationSummary
    quantity: int
    unitPrice: Money
    lineTotal: Money
    previewImage: Optional[str] = None

class CartTotals(BaseModel):
    subtotal: Money
    discountTotal: Money
    taxTotal: Money
    shippingTotal: Money
    grandTotal: Money

class Cart(BaseModel):
    id: str
    currency: str
    items: List[CartItem]
    totals: CartTotals
    updatedAt: datetime

# ===== Shipping & Checkout Schemas =====

class ShippingMethod(BaseModel):
    id: str
    label: str
    description: Optional[str] = None
    amount: Money
    estimatedDaysMin: int
    estimatedDaysMax: int

class Address(BaseModel):
    firstName: str
    lastName: str
    company: Optional[str] = None
    line1: str
    line2: Optional[str] = None
    city: str
    region: Optional[str] = None
    postalCode: str
    countryCode: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None

class CheckoutQuoteRequest(BaseModel):
    cartId: Optional[str] = None
    shippingAddress: Address
    shippingMethodId: str
    promoCode: Optional[str] = None

class CheckoutQuoteResponse(BaseModel):
    cartId: str
    totals: CartTotals
    shippingMethod: ShippingMethod

# ===== Order Schemas =====

class PaymentInput(BaseModel):
    provider: str = Field(pattern="^(stripe|paypal|test)$")
    token: str
    savePaymentMethod: bool = False

class CreateOrderRequest(BaseModel):
    cartId: Optional[str] = None
    shippingAddress: Address
    billingAddress: Optional[Address] = None
    shippingMethodId: str
    payment: PaymentInput
    email: Optional[EmailStr] = None

class OrderItem(BaseModel):
    id: str
    personalizationId: str
    slug: str
    title: str
    quantity: int
    unitPrice: Money
    lineTotal: Money
    personalization: CartPersonalizationSummary

class Order(BaseModel):
    id: str
    number: str
    status: str
    placedAt: datetime
    currency: str
    totals: CartTotals
    items: List[OrderItem]
    shippingAddress: Address
    billingAddress: Optional[Address] = None
    shippingMethod: ShippingMethod
    personalizationPreviews: List[PreviewPage] = []

class OrderSummary(BaseModel):
    id: str
    number: str
    status: str
    placedAt: datetime
    total: Money

class OrderListResponse(BaseModel):
    data: List[OrderSummary]
    meta: PaginationMeta

