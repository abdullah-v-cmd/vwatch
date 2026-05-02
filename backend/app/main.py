"""
V-Watch Backend - FastAPI Application Entry Point
"""

import logging
import asyncio
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
from .api import auth, violations, users, config_api, live_monitoring, yolo_analysis
from .api import camera_stream
from .services.camera_manager import camera_manager

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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("🚀 V-Watch Backend starting...")

    # ── Database ─────────────────────────────────────────────────────────────
    try:
        await create_tables()
        logger.info("✅ Database tables created/verified")
        from .core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await create_default_admin(db)
    except Exception as e:
        logger.warning(f"⚠️  Database setup failed (may need PostgreSQL): {e}")

    # ── Upload directories ────────────────────────────────────────────────────
    upload_path = Path(settings.UPLOAD_DIR)
    upload_path.mkdir(parents=True, exist_ok=True)
    (upload_path / "yolo_temp").mkdir(parents=True, exist_ok=True)
    logger.info(f"✅ Upload dir ready: {upload_path.resolve()}")

    # ── Persistent Camera Manager ─────────────────────────────────────────────
    # Give the camera manager a reference to the running event loop so it can
    # schedule async broadcasts from background threads.
    loop = asyncio.get_event_loop()
    camera_manager.set_event_loop(loop)

    # Wire up the WebSocket broadcast callback (avoids circular imports)
    async def _broadcast_violation(event: dict):
        await live_monitoring.manager.broadcast(event)

    camera_manager.set_broadcast_callback(_broadcast_violation)

    # Auto-start any cameras that were pre-configured
    camera_manager.start_all()
    logger.info("✅ Camera Manager started")
    logger.info("✅ V-Watch Backend ready")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("🛑 V-Watch Backend shutting down...")
    camera_manager.stop_all()
    logger.info("✅ All cameras stopped")


# ─── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
**V-Watch Traffic Violation Management API**

Centralized backend for AI-powered traffic violation detection.

## Features
* JWT Authentication with RBAC
* **Persistent Backend Camera System** — cameras run independently of frontend
* **Multi-Camera Support** — each camera has its own thread, stream, and MJPEG endpoint
* **MJPEG Streaming** — `GET /api/v1/cameras/stream/{camera_id}` (embeddable in `<img>`)
* **WebSocket per camera** — `WS /api/v1/cameras/ws/{camera_id}` for base64 frames
* Live violation WebSocket broadcast — `WS /api/v1/live/ws`
* Violation submission from Edge AI (no auth required for POST /violations)
* Human verification workflow (approve / reject)
* Evidence integrity verification (SHA-256)
* YOLO model status & admin video analysis
""",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(GZipMiddleware, minimum_size=1000)

_origins = settings.ALLOWED_ORIGINS
_allow_all = "*" in _origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _origins,
    allow_credentials=False if _allow_all else True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
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
app.include_router(yolo_analysis.router, prefix=API_PREFIX)
app.include_router(camera_stream.router, prefix=API_PREFIX)   # ← new persistent stream

# Static files for uploaded evidence
uploads_path = Path(settings.UPLOAD_DIR)
uploads_path.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")


# ─── Health / Root ────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health_check():
    cam_summary = camera_manager.status_summary()
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "name": settings.APP_NAME,
        "cameras": {
            "total": cam_summary["total_cameras"],
            "running": cam_summary["running"],
        },
    }


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "V-Watch Traffic Management API",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "camera_streams": "/api/v1/cameras/stream/{camera_id}",
    }
