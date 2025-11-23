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

