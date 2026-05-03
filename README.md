# V-Watch — AI Traffic Violation Management System

An end-to-end traffic violation detection and management platform powered by YOLOv8, FastAPI, React, and PostgreSQL — with a **persistent backend camera system** that keeps running regardless of browser navigation.

---

## What's New — Persistent Camera Architecture

### ❌ Old Problem
- Camera stream stopped when user navigated away from Live Monitoring page
- Detection halted on page switch
- Stream had to be manually restarted every time

### ✅ New Solution
- **Backend owns cameras** — each camera runs in its own Python thread inside FastAPI
- **MJPEG streaming** — `GET /api/v1/cameras/stream/{camera_id}` serves live frames as HTTP multipart; the browser `<img>` tag simply reconnects when you return
- **Global WebSocket** — `/api/v1/live/ws` connection persists across React page navigation
- **Zustand store** — camera list and violation feed survive React unmounts
- **YOLO detection** runs every 3rd frame inside the backend thread — annotated bounding boxes drawn server-side before streaming

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        V-Watch System                                    │
│                                                                          │
│  Browser (React + Vite)                                                  │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  LiveMonitoringPage                                              │    │
│  │  ┌─────────────┐  ┌──────────────────────────────────────────┐ │    │
│  │  │ Camera Card │  │  <img src="/api/v1/cameras/stream/cam1"> │ │    │
│  │  │ (Zustand)   │  │  MJPEG — browser keeps HTTP conn open    │ │    │
│  │  └─────────────┘  └──────────────────────────────────────────┘ │    │
│  │  ┌──────────────────────────────────────────────┐              │    │
│  │  │  Global WebSocket  /live/ws                  │              │    │
│  │  │  Persists across page navigation             │              │    │
│  │  └──────────────────────────────────────────────┘              │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │  HTTP / WS                                │
│  FastAPI Backend (Port 8000)                                             │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  CameraManager (Singleton)                                       │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │    │
│  │  │ cam_thread_1 │  │ cam_thread_2 │  │ cam_thread_N │          │    │
│  │  │ OpenCV+YOLO  │  │ OpenCV+YOLO  │  │ OpenCV+YOLO  │          │    │
│  │  │ ─ Webcam     │  │ ─ RTSP       │  │ ─ HTTP MJPEG │          │    │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │    │
│  │         │                 │                  │                   │    │
│  │  MJPEG Endpoints          │            WebSocket Broadcast       │    │
│  │  /cameras/stream/{id}     └────────────────► /live/ws           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                              │                                           │
│  PostgreSQL 16 (Port 5432)   │                                           │
│  ┌───────────────────────────▼────────────────────────────────────┐     │
│  │  violations · users · audit_logs · system_config               │     │
│  └────────────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────────┘
```

### Services
| Service | Port | Description |
|---------|------|-------------|
| `backend` | 8000 | FastAPI REST + WebSocket + MJPEG streams |
| `frontend` | 3000 | React (Vite / nginx) |
| `postgres` | 5432 | PostgreSQL 16 |
| `edge_ai` | — | YOLOv8 edge detection (optional) |

---

## Features

### Persistent Camera System (New)
- ✅ **Backend camera threads** — cameras run in `threading.Thread`, completely independent of frontend
- ✅ **MJPEG streaming** — `multipart/x-mixed-replace` served over plain HTTP; no WebRTC/WebSocket needed for video
- ✅ **Per-camera WebSocket** — `WS /api/v1/cameras/ws/{camera_id}` pushes base64 JPEG frames for canvas overlay use cases
- ✅ **Auto-reconnect RTSP** — backend thread reconnects to RTSP/HTTP streams on failure
- ✅ **Server-side YOLO** — detection + bounding boxes drawn in backend, streamed in MJPEG
- ✅ **Frontend survives navigation** — `<img>` tag with MJPEG URL simply re-renders; stream never stopped
- ✅ **Zustand persistence** — camera list + violation feed persist in localStorage across page refreshes

### Existing Features
- **AI Detection** — YOLOv8n vehicle detection (singleton, loaded once; mock mode if not installed)
- **Live Violation Feed** — WebSocket broadcast to all dashboard tabs instantly
- **Multi-Camera Support** — unlimited cameras, each with independent ID and stream endpoint
- **Violations Workflow** — Pending → Approve / Reject with fine management
- **Admin YOLO Test** — upload video, run detection, inspect results
- **Evidence Integrity** — SHA-256 hash verification for every uploaded file
- **RBAC** — Admin, Traffic Police, Viewer roles with JWT
- **Edge AI** — standalone module with DeepSORT, ANPR, offline buffering
- **Docker-ready** — single `docker compose up` starts everything

---

## Quick Start — Docker (Recommended)

### Prerequisites
- Docker ≥ 24 and Docker Compose ≥ 2.20
- 4 GB RAM free

### 1. Clone and configure
```bash
cd vwatch
cp backend/.env.example backend/.env
# Optional: set SECRET_KEY in backend/.env
```

### 2. Start all services
```bash
docker compose up -d
```

| URL | Service |
|-----|---------|
| http://localhost:3000 | Frontend dashboard |
| http://localhost:8000/docs | Swagger API docs |
| http://localhost:8000/redoc | ReDoc |

### 3. (Optional) Start Edge AI
```bash
🧱 STEP 1 — Build image first

