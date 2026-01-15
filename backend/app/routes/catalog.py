"""
Catalog routes - books, highlights, previews
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, distinct, func, text
from typing import Dict, List, Optional, Set
import boto3
from urllib.parse import urlparse
import re

from ..db import get_db
from ..models import Book, BookPreview, Job
from ..book.manifest_store import load_manifest
from ..exceptions import S3StorageError
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
    BookSpecs,
    BookFiltersResponse,
    FilterCategory,
    FilterAgeRange
)
from ..auth import get_current_user_optional, User
from ..logger import logger
from ..config import settings

router = APIRouter(tags=["Catalog"])

_s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)

CATEGORY_LABELS: Dict[str, str] = {
    "boy": "Для мальчиков",
    "girl": "Для девочек",
    "holiday": "Праздничные истории",
    "bestseller": "Хиты продаж",
}


def _presigned_get(uri: str, expires=3600) -> str:
    if not uri:
        return uri
    parsed_endpoint = urlparse(settings.AWS_ENDPOINT_URL) if settings.AWS_ENDPOINT_URL else None

    bucket = None
    key = None

    if uri.startswith("http"):
        p = urlparse(uri)
        
        # If host matches configured endpoint, use path-style parsing
        if parsed_endpoint and p.netloc == parsed_endpoint.netloc:
            path = p.path.lstrip("/")
            parts = path.split("/", 1)
            if len(parts) == 2:
                bucket, key = parts
            elif parts:
                # Single path segment - use configured bucket
                bucket = settings.S3_BUCKET_NAME
                key = parts[0]
        else:
            # Different host - try virtual-host style first: bucket.domain.tld/...
            host_parts = p.netloc.split(".")
            if len(host_parts) >= 3:
                bucket = host_parts[0]
                key = p.path.lstrip("/")

            # path-style fallback: domain.tld/bucket/key
            if not bucket:
                path = p.path.lstrip("/")
                parts = path.split("/", 1)
                if len(parts) == 2:
                    bucket, key = parts

            # If couldn't parse bucket/key from foreign URL, return as is
            if not bucket or not key:
                return uri

            # Foreign bucket (not our configured one) should not be re-signed via our S3 endpoint.
            # Otherwise we end up generating invalid links for 3rd-party public URLs.
            if bucket != settings.S3_BUCKET_NAME:
                return uri
        
        # Successfully parsed HTTP URL - generate presigned URL
        return _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )

    # Handle s3:// scheme
    if uri.startswith("s3://"):
        p = urlparse(uri)
        bucket = p.netloc
        key = p.path.lstrip("/")
    else:
        # Relative path - use configured bucket
        bucket = settings.S3_BUCKET_NAME
        key = uri.lstrip("/")

    return _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def _maybe_presign_list(items: Optional[List[str]]) -> List[str]:
    if not items:
        return []
    return [_presigned_get(x) for x in items]

def _is_mockish_preview_uri(uri: str) -> bool:
    """
    Storefront should never show legacy mock/demo assets.

    Heuristics:
    - legacy face-swap "illustrations/*" assets
    - marketing responsive images used as seed/demo placeholders
    - known placeholder hosts
    """
    value = (uri or "").lower()
    if not value:
        return True
    if "/illustrations/" in value or value.startswith("illustrations/"):
        return True
    if "twcstorage" in value:
        return True
    if "/responsive-images/" in value:
        return True
    if "via.placeholder" in value or "picsum.photos" in value:
        return True
    return False

def _category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)

def _category_to_tag(category: Optional[str]) -> Optional[BookTag]:
    if not category:
        return None
    slug = category.strip()
    if not slug:
        return None
    return BookTag(label=_category_label(slug))

def _create_fuzzy_pattern(term: str) -> str:
    """
    Создает регулярное выражение для fuzzy поиска с пропуском букв.
    Например: "прнцесса" -> "п.*р.*н.*ц.*е.*с.*с.*а"
    Это позволит найти "принцесса" даже если пропущена буква "и".
    Используем для PostgreSQL оператора ~* (case-insensitive).
    """
    if not term:
        return ""
    
    # Для PostgreSQL регулярных выражений нужно экранировать специальные символы
    # Но нам нужны некоторые из них (. для любого символа, * для повторения)
    # Экранируем только опасные символы, НЕ экранируем . и * так как они нужны
    term_lower = term.lower()
    
    # Создаем паттерн: между каждой буквой вставляем .* (любые символы, включая 0)
    # Это позволит находить слова даже если пропущены буквы
    # Пример: "прнцесса" станет "п.*р.*н.*ц.*е.*с.*с.*а" что найдет "принцесса"
    pattern_parts = []
    for i, char in enumerate(term_lower):
        # Экранируем специальные символы PostgreSQL regex, кроме . и * которые нам нужны
        # Символы которые нужно экранировать: ^$+?{}[]\|()
        if char in r'^$+?{}[]\|()':
            pattern_parts.append(f"\\{char}")
        else:
            pattern_parts.append(char)
        # Между буквами вставляем .* для допущения пропусков
        if i < len(term_lower) - 1:
            pattern_parts.append(".*")
    
    return "".join(pattern_parts)

def _manifest_gallery_uris(slug: str, limit: int) -> List[str]:
    """
    Return a list of manifest base_uri values suitable for a storefront gallery.
    """
    manifest = load_manifest(slug)
    candidates = sorted(manifest.pages, key=lambda p: p.page_num)
    uris = [
        p.base_uri for p in candidates
        if p.base_uri and "/thumbnails/" not in p.base_uri and not _is_mockish_preview_uri(p.base_uri)
    ]
    return uris[: max(0, limit)]


def _book_to_summary(book: Book) -> BookSummary:
    """Convert Book model to BookSummary schema"""
    category_tag = _category_to_tag(book.category)
    tags = [category_tag] if category_tag else []

    hero_uri = book.hero_image or ""
    if _is_mockish_preview_uri(hero_uri):
        try:
            manifest_uris = _manifest_gallery_uris(book.slug, limit=1)
            if manifest_uris:
                hero_uri = manifest_uris[0] or hero_uri
        except S3StorageError as e:
            logger.info(f"Manifest not available for slug={book.slug}, keeping DB hero image: {e}")

    return BookSummary(
        slug=book.slug,
        title=book.title,
        subtitle=book.subtitle,
        heroImage=_presigned_get(hero_uri),
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
        galleryImages=_maybe_presign_list(book.gallery_images),
        specs=specs
    )

@router.get("/books/filters", response_model=BookFiltersResponse)
async def get_book_filters(
    db: AsyncSession = Depends(get_db)
):
    """Get available filter options (categories and age ranges) from books"""
    try:
        # Get distinct categories
        categories_query = select(distinct(Book.category))
        categories_result = await db.execute(categories_query)
        category_values = sorted([cat for cat in categories_result.scalars().all() if cat])
        
        categories: List[FilterCategory] = [
            FilterCategory(slug=cat, label=_category_label(cat))
            for cat in category_values
        ]
        
        # Get distinct age ranges
        age_ranges_query = select(distinct(Book.age_range))
        age_ranges_result = await db.execute(age_ranges_query)
        age_range_values = sorted([age for age in age_ranges_result.scalars().all() if age])
        
        # Map age ranges to labels
        age_range_labels = {
            "2-4": "2–4 года",
            "4-6": "4–6 лет",
            "6-8": "6–8 лет",
            "8-10": "8–10 лет",
            "10-12": "10–12 лет",
        }
        
        age_ranges: List[FilterAgeRange] = [
            FilterAgeRange(id=age, label=age_range_labels.get(age, age))
            for age in age_range_values
        ]

        tags = sorted(
            [tag for tag in (_category_to_tag(category) for category in category_values) if tag],
            key=lambda t: t.label.casefold(),
        )

        # Collect distinct years if present in book specs JSON.
        specs_query = select(Book.specs)
        specs_result = await db.execute(specs_query)
        specs_payloads = specs_result.scalars().all()

        def _parse_years(value: object) -> List[int]:
            try:
                if value is None:
                    return []
                if isinstance(value, int):
                    return [value]
                if isinstance(value, str):
                    text = value.strip()
                    if text.isdigit():
                        parsed = int(text)
                        return [parsed]
                    return []
                if isinstance(value, list):
                    years: List[int] = []
                    for item in value:
                        years.extend(_parse_years(item))
                    return years
                return []
            except (ValueError, TypeError, OverflowError) as e:
                logger.warning(f"Error parsing year value: {value}, error: {e}")
                return []

        year_set: Set[int] = set()
        for payload in specs_payloads:
            try:
                if payload is None:
                    continue
                if not isinstance(payload, dict):
                    continue
                years_value = payload.get("years")
                year_value = payload.get("year")
                for year in _parse_years(years_value):
                    year_set.add(year)
                for year in _parse_years(year_value):
                    year_set.add(year)
            except Exception as e:
                logger.warning(f"Error processing specs payload: {payload}, error: {e}")
                continue

        years: List[int] = sorted(year_set)
        
        return BookFiltersResponse(
            categories=categories,
            ageRanges=age_ranges,
            tags=tags,
            years=years,
        )
    except Exception as e:
        logger.error(f"Error in get_book_filters: {type(e).__name__} - {str(e)}", exc_info=True)
        raise

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
        # Умный поиск: разбиваем запрос на слова и ищем по всем полям
        search_terms = [term.strip() for term in search.split() if term.strip()]
        
        if search_terms:
            # Для каждого слова ищем в title, subtitle, description, description_secondary
            # Книга должна содержать ВСЕ слова (AND логика)
            # Используем fuzzy search с пропуском букв
            word_filters = []
            
            for term in search_terms:
                # Сначала пробуем точный поиск (быстрее)
                exact_pattern = f"%{term}%"
                # Затем fuzzy поиск с пропуском букв (медленнее, но более гибкий)
                fuzzy_pattern = _create_fuzzy_pattern(term)
                
                # Используем ilike для точного поиска и ~* для fuzzy поиска
                # PostgreSQL оператор ~* для case-insensitive регулярных выражений
                # Используем Column().op() для создания оператора ~*
                word_search = or_(
                    # Точный поиск (быстрее)
                    Book.title.ilike(exact_pattern),
                    Book.subtitle.ilike(exact_pattern),
                    Book.description.ilike(exact_pattern),
                    Book.description_secondary.ilike(exact_pattern),
                    # Fuzzy search через регулярные выражения PostgreSQL (~* case-insensitive)
                    func.lower(Book.title).op('~*')(fuzzy_pattern),
                    func.lower(Book.subtitle).op('~*')(fuzzy_pattern),
                    func.lower(Book.description).op('~*')(fuzzy_pattern),
                    func.lower(Book.description_secondary).op('~*')(fuzzy_pattern)
                )
                word_filters.append(word_search)
            
            # Все слова должны найтись (AND)
            if word_filters:
                search_filter = and_(*word_filters)
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
        # Storefront keys are stable and used by the frontend to place blocks.
        # Titles/CTA labels are provided for convenience, but frontend may override them.
        "new-arrivals": (None, "Новинки", None),
        "bestsellers": (None, "Бестселлеры", None),
        "boys": (None, "Для мальчиков", None),
        "girls": (None, "Для девочек", None),
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
    
    detail = _book_to_detail(book)

    # If seeded/demo assets are present in DB, prefer real book pages from manifest.
    should_replace_hero = _is_mockish_preview_uri(book.hero_image)
    should_replace_gallery = any(_is_mockish_preview_uri(x) for x in (book.gallery_images or []))
    if should_replace_hero or should_replace_gallery:
        try:
            manifest_uris = _manifest_gallery_uris(slug, limit=6)
            if manifest_uris:
                hero_uri = manifest_uris[0]
                gallery_uris = manifest_uris
                detail = detail.copy(
                    update={
                        "heroImage": _presigned_get(hero_uri),
                        "galleryImages": _maybe_presign_list(gallery_uris),
                    }
                )
        except S3StorageError as e:
            logger.info(f"Manifest not available for slug={slug}, keeping DB gallery images: {e}")

    return detail

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
    
    # Prefer manifest-driven previews (real book pages) over DB seeded/demo previews.
    manifest = None
    try:
        manifest = load_manifest(slug)
    except S3StorageError as e:
        logger.info(f"Manifest not available for slug={slug}, falling back to DB previews: {e}")

    # If personalizationId is provided and user is authenticated, unlock more pages.
    # We resolve this flag once and apply it to either source (manifest or DB).
    unlock_all = False
    
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
            unlock_all = True

    if manifest:
        # Use first N pages from manifest as storefront previews.
        # We intentionally keep this small to avoid leaking full content.
        candidate_pages = sorted(manifest.pages, key=lambda p: p.page_num)
        candidate_pages = [
            p for p in candidate_pages
            if p.base_uri and "/thumbnails/" not in p.base_uri and not _is_mockish_preview_uri(p.base_uri)
        ]
        max_pages = 6
        selected = candidate_pages[:max_pages]

        default_unlocked = min(2, len(selected))
        pages: List[PreviewPage] = []
        for idx, spec in enumerate(selected):
            locked = idx >= default_unlocked
            if unlock_all:
                locked = False
            pages.append(
                PreviewPage(
                    index=idx,
                    imageUrl=_presigned_get(spec.base_uri),
                    locked=locked,
                    caption=None,
                )
            )

        unlocked_count = len(pages) if unlock_all else default_unlocked
        return PreviewResponse(pages=pages, unlockedCount=unlocked_count, totalCount=len(pages))

    # Fallback to DB previews (but remove any mock/demo assets).
    preview_result = await db.execute(
        select(BookPreview)
        .filter(BookPreview.slug == slug)
        .order_by(BookPreview.page_index)
    )
    preview_pages = preview_result.scalars().all()

    preview_pages = [
        p for p in preview_pages
        if p.image_url
        and "/thumbnails/" not in p.image_url
        and not _is_mockish_preview_uri(p.image_url)
    ]

    unlocked_count = sum(1 for p in preview_pages if not p.locked)
    if unlock_all:
        unlocked_count = len(preview_pages)
    
    pages = []
    for i, page in enumerate(preview_pages):
        locked = page.locked
        if (personalizationId and current_user and i < unlocked_count) or unlock_all:
            locked = False
        
        pages.append(PreviewPage(
            index=page.page_index,
            imageUrl=_presigned_get(page.image_url),
            locked=locked,
            caption=page.caption
        ))
    
    return PreviewResponse(
        pages=pages,
        unlockedCount=unlocked_count,
        totalCount=len(preview_pages)
    )

