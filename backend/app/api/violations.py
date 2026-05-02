"""
V-Watch Backend - Violations API Routes
"""

import os
import hashlib
import uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import (
    APIRouter, Depends, HTTPException, status, UploadFile, File,
    Query, Request, BackgroundTasks
)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, and_, or_, desc, text
from sqlalchemy.orm import selectinload

from ..core.database import get_db
from ..core.dependencies import get_current_user, require_police
from ..core.config import settings
from ..models.user import User, UserRole
from ..models.violation import Violation, ViolationType, ViolationStatus, AuditLog, Vehicle
from ..schemas.violation import (
    ViolationCreate, ViolationReview, ViolationResponse,
    ViolationListResponse, ViolationStats
)
from ..services.notification import NotificationService

router = APIRouter(prefix="/violations", tags=["Violations"])
notifier = NotificationService()

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ─── Submit violation (Edge AI – no auth) ────────────────────────────────────

@router.post("", response_model=ViolationResponse, status_code=201)
async def create_violation(
    violation_data: ViolationCreate,
    db: AsyncSession = Depends(get_db),
):
    """Submit a new traffic violation (called by edge AI – no auth required)."""
    existing = await db.execute(
        select(Violation).where(Violation.evidence_id == violation_data.evidence_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Duplicate evidence_id")

    fine_map = {
        ViolationType.SPEEDING: settings.DEFAULT_SPEEDING_FINE,
        ViolationType.RED_LIGHT: settings.DEFAULT_REDLIGHT_FINE,
        ViolationType.WRONG_DIRECTION: settings.DEFAULT_WRONGDIR_FINE,
        ViolationType.LANE_VIOLATION: settings.DEFAULT_LANE_FINE,
    }

    violation = Violation(
        evidence_id=violation_data.evidence_id,
        vehicle_id=violation_data.vehicle_id,
        plate_number=violation_data.plate_number.upper(),
        vehicle_type=violation_data.vehicle_type,
        violation_type=violation_data.violation_type,
        status=ViolationStatus.PENDING,
        speed_recorded=violation_data.speed_recorded,
        speed_limit=violation_data.speed_limit,
        location=violation_data.location,
        camera_id=violation_data.camera_id,
        violation_time=violation_data.violation_time,
        confidence=violation_data.confidence,
        frame_sha256=violation_data.frame_sha256,
        plate_sha256=violation_data.plate_sha256,
        video_sha256=violation_data.video_sha256,
        metadata_sha256=violation_data.metadata_sha256,
        fine_amount=fine_map.get(violation_data.violation_type, 200.0),
        extra_data=violation_data.extra_data,
    )

    db.add(violation)
    await db.commit()
    await db.refresh(violation)

    # Broadcast to live monitoring WebSocket clients
    try:
        from .live_monitoring import manager
        await manager.broadcast({
            "type": "violation",
            "data": {
                "id": violation.id,
                "camera_id": violation.camera_id,
                "violation_type": violation.violation_type.value,
                "plate_number": violation.plate_number,
                "confidence": violation.confidence,
                "speed": violation.speed_recorded,
                "timestamp": violation.violation_time.isoformat() if violation.violation_time else None,
                "location": violation.location,
                "status": violation.status.value,
            },
        })
    except Exception:
        pass  # Don't fail if WebSocket broadcast fails

    return violation


# ─── Stats (must be BEFORE /{violation_id}) ───────────────────────────────────

@router.get("/stats", response_model=ViolationStats)
async def get_stats(
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Get violation statistics for dashboard including weekly chart data."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    total = (await db.execute(select(func.count(Violation.id)))).scalar() or 0
    pending = (await db.execute(select(func.count(Violation.id)).where(Violation.status == ViolationStatus.PENDING))).scalar() or 0
    approved = (await db.execute(select(func.count(Violation.id)).where(Violation.status == ViolationStatus.APPROVED))).scalar() or 0
    rejected = (await db.execute(select(func.count(Violation.id)).where(Violation.status == ViolationStatus.REJECTED))).scalar() or 0
    paid = (await db.execute(select(func.count(Violation.id)).where(Violation.status == ViolationStatus.PAID))).scalar() or 0
    today = (await db.execute(select(func.count(Violation.id)).where(Violation.violation_time >= today_start))).scalar() or 0

    fines_result = await db.execute(
        select(func.sum(Violation.fine_amount)).where(
            and_(Violation.fine_paid == True, Violation.fine_amount != None)
        )
    )
    total_fines = fines_result.scalar() or 0.0

    # By type
    type_result = await db.execute(
        select(Violation.violation_type, func.count(Violation.id))
        .group_by(Violation.violation_type)
    )
    violations_by_type = {str(row[0].value): row[1] for row in type_result}

    # --- Weekly violations (last 7 days) ---
    violations_by_day = []
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i in range(6, -1, -1):
        day_start = (today_start - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        day_count = (await db.execute(
            select(func.count(Violation.id)).where(
                and_(
                    Violation.violation_time >= day_start,
                    Violation.violation_time < day_end,
                )
            )
        )).scalar() or 0
        violations_by_day.append({
            "date": day_names[day_start.weekday()],
            "count": day_count,
            "full_date": day_start.strftime("%Y-%m-%d"),
        })

    return ViolationStats(
        total_violations=total,
        pending_count=pending,
        approved_count=approved,
        rejected_count=rejected,
        paid_count=paid,
        today_count=today,
        total_fines_collected=total_fines,
        violations_by_type=violations_by_type,
        violations_by_day=violations_by_day,
    )


# ─── Manual create (admin test – must be BEFORE /{violation_id}) ──────────────

@router.post("/manual", response_model=ViolationResponse, status_code=201)
async def create_violation_manual(
    violation_data: ViolationCreate,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Manually create a violation record (for admin testing)."""
    existing = await db.execute(
        select(Violation).where(Violation.evidence_id == violation_data.evidence_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Duplicate evidence_id")

    fine_map = {
        ViolationType.SPEEDING: settings.DEFAULT_SPEEDING_FINE,
        ViolationType.RED_LIGHT: settings.DEFAULT_REDLIGHT_FINE,
        ViolationType.WRONG_DIRECTION: settings.DEFAULT_WRONGDIR_FINE,
        ViolationType.LANE_VIOLATION: settings.DEFAULT_LANE_FINE,
    }

    violation = Violation(
        evidence_id=violation_data.evidence_id,
        vehicle_id=violation_data.vehicle_id,
        plate_number=violation_data.plate_number.upper(),
        vehicle_type=violation_data.vehicle_type,
        violation_type=violation_data.violation_type,
        status=ViolationStatus.PENDING,
        speed_recorded=violation_data.speed_recorded,
        speed_limit=violation_data.speed_limit,
        location=violation_data.location,
        camera_id=violation_data.camera_id,
        violation_time=violation_data.violation_time,
        confidence=violation_data.confidence,
        frame_sha256=violation_data.frame_sha256,
        plate_sha256=violation_data.plate_sha256,
        video_sha256=violation_data.video_sha256,
        metadata_sha256=violation_data.metadata_sha256,
        fine_amount=fine_map.get(violation_data.violation_type, 200.0),
        extra_data=violation_data.extra_data,
    )

    db.add(violation)
    await db.commit()
    await db.refresh(violation)

    # Broadcast to WebSocket clients
    try:
        from .live_monitoring import manager
        await manager.broadcast({
            "type": "violation",
            "data": {
                "id": violation.id,
                "camera_id": violation.camera_id,
                "violation_type": violation.violation_type.value,
                "plate_number": violation.plate_number,
                "confidence": violation.confidence,
                "timestamp": violation.violation_time.isoformat() if violation.violation_time else None,
                "location": violation.location,
                "status": violation.status.value,
            },
        })
    except Exception:
        pass

    return violation


# ─── List violations ──────────────────────────────────────────────────────────

@router.get("", response_model=ViolationListResponse)
async def list_violations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[ViolationStatus] = None,
    violation_type: Optional[ViolationType] = None,
    plate_number: Optional[str] = None,
    camera_id: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    search: Optional[str] = None,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """List violations with filters and pagination."""
    query = select(Violation)
    count_query = select(func.count(Violation.id))

    filters = []
    if status:
        filters.append(Violation.status == status)
    if violation_type:
        filters.append(Violation.violation_type == violation_type)
    if plate_number:
        filters.append(Violation.plate_number.ilike(f"%{plate_number}%"))
    if camera_id:
        filters.append(Violation.camera_id == camera_id)
    if date_from:
        filters.append(Violation.violation_time >= date_from)
    if date_to:
        filters.append(Violation.violation_time <= date_to)
    if search:
        filters.append(
            or_(
                Violation.plate_number.ilike(f"%{search}%"),
                Violation.location.ilike(f"%{search}%"),
                Violation.vehicle_id.ilike(f"%{search}%"),
            )
        )

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    offset = (page - 1) * page_size
    query = query.order_by(desc(Violation.violation_time)).offset(offset).limit(page_size)
    result = await db.execute(query)
    violations = result.scalars().all()

    return ViolationListResponse(
        items=[ViolationResponse.model_validate(v) for v in violations],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


# ─── Single violation (AFTER static routes) ───────────────────────────────────

@router.get("/{violation_id}", response_model=ViolationResponse)
async def get_violation(
    violation_id: int,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Get a single violation by ID."""
    result = await db.execute(select(Violation).where(Violation.id == violation_id))
    violation = result.scalar_one_or_none()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")
    return violation


@router.post("/{violation_id}/approve", response_model=ViolationResponse)
async def approve_violation(
    violation_id: int,
    review: ViolationReview,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending violation."""
    result = await db.execute(select(Violation).where(Violation.id == violation_id))
    violation = result.scalar_one_or_none()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")
    if violation.status != ViolationStatus.PENDING:
        raise HTTPException(status_code=400, detail="Violation is not in pending status")

    violation.status = ViolationStatus.APPROVED
    violation.reviewer_id = current_user.id
    violation.reviewer_remarks = review.remarks
    violation.reviewed_at = datetime.now(timezone.utc)
    if review.fine_amount:
        violation.fine_amount = review.fine_amount

    audit = AuditLog(
        user_id=current_user.id,
        action="APPROVE_VIOLATION",
        resource_type="violation",
        resource_id=str(violation_id),
        details={"remarks": review.remarks},
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(violation)

    background_tasks.add_task(
        notifier.notify_violation_approved,
        violation=violation.__dict__,
        owner_email=None,
        owner_phone=None,
    )

    return violation


@router.post("/{violation_id}/reject", response_model=ViolationResponse)
async def reject_violation(
    violation_id: int,
    review: ViolationReview,
    request: Request,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Reject a pending violation."""
    result = await db.execute(select(Violation).where(Violation.id == violation_id))
    violation = result.scalar_one_or_none()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")
    if violation.status not in (ViolationStatus.PENDING, ViolationStatus.APPEALED):
        raise HTTPException(status_code=400, detail="Cannot reject this violation")

    violation.status = ViolationStatus.REJECTED
    violation.reviewer_id = current_user.id
    violation.reviewer_remarks = review.remarks
    violation.reviewed_at = datetime.now(timezone.utc)

    audit = AuditLog(
        user_id=current_user.id,
        action="REJECT_VIOLATION",
        resource_type="violation",
        resource_id=str(violation_id),
        details={"remarks": review.remarks},
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(violation)
    return violation


@router.post("/{violation_id}/files")
async def upload_violation_files(
    violation_id: int,
    frame: Optional[UploadFile] = File(None),
    plate: Optional[UploadFile] = File(None),
    video: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload evidence files for a violation (no auth – called by edge AI)."""
    result = await db.execute(select(Violation).where(Violation.id == violation_id))
    violation = result.scalar_one_or_none()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")

    ev_dir = UPLOAD_DIR / str(violation_id)
    ev_dir.mkdir(parents=True, exist_ok=True)

    updates = {}
    for file_type, upload_file in [("frame", frame), ("plate", plate), ("video", video)]:
        if upload_file is None:
            continue
        content = await upload_file.read()
        if len(content) == 0:
            continue
        if len(content) > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"File too large: {file_type}")

        ext = Path(upload_file.filename).suffix if upload_file.filename else ".bin"
        filename = f"{file_type}{ext}"
        file_path = ev_dir / filename

        with open(file_path, "wb") as f:
            f.write(content)

        url = f"/api/v1/violations/{violation_id}/files/{filename}"
        if file_type == "frame":
            updates["frame_image_url"] = url
        elif file_type == "plate":
            updates["plate_image_url"] = url
        elif file_type == "video":
            updates["video_clip_url"] = url

    if updates:
        await db.execute(update(Violation).where(Violation.id == violation_id).values(**updates))
        await db.commit()

    return {"message": "Files uploaded successfully", "urls": updates}


@router.get("/{violation_id}/files/{filename}")
async def serve_violation_file(violation_id: int, filename: str):
    """Serve an evidence file."""
    safe_filename = Path(filename).name
    file_path = UPLOAD_DIR / str(violation_id) / safe_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))


@router.post("/{violation_id}/verify-integrity")
async def verify_evidence_integrity(
    violation_id: int,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Verify SHA-256 integrity of evidence files."""
    result = await db.execute(select(Violation).where(Violation.id == violation_id))
    violation = result.scalar_one_or_none()
    if not violation:
        raise HTTPException(status_code=404, detail="Violation not found")

    ev_dir = UPLOAD_DIR / str(violation_id)
    integrity_results = {}

    def check_file(url: Optional[str], expected_hash: Optional[str], label: str):
        if not url or not expected_hash:
            integrity_results[label] = "no_file"
            return
        filename = Path(url).name
        file_path = ev_dir / filename
        if not file_path.exists():
            integrity_results[label] = "file_missing"
            return
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        integrity_results[label] = "valid" if actual == expected_hash else "TAMPERED"

    check_file(violation.frame_image_url, violation.frame_sha256, "frame")
    check_file(violation.plate_image_url, violation.plate_sha256, "plate")
    check_file(violation.video_clip_url, violation.video_sha256, "video")

    all_valid = all(v in ("valid", "no_file") for v in integrity_results.values())
    return {"violation_id": violation_id, "results": integrity_results, "tamper_free": all_valid}