Run this in your project folder:

docker-compose build edge_ai

👉 This creates your Docker image (installs Python, Torch, YOLO, etc.)

🚀 STEP 2 — Start the container
Option A (recommended – attach logs)
docker-compose --profile edge up edge_ai
Option B (run in background)
docker-compose --profile edge up -d edge_ai
```

### 4. Stop everything
```bash
docker compose down
docker compose down -v   # also delete DB volume
```

---

## Manual Start (Development)

### Prerequisites
- Python 3.11+
- Node.js 20+
- PostgreSQL 16 running locally

### 1. Database
```bash
psql -U postgres <<'SQL'
CREATE USER vwatch WITH PASSWORD 'vwatch_pass';
CREATE DATABASE vwatch_db OWNER vwatch;
SQL
psql -U vwatch -d vwatch_db < scripts/init_db.sql
```

### 2. Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
# For real YOLO detection (optional):
pip install ultralytics

cp .env.example .env
# Edit .env: set DATABASE_URL, SECRET_KEY

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend: http://localhost:8000  
Swagger: http://localhost:8000/docs

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173

---

## Default Credentials

| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `Admin@123!` |

---

## API Reference

All endpoints prefixed with `/api/v1`. Full interactive docs at `/docs`.

### Persistent Camera System (NEW)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/cameras` | Police | List all backend cameras with live state |
| POST | `/cameras` | Police | Register + auto-start a camera |
| POST | `/cameras/{id}/start` | Police | Start capture thread |
| POST | `/cameras/{id}/stop` | Police | Stop capture thread (config retained) |
| POST | `/cameras/{id}/restart` | Police | Stop then start |
| DELETE | `/cameras/{id}` | Police | Stop + permanently remove |
| GET | `/cameras/{id}/status` | Police | Single camera live status |
| GET | `/cameras/system/status` | Police | Full system summary |
| **GET** | **`/cameras/stream/{id}`** | **None** | **MJPEG live stream (embed as `<img>`!)** |
| WS | `/cameras/ws/{id}` | None | Per-camera base64 JPEG frame push |

### Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | None | Login (returns JWT) |
| POST | `/auth/refresh` | None | Refresh access token |
| GET | `/auth/me` | Bearer | Current user profile |
| POST | `/auth/change-password` | Bearer | Change password |
| POST | `/auth/logout` | Bearer | Logout |

### Live Monitoring (WebSocket + REST)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| WS | `/live/ws` | None | Global violation + camera event feed |
| POST | `/live/violations/report` | None | Submit live violation (saves to DB + broadcasts) |
| POST | `/live/cameras/status` | None | Update camera status |
| GET | `/live/cameras` | Police | List cameras (backend + DB merged) |
| POST | `/live/cameras` | Police | Add camera (registers in backend manager + DB) |
| DELETE | `/live/cameras/{id}` | Police | Remove camera |
| GET | `/live/stats` | Police | Live statistics |
| GET | `/live/recent-violations` | Police | Recent violations list |
| GET | `/live/ws/count` | None | WebSocket client count |

