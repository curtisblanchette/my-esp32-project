"""
FastAPI HTTP API for voice processing and AI queries.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .config import VOSK_MODEL_PATH, PIPER_MODEL_PATH
from .services.voice_service import VoiceService
from .services.shared import get_shared_services

logger = logging.getLogger(__name__)

# Global services (initialized on startup)
voice_service: VoiceService | None = None


def _get_ollama():
    """Get the shared Ollama client, initializing if needed (standalone mode)."""
    shared = get_shared_services()
    if shared.ollama is None:
        # Running in standalone mode (not via orchestrator) - initialize Ollama
        from .config import OLLAMA_URL, OLLAMA_MODEL
        shared.init_ollama(base_url=OLLAMA_URL, model=OLLAMA_MODEL)
    return shared.ollama


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup."""
    global voice_service

    logger.info("Initializing voice service...")
    voice_service = VoiceService(
        vosk_model_path=VOSK_MODEL_PATH,
        piper_model_path=PIPER_MODEL_PATH,
    )

    # Note: Ollama client is shared and initialized by the orchestrator
    # We don't close it here as the orchestrator manages its lifecycle

    yield


app = FastAPI(
    title="ESP32 AI Voice API",
    description="Voice processing and AI assistant for IoT control",
    version="1.0.0",
    lifespan=lifespan,
)


class TranscriptionResponse(BaseModel):
    text: str
    success: bool


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str
    action: str | None = None
    target: str | None = None
    value: Any = None


class VoiceCommandResponse(BaseModel):
    transcription: str
    response: str
    action: str | None = None
    target: str | None = None
    value: Any = None


class HealthResponse(BaseModel):
    status: str
    stt_available: bool
    tts_available: bool
    llm_available: bool


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service health and availability."""
    ollama = _get_ollama()
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
    Synthesize text to speech using Piper.

    Returns WAV audio data.
    """
    if not voice_service or not voice_service.is_tts_available():
        raise HTTPException(status_code=503, detail="Text-to-speech service not available")

    try:
        audio_data = voice_service.synthesize(request.message)
        return Response(content=audio_data, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/voice/command", response_model=VoiceCommandResponse)
async def voice_command(audio: UploadFile = File(...)):
    """
    Process a voice command end-to-end.

    1. Transcribe audio to text
    2. Send to Ollama for processing
    3. Return response (and optional audio if TTS available)
    """
    if not voice_service or not voice_service.is_stt_available():
        raise HTTPException(status_code=503, detail="Speech-to-text service not available")

    ollama = _get_ollama()
    if not ollama or not ollama.is_available():
        raise HTTPException(status_code=503, detail="LLM service not available")

    try:
        # Step 1: Transcribe
        audio_data = await audio.read()
        transcription = voice_service.transcribe(audio_data)

        if not transcription:
            return VoiceCommandResponse(
                transcription="",
                response="I didn't catch that. Could you please repeat?",
            )

        # Step 2: Process with LLM
        llm_response = _process_with_llm(transcription)

        return VoiceCommandResponse(
            transcription=transcription,
            response=llm_response.get("response", ""),
            action=llm_response.get("action"),
            target=llm_response.get("target"),
            value=llm_response.get("value"),
        )

    except Exception as e:
        logger.error(f"Voice command processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/voice/command/audio")
async def voice_command_with_audio(audio: UploadFile = File(...)):
    """
    Process a voice command and return audio response.

    Returns WAV audio of the AI response.
    """
    if not voice_service:
        raise HTTPException(status_code=503, detail="Voice service not available")

    if not voice_service.is_stt_available():
        raise HTTPException(status_code=503, detail="Speech-to-text service not available")

    if not voice_service.is_tts_available():
        raise HTTPException(status_code=503, detail="Text-to-speech service not available")

    ollama = _get_ollama()
    if not ollama or not ollama.is_available():
        raise HTTPException(status_code=503, detail="LLM service not available")

    try:
        # Transcribe
        audio_data = await audio.read()
        transcription = voice_service.transcribe(audio_data)

        if not transcription:
            response_text = "I didn't catch that. Could you please repeat?"
        else:
            # Process with LLM
            llm_response = _process_with_llm(transcription)
            response_text = llm_response.get("response", "I'm not sure how to respond to that.")

        # Synthesize response
        response_audio = voice_service.synthesize(response_text)
        return Response(content=response_audio, media_type="audio/wav")

    except Exception as e:
        logger.error(f"Voice command with audio failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a text message to the AI assistant.
    """
    ollama = _get_ollama()
    if not ollama or not ollama.is_available():
        raise HTTPException(status_code=503, detail="LLM service not available")

    try:
        llm_response = _process_with_llm(request.message)
        return ChatResponse(
            response=llm_response.get("response", ""),
            action=llm_response.get("action"),
            target=llm_response.get("target"),
            value=llm_response.get("value"),
        )
    except Exception as e:
        logger.error(f"Chat processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _process_with_llm(user_message: str) -> dict:
    """Process user message with Ollama and extract structured response."""
    import json

    system_prompt = """You are a helpful smart home assistant. You can control devices and provide information.

Available commands:
- Turn relay1 ON/OFF: {"action": "command", "target": "relay1", "value": true/false, "response": "your response"}
- Check sensors: {"action": "query", "target": "sensors", "response": "your response"}
- No action needed: {"action": "none", "response": "your response"}

Always respond with valid JSON. The "response" field should contain a natural language response to say to the user."""

    try:
        ollama = _get_ollama()
        if not ollama:
            return {"action": "none", "response": "LLM service not available."}

        response_text = ollama.generate(
            prompt=user_message,
            system=system_prompt,
            format="json",
        )

        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            return {"action": "none", "response": response_text}

    except Exception as e:
        logger.error(f"LLM processing error: {e}")
        return {"action": "none", "response": "I'm having trouble processing that right now."}
