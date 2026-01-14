"""
Basic API tests for the Face Transfer + WonderWraps API
"""
import pytest
from httpx import AsyncClient
from app.main import app

@pytest.mark.asyncio
async def test_health_check():
    """Test health endpoint"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

@pytest.mark.asyncio
async def test_version():
    """Test version endpoint"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/version")
        assert response.status_code == 200
        assert "version" in response.json()
        assert response.json()["version"] == "1.0.1"

@pytest.mark.asyncio
async def test_signup():
    """Test user registration"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/auth/signup", json={
            "email": "test@example.com",
            "password": "testpassword123",
            "firstName": "Test",
            "lastName": "User"
        })
        # May fail if user already exists, but should be 201 or 409
        assert response.status_code in [201, 409]

@pytest.mark.asyncio
async def test_get_books():
    """Test getting book list"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/books")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "meta" in data
        assert isinstance(data["data"], list)

@pytest.mark.asyncio
async def test_get_book_filters_shape():
    """Test getting book filters returns expected keys"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/books/filters")
        assert response.status_code == 200
        data = response.json()
        assert "categories" in data
        assert "ageRanges" in data
        assert "tags" in data
        assert "years" in data
        assert isinstance(data["categories"], list)
        assert isinstance(data["ageRanges"], list)
        assert isinstance(data["tags"], list)
        assert isinstance(data["years"], list)

@pytest.mark.asyncio
async def test_categories_are_used_as_tags():
    """Ensure tags are derived from categories"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        filters_response = await client.get("/books/filters")
        assert filters_response.status_code == 200
        filters_data = filters_response.json()

        categories = filters_data.get("categories", [])
        tags = filters_data.get("tags", [])

        if not categories:
            pytest.skip("No categories available to validate category tags")

        tag_labels = {
            tag.get("label")
            for tag in tags
            if isinstance(tag, dict) and tag.get("label")
        }

        category_label_by_slug = {
            category.get("slug"): category.get("label")
            for category in categories
            if isinstance(category, dict) and category.get("slug") and category.get("label")
        }

        category_labels = set(category_label_by_slug.values())
        assert tag_labels == category_labels

        for category in categories:
            if not isinstance(category, dict):
                continue
            label = category.get("label")
            if label:
                assert label in tag_labels

        books_response = await client.get("/books")
        assert books_response.status_code == 200
        books_data = books_response.json()
        books = books_data.get("data", [])

        if not books:
            pytest.skip("No books available to validate book tag categories")

        for book in books:
            if not isinstance(book, dict):
                continue
            category_slug = book.get("category")
            if not category_slug:
                continue
            category_label = category_label_by_slug.get(category_slug, category_slug)
            book_tags = book.get("tags", [])
            book_tag_labels = {
                tag.get("label")
                for tag in book_tags
                if isinstance(tag, dict) and tag.get("label")
            }
            assert category_label in book_tag_labels

@pytest.mark.asyncio
async def test_get_book_highlights():
    """Test getting book highlights"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/books/highlights")
        assert response.status_code == 200
        data = response.json()
        assert "sections" in data
        assert isinstance(data["sections"], list)

@pytest.mark.asyncio
async def test_get_illustrations():
    """Test getting illustrations"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/illustrations/")
        assert response.status_code == 200
        data = response.json()
        assert "illustrations" in data
        assert isinstance(data["illustrations"], list)

@pytest.mark.asyncio
async def test_cart_requires_auth():
    """Test that cart endpoint requires authentication"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/cart")
        assert response.status_code == 403  # Forbidden without auth

@pytest.mark.asyncio
async def test_orders_requires_auth():
    """Test that orders endpoint requires authentication"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/orders")
        assert response.status_code == 403  # Forbidden without auth

@pytest.mark.asyncio
async def test_profile_requires_auth():
    """Test that profile endpoint requires authentication"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/account/profile")
        assert response.status_code == 403  # Forbidden without auth

