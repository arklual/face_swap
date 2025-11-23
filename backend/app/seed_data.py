"""
Seed database with sample data for testing
"""
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from .db import engine
from .models import Base, Book, BookPreview

async def seed_books():
    """Seed sample books"""
    books = [
        {
            "slug": "adventure-time-with-alex",
            "title": "Adventure Time with Alex",
            "subtitle": "Join Alex on an epic journey",
            "description": "A personalized adventure story where your child becomes the hero of their own tale.",
            "description_secondary": "Perfect for bedtime reading and inspiring imagination.",
            "hero_image": "https://example.com/images/adventure-hero.jpg",
            "gallery_images": [
                "https://example.com/images/adventure-1.jpg",
                "https://example.com/images/adventure-2.jpg",
                "https://example.com/images/adventure-3.jpg"
            ],
            "bullets": [
                "Personalized with your child's name and photo",
                "Beautiful full-color illustrations",
                "Hardcover edition with premium paper",
                "Perfect for ages 4-8"
            ],
            "age_range": "4-6",
            "category": "boy",
            "price_amount": 34.99,
            "price_currency": "USD",
            "compare_at_price_amount": 44.99,
            "compare_at_price_currency": "USD",
            "discount_percent": 22.0,
            "tags": [
                {"label": "Bestseller", "tone": "accent"},
                {"label": "New", "tone": "brand"}
            ],
            "specs": {
                "idealFor": "Boys 4-8",
                "ageRange": "4-8 years",
                "characters": "Your child as the hero",
                "genre": "Adventure",
                "pages": "32 pages",
                "shipping": "Ships within 5-7 business days"
            }
        },
        {
            "slug": "magical-princess-story",
            "title": "Magical Princess Story",
            "subtitle": "A royal adventure awaits",
            "description": "Your little princess becomes the star of this enchanting fairy tale adventure.",
            "description_secondary": "Beautifully illustrated with stunning watercolor artwork.",
            "hero_image": "https://example.com/images/princess-hero.jpg",
            "gallery_images": [
                "https://example.com/images/princess-1.jpg",
                "https://example.com/images/princess-2.jpg"
            ],
            "bullets": [
                "Personalized with your child's name and photo",
                "Enchanting watercolor illustrations",
                "Hardcover with gilded edges",
                "Perfect for ages 3-7"
            ],
            "age_range": "4-6",
            "category": "girl",
            "price_amount": 34.99,
            "price_currency": "USD",
            "compare_at_price_amount": None,
            "compare_at_price_currency": None,
            "discount_percent": None,
            "tags": [
                {"label": "Popular", "tone": "soft"}
            ],
            "specs": {
                "idealFor": "Girls 3-7",
                "ageRange": "3-7 years",
                "characters": "Your child as the princess",
                "genre": "Fantasy",
                "pages": "28 pages",
                "shipping": "Ships within 5-7 business days"
            }
        },
        {
            "slug": "holiday-magic-book",
            "title": "Holiday Magic",
            "subtitle": "A festive celebration",
            "description": "Your child joins Santa on a magical holiday adventure filled with wonder and joy.",
            "description_secondary": None,
            "hero_image": "https://example.com/images/holiday-hero.jpg",
            "gallery_images": [],
            "bullets": [
                "Personalized holiday story",
                "Festive illustrations",
                "Perfect holiday gift"
            ],
            "age_range": "2-4",
            "category": "holiday",
            "price_amount": 29.99,
            "price_currency": "USD",
            "compare_at_price_amount": 39.99,
            "compare_at_price_currency": "USD",
            "discount_percent": 25.0,
            "tags": [
                {"label": "Holiday Special", "tone": "accent"}
            ],
            "specs": {
                "idealFor": "All children 2-8",
                "ageRange": "2-8 years",
                "characters": "Your child with Santa",
                "genre": "Holiday",
                "pages": "24 pages",
                "shipping": "Express shipping available"
            }
        }
    ]
    
    async with AsyncSession(engine) as session:
        for book_data in books:
            book = Book(**book_data)
            session.add(book)
            
            # Add some preview pages for each book
            for i in range(6):
                preview = BookPreview(
                    id=f"{book_data['slug']}-preview-{i}",
                    slug=book_data['slug'],
                    page_index=i,
                    image_url=f"https://example.com/previews/{book_data['slug']}/page-{i}.jpg",
                    locked=(i > 2),  # First 3 pages unlocked
                    caption=f"Page {i + 1}" if i == 0 else None
                )
                session.add(preview)
        
        await session.commit()
        print("✅ Sample books seeded successfully")

async def main():
    """Main seed function"""
    print("🌱 Seeding database...")
    
    # Create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Seed data
    await seed_books()
    
    print("✅ Database seeding complete!")

if __name__ == "__main__":
    asyncio.run(main())

