"""
Catalog routes - books, highlights, previews
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_
from typing import Optional, List

from ..db import get_db
from ..models import Book, BookPreview, Job
from ..schemas import (
    BookListResponse,
    BookHighlightsResponse,
    BookDetail,
    BookSummary,
    RelatedBooksResponse,
    PreviewResponse,
    PreviewPage,
    PaginationMeta,
    HighlightSection,
    Money,
    BookTag,
    BookSpecs
)
from ..auth import get_current_user_optional, User
from ..logger import logger

router = APIRouter(tags=["Catalog"])

def _book_to_summary(book: Book) -> BookSummary:
    """Convert Book model to BookSummary schema"""
    tags = [BookTag(**tag) for tag in (book.tags or [])]
    
    return BookSummary(
        slug=book.slug,
        title=book.title,
        subtitle=book.subtitle,
        heroImage=book.hero_image,
        ageRange=book.age_range,
        category=book.category,
        price=Money(amount=book.price_amount, currency=book.price_currency),
        compareAtPrice=Money(
            amount=book.compare_at_price_amount,
            currency=book.compare_at_price_currency
        ) if book.compare_at_price_amount else None,
        discountPercent=book.discount_percent,
        tags=tags
    )

def _book_to_detail(book: Book) -> BookDetail:
    """Convert Book model to BookDetail schema"""
    summary = _book_to_summary(book)
    specs = BookSpecs(**book.specs) if book.specs else BookSpecs(
        idealFor="", ageRange="", characters="", genre="", pages="", shipping=""
    )
    
    return BookDetail(
        **summary.dict(),
        description=book.description,
        descriptionSecondary=book.description_secondary,
        bullets=book.bullets or [],
        galleryImages=book.gallery_images or [],
        specs=specs
    )

@router.get("/books", response_model=BookListResponse)
async def get_books(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    ageRange: Optional[str] = Query(None, alias="ageRange"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Get list of books with search and filters"""
    query = select(Book)
    
    # Apply filters
    filters = []
    if search:
        search_filter = or_(
            Book.title.ilike(f"%{search}%"),
            Book.subtitle.ilike(f"%{search}%")
        )
        filters.append(search_filter)
    
    if category:
        filters.append(Book.category == category)
    
    if ageRange:
        filters.append(Book.age_range == ageRange)
    
    if filters:
        query = query.filter(and_(*filters))
    
    # Apply pagination
    if cursor:
        query = query.filter(Book.slug > cursor)
    
    query = query.order_by(Book.slug).limit(limit + 1)
    
    result = await db.execute(query)
    books = result.scalars().all()
    
    # Check if there are more results
    has_more = len(books) > limit
    if has_more:
        books = books[:limit]
    
    next_cursor = books[-1].slug if has_more and books else None
    
    return BookListResponse(
        data=[_book_to_summary(book) for book in books],
        meta=PaginationMeta(
            total=len(books),  # In production, you'd do a count query
            limit=limit,
            nextCursor=next_cursor
        )
    )

@router.get("/books/highlights", response_model=BookHighlightsResponse)
async def get_book_highlights(
    section: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Get book highlights for homepage carousels"""
    sections = []
    
    section_configs = {
        "holiday": ("holiday", "Holiday Specials", "Shop All Holiday Books"),
        "new-arrivals": (None, "New Arrivals", "Shop All New Books"),
        "bestsellers": ("bestseller", "Bestsellers", "Shop All Bestsellers"),
        "girls": ("girl", "For Girls", "Shop All Girls Books"),
        "boys": ("boy", "For Boys", "Shop All Boys Books"),
    }
    
    sections_to_fetch = [section] if section else list(section_configs.keys())
    
    for sect_key in sections_to_fetch:
        if sect_key not in section_configs:
            continue
        
        category, title, cta = section_configs[sect_key]
        
        query = select(Book)
        if category:
            query = query.filter(Book.category == category)
        query = query.limit(10)
        
        result = await db.execute(query)
        books = result.scalars().all()
        
        sections.append(HighlightSection(
            key=sect_key,
            title=title,
            ctaLabel=cta,
            items=[_book_to_summary(book) for book in books]
        ))
    
    return BookHighlightsResponse(sections=sections)

@router.get("/books/{slug}", response_model=BookDetail)
async def get_book(slug: str, db: AsyncSession = Depends(get_db)):
    """Get detailed book information"""
    result = await db.execute(select(Book).filter(Book.slug == slug))
    book = result.scalar_one_or_none()
    
    if not book:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "BOOK_NOT_FOUND", "message": "Book not found"}}
        )
    
    return _book_to_detail(book)

@router.get("/books/{slug}/related", response_model=RelatedBooksResponse)
async def get_related_books(slug: str, db: AsyncSession = Depends(get_db)):
    """Get recommended books"""
    # First, get the book to find related books
    result = await db.execute(select(Book).filter(Book.slug == slug))
    book = result.scalar_one_or_none()
    
    if not book:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "BOOK_NOT_FOUND", "message": "Book not found"}}
        )
    
    # Find books in the same category or age range
    query = select(Book).filter(
        and_(
            Book.slug != slug,
            or_(
                Book.category == book.category,
                Book.age_range == book.age_range
            )
        )
    ).limit(6)
    
    result = await db.execute(query)
    related_books = result.scalars().all()
    
    return RelatedBooksResponse(
        data=[_book_to_summary(b) for b in related_books]
    )

@router.get("/books/{slug}/previews", response_model=PreviewResponse)
async def get_book_previews(
    slug: str,
    personalizationId: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """Get preview pages for a book"""
    # Check if book exists
    book_result = await db.execute(select(Book).filter(Book.slug == slug))
    book = book_result.scalar_one_or_none()
    
    if not book:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "BOOK_NOT_FOUND", "message": "Book not found"}}
        )
    
    # Get preview pages
    preview_result = await db.execute(
        select(BookPreview)
        .filter(BookPreview.slug == slug)
        .order_by(BookPreview.page_index)
    )
    preview_pages = preview_result.scalars().all()
    
    # If personalizationId is provided and user is authenticated, unlock more pages
    unlocked_count = sum(1 for p in preview_pages if not p.locked)
    
    if personalizationId and current_user:
        # Check if personalization belongs to user
        pers_result = await db.execute(
            select(Job).filter(
                Job.job_id == personalizationId,
                Job.user_id == current_user.id
            )
        )
        personalization = pers_result.scalar_one_or_none()
        
        if personalization and personalization.status in ["preview_ready", "confirmed"]:
            # Unlock all pages for authenticated users with valid personalization
            unlocked_count = len(preview_pages)
    
    pages = []
    for i, page in enumerate(preview_pages):
        locked = page.locked
        if personalizationId and current_user and i < unlocked_count:
            locked = False
        
        pages.append(PreviewPage(
            index=page.page_index,
            imageUrl=page.image_url,
            locked=locked,
            caption=page.caption
        ))
    
    return PreviewResponse(
        pages=pages,
        unlockedCount=unlocked_count,
        totalCount=len(preview_pages)
    )

