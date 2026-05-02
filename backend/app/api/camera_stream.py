"""
V-Watch Backend - Camera Streaming API
========================================
MJPEG streaming + WebSocket frame push + camera lifecycle REST endpoints.

Endpoints:
  GET  /cameras/stream/{camera_id}        → MJPEG multipart stream
  WS   /cameras/ws/{camera_id}            → WebSocket frame push (base64 JPEG)
  GET  /cameras                           → list all backend cameras + state
  POST /cameras                           → register + start a camera
  POST /cameras/{camera_id}/start         → start existing camera
  POST /cameras/{camera_id}/stop          → stop camera (keeps config)
  POST /cameras/{camera_id}/restart       → restart camera
  DELETE /cameras/{camera_id}             → stop + remove camera
  GET  /cameras/{camera_id}/status        → single camera status
  GET  /cameras/system/status             → full system summary
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

from fastapi import (
    APIRouter, HTTPException, WebSocket, WebSocketDisconnect,
    Depends, Query
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.dependencies import require_police
from ..models.user import User
from ..services.camera_manager import camera_manager, CameraState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras", tags=["Camera Streams"])

# ── Schemas ────────────────────────────────────────────────────────────────────

class AddCameraRequest(BaseModel):
    camera_id: str
    name: str
    source: str             # "0" | "webcam" | rtsp://... | http://...
    source_type: str        # "webcam" | "rtsp" | "http"
    location: str = ""
    speed_limit: float = 60.0
    enabled: bool = True
    auto_start: bool = True  # Start capture immediately after registering


# ── MJPEG Streaming ────────────────────────────────────────────────────────────

MJPEG_BOUNDARY = b"--vwatch_frame"
MJPEG_HEADER = (
    b"Content-Type: image/jpeg\r\n"
    b"Content-Length: {length}\r\n\r\n"
)

# Placeholder frame when camera is not running (1x1 grey JPEG)
_PLACEHOLDER_JPEG: Optional[bytes] = None

def _get_placeholder() -> bytes:
    global _PLACEHOLDER_JPEG
    if _PLACEHOLDER_JPEG is None:
        try:
            import cv2
            import numpy as np
            img = np.zeros((480, 640, 3), dtype="uint8")
            img[:] = (40, 40, 40)
            cv2.putText(img, "Camera Offline", (180, 230),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
            cv2.putText(img, "Waiting for stream...", (160, 270),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (160, 160, 160), 1)
            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
            _PLACEHOLDER_JPEG = buf.tobytes()
        except Exception:
            # Tiny grey JPEG fallback (hard-coded minimal JFIF)
            _PLACEHOLDER_JPEG = (
                b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01"
                b"\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07"
                b"\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14"
                b"\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
                b"'9=82<.342\x1edL\x1b\x1c(7),01444\x1f'9=82<.342\x1edL\x1b"
                b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4"
                b"\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
                b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
                b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xf0\x1f\xff\xd9"
            )
    return _PLACEHOLDER_JPEG


async def _mjpeg_frame_generator(camera_id: str):
    """
    Async generator: yields MJPEG multipart frames continuously.
    - If camera is running: subscribes to live frame queue
    - If camera is offline: yields placeholder every second
    """
    cam = camera_manager.get_camera(camera_id)

    # Always yield at least one frame so the browser doesn't show blank
    placeholder = _get_placeholder()
    yield MJPEG_BOUNDARY + b"\r\n"
    yield MJPEG_HEADER.replace(b"{length}", str(len(placeholder)).encode())
    yield placeholder + b"\r\n"

    # Create a per-connection async queue
    q: asyncio.Queue = asyncio.Queue(maxsize=2)

    # Subscribe to live frames if camera exists
    if cam:
        cam.add_subscriber(q)

    try:
        while True:
            if cam and cam.state == CameraState.RUNNING:
                try:
                    jpeg = await asyncio.wait_for(q.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    # Camera is running but no new frame yet; send placeholder
                    jpeg = cam.get_frame() or placeholder
            else:
                # Camera offline – send placeholder frame every second
                await asyncio.sleep(1.0)
                jpeg = placeholder

            yield MJPEG_BOUNDARY + b"\r\n"
            yield MJPEG_HEADER.replace(b"{length}", str(len(jpeg)).encode())
            yield jpeg + b"\r\n"

    except (asyncio.CancelledError, GeneratorExit):
        pass
    finally:
        if cam:
            cam.remove_subscriber(q)


@router.get("/stream/{camera_id}")
async def stream_camera_mjpeg(camera_id: str):
    """
    MJPEG stream for a single camera. Works even if camera is not yet started
    (shows an 'offline' placeholder). The browser can embed this as:
      <img src="/api/v1/cameras/stream/camera_1" />
    Stream survives page navigation because it's a pure HTTP response.
    """
    return StreamingResponse(
        _mjpeg_frame_generator(camera_id),
        media_type="multipart/x-mixed-replace;boundary=vwatch_frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ── WebSocket Frame Push ───────────────────────────────────────────────────────

@router.websocket("/ws/{camera_id}")
async def websocket_camera_stream(websocket: WebSocket, camera_id: str):
    """
    WebSocket endpoint per camera. Pushes base64-encoded JPEG frames so
    the React canvas overlay can still draw bounding-box data received
    separately via the /live/ws channel.

    Message from server:
      { type: "frame", camera_id: "...", data: "<base64 jpeg>",
        fps: 24.5, state: "running" }
    """
    await websocket.accept()
    logger.info(f"[CamWS:{camera_id}] Client connected")

    cam = camera_manager.get_camera(camera_id)
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    if cam:
        cam.add_subscriber(q)

    try:
        while True:
            if cam and cam.state == CameraState.RUNNING:
                try:
                    jpeg = await asyncio.wait_for(q.get(), timeout=2.0)
                    import base64
                    b64 = base64.b64encode(jpeg).decode()
                    await websocket.send_json({
                        "type": "frame",
                        "camera_id": camera_id,
                        "data": b64,
                        "fps": cam.fps,
                        "state": cam.state,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except asyncio.TimeoutError:
                    # Keep-alive ping
                    await websocket.send_json({
                        "type": "ping",
                        "camera_id": camera_id,
                        "state": cam.state if cam else "unknown",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            else:
                await asyncio.sleep(1.0)
                await websocket.send_json({
                    "type": "status",
                    "camera_id": camera_id,
                    "state": cam.state if cam else "not_found",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    except WebSocketDisconnect:
        logger.info(f"[CamWS:{camera_id}] Client disconnected")
    except Exception as exc:
        logger.debug(f"[CamWS:{camera_id}] Error: {exc}")
    finally:
        if cam:
            cam.remove_subscriber(q)


# ── REST Camera Management ─────────────────────────────────────────────────────

@router.get("")
async def list_cameras(current_user: User = Depends(require_police)):
    """List all registered backend cameras with live status."""
    return {
        "cameras": camera_manager.list_cameras(),
        "total": len(camera_manager._cameras),
    }


@router.post("")
async def add_camera(
    req: AddCameraRequest,
    current_user: User = Depends(require_police),
):
    """
    Register a new camera in the backend.
    If auto_start=True (default), capture starts immediately.
    The camera will keep running regardless of frontend connections.
    """
    # Normalise source_type
    source_type = req.source_type.lower()
    source = req.source.strip()

    # Treat '0', 'webcam', 'default' as webcam
    if source.lower() in ("0", "webcam", "default", ""):
        source = "0"
        source_type = "webcam"

    cam = camera_manager.add_camera(
        camera_id=req.camera_id,
        name=req.name,
        source=source,
        source_type=source_type,
        location=req.location,
        speed_limit=req.speed_limit,
        enabled=req.enabled,
    )

    if req.auto_start and req.enabled:
        started = camera_manager.start_camera(req.camera_id)
    else:
        started = False

    return {
        "status": "registered",
        "camera_id": cam.camera_id,
        "auto_started": started,
        "stream_url": f"/api/v1/cameras/stream/{cam.camera_id}",
        "ws_url": f"/api/v1/cameras/ws/{cam.camera_id}",
        "camera": cam.to_dict(),
    }


@router.post("/{camera_id}/start")
async def start_camera(
    camera_id: str,
    current_user: User = Depends(require_police),
):
    """Start (or restart) a camera's capture thread."""
    cam = camera_manager.get_camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")
    ok = camera_manager.start_camera(camera_id)
    return {
        "status": "started" if ok else "error",
        "camera_id": camera_id,
        "stream_url": f"/api/v1/cameras/stream/{camera_id}",
    }


