#!/bin/bash

echo "Stopping Docker services..."
docker compose down

echo "Stopping AI service..."
pkill -f "uvicorn src.api:app" 2>/dev/null || true

echo "Stopping Ollama..."
pkill -f "ollama serve" 2>/dev/null || true

echo "All services stopped"