### Violations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/violations` | None | Submit violation (Edge AI) |
| GET | `/violations` | Police | List with filters & pagination |
| GET | `/violations/stats` | Police | Dashboard statistics |
| GET | `/violations/{id}` | Police | Get single violation |
| POST | `/violations/{id}/approve` | Police | Approve violation |
| POST | `/violations/{id}/reject` | Police | Reject violation |
| POST | `/violations/{id}/files` | None | Upload evidence files |
| GET | `/violations/{id}/files/{filename}` | None | Serve evidence file |
| POST | `/violations/{id}/verify-integrity` | Police | Verify SHA-256 hashes |

### YOLO Analysis

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/yolo/status` | Police | Model status (running/mock) |
| POST | `/yolo/analyze-video` | Police | Upload + analyze video |

### Users (Admin only)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/users` | Admin | List users |
| POST | `/users` | Admin | Create user |
| GET | `/users/{id}` | Admin | Get user |
| PATCH | `/users/{id}` | Admin | Update user |
| DELETE | `/users/{id}` | Admin | Deactivate user |

---

## How the Persistent Camera System Works

### Backend: `CameraManager` Singleton

```python
# backend/app/services/camera_manager.py

class CameraManager:
    """
    Each camera runs in its own daemon thread.
    Threads are started at app startup and persist
    for the entire lifetime of the FastAPI process.
    """

    def start_camera(self, camera_id: str):
        thread = threading.Thread(target=_camera_thread, args=(cam, broadcast))
        thread.daemon = True
        thread.start()   # Never stops when frontend disconnects
```

### MJPEG Stream Flow

```
Backend thread (Python)          Browser (<img> tag)
        │                                │
        ├─ cv2.read() frame              │
        ├─ YOLO detect + draw bbox       │
        ├─ cv2.imencode JPEG             │
        ├─ cam.set_frame(jpeg)           │
        │                                │
        ▼                                ▼
GET /cameras/stream/{id}    ◄────── img src="/api/v1/cameras/stream/cam1"
        │                                │
        ├─ StreamingResponse             ├─ Browser reads multipart chunks
        ├─ multipart/x-mixed-replace     ├─ Renders as live video
        └─ Yields frame bytes ───────────►│
                                         │
             [user navigates away]        │
             [user comes back]            │
                                         │
                              img re-renders same URL
                              Backend stream: still running ✅
```

### Frontend: No Camera Control on Page Unmount

```typescript
// The MJPEG URL is just a string — React doesn't "own" the connection
<img src={`/api/v1/cameras/stream/${cam.camera_id}`} />

// When user leaves the page, React unmounts the <img>
// The browser closes this particular HTTP response reader
// BUT the backend thread keeps capturing + YOLO detecting

// When user returns, React mounts the <img> again
// The <img> sends a NEW GET request to the same URL
// Backend immediately starts streaming from the latest frame
// → Instant reconnect, zero state loss
```

### Global WebSocket (Violation Feed)

```typescript
// utils/api.ts
let _globalWs: LiveMonitoringWebSocket | null = null

export function getGlobalWs(): LiveMonitoringWebSocket {
  if (!_globalWs) {
    _globalWs = new LiveMonitoringWebSocket()
    _globalWs.connect()         // connects ONCE for entire app session
  }
  return _globalWs              // same instance returned on every call
}

// In LiveMonitoringPage:
const ws = getGlobalWs()        // always the same WS connection
ws.onViolation = (data) => { ... }  // just update the callback

// On page unmount:
// ws.onViolation = null  (clear callbacks)
// DO NOT call ws.destroy() — WS must stay connected!
```

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://vwatch:vwatch_pass@localhost:5432/vwatch_db` | Async DB URL |
| `DATABASE_URL_SYNC` | `postgresql://vwatch:vwatch_pass@localhost:5432/vwatch_db` | Sync DB URL |
| `SECRET_KEY` | auto-generated | JWT signing key |
| `UPLOAD_DIR` | `/app/uploads` | Evidence file storage |
| `YOLO_MODEL_PATH` | `yolov8n.pt` | YOLO model file |
| `YOLO_DEVICE` | `cpu` | `cpu` or `cuda` |
| `ALLOWED_ORIGINS` | `["*"]` | CORS allowed origins |

