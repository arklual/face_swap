import os
import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

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

# Import routers
from .routes import auth, catalog, personalizations, cart, orders, account

APP_DESCRIPTION = """
Скомбинированная OpenAPI-спека двух подсистем:ыфы
1) Face Transfer API — перенос лица ребёнка на иллюстрации.
2) WonderWraps API — SPA-бэкенд для каталога книг, персонализаций, корзины и заказов.
"""

TAGS_METADATA = [
    {"name": "Health", "description": "Проверка состояния сервиса и версии"},
    {"name": "Auth", "description": "Методы аутентификации клиентов и управления сессией"},
    {"name": "Catalog", "description": "Каталог книг и рекламные подборки"},
    {"name": "Personalizations", "description": "Генерация персонализированной книги"},
    {"name": "Cart", "description": "Операции с корзиной"},
    {"name": "Orders", "description": "История заказов и их деталей"},
    {"name": "Account", "description": "Профиль, предпочтения и сохранённые книги"},
]

SWAGGER_SERVER_URL = os.environ.get("SWAGGER_SERVER_URL") or os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000"

app = FastAPI(
    title="Face Transfer + WonderWraps API",
    version="1.0.1",
    description=APP_DESCRIPTION.strip(),
    openapi_tags=TAGS_METADATA,
    servers=[
        {
            "url": SWAGGER_SERVER_URL,
            "description": "Локальная / dev среда (docker compose)",
        }
    ],
)

app.add_exception_handler(FaceAppBaseException, faceapp_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # We use Bearer tokens (Authorization header), not cookies.
    # Browsers can reject `Access-Control-Allow-Origin: *` when `Allow-Credentials: true`,
    # which then surfaces as a generic "network error" on the frontend.
    allow_credentials=False,
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


def custom_openapi():
    """
    Enrich generated OpenAPI schema with custom metadata so Swagger UI
    matches the merged specification shared in the docs.
    """
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    openapi_schema["servers"] = jsonable_encoder(app.servers)
    components = openapi_schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    bearer_auth = security_schemes.get("bearerAuth") or {}
    bearer_auth.update({
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "JWT авторизация, используемая для всех защищённых методов.",
    })
    security_schemes["bearerAuth"] = bearer_auth
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi