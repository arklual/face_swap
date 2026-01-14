from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from .config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
# Important for AsyncSession:
# expire_on_commit=True can trigger implicit lazy-load IO on attribute access after commit,
# which in async context may raise sqlalchemy.exc.MissingGreenlet.
AsyncSessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession,
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session