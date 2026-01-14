"""
Seed database with sample data for testing
"""
import asyncio
import argparse
from uuid import uuid4
from typing import Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from .db import engine
from .models import Base, Book, BookPreview

# Public CDN images used for demo seed data.
# These are intentionally "real" product/marketing images, not internal mock illustration keys.
DEMO_IMAGE_URLS: list[str] = [
    "https://storage.wonderwraps.com/f625aba1-11ef-4c83-a4e0-4acd63772684/responsive-images/O6gYu8CZz0GztoEMFRPmWAVJ4oZ0jN-metaRm9yZ290dGVuIFJvYm90LnBuZw%3D%3D-___media_library_original_600_600.png",
    "https://storage.wonderwraps.com/c9a98b73-57ec-4574-95e6-174a0a6f1c77/responsive-images/hmxC4ET2J65TO9is4yNxuPGhZWwQ3R-metaa2luZGVyZ2FydGVuIGZpbmFsLnBuZw%3D%3D-___media_library_original_600_600.png",
    "https://storage.wonderwraps.com/acbfeb55-1798-48d3-8841-2caaf05ac552/responsive-images/bDvN5yynm7Fe38AA6mPxaeceeHD6ha-metaQ09WRVIucG5n-___media_library_original_600_600.png",
    "https://storage.wonderwraps.com/73f6ba12-5600-4637-b3ad-4ff32f2e2252/responsive-images/RwON2apwtuxltG8tXSpnG7AdpGdgUZ-metaSGFycGVycy5wbmc%3D-___media_library_original_600_600.png",
    "https://storage.wonderwraps.com/7b750645-1241-42be-9634-1421687fde5b/responsive-images/8ZKH01wuBIj3lqVA1FpFJTwXRDscdB-metaMC5wbmc%3D-___media_library_original_600_600.png",
    "https://storage.wonderwraps.com/6fbe63dc-48c5-4a13-b6cc-62076f2493ff/responsive-images/PJihL5UxegfMRSNfNj39Dsl1kZ6LS0-metaY292ZXIucG5n-___media_library_original_600_600.png",
]

def _resolve_s3_hero_and_gallery(slug: str) -> Tuple[str, list[str]]:
    """
    Prefer real template assets from S3.

    Strategy:
    - Try to load `templates/{slug}/manifest.json` and use the earliest pages as hero/gallery.
    - If manifest is not available, fall back to stable S3-relative keys.
      (They will be presigned by the API using configured `S3_BUCKET_NAME`.)
    """
    try:
        # Optional dependency: `boto3` is required by manifest_store.
        # Seeder should still work in minimal environments without boto3.
        from .book.manifest_store import load_manifest  # noqa: WPS433
        from .exceptions import S3StorageError  # noqa: WPS433

        manifest = load_manifest(slug)
        candidates = sorted(manifest.pages, key=lambda p: p.page_num)
        uris = [p.base_uri for p in candidates if p.base_uri and "/thumbnails/" not in p.base_uri]
        if uris:
            hero = uris[0]
            gallery = uris[:6]
            return hero, gallery
    except ModuleNotFoundError:
        # boto3 not installed (or other optional deps) – fall back to template keys.
        pass
    except Exception as e:
        # If manifest_store is available, it raises S3StorageError for S3/manifest issues.
        # We intentionally ignore it here and fall back to template keys.
        if e.__class__.__name__ == "S3StorageError":
            pass
    except Exception:
        # Seeder should not fail because of optional assets.
        pass

    # Conservative fallback: keep everything inside the template folder.
    hero_fallback = f"templates/{slug}/cover.png"
    gallery_fallback = [
        hero_fallback,
        f"templates/{slug}/front_cover.png",
        f"templates/{slug}/back_cover.png",
    ]
    return hero_fallback, gallery_fallback

def _story_previews() -> list[dict]:
    """
    A small storefront preview (captions) extracted from the provided story text.
    Image URLs are intentionally generic and public.
    """
    preview_image = "https://images.unsplash.com/photo-1519681393784-d120267933ba?auto=format&fit=crop&w=1200&q=80"
    pages: list[dict] = [
        {
            "page_index": 0,
            "caption": (
                "Сказка “Алина и фонарик доброты” о том, что добро — это не великие подвиги, "
                "а маленькие поступки, которые делают жизнь вокруг чуть лучше."
            ),
        },
        {
            "page_index": 1,
            "caption": (
                "Вечером, как всегда, Алина лежала в своей постели, а мама читала ей сказку перед сном... "
                "Засыпая, Алина задумалась: а как это — делать добро?"
            ),
        },
        {
            "page_index": 3,
            "caption": (
                "— Мам, расскажи, что значит делать добро?\n"
                "— Это значит сделать что-то полезное и хорошее, помочь кому-то. "
                "Добро — как фонарик: где оно есть, там светлее."
            ),
        },
        {
            "page_index": 5,
            "caption": (
                "Алина вышла во двор... У детской площадки стоял Гоша и грустно смотрел на свой шлем — "
                "он никак не мог застегнуть его."
            ),
        },
        {
            "page_index": 9,
            "caption": (
                "Алина заметила на дорожке толстую палку... Девочка стащила палку в сторону и положила у клумбы, "
                "чтобы никто не споткнулся."
            ),
        },
        {
            "page_index": 21,
            "caption": (
                "— Именно это и есть подвиги. Ты помогла людям рядом, а твой фонарик доброты сегодня светил целый день."
            ),
        },
    ]
    return [{"image_url": preview_image, "locked": False, **p} for p in pages]


async def seed_books_and_previews() -> None:
    """Seed a single real book used by the storefront."""
    hero_image, gallery_images = _resolve_s3_hero_and_gallery("magical-princess-story")
    book_data = {
        "slug": "magical-princess-story",
        "title": "Алина и фонарик Доброты",
        "subtitle": "Сказка о маленьких добрых поступках",
        "description": (
            "Добро — это не великие подвиги, а маленькие поступки, которые делают жизнь вокруг чуть лучше. "
            "Эта история учит замечать чужие трудности и помогать, даже если помощь кажется пустяковой."
        ),
        "description_secondary": (
            "У каждого есть свой «фонарик доброты», и он загорается, когда мы выбираем быть внимательными "
            "и добрыми к людям рядом."
        ),
        "hero_image": hero_image,
        "gallery_images": gallery_images,
        "bullets": [
            "Тёплая история для чтения перед сном",
            "Помогает развивать эмпатию и внимательность",
            "Подходит для совместного чтения и обсуждения",
        ],
        "age_range": "4-6",
        "category": "girl",
        "price_amount": 34.99,
        "price_currency": "USD",
        "compare_at_price_amount": 44.99,
        "compare_at_price_currency": "USD",
        "discount_percent": 22.0,
        "specs": {
            "idealFor": "Для чтения с родителями",
            "ageRange": "4–6 лет",
            "characters": "Алина",
            "genre": "Сказка",
            "pages": "24+ страниц",
            "shipping": "Печать и доставка по заказу",
        },
    }

    async with AsyncSession(engine) as session:
        session.add(Book(**book_data))
        for p in _story_previews():
            session.add(
                BookPreview(
                    id=str(uuid4()),
                    slug=book_data["slug"],
                    page_index=int(p["page_index"]),
                    image_url=str(p["image_url"]),
                    locked=bool(p["locked"]),
                    caption=str(p["caption"]) if p.get("caption") is not None else None,
                )
            )
        await session.commit()
        print("✅ Seeded 1 book + previews successfully")


async def reset_database() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed WonderWraps database")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop all tables before seeding (DESTRUCTIVE).",
    )
    return parser.parse_args()


async def main() -> None:
    print("🌱 Seeding database...")
    args = _parse_args()

    if args.drop:
        print("🧨 Dropping all tables...")
        await reset_database()
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    await seed_books_and_previews()
    print("✅ Database seeding complete!")

if __name__ == "__main__":
    asyncio.run(main())

