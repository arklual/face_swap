from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import time
from .config import settings
from .db import engine
from .models import Base
from .logger import logger
from .exceptions import (
    FaceAppBaseException,
    faceapp_exception_handler,
    http_exception_handler,
    generic_exception_handler,
)
from fastapi import HTTPException

# Import routers
from .routes import auth, catalog, personalizations, cart, orders, account

app = FastAPI(
    title="Face Transfer + WonderWraps API",
    version="1.0.1",
    description="""
    Скомбинированная OpenAPI-спека двух подсистем:
    1) Face Transfer API — перенос лица ребёнка на иллюстрации.
    2) WonderWraps API — SPA-бэкенд для каталога книг, персонализаций, корзины и заказов.
    """,
    servers=[
        {"url": "https://api.wonderwraps.test", "description": "Локальная / dev среда"},
        {"url": "https://api.wonderwraps.com", "description": "Продакшн"}
    ]
)

app.add_exception_handler(FaceAppBaseException, faceapp_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    logger.info(
        f"Request: {request.method} {request.url.path}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
        }
    )
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    logger.info(
        f"Response: {response.status_code}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration * 1000, 2),
        }
    )
    
    return response

@app.on_event("startup")
async def startup():
    logger.info("Starting Face Transfer + WonderWraps API")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

@app.on_event("shutdown")
async def shutdown():
    logger.info("Shutting down Face Transfer + WonderWraps API")

# Health and version endpoints
@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}

@app.get("/version", tags=["Health"])
async def get_version():
    """Get API version"""
    return {"version": "1.0.1"}

# Include routers
app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(personalizations.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(account.router)