### Frontend (build-time)

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:8000/api/v1` | Backend API base URL |
| `VITE_WS_URL` | `ws://localhost:8000/api/v1` | WebSocket base URL |

---

## Project Structure

```
vwatch/
├── backend/                        # FastAPI backend
│   ├── app/
│   │   ├── api/
│   │   │   ├── auth.py
│   │   │   ├── violations.py
│   │   │   ├── live_monitoring.py  # WebSocket + live violation feed
│   │   │   ├── camera_stream.py    # ← NEW: MJPEG + per-camera WS + lifecycle
│   │   │   ├── yolo_analysis.py
│   │   │   ├── users.py
│   │   │   └── config_api.py
│   │   ├── core/                   # Config, DB, security, deps
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   ├── schemas/                # Pydantic schemas
│   │   ├── services/
│   │   │   ├── notification.py
│   │   │   └── camera_manager.py   # ← NEW: persistent camera thread manager
│   │   └── main.py                 # App entry point + camera startup
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/                       # React + Vite + Tailwind + Zustand
│   ├── src/
│   │   ├── pages/
│   │   │   └── LiveMonitoringPage.tsx  # ← REWRITTEN: MJPEG display + backend control
│   │   ├── components/Layout.tsx
│   │   ├── store/
│   │   │   ├── authStore.ts
│   │   │   └── cameraStore.ts      # ← NEW: Zustand persistent camera state
│   │   └── utils/
│   │       └── api.ts              # ← UPDATED: cameraApi + global WS singleton
│   └── Dockerfile
│
├── edge_ai/                        # YOLOv8 edge detection module
├── config/
│   └── edge_config.json
├── scripts/
│   └── init_db.sql
└── docker-compose.yml
```

---

## Testing the Persistent Stream

### Quick Test via curl
```bash
# 1. Start the backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 2. Register a webcam
curl -X POST http://localhost:8000/api/v1/cameras \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"camera_id":"cam1","name":"Test Webcam","source":"0","source_type":"webcam","auto_start":true}'

# 3. Check it's running
curl http://localhost:8000/api/v1/cameras/cam1/status \
  -H "Authorization: Bearer $TOKEN"
# → {"state": "running", "fps": 29.5, ...}

# 4. Open the MJPEG stream in browser
# http://localhost:8000/api/v1/cameras/stream/cam1
# (No auth required for stream endpoint)

# 5. Navigate to another page in the frontend — stream continues
# 6. Return — stream reconnects instantly
```

### WebSocket Violation Events
```javascript
const ws = new WebSocket('ws://localhost:8000/api/v1/live/ws')
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data)
  if (msg.type === 'violation') {
    console.log('🚨 Violation:', msg.data)
  }
  if (msg.type === 'connected') {
    console.log('📹 Backend cameras:', msg.cameras)
  }
}
```

---

## Troubleshooting

### Camera state stuck at "starting"
```bash
# Check backend logs
docker compose logs backend --tail=50

# Try restart via API
curl -X POST http://localhost:8000/api/v1/cameras/cam1/restart \
  -H "Authorization: Bearer $TOKEN"
```

### MJPEG stream shows "Camera Offline" placeholder
- Camera thread failed to open the source (check `error_message` field in status)
- For webcam: ensure no other process is using device 0
- For RTSP: verify the URL and network connectivity from the backend server

### WebSocket not receiving violations
```bash
# Check WS client count
curl http://localhost:8000/api/v1/live/ws/count
# Should be ≥ 1 if browser is connected

# Check backend WS logs
docker compose logs backend | grep '\[WS\]'
```

### YOLO running in mock mode
```bash
# Install ultralytics in running container
docker compose exec backend pip install ultralytics
docker compose restart backend

# Check status
curl http://localhost:8000/api/v1/yolo/status \
  -H "Authorization: Bearer $TOKEN"
# → {"running": true, "model_name": "yolov8n.pt", "mock_mode": false}
```

### Frontend stream not loading after navigation
- This should be automatic — the `<img>` tag re-requests the MJPEG URL
- If it shows blank: press the ↺ Restart button on the camera card to force a new stream key
- Ensure CORS allows the frontend origin in `ALLOWED_ORIGINS`

---

## License

MIT License — see [LICENSE](LICENSE) for details.
