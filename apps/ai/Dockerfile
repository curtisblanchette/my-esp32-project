FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    wget \
    unzip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create models directory
RUN mkdir -p /app/models

# Download Vosk model (small English model ~40MB)
RUN wget -q https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
    && unzip -q vosk-model-small-en-us-0.15.zip -d /app/models \
    && rm vosk-model-small-en-us-0.15.zip

# Download Kokoro ONNX model (~326MB fp32)
RUN curl -L -o /app/models/kokoro-v1.0.onnx \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"

# Download Kokoro voice embeddings (~27MB)
RUN curl -L -o /app/models/voices-v1.0.bin \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# Copy application code
COPY config/ config/
COPY src/ src/

# Set Python path and model paths
ENV PYTHONPATH=/app
ENV VOSK_MODEL_PATH=/app/models/vosk-model-small-en-us-0.15
ENV KOKORO_MODEL_PATH=/app/models/kokoro-v1.0.onnx
ENV KOKORO_VOICES_PATH=/app/models/voices-v1.0.bin
ENV KOKORO_VOICE=af_heart
ENV KOKORO_SPEED=1.0
ENV KOKORO_LANG=en-us

# Expose HTTP API port
EXPOSE 8000

# Run the API server
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
