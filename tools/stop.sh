#!/bin/bash

echo "Stopping Docker services..."
docker compose down

echo "Stopping Cortex..."
pkill -f "python -m src.main" 2>/dev/null || true

echo "Stopping Ollama..."
pkill -f "ollama serve" 2>/dev/null || true

echo "All services stopped"