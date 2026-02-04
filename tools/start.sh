#!/bin/bash
set -e

cd "$(dirname "$0")/.."

echo "Starting Ollama..."
ollama serve &
OLLAMA_PID=$!

# Wait for Ollama to be ready
echo "Waiting for Ollama to be ready..."
until curl -s http://localhost:11434/api/tags > /dev/null 2>&1; do
  sleep 1
done
echo "Ollama is ready"

# Ensure model is available
MODEL="llama3.2:3b"
if ! ollama list | grep -q "$MODEL"; then
  echo "Pulling $MODEL..."
  ollama pull "$MODEL"
fi

echo "Starting AI service..."
export PIPER_MODEL_PATH=models/en_US-ryan-high.onnx
export VOSK_MODEL_PATH=models/vosk-model-small-en-us-0.15
export MQTT_HOST=localhost
export MQTT_PORT=1883
export OLLAMA_URL=http://localhost:11434
export OLLAMA_MODEL=llama3.2:3b
export API_URL=http://localhost:3000

cd apps/ai
../../.venv/bin/uvicorn src.api:app --host 0.0.0.0 --port 8000 &
AI_PID=$!
cd ../..

# Wait for AI service to be ready
echo "Waiting for AI service to be ready..."
until curl -s http://localhost:8000/health > /dev/null 2>&1; do
  sleep 1
done
echo "AI service is ready"

echo "Starting Docker services..."
docker compose up -d

echo ""
echo "All services started:"
echo "  - Ollama:  http://localhost:11434 (PID: $OLLAMA_PID)"
echo "  - AI:      http://localhost:8000 (PID: $AI_PID)"
echo "  - API:     http://localhost:3000"
echo "  - Web:     http://localhost:5173"
echo ""
echo "To stop: ./tools/stop.sh"

# Keep script running to maintain background processes
trap "kill $OLLAMA_PID $AI_PID 2>/dev/null" EXIT
wait