"""
FastAPI HTTP API for voice processing (STT/TTS utilities).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .config import (
    VOSK_MODEL_PATH,
    KOKORO_MODEL_PATH,
    KOKORO_VOICES_PATH,
    KOKORO_VOICE,
    KOKORO_SPEED,
    KOKORO_LANG,
)
from .services.voice_service import VoiceService
from .services.shared import get_shared_services

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Global services (initialized on startup)
voice_service: VoiceService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup."""
    global voice_service

    logger.info("Initializing voice service...")
    voice_service = VoiceService(
        vosk_model_path=VOSK_MODEL_PATH,
        kokoro_model_path=KOKORO_MODEL_PATH,
        kokoro_voices_path=KOKORO_VOICES_PATH,
        kokoro_voice=KOKORO_VOICE,
        kokoro_speed=KOKORO_SPEED,
        kokoro_lang=KOKORO_LANG,
    )

    yield


app = FastAPI(
    title="ESP32 AI Voice API",
    description="Voice processing (STT/TTS) utilities for IoT control",
    version="1.0.0",
    lifespan=lifespan,
)


class TranscriptionResponse(BaseModel):
    text: str
    success: bool


class ChatRequest(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    stt_available: bool
    tts_available: bool
    llm_available: bool


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service health and availability."""
    shared = get_shared_services()
    ollama = shared.ollama
    return HealthResponse(
        status="ok",
        stt_available=voice_service.is_stt_available() if voice_service else False,
        tts_available=voice_service.is_tts_available() if voice_service else False,
        llm_available=ollama.is_available() if ollama else False,
    )


@app.post("/voice/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(audio: UploadFile = File(...)):
    """
    Transcribe audio to text using Vosk.

    Accepts WAV files (16kHz mono preferred) or raw PCM audio.
    """
    if not voice_service or not voice_service.is_stt_available():
        raise HTTPException(status_code=503, detail="Speech-to-text service not available")

    try:
        audio_data = await audio.read()
        text = voice_service.transcribe(audio_data)
        return TranscriptionResponse(text=text, success=True)
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/voice/synthesize")
async def synthesize_speech(request: ChatRequest):
    """
    Synthesize text to speech using Kokoro.

    Returns WAV audio data (24kHz).
    """
    if not voice_service or not voice_service.is_tts_available():
        raise HTTPException(status_code=503, detail="Text-to-speech service not available")

    try:
        audio_data = voice_service.synthesize(request.message)
        return Response(content=audio_data, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
