from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Union
import traceback
from .logger import logger


class FaceAppBaseException(Exception):
    """Base exception for face transfer application"""
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", status_code: int = 500):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(self.message)


class PhotoAnalysisError(FaceAppBaseException):
    """Raised when photo analysis fails"""
    def __init__(self, message: str = "Failed to analyze photo"):
        super().__init__(message, "PHOTO_ANALYSIS_ERROR", 500)


class FaceTransferError(FaceAppBaseException):
    """Raised when face transfer fails"""
    def __init__(self, message: str = "Failed to transfer face"):
        super().__init__(message, "FACE_TRANSFER_ERROR", 500)


class S3StorageError(FaceAppBaseException):
    """Raised when S3 operations fail"""
    def __init__(self, message: str = "S3 storage operation failed"):
        super().__init__(message, "S3_STORAGE_ERROR", 502)


class JobNotFoundError(FaceAppBaseException):
    """Raised when job is not found"""
    def __init__(self, job_id: str):
        super().__init__(f"Job {job_id} not found", "JOB_NOT_FOUND", 404)


class InvalidJobStateError(FaceAppBaseException):
    """Raised when job is in invalid state for operation"""
    def __init__(self, job_id: str, current_state: str, expected_state: str):
        super().__init__(
            f"Job {job_id} is in state '{current_state}', expected '{expected_state}'",
            "INVALID_JOB_STATE",
            400,
        )


async def faceapp_exception_handler(request: Request, exc: FaceAppBaseException):
    """Handle custom application exceptions"""
    logger.error(
        f"Application exception: {exc.code} - {exc.message}",
        extra={
            "error_code": exc.code,
            "error_message": exc.message,
            "request_path": request.url.path,
        }
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.code,
            "message": exc.message,
            "status_code": exc.status_code,
        }
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    logger.warning(
        f"HTTP {exc.status_code}: {exc.detail}",
        extra={
            "http_status_code": exc.status_code,
            "http_detail": exc.detail,
            "request_path": request.url.path,
        }
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTP_ERROR",
            "message": exc.detail,
            "status_code": exc.status_code,
        }
    )


async def generic_exception_handler(request: Request, exc: Exception):
    """Handle all other exceptions"""
    logger.error(
        f"Unhandled exception: {type(exc).__name__} - {str(exc)}",
        extra={
            "exc_type": type(exc).__name__,
            "exc_message": str(exc),
            "request_path": request.url.path,
            "exc_traceback": traceback.format_exc(),
        }
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An internal error occurred. Please try again later.",
        }
    )

