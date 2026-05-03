# V-Watch — Production-Grade 24/7 CCTV AI System

[![Docker](https://img.shields.io/badge/Docker-Compose-blue)](https://docs.docker.com/compose/)
[![Python](https://img.shields.io/badge/Python-3.11-green)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-teal)](https://fastapi.tiangolo.com)
[![YOLOv8](https://img.shields.io/badge/YOLOv8-Ultralytics-red)](https://ultralytics.com)

> **Production-grade traffic violation detection system with 24/7 uptime, auto-recovery watchdog, persistent camera streaming, and multi-container Docker architecture.**

---

## ✅ All Problems Fixed

| Problem | Root Cause | Fix Applied |
|---------|-----------|-------------|
| Camera stops on page change | Camera lifecycle tied to HTTP request | Dedicated `stream_relay` container — independent HTTP MJPEG stream |
| YOLO re-downloads model | No model persistence | Model **baked into Docker image** at build time; shared `model_cache` volume |
| `Camera index out of range` | Trying webcam device 0 inside Docker | `CAMERA_SOURCE=demo` default; optional `/dev/video0` device mapping |
| Container crashes & restarts | No recovery logic | `restart: always` + internal thread watchdog + external `watchdog` service |
| Edge AI depends on frontend | Tightly coupled architecture | Edge AI is now **fully independent** — runs in its own container with health endpoint |
| No auto-recovery | Manual intervention needed | `watchdog` service polls `/health` every 15s, auto-restarts via Docker CLI |
| No RTSP support | Hard-coded webcam only | Full RTSP support via `CAMERA_SOURCE=rtsp://...` |
| No reconnect logic | Single-attempt camera open | Exponential back-off reconnect: 2s → 3s → 4.5s → ... up to 60s |

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     V-Watch Docker Network (172.20.0.0/24)               │
│                                                                           │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                 │
│  │   Frontend   │   │   Backend    │   │  PostgreSQL  │                 │
│  │  React/Nginx │──▶│   FastAPI    │──▶│   16-alpine  │                 │
│  │  Port: 3000  │   │  Port: 8000  │   │  Port: 5432  │                 │
│  └──────────────┘   └──────┬───────┘   └──────────────┘                 │
│                             │                                             │
│  ┌──────────────────────────▼──────────────────────────┐                │
│  │              stream_relay (ALWAYS ON)               │                │
│  │  • Persistent MJPEG HTTP stream                     │                │
│  │  • Auto-reconnects camera source                    │                │
│  │  • Works even during Edge AI restarts               │                │
│  │  Port: 8002  /stream/CAM_001  /snapshot/CAM_001     │                │
│  └──────────────────────────┬──────────────────────────┘                │
│                              │ Camera Source                             │
│  ┌───────────────────────────▼──────────────────────────┐               │
│  │           edge_ai (profile: edge / full)             │               │
│  │  • YOLO singleton — loaded ONCE, stays in memory     │               │
│  │  • StreamHandler — persistent capture thread         │               │
│  │  • Auto-reconnect with exponential back-off          │               │
│  │  • /health endpoint on port 8001                     │               │
│  │  • Violation API client with offline buffer          │               │
│  └──────────────────────────────────────────────────────┘               │
│                                                                           │
│  ┌───────────────────────────────────────────────────────┐               │
│  │           watchdog (profile: watchdog / full)         │               │
│  │  • Polls /health every 15 seconds                     │               │
│  │  • Auto-restarts containers via Docker CLI            │               │
│  │  • Exponential back-off (prevents restart storms)     │               │
│  │  • /status endpoint on port 9090                      │               │
│  └───────────────────────────────────────────────────────┘               │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 📋 Prerequisites

### Required Software
```bash
# Check versions (minimum required)
docker --version          # Docker 24.x or later
docker compose version    # Docker Compose v2.x or later
git --version             # Any recent version
```

### Install Docker (if not installed)
```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# macOS: Download Docker Desktop from https://docker.com/products/docker-desktop
# Windows: Download Docker Desktop from https://docker.com/products/docker-desktop
```

---

## 🚀 Quick Start (5 Minutes)

### Step 1: Clone the Repository
```bash
git clone https://github.com/YOUR_USERNAME/vwatch-cctv-ai.git
cd vwatch-cctv-ai
```

### Step 2: Configure Environment
```bash
# Copy example config
cp .env.example .env

# (Optional) Edit .env to customize settings
# The defaults work out-of-the-box with demo/synthetic camera
nano .env
```

### Step 3: Start Core Services
```bash
# Start: postgres + backend + stream_relay + frontend
docker compose up -d --build
```

### Step 4: Verify Everything is Running
```bash
# Check health of all services
./scripts/healthcheck.sh

# Or manually:
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8002/health
```

### Step 5: Open the Dashboard
```
Frontend:    http://localhost:3000
API Docs:    http://localhost:8000/docs
Live Stream: http://localhost:8002/stream/CAM_001

Default login:
  Username: admin
  Password: Admin@123!
```

---

## 🎥 Camera Configuration

### Option A: Demo Mode (No Camera — Default)
Works immediately with no hardware. Generates synthetic frames.
```bash
# .env
CAMERA_SOURCE=demo
CAMERA_ID=CAM_001
```

### Option B: USB Webcam (`/dev/video0`)
```bash
# .env
CAMERA_SOURCE=0
CAMERA_ID=CAM_001
```

**Also uncomment in `docker-compose.yml`** under `edge_ai` and `stream_relay`:
```yaml
devices:
  - /dev/video0:/dev/video0
```

Verify your camera device:
```bash
v4l2-ctl --list-devices      # List all video devices
ls -la /dev/video*           # Check device nodes
```

### Option C: RTSP Network Camera
```bash
# .env
CAMERA_SOURCE=rtsp://admin:password@192.168.1.100:554/stream1
CAMERA_ID=CAM_ENTRANCE
LOCATION=Building Entrance
```

### Option D: Multiple Cameras
Edit `config/relay_cameras.json`:
```json
[
  { "id": "CAM_001", "source": "0",                    "fps": 15 },
  { "id": "CAM_002", "source": "rtsp://192.168.1.101:554/stream", "fps": 10 },
  { "id": "CAM_003", "source": "demo",                 "fps": 15 }
]
```

---

## 🐳 Docker Build & Run Commands

### Build All Images
```bash
# Build all services (no cache — fresh build)
docker compose --profile full build --no-cache

# Build specific service
docker compose build edge_ai
docker compose build stream_relay
docker compose build watchdog
```

### Run Modes

#### Core Only (No AI Detection)
```bash
docker compose up -d
# Starts: postgres, backend, stream_relay, frontend
```

#### Core + Edge AI Detection
```bash
docker compose --profile edge up -d
# Starts: postgres, backend, stream_relay, frontend, edge_ai
```

#### Full Production (Core + Edge AI + Watchdog)
```bash
docker compose --profile full up -d
# Starts: ALL services including watchdog auto-recovery
```

#### Using the Start Script
```bash
chmod +x scripts/start.sh
./scripts/start.sh core      # Core services only
./scripts/start.sh edge      # Core + Edge AI
./scripts/start.sh full      # All services + watchdog
```

### View Logs
```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f edge_ai
docker compose logs -f stream_relay
docker compose logs -f watchdog
docker compose logs -f backend

# Last 100 lines
docker compose logs --tail=100 edge_ai
```

### Stop Everything
```bash
docker compose --profile full down

# Or use the script
./scripts/stop.sh
```

### Restart a Single Service
```bash
docker compose restart edge_ai
docker compose restart stream_relay
docker compose restart backend
```

---

## 🔧 Production Deployment

### Step 1: Set Strong Secrets
```bash
# Generate a strong secret key
openssl rand -hex 32

# Update .env
SECRET_KEY=<your-generated-key>
POSTGRES_PASSWORD=<strong-password>
```

### Step 2: Run Full Production Stack
```bash
docker compose --profile full up -d --build
```

### Step 3: Verify Watchdog is Active
```bash
# Check watchdog status
curl http://localhost:9090/status | python3 -m json.tool

# View watchdog logs
docker compose logs -f watchdog
```

### Step 4: Set Up Log Rotation (Optional)
```bash
# Logs are automatically rotated (20MB max, 5 files) in each container
# Docker container logs also have rotation configured in docker-compose.yml:
# max-size: "20m", max-file: "5"
```

---

## 📊 Service URLs Reference

| Service | URL | Description |
|---------|-----|-------------|
| Frontend | `http://localhost:3000` | React dashboard |
| Backend API | `http://localhost:8000` | FastAPI REST API |
| API Docs | `http://localhost:8000/docs` | Swagger UI |
| Backend Health | `http://localhost:8000/health` | Health check |
| MJPEG Stream | `http://localhost:8002/stream/CAM_001` | Live camera stream |
| Snapshot | `http://localhost:8002/snapshot/CAM_001` | Latest frame JPEG |
| Relay Health | `http://localhost:8002/health` | Relay health |
| Relay Cameras | `http://localhost:8002/cameras` | Camera list |
| Edge AI Health | `http://localhost:8001/health` | Edge AI health |
| Edge AI Metrics | `http://localhost:8001/metrics` | Detailed metrics |
| Watchdog Status | `http://localhost:9090/status` | Recovery status |
| Watchdog Health | `http://localhost:9090/health` | Watchdog health |

---

## 🌐 Embedding Live Stream in Frontend

The stream relay serves a persistent MJPEG stream. Embed it in any HTML:

```html
<!-- Always-on camera stream (survives page navigation) -->
<img src="http://localhost:8002/stream/CAM_001" 
     alt="Live Camera" 
     style="width:100%;max-width:1280px" />
```

In React:
```tsx
// Camera stream that never stops (backend serves MJPEG independently)
<img
  src={`${RELAY_URL}/stream/${cameraId}`}
  alt="Live Stream"
  className="w-full rounded"
  onError={(e) => e.currentTarget.src = '/offline-placeholder.jpg'}
/>
```

---

## 🔍 Troubleshooting

### Camera: "Cannot open source" / "Camera index out of range"
```bash
# SOLUTION 1: Use demo mode (no hardware needed)
# In .env:
CAMERA_SOURCE=demo

# SOLUTION 2: Check if webcam is accessible
ls -la /dev/video*
v4l2-ctl --list-devices

# SOLUTION 3: Uncomment devices in docker-compose.yml for edge_ai + stream_relay:
# devices:
#   - /dev/video0:/dev/video0

# SOLUTION 4: Use privileged mode (last resort)
# Add to edge_ai in docker-compose.yml:
# privileged: true
```

### YOLO: "Model downloads on every restart"
```bash
# SOLUTION: This is FIXED. Model is baked into the Docker image.
# Force rebuild to bake model:
docker compose build --no-cache edge_ai

# Verify model is in image:
docker run --rm vwatch_edge ls -la /app/models/
```

### Edge AI container keeps crashing
```bash
# Check logs
docker compose logs --tail=100 edge_ai

# Common fixes:
# 1. Increase Docker memory limit to at least 4GB
# 2. Switch to demo mode: CAMERA_SOURCE=demo
# 3. Check YOLO model is available: docker exec vwatch_edge ls /app/models/

# Run with extended start period:
# start_period: 120s  ← change in docker-compose.yml healthcheck
```

### Stream shows "offline" placeholder
```bash
# Check stream_relay health
curl http://localhost:8002/health

# Check relay logs
docker compose logs -f stream_relay

# Restart relay
docker compose restart stream_relay
```

### Backend not connecting to PostgreSQL
```bash
# Check postgres is healthy
docker compose ps postgres
docker compose logs postgres

# Wait for postgres to be fully ready (takes ~15s on first run)
docker compose restart backend
```

### Watchdog not restarting containers
```bash
# Check Docker socket is mounted
docker exec vwatch_watchdog ls -la /var/run/docker.sock

# Check watchdog status
curl http://localhost:9090/status

# Check watchdog logs
docker compose logs -f watchdog
```

---

## 🔐 Security Notes

1. **Change default secrets** before production:
   ```bash
   SECRET_KEY=$(openssl rand -hex 32)
   POSTGRES_PASSWORD=$(openssl rand -hex 16)
   ```

2. **Default admin credentials**: `admin / Admin@123!` — **change immediately**

3. **Docker socket**: The watchdog requires `/var/run/docker.sock` (read-only). This is necessary for container restart. Only expose in trusted environments.

4. **CORS**: Set `ALLOWED_ORIGINS` to your specific domain in production.

---

## 📁 Project Structure

```
vwatch-cctv-ai/
├── backend/                    # FastAPI backend
│   ├── app/
│   │   ├── api/               # REST + WebSocket endpoints
│   │   │   ├── camera_stream.py   # MJPEG + WS + camera management
│   │   │   ├── live_monitoring.py # Live WebSocket events
│   │   │   └── violations.py      # Violation CRUD
│   │   ├── services/
│   │   │   └── camera_manager.py  # Persistent camera + internal watchdog
│   │   └── main.py
│   └── Dockerfile
│
├── edge_ai/                    # YOLO detection engine
│   ├── main.py                # Production engine (health endpoint, reconnect)
│   ├── stream_handler.py      # Persistent camera stream with auto-reconnect
│   ├── detectors/             # YOLO singleton vehicle detector
│   ├── trackers/              # DeepSORT multi-object tracking
│   ├── violations/            # Speed, red-light, wrong direction detection
│   ├── anpr/                  # License plate recognition
│   ├── evidence/              # Evidence screenshot + clip generation
│   ├── utils/                 # API client (offline buffer), face blurring
│   └── Dockerfile             # YOLO model baked in at build time
│
├── stream_relay/               # ★ NEW: Persistent MJPEG relay
│   ├── relay.py               # Multi-camera HTTP MJPEG server
│   ├── Dockerfile
│   └── requirements.txt
│
├── watchdog/                   # ★ NEW: Auto-recovery watchdog
│   ├── watchdog.py            # Health monitor + Docker restart
│   ├── Dockerfile
│   └── requirements.txt
│
├── frontend/                   # React + TypeScript frontend
│   ├── src/pages/
│   └── Dockerfile
│
├── config/
│   ├── edge_config.json       # Edge AI configuration
│   └── relay_cameras.json     # Stream relay camera list
│
├── scripts/
│   ├── start.sh               # Start all services
│   ├── stop.sh                # Stop all services
│   ├── healthcheck.sh         # Check all service health
│   └── init_db.sql            # PostgreSQL initialization
│
├── docker-compose.yml          # Production multi-service compose
├── .env.example                # Environment variable template
└── README.md                   # This file
```

---

## 🧪 Testing the System

### Test Camera Stream
```bash
# Open in browser:
http://localhost:8002/stream/CAM_001

# Download a snapshot:
curl -o snapshot.jpg http://localhost:8002/snapshot/CAM_001
```

### Test Backend API
```bash
# Health check
curl http://localhost:8000/health

# Get violations (requires auth)
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/violations

# Full API docs
open http://localhost:8000/docs
```

### Test Watchdog Recovery
```bash
# Manually stop backend and watch watchdog restart it
docker stop vwatch_backend
sleep 20
docker compose logs --tail=20 watchdog
# You should see: "Triggered auto-recovery restart"
docker compose ps backend   # Should be running again
```

### Test Camera Reconnect
```bash
# Verify Edge AI auto-reconnects on camera failure
docker compose logs -f edge_ai | grep -i "reconnect\|stream\|camera"
```

---

## 📈 Performance Tuning

### Reduce CPU Usage
```bash
# Lower target FPS
TARGET_FPS=10    # in .env

# Increase YOLO frame skip (detect every 5 frames instead of 3)
# Edit edge_ai/main.py: detection_frame_skip = 5
```

### Use GPU (NVIDIA)
```bash
# In .env:
YOLO_DEVICE=cuda:0

# In docker-compose.yml edge_ai section, add:
# deploy:
#   resources:
#     reservations:
#       devices:
#         - driver: nvidia
#           count: 1
#           capabilities: [gpu]
```

### Memory Limits
```bash
# In docker-compose.yml, add to edge_ai:
# mem_limit: 4g
# memswap_limit: 4g
```

---

## 📞 Support

- **Logs location**: Docker container logs + `/app/logs/` inside containers
- **Violation buffer**: `/app/uploads/offline_buffer.jsonl` (auto-replayed on reconnect)
- **Model cache**: Shared Docker volume `model_cache`
- **Evidence files**: Shared Docker volume `evidence_store`

---

*V-Watch — Built for 24/7 production CCTV AI monitoring*
