import pytest
from httpx import AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_regenerate_page_with_photo_requires_file():
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/regenerate/test-job/page/1/with-photo")
        assert response.status_code == 422
