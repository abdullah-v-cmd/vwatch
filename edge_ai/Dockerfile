# ───── Base Image ─────────────────────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YOLO_MODEL_PATH=/app/models/yolov8n.pt

# ───── System Dependencies ────────────────────
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    libgomp1 \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ───── Install Python Dependencies (FAST CACHE) ─────
COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install ultralytics && \
    pip install -r requirements.txt

# ───── Copy Project ───────────────────────────
COPY . .

# ───── Create folders ─────────────────────────
RUN mkdir -p models evidence_store config

# ───── Download YOLO model safely ─────────────
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# ───── Create non-root user (security) ────────
RUN addgroup --system vwatch && adduser --system --group vwatch
RUN chown -R vwatch:vwatch /app

USER vwatch

CMD ["python", "main.py", "--config", "config/edge_config.json", "--no-display"]
