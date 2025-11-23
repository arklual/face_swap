import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://user:password@db/dbname"

    AWS_ENDPOINT_URL: str
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    AWS_REGION_NAME: str = "ru-1"
    S3_BUCKET_NAME: str

    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    HF_HOME: str = "/models/hf"
    QWEN_MODEL_ID: str = "Qwen/Qwen2-VL-2B-Instruct"

    COMFY_BASE_URL: str = "http://127.0.0.1:8188"
    IPADAPTER_STRENGTH_SCALE: float = 1.0

    JWT_SECRET_KEY: str = "8KRTRfzwYFFr1I974x6BtWsZSULD9t416UKKkJZGd7DJ3AAtTpQbsf3z1h877joQ"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    class Config:
        env_file = ".env"

settings = Settings()
