"""
V-Watch Backend - System Configuration & Audit Logs API
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete
from typing import Optional, List
from datetime import datetime

from ..core.database import get_db
from ..core.dependencies import get_current_user, require_admin, require_police
from ..models.user import User
from ..models.violation import SystemConfig, AuditLog
from ..schemas.violation import SystemConfigUpdate

router = APIRouter(tags=["System Configuration"])


# ─── System Config ────────────────────────────────────────────────────────────

@router.get("/config", prefix="/config")
async def list_config(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all system configuration entries."""
    result = await db.execute(select(SystemConfig))
    configs = result.scalars().all()
    return [
        {"key": c.key, "value": c.value, "description": c.description}
        for c in configs
    ]


@router.get("/config/{key}")
async def get_config(
    key: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get a system configuration value by key."""
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail=f"Config key '{key}' not found")
    return {"key": config.key, "value": config.value, "description": config.description}


@router.put("/config")
async def upsert_config(
    config_data: SystemConfigUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a system configuration entry."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == config_data.key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = config_data.value
        if config_data.description:
            existing.description = config_data.description
        existing.updated_by = admin.id
    else:
        new_config = SystemConfig(
            key=config_data.key,
            value=config_data.value,
            description=config_data.description,
            updated_by=admin.id,
        )
        db.add(new_config)

    await db.commit()
    return {"message": "Configuration updated", "key": config_data.key}


@router.delete("/config/{key}")
async def delete_config(
    key: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a system configuration entry."""
    await db.execute(delete(SystemConfig).where(SystemConfig.key == key))
    await db.commit()
    return {"message": f"Config key '{key}' deleted"}


# ─── Audit Logs ──────────────────────────────────────────────────────────────

@router.get("/audit-logs")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get audit logs (Admin only)."""
    from sqlalchemy import and_, desc

    query = select(AuditLog)
    count_query = select(func.count(AuditLog.id))
    filters = []

    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if action:
        filters.append(AuditLog.action.ilike(f"%{action}%"))
    if date_from:
        filters.append(AuditLog.created_at >= date_from)
    if date_to:
        filters.append(AuditLog.created_at <= date_to)

    if filters:
        from sqlalchemy import and_
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    total = (await db.execute(count_query)).scalar()
    offset = (page - 1) * page_size
    result = await db.execute(
        query.order_by(desc(AuditLog.created_at)).offset(offset).limit(page_size)
    )
    logs = result.scalars().all()

    return {
        "items": [
            {
                "id": log.id,
                "user_id": log.user_id,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "details": log.details,
                "ip_address": log.ip_address,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ─── Cameras ──────────────────────────────────────────────────────────────────

@router.get("/cameras")
async def list_cameras(
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Get list of registered cameras from system config."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key.like("camera.%"))
    )
    configs = result.scalars().all()
    cameras = [{"key": c.key, "config": c.value} for c in configs]
    return cameras
