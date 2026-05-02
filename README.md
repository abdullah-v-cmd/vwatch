# V-Watch — AI Traffic Violation Management System

An end-to-end traffic violation detection and management platform powered by YOLOv8, FastAPI, React, and PostgreSQL.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Features](#features)
- [Quick Start — Docker (Recommended)](#quick-start--docker-recommended)
- [Manual Start (Development)](#manual-start-development)
- [Default Credentials](#default-credentials)
- [API Reference](#api-reference)
- [Environment Variables](#environment-variables)
- [Edge AI Module](#edge-ai-module)
- [Admin YOLO Test](#admin-yolo-test)
- [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Docker Network (vwatch_net)          │
│                                                             │
│  ┌──────────────┐   nginx proxy    ┌──────────────────────┐ │
│  │   Frontend   │ ───────────────► │   Backend (FastAPI)  │ │
│  │  React+Nginx │ :3000→:80        │   Python 3.11        │ │
│  │  Port 3000   │                  │   Port 8000          │ │
│  └──────────────┘                  └──────────┬───────────┘ │
│                                               │             │
│  ┌──────────────┐                  ┌──────────▼───────────┐ │
│  │   Edge AI    │ HTTP POST        │   PostgreSQL 16      │ │
│  │  YOLOv8 CPU  │ ───────────────► │   Port 5432          │ │
│  │  (--profile  │                  └──────────────────────┘ │
│  │    edge)     │                                           │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────┘
```

**Services:**
| Service | Image | Port | Description |
|---------|-------|------|-------------|
| `postgres` | postgres:16-alpine | 5432 | Database |
| `backend` | ./backend | 8000 | FastAPI REST + WebSocket |
| `frontend` | ./frontend | 3000→80 | React (nginx) |
| `edge_ai` | ./edge_ai | — | YOLOv8 detection (opt-in) |

---

## Features

- **AI Detection** – YOLOv8n vehicle detection with bounding boxes (singleton model, loaded once)
- **Live Monitoring** – Real-time webcam/CCTV feeds with canvas bbox overlay; streams survive page navigation
- **WebSocket feed** – Violations appear instantly on the Violations page and Live Monitoring feed
- **Violations workflow** – Pending → Approve / Reject with remarks and fine management
- **Admin YOLO Test** – Upload a video file and run YOLO detection directly from the browser
- **Evidence integrity** – SHA-256 hash verification for every uploaded frame/plate/video
- **RBAC** – Admin, Traffic Police, and Viewer roles with JWT authentication
- **Edge AI** – Standalone Python module streams from webcam/RTSP, submits violations to backend with offline buffering
- **Docker-ready** – Single `docker compose up` starts the full stack

---

## Quick Start — Docker (Recommended)

### Prerequisites

- Docker ≥ 24 and Docker Compose ≥ 2.20
- 4 GB RAM free

### 1. Clone and configure

```bash
git clone https://github.com/abdullah-v-cmd/vwatch.git
cd vwatch

# Optional: override the JWT secret (strongly recommended for production)
echo 'SECRET_KEY=your_random_32_char_secret_here' > .env
```

### 2. Start core services

```bash
docker compose up -d
```

This starts **postgres**, **backend**, and **frontend**.

| URL | Service |
|-----|---------|
| http://localhost:3000 | Frontend dashboard |
| http://localhost:8000/docs | Backend API docs (Swagger) |
| http://localhost:8000/redoc | ReDoc |

### 3. (Optional) Start Edge AI with webcam

> Requires a webcam attached to the host. On Windows/Mac, comment out the `devices` section in `docker-compose.yml`.

```bash
docker compose --profile edge up -d edge_ai
```

### 4. Stop everything

```bash
docker compose down          # stop containers
docker compose down -v       # stop + delete all data volumes
```

### Rebuilding after code changes

```bash
docker compose build --no-cache
docker compose up -d
```

---

## Manual Start (Development)

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 16 running locally

### 1. Database

```bash
# Create database and user
psql -U postgres <<'SQL'
CREATE USER vwatch WITH PASSWORD 'vwatch_pass';
CREATE DATABASE vwatch_db OWNER vwatch;
SQL

# Apply schema
psql -U vwatch -d vwatch_db < scripts/init_db.sql
```

### 2. Backend

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env: set DATABASE_URL, DATABASE_URL_SYNC, SECRET_KEY

# Start development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend available at: http://localhost:8000  
Swagger docs at: http://localhost:8000/docs

### 3. Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start development server (proxies /api → localhost:8000)
npm run dev
```

Frontend available at: http://localhost:3000

### 4. Edge AI (optional)

```bash
cd edge_ai

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install CPU-only dependencies (no GPU required)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Run with default webcam
python main.py --config config/edge_config.json

# Run without display (headless / server)
python main.py --config config/edge_config.json --no-display

# Run on a video file for testing
python main.py --source /path/to/video.mp4 --no-display
```

---

## Default Credentials

| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `Admin@123!` |

> The default admin is created automatically on first startup.
> Change the password immediately in production via: **Profile → Change Password**.

---

## API Reference

All endpoints are prefixed with `/api/v1`. Interactive docs available at `/docs`.

### Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | None | Login (returns JWT) |
| POST | `/auth/refresh` | None | Refresh access token |
| GET | `/auth/me` | Bearer | Current user profile |
| POST | `/auth/change-password` | Bearer | Change password |
| POST | `/auth/logout` | Bearer | Logout |

### Violations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/violations` | None | Submit violation (Edge AI) |
| POST | `/violations/manual` | Police | Manually create violation |
| GET | `/violations` | Police | List with filters & pagination |
| GET | `/violations/stats` | Police | Dashboard statistics |
| GET | `/violations/{id}` | Police | Get single violation |
| POST | `/violations/{id}/approve` | Police | Approve violation |
| POST | `/violations/{id}/reject` | Police | Reject violation |
| POST | `/violations/{id}/files` | None | Upload evidence files |
| GET | `/violations/{id}/files/{filename}` | None | Serve evidence file |
| POST | `/violations/{id}/verify-integrity` | Police | Verify SHA-256 hashes |

### Live Monitoring

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| WS | `/live/ws` | None | WebSocket feed |
| POST | `/live/violations/report` | None | Report + save live violation |
| POST | `/live/cameras/status` | None | Update camera status |
| GET | `/live/cameras` | Police | List configured cameras |
| POST | `/live/cameras` | Police | Add camera |
| DELETE | `/live/cameras/{id}` | Police | Remove camera |
| GET | `/live/stats` | Police | Live statistics |
| GET | `/live/recent-violations` | Police | Recent violations |
| GET | `/live/ws/count` | None | WebSocket client count |

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

### Config & Audit (Admin only)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/config` | Admin | List config keys |
| PUT | `/config` | Admin | Upsert config key |
| DELETE | `/config/{key}` | Admin | Delete config key |
| GET | `/audit-logs` | Admin | Audit log with filters |

---

## Environment Variables

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://vwatch:vwatch_pass@localhost:5432/vwatch_db` | Async DB URL |
| `DATABASE_URL_SYNC` | `postgresql://vwatch:vwatch_pass@localhost:5432/vwatch_db` | Sync DB URL |
| `SECRET_KEY` | auto-generated | JWT signing key (set in production!) |
| `DEBUG` | `false` | Enable debug mode |
| `UPLOAD_DIR` | `/app/uploads` | Evidence file storage path |
| `ALLOWED_ORIGINS` | `["*"]` | CORS allowed origins |
| `YOLO_MODEL_PATH` | `yolov8n.pt` | YOLO model file |
| `YOLO_DEVICE` | `cpu` | `cpu` or `cuda` |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_USER` | `` | SMTP username |
| `SMTP_PASSWORD` | `` | SMTP password |

### Frontend (build-time)

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `/api/v1` (Docker) / `http://localhost:8000/api/v1` (dev) | Backend API base URL |
| `VITE_WS_URL` | `ws://localhost/api/v1` (Docker) / `ws://localhost:8000/api/v1` (dev) | WebSocket base URL |

### Edge AI

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_URL` | `http://localhost:8000` | Backend URL (use `http://backend:8000` in Docker) |
| `CAMERA_SOURCE` | `0` | Video source (0=webcam, or RTSP URL) |
| `CAMERA_ID` | `CAM_001` | Camera identifier |
| `LOCATION` | `Main Street Camera` | Camera location label |
| `YOLO_MODEL_PATH` | `yolov8n.pt` | YOLO model |
| `YOLO_DEVICE` | `cpu` | Device |
| `DISPLAY` | `true` | Show OpenCV window |

---

## Edge AI Module

The Edge AI module (`edge_ai/`) processes video streams and automatically submits violations to the backend.

### How it works

1. **Video stream** — OpenCV reads webcam, RTSP, or file source
2. **YOLO detection** — Single YOLOv8 model instance detects vehicles (singleton pattern, loaded once)
3. **DeepSORT tracking** — Assigns persistent IDs across frames
4. **Violation analysis** — Speed, red-light, wrong direction, lane change detection
5. **ANPR** — License plate recognition via EasyOCR + Tesseract
6. **Evidence** — Frame, plate, video clip saved to `evidence_store/`
7. **API submission** — POSTs to `POST /api/v1/violations`, uploads files, broadcasts to WebSocket
8. **Offline buffering** — Queued to `offline_buffer.jsonl` if backend unreachable

### Evidence upload flow

```
Edge AI                           Backend
  │                                  │
  ├─ POST /api/v1/violations ───────►│ Creates PENDING record → returns {id}
  │                                  │
  ├─ POST /api/v1/violations/{id}/files ──► Saves frame.jpg, plate.jpg, clip.mp4
  │                                  │
  └─ POST /api/v1/live/violations/report ──► Broadcasts to WebSocket clients
```

---

## Admin YOLO Test

The **YOLO Test** page (Admin sidebar → "YOLO Test") lets you:

1. Check whether the YOLO model is loaded or running in mock mode
2. Upload a video (MP4, AVI, MOV, MKV, WebM — max 200 MB)
3. Set confidence threshold and camera metadata
4. Run YOLOv8 detection — results show per-frame bounding boxes, class breakdown, thumbnail
5. Optionally save detected vehicles as PENDING violation records

**Mock mode**: If `ultralytics` is not installed in the backend container, the system runs in mock mode — all API flows work identically but detections are simulated placeholder boxes.

To enable real YOLO detection in the backend container:
```bash
docker compose exec backend pip install ultralytics
docker compose restart backend
```

---

## Troubleshooting

### Backend fails to start

```bash
# Check logs
docker compose logs backend --tail=50

# Most common: database not ready yet
docker compose restart backend
```

### WebSocket connection fails

- In Docker: nginx is configured to proxy WebSocket at `/api/v1/live/ws` with correct `Upgrade` headers
- In dev: vite proxy handles WebSocket (`ws: true` in `vite.config.ts`)
- Check browser console for connection errors

### Evidence files not found (404)

- Backend serves files at `/api/v1/violations/{id}/files/{filename}`
- In Docker, both backend and edge_ai share the `evidence_store` volume
- Verify the volume is mounted: `docker compose exec backend ls /app/uploads`

### YOLO model not loading

```bash
# Backend logs show whether ultralytics loaded
docker compose logs backend | grep YOLO

# Install manually inside running container
docker compose exec backend pip install ultralytics
docker compose restart backend
```

### Camera permission denied (webcam)

- Browser must have camera permission granted
- In Chrome: click the camera icon in the address bar → Allow
- HTTPS is required for camera access on deployed (non-localhost) sites

### Violations not appearing on Violations page

- Violations are saved to DB by `POST /live/violations/report` (live webcam) or `POST /violations` (edge AI)
- The Violations page auto-refreshes via WebSocket when new violations arrive
- Click **Refresh** button or the blue "N new — click to refresh" banner

### Docker containers can't communicate

All services must be on the `vwatch_net` bridge network. Verify:
```bash
docker network inspect vwatch_vwatch_net
```

The edge_ai uses `http://backend:8000` (Docker service name, not `localhost`).

---

## Project Structure

```
vwatch/
├── backend/                    # FastAPI backend
│   ├── app/
│   │   ├── api/               # Route handlers
│   │   │   ├── auth.py
│   │   │   ├── violations.py  # Core violations CRUD
│   │   │   ├── live_monitoring.py  # WebSocket + live feed
│   │   │   ├── yolo_analysis.py    # Admin YOLO test endpoint
│   │   │   ├── users.py
│   │   │   └── config_api.py
│   │   ├── core/              # Config, DB, security, deps
│   │   ├── models/            # SQLAlchemy ORM models
│   │   ├── schemas/           # Pydantic request/response schemas
│   │   ├── services/          # Notification service
│   │   └── main.py            # App entry point
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                  # React + Vite + Tailwind
│   ├── src/
│   │   ├── pages/             # Page components
│   │   ├── components/        # Layout, shared UI
│   │   ├── store/             # Zustand auth store
│   │   └── utils/             # Axios API client, WebSocket
│   ├── nginx.conf             # Nginx with WS proxy
│   └── Dockerfile
├── edge_ai/                   # YOLOv8 edge detection module
│   ├── main.py                # Engine orchestrator
│   ├── detectors/             # YOLO singleton vehicle detector
│   ├── trackers/              # DeepSORT multi-object tracker
│   ├── violations/            # Speed, red-light, direction detectors
│   ├── anpr/                  # License plate recognition
│   ├── evidence/              # Evidence generation
│   └── utils/                 # API client (with offline buffer)
├── config/
│   └── edge_config.json       # Edge AI configuration
├── scripts/
│   └── init_db.sql            # Database initialization
└── docker-compose.yml
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.