@router.post("/{camera_id}/stop")
async def stop_camera(
    camera_id: str,
    current_user: User = Depends(require_police),
):
    """Stop a camera's capture thread. Config is retained."""
    cam = camera_manager.get_camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")
    ok = camera_manager.stop_camera(camera_id)
    return {"status": "stopped" if ok else "not_running", "camera_id": camera_id}


@router.post("/{camera_id}/restart")
async def restart_camera(
    camera_id: str,
    current_user: User = Depends(require_police),
):
    """Stop then start a camera."""
    cam = camera_manager.get_camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")
    ok = camera_manager.restart_camera(camera_id)
    return {
        "status": "restarted" if ok else "error",
        "camera_id": camera_id,
        "stream_url": f"/api/v1/cameras/stream/{camera_id}",
    }


@router.delete("/{camera_id}")
async def delete_camera(
    camera_id: str,
    current_user: User = Depends(require_police),
):
    """Stop and permanently remove a camera from the backend."""
    ok = camera_manager.remove_camera(camera_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")
    return {"status": "removed", "camera_id": camera_id}


@router.get("/system/status")
async def system_status(current_user: User = Depends(require_police)):
    """Full camera system summary."""
    return camera_manager.status_summary()


@router.get("/{camera_id}/status")
async def camera_status(
    camera_id: str,
    current_user: User = Depends(require_police),
):
    """Get detailed status of a single camera."""
    cam = camera_manager.get_camera(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")
    return cam.to_dict()
