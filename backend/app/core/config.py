"""
V-Watch Backend - Application Configuration
"""

from pydantic_settings import BaseSettings
from typing import List, Optional
import secrets


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "V-Watch Traffic Management System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # Security
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://vwatch:vwatch_pass@localhost:5432/vwatch_db"
    DATABASE_URL_SYNC: str = "postgresql://vwatch:vwatch_pass@localhost:5432/vwatch_db"

    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://vwatch.example.com",
    ]

    # File Storage
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_IMAGE_TYPES: List[str] = ["image/jpeg", "image/png", "image/webp"]
    ALLOWED_VIDEO_TYPES: List[str] = ["video/mp4", "video/avi", "video/x-matroska"]

    # Notifications
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMS_PROVIDER: str = "mock"
    SMS_API_KEY: str = ""

    # Fine System
    DEFAULT_SPEEDING_FINE: float = 200.0
    DEFAULT_REDLIGHT_FINE: float = 500.0
    DEFAULT_WRONGDIR_FINE: float = 300.0
    DEFAULT_LANE_FINE: float = 150.0

    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
