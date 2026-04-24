# V-Watch Edge AI Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# System deps for OpenCV + EasyOCR
RUN apt-get update && apt-get install -y \
    libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libgl1-mesa-glx libgles2-mesa \
    tesseract-ocr tesseract-ocr-eng \
    libgomp1 wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Install torch CPU only for edge (smaller)
RUN pip install --upgrade pip && \
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu --quiet && \
    pip install -r requirements.txt --quiet

COPY . .

RUN mkdir -p evidence_store models config

CMD ["python", "main.py", "--config", "config/edge_config.json", "--no-display"]
