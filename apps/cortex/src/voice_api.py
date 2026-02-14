"""
Cortex — unified FastAPI application.

Consolidates all backend functionality: REST API, WebSocket, voice STT/TTS.
This replaces both the old voice-only API and the Node.js Express server.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from .config import (
    REDIS_URL, SQLITE_PATH, SQLITE_JOURNAL_MODE,
    VOSK_MODEL_PATH,
    KOKORO_MODEL_PATH, KOKORO_VOICES_PATH,
    KOKORO_VOICE, KOKORO_SPEED, KOKORO_LANG,
)
from .services.redis_client import RedisClient
from .services.sqlite_client import SqliteClient
from .services.websocket_server import WebSocketServer
from .services.shared import get_shared_services
from .services.background_jobs import start_aggregation_job, start_command_expiration_job
from .api.telemetry import create_telemetry_router
from .api.devices import create_devices_router
from .api.relays import create_relays_router
from .api.commands import create_commands_router
from .api.events import create_events_router
from .api.chat import create_chat_router
from .api.voice import create_voice_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Global service instances (initialized in lifespan)
sqlite: SqliteClient | None = None
redis_client: RedisClient | None = None
ws_server: WebSocketServer | None = None
voice_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all services on startup, clean up on shutdown."""
    global sqlite, redis_client, ws_server, voice_service

    # Initialize storage
    logger.info("Initializing storage...")
    sqlite = SqliteClient(SQLITE_PATH, SQLITE_JOURNAL_MODE)
    sqlite.connect()

    redis_client = RedisClient(REDIS_URL)
    try:
        redis_client.connect()
    except Exception as e:
        logger.warning(f"Redis unavailable, running without hot storage: {e}")
        redis_client = None

    # Initialize WebSocket server
    ws_server = WebSocketServer()

    # Initialize Ollama client
    shared = get_shared_services()
    ollama = shared.init_ollama()

    # Initialize voice service
    logger.info("Initializing voice service...")
    try:
        from .services.voice_service import VoiceService
        voice_service = VoiceService(
            vosk_model_path=VOSK_MODEL_PATH,
            kokoro_model_path=KOKORO_MODEL_PATH,
            kokoro_voices_path=KOKORO_VOICES_PATH,
            kokoro_voice=KOKORO_VOICE,
            kokoro_speed=KOKORO_SPEED,
            kokoro_lang=KOKORO_LANG,
        )
    except Exception as e:
        logger.warning(f"Voice service unavailable: {e}")
        voice_service = None

    # Mount API routes (must happen before app starts serving)
    _mount_routes(app, sqlite, redis_client, ws_server, ollama, voice_service)

    # Start background jobs
    bg_tasks = []
    if redis_client:
        bg_tasks.append(asyncio.create_task(start_aggregation_job(redis_client, sqlite)))
    bg_tasks.append(asyncio.create_task(start_command_expiration_job(sqlite, ws_server)))

    logger.info("Cortex API ready")
    yield

    # Cleanup
    logger.info("Shutting down...")
    for task in bg_tasks:
        task.cancel()

    if redis_client:
        redis_client.close()
    if sqlite:
        sqlite.close()
    shared.close()


def _mount_routes(app, sqlite, redis_client, ws_server, ollama, voice_service):
    """Mount all API routers."""
    # Use a no-op redis if unavailable (analysis/history still work via SQLite)
    redis_for_routes = redis_client or _NoOpRedis()

    # Phase 1: Create DataReader for unified Redis+SQLite reads
    from .services.data_reader import DataReader
    data_reader = DataReader(redis_for_routes, sqlite)

    app.include_router(
        create_telemetry_router(sqlite, redis_for_routes, ws_server),
        prefix="/api",
    )
    app.include_router(
        create_devices_router(sqlite, ws_server),
        prefix="/api/devices",
    )
    # Relay routes need deviceId from the path
    relays_router = create_relays_router(sqlite, ws_server)
    app.include_router(relays_router, prefix="/api/devices/{deviceId}/relays")

    app.include_router(
        create_commands_router(sqlite, ws_server),
        prefix="/api/commands",
    )
    app.include_router(
        create_events_router(sqlite),
        prefix="/api/events",
    )
    app.include_router(
        create_chat_router(sqlite, redis_for_routes, ws_server, ollama, data_reader),
        prefix="/api/chat",
    )
    app.include_router(
        create_voice_router(sqlite, redis_for_routes, ws_server, ollama, voice_service, data_reader),
        prefix="/api/voice",
    )

    # Store services on app.state so main.py can inject MQTT later
    app.state.sqlite = sqlite
    app.state.redis_client = redis_client
    app.state.ws_server = ws_server
    app.state.ollama = ollama
    app.state.voice_service = voice_service


class _NoOpRedis:
    """Fallback when Redis is unavailable."""
    def get_readings_in_range(self, *args, **kwargs):
        return []
    def get_all_readings(self):
        return []
    def store_reading(self, *args, **kwargs):
        pass
    def delete_readings(self, *args, **kwargs):
        pass


app = FastAPI(
    title="Cortex API",
    description="Cortex — unified backend for ESP32 IoT system",
    version="2.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket Endpoint ───────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if ws_server and sqlite:
        await ws_server.handle_connection(websocket, sqlite)


# ── Health Check ─────────────────────────────────────────────────────

@app.get("/health")
async def health():
    shared = get_shared_services()
    return {
        "status": "ok",
        "stt_available": voice_service.is_stt_available() if voice_service else False,
        "tts_available": voice_service.is_tts_available() if voice_service else False,
        "llm_available": shared.ollama.is_available() if shared.ollama else False,
    }
