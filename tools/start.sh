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

echo "Starting Docker services (Mosquitto, Redis, Web)..."
docker compose up -d
sleep 2

echo "Starting Cortex..."
export MQTT_HOST=localhost
export MQTT_PORT=1883
export OLLAMA_URL=http://localhost:11434
export OLLAMA_MODEL=llama3.2:3b
export REDIS_URL=redis://localhost:6381
export SQLITE_PATH=data/telemetry.sqlite
export SQLITE_JOURNAL_MODE=WAL
export HTTP_PORT=8000
export VOSK_MODEL_PATH=models/vosk-model-small-en-us-0.15
export KOKORO_MODEL_PATH=models/kokoro-v1.0.onnx
export KOKORO_VOICES_PATH=models/voices-v1.0.bin

cd apps/cortex
../../.venv/bin/python -m src.main &
CORTEX_PID=$!
cd ../..

# Wait for Cortex to be ready
echo "Waiting for Cortex to be ready..."
until curl -s http://localhost:8000/health > /dev/null 2>&1; do
  sleep 1
done
echo "Cortex is ready"

docker compose logs -f &
DOCKER_LOGS_PID=$!

echo ""
echo "All services started:"
echo "  - Ollama:    http://localhost:11434 (PID: $OLLAMA_PID)"
echo "  - Cortex:    http://localhost:8000 (PID: $CORTEX_PID)"
echo "  - WebSocket: ws://localhost:8000/ws"
echo "  - Web:       http://localhost:5173"
echo ""
echo "To stop: ./tools/stop.sh"

# Keep script running; clean up everything on exit
cleanup() {
  echo ""
  echo "Shutting down services..."
  kill $DOCKER_LOGS_PID 2>/dev/null
  kill $CORTEX_PID 2>/dev/null
  kill $OLLAMA_PID 2>/dev/null
  docker compose down
  echo "All services stopped"
}
trap cleanup EXIT INT TERM
wait
