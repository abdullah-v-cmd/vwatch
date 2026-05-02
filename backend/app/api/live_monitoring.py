"""
V-Watch Backend - Live Monitoring API
WebSocket + REST endpoints for real-time camera monitoring
"""

import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import (
    APIRouter, Depends, HTTPException, WebSocket,
    WebSocketDisconnect, Query, Body
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from pydantic import BaseModel

from ..core.database import get_db
from ..core.dependencies import require_police, get_current_user
from ..models.user import User
from ..models.violation import Violation, ViolationStatus, ViolationType, SystemConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["Live Monitoring"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class CameraConfig(BaseModel):
    camera_id: str
    name: str
    url: str               # 'webcam', RTSP, HTTP/HTTPS
    source_type: str       # 'webcam' | 'rtsp' | 'http'
    location: Optional[str] = ""
    speed_limit: Optional[float] = 60.0
    enabled: bool = True


class LiveViolationEvent(BaseModel):
    camera_id: str
    violation_type: str
    plate_number: Optional[str] = "UNKNOWN"
    confidence: float = 0.0
    speed: Optional[float] = None
    timestamp: Optional[str] = None
    location: Optional[str] = ""
    frame_base64: Optional[str] = None   # base64 encoded frame thumbnail


class CameraStatusUpdate(BaseModel):
    camera_id: str
    status: str   # 'active' | 'idle' | 'error'
    fps: Optional[float] = None
    message: Optional[str] = None


# ─── WebSocket Connection Manager ─────────────────────────────────────────────

class ConnectionManager:
    """Manages active WebSocket connections for live monitoring."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"[WS] Client connected. Total: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"[WS] Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast a JSON message to all connected clients."""
        if not self.active_connections:
            return
        data = json.dumps(message, default=str)
        disconnected = []
        async with self._lock:
            connections = list(self.active_connections)
        for ws in connections:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            await self.disconnect(ws)

    async def send_personal(self, websocket: WebSocket, message: Dict[str, Any]):
        """Send message to a single client."""
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception as e:
            logger.error(f"[WS] Send error: {e}")
            await self.disconnect(websocket)

    @property
    def count(self) -> int:
        return len(self.active_connections)


manager = ConnectionManager()


# ─── WebSocket Endpoint ───────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time live monitoring events.
    
    Messages from server:
    - { type: "violation", data: {...} }
    - { type: "camera_status", data: {...} }
    - { type: "ping", timestamp: "..." }
    - { type: "stats", data: {...} }
    
    Messages from client:
    - { type: "ping" }
    - { type: "subscribe", cameras: ["CAM_001", ...] }
    """
    await manager.connect(websocket)
    # Send welcome message
    await manager.send_personal(websocket, {
        "type": "connected",
        "message": "Connected to V-Watch Live Monitoring",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "clients": manager.count,
    })

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                try:
                    msg = json.loads(raw)
                    if msg.get("type") == "ping":
                        await manager.send_personal(websocket, {
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    elif msg.get("type") == "subscribe":
                        # Acknowledge subscription
                        await manager.send_personal(websocket, {
                            "type": "subscribed",
                            "cameras": msg.get("cameras", []),
                        })
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                # Send periodic ping to keep connection alive
                try:
                    await manager.send_personal(websocket, {
                        "type": "ping",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


# ─── REST Endpoints ───────────────────────────────────────────────────────────

@router.post("/violations/report")
async def report_live_violation(
    event: LiveViolationEvent,
    db: AsyncSession = Depends(get_db),
):
    """
    Edge AI reports a live violation detection.
    This broadcasts to all WebSocket clients immediately
    and creates a pending violation record.
    """
    timestamp = event.timestamp or datetime.now(timezone.utc).isoformat()

    # Broadcast to all connected dashboards
    await manager.broadcast({
        "type": "violation",
        "data": {
            "camera_id": event.camera_id,
            "violation_type": event.violation_type,
            "plate_number": event.plate_number or "UNKNOWN",
            "confidence": round(event.confidence, 3),
            "speed": event.speed,
            "timestamp": timestamp,
            "location": event.location or "",
            "frame_base64": event.frame_base64,
        },
    })

    logger.info(f"[LiveMonitoring] Violation broadcast: {event.violation_type} cam={event.camera_id}")
    return {"status": "broadcasted", "clients": manager.count}


@router.post("/cameras/status")
async def update_camera_status(
    update: CameraStatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Edge AI reports camera status change."""
    await manager.broadcast({
        "type": "camera_status",
        "data": {
            "camera_id": update.camera_id,
            "status": update.status,
            "fps": update.fps,
            "message": update.message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    })
    return {"status": "ok"}


@router.get("/cameras")
async def list_monitored_cameras(
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """
    List all configured cameras for live monitoring.
    Cameras are stored in SystemConfig with key pattern 'camera.{id}'
    """
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key.like("camera.%"))
    )
    configs = result.scalars().all()

    cameras = []
    for c in configs:
        try:
            cam_data = json.loads(c.value) if c.value else {}
            cameras.append({
                "camera_id": c.key.replace("camera.", ""),
                "config_key": c.key,
                "name": cam_data.get("name", c.key),
                "url": cam_data.get("url", ""),
                "source_type": cam_data.get("source_type", "rtsp"),
                "location": cam_data.get("location", ""),
                "speed_limit": cam_data.get("speed_limit", 60.0),
                "enabled": cam_data.get("enabled", True),
            })
        except Exception:
            cameras.append({
                "camera_id": c.key.replace("camera.", ""),
                "config_key": c.key,
                "name": c.key,
                "url": c.value or "",
                "source_type": "rtsp",
                "location": "",
                "speed_limit": 60.0,
                "enabled": True,
            })

    return {"cameras": cameras, "total": len(cameras)}


@router.post("/cameras")
async def add_monitored_camera(
    cam: CameraConfig,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Add a new camera to live monitoring."""
    key = f"camera.{cam.camera_id}"

    # Check if already exists
    existing = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    existing_cfg = existing.scalar_one_or_none()

    cam_value = json.dumps({
        "name": cam.name,
        "url": cam.url,
        "source_type": cam.source_type,
        "location": cam.location or "",
        "speed_limit": cam.speed_limit or 60.0,
        "enabled": cam.enabled,
    })

    if existing_cfg:
        existing_cfg.value = cam_value
        existing_cfg.updated_by = current_user.id
    else:
        new_cfg = SystemConfig(
            key=key,
            value=cam_value,
            description=f"Live monitoring camera: {cam.name}",
            updated_by=current_user.id,
        )
        db.add(new_cfg)

    await db.commit()

    # Broadcast camera added event
    await manager.broadcast({
        "type": "camera_added",
        "data": {"camera_id": cam.camera_id, "name": cam.name},
    })

    return {"status": "added", "camera_id": cam.camera_id}


@router.delete("/cameras/{camera_id}")
async def remove_monitored_camera(
    camera_id: str,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Remove a camera from live monitoring."""
    from sqlalchemy import delete
    key = f"camera.{camera_id}"
    await db.execute(delete(SystemConfig).where(SystemConfig.key == key))
    await db.commit()

    await manager.broadcast({
        "type": "camera_removed",
        "data": {"camera_id": camera_id},
    })

    return {"status": "removed", "camera_id": camera_id}


@router.get("/stats")
async def get_live_stats(
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Get live monitoring statistics."""
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Last hour violations
    last_hour = (await db.execute(
        select(func.count(Violation.id)).where(Violation.created_at >= hour_ago)
    )).scalar() or 0

    # Today violations
    today = (await db.execute(
        select(func.count(Violation.id)).where(Violation.violation_time >= today_start)
    )).scalar() or 0

    # By type today
    type_result = await db.execute(
        select(Violation.violation_type, func.count(Violation.id))
        .where(Violation.violation_time >= today_start)
        .group_by(Violation.violation_type)
    )
    by_type = {str(row[0].value): row[1] for row in type_result}

    # Recent violations (last 10)
    recent_result = await db.execute(
        select(Violation)
        .order_by(desc(Violation.created_at))
        .limit(10)
    )
    recent = recent_result.scalars().all()

    return {
        "connected_clients": manager.count,
        "last_hour_violations": last_hour,
        "today_violations": today,
        "violations_by_type_today": by_type,
        "recent_violations": [
            {
                "id": v.id,
                "plate_number": v.plate_number,
                "violation_type": v.violation_type.value,
                "camera_id": v.camera_id,
                "violation_time": v.violation_time.isoformat() if v.violation_time else None,
                "confidence": v.confidence,
                "status": v.status.value,
            }
            for v in recent
        ],
    }


@router.get("/recent-violations")
async def get_recent_violations(
    limit: int = Query(20, ge=1, le=100),
    camera_id: Optional[str] = None,
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """Get recent violations for live feed display."""
    query = select(Violation).order_by(desc(Violation.created_at)).limit(limit)
    if camera_id:
        query = query.where(Violation.camera_id == camera_id)

    result = await db.execute(query)
    violations = result.scalars().all()

    return {
        "violations": [
            {
                "id": v.id,
                "plate_number": v.plate_number,
                "violation_type": v.violation_type.value,
                "camera_id": v.camera_id,
                "location": v.location,
                "violation_time": v.violation_time.isoformat() if v.violation_time else None,
                "confidence": v.confidence,
                "speed_recorded": v.speed_recorded,
                "status": v.status.value,
                "frame_image_url": v.frame_image_url,
            }
            for v in violations
        ],
        "total": len(violations),
    }


@router.get("/ws/count")
async def get_ws_client_count():
    """Get number of active WebSocket connections."""
    return {"connected_clients": manager.count}
