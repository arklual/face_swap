from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from .config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=AsyncSession)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session