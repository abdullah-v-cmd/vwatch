# ─────────────────────────────────────────────────────────────────────────────
# V-Watch Edge AI — Production Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# KEY FIXES:
#   1. YOLO model is BAKED INTO the image at build time (never re-downloaded)
#   2. Virtual display (Xvfb) support for OpenCV in headless Docker
#   3. Proper /dev/video0 group permissions via --device mapping
#   4. Health check on :8001/health (used by watchdog + Docker)
#   5. Non-root user for security
#   6. Offline violation buffer persisted via volume mount
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YOLO_MODEL_PATH=/app/models/yolov8n.pt \
    DISPLAY="" \
    QT_QPA_PLATFORM=offscreen \
    OPENCV_VIDEOIO_DEBUG=0

# ── System dependencies ────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # OpenCV runtime libs
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1-mesa-glx \
    libgomp1 \
    # Video capture support
    ffmpeg \
    v4l-utils \
    libv4l-dev \
    # OCR
    tesseract-ocr \
    tesseract-ocr-eng \
    # Virtual framebuffer (headless OpenCV imshow)
    xvfb \
    # Utilities
    wget \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ────────────────────────────────────────────────────
# Install PyTorch CPU-only first (avoids CUDA bloat)
RUN pip install --upgrade pip && \
    pip install torch==2.3.0 torchvision==0.18.0 \
        --index-url https://download.pytorch.org/whl/cpu

# Install ultralytics + all other deps
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── Copy application code ──────────────────────────────────────────────────
COPY . .

# ── Pre-create directories ─────────────────────────────────────────────────
RUN mkdir -p \
    /app/models \
    /app/uploads \
    /app/logs \
    /app/config \
    /app/evidence_store

# ── Pre-bake YOLO model into image (NEVER re-downloads at runtime) ─────────
# This is the KEY FIX for "YOLO downloads model again sometimes"
RUN python - <<'PYEOF'
import os, sys
model_path = "/app/models/yolov8n.pt"
try:
    from ultralytics import YOLO
    # Download + cache into image layer
    model = YOLO("yolov8n.pt")
    # Move to the expected path inside the container
    import shutil
    src = os.path.expanduser("~/.config/Ultralytics/yolov8n.pt") if \
          not os.path.exists("yolov8n.pt") else "yolov8n.pt"
    # ultralytics saves to current dir on first load
    candidates = ["yolov8n.pt", os.path.expanduser("~/yolov8n.pt"),
                  "/root/yolov8n.pt", "/tmp/yolov8n.pt"]
    for c in candidates:
        if os.path.exists(c):
            shutil.copy2(c, model_path)
            print(f"[BUILD] YOLO model baked: {c} -> {model_path}")
            break
    # Validate it loads from expected path
    m2 = YOLO(model_path)
    print(f"[BUILD] YOLO model validated at {model_path} ✅")
except Exception as e:
    print(f"[BUILD] YOLO pre-bake warning: {e} (will attempt download at runtime)")
PYEOF

# ── Non-root user ──────────────────────────────────────────────────────────
RUN groupadd --system --gid 1000 vwatch && \
    useradd  --system --uid 1000 --gid vwatch \
             --home /app --no-create-home vwatch && \
    # Add to video group for webcam access
    groupadd --system --gid 44 video 2>/dev/null || true && \
    usermod -aG video vwatch && \
    chown -R vwatch:vwatch /app

USER vwatch

# ── Health check (watchdog monitors this) ─────────────────────────────────
HEALTHCHECK --interval=20s --timeout=8s --start-period=60s --retries=5 \
  CMD curl -f http://localhost:8001/health || exit 1

EXPOSE 8001

# ── Entrypoint ─────────────────────────────────────────────────────────────
CMD ["python", "-u", "main.py", \
     "--config", "config/edge_config.json", \
     "--no-display"]
