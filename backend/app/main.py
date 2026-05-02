"""
V-Watch Backend - FastAPI Application Entry Point
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .core.config import settings
from .core.database import create_tables
from .core.security import PasswordHasher
from .models.user import User, UserRole
from .api import auth, violations, users, config_api, live_monitoring

logger = logging.getLogger(__name__)


async def create_default_admin(db):
    """Create default admin user if none exists."""
    from sqlalchemy import select
    result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
    existing = result.scalar_one_or_none()
    if not existing:
        hasher = PasswordHasher()
        admin = User(
            email="admin@vwatch.gov",
            username="admin",
            full_name="System Administrator",
            hashed_password=hasher.hash("Admin@123!"),
            role=UserRole.ADMIN,
            is_active=True,
            is_superuser=True,
        )
        db.add(admin)
        await db.commit()
        logger.info("✅ Default admin created: admin / Admin@123!")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    logger.info("🚀 V-Watch Backend starting...")
    try:
        await create_tables()
        logger.info("✅ Database tables created/verified")

        # Create default admin
        from .core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await create_default_admin(db)
    except Exception as e:
        logger.warning(f"⚠️  Database setup failed (may need PostgreSQL): {e}")

    # Create upload directory
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    logger.info("✅ V-Watch Backend ready")

    yield

    logger.info("🛑 V-Watch Backend shutting down...")


# ─── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
    **V-Watch Traffic Violation Management API**

    Centralized backend for AI-powered traffic violation detection.

    ## Features
    * JWT Authentication with RBAC
    * Violation submission from Edge AI
    * Human verification workflow
    * Evidence integrity verification (SHA-256)
    * Live monitoring with WebSocket
    * Notification system
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ─── Exception Handlers ───────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# ─── Routes ───────────────────────────────────────────────────────────────────

API_PREFIX = settings.API_V1_PREFIX

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(violations.router, prefix=API_PREFIX)
app.include_router(users.router, prefix=API_PREFIX)
app.include_router(config_api.router, prefix=API_PREFIX)
app.include_router(live_monitoring.router, prefix=API_PREFIX)

# Static files for uploaded evidence
uploads_path = Path(settings.UPLOAD_DIR)
uploads_path.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "name": settings.APP_NAME,
    }


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "V-Watch Traffic Management API",
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }
