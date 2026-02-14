"""
Voice routes: POST /api/voice/transcribe, /api/voice/synthesize, /api/voice/command

These are now direct endpoints (no proxy layer) since voice service lives in the same process.
"""

import logging

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..services.intent_executor import execute_intent

logger = logging.getLogger(__name__)


class SynthesizeRequest(BaseModel):
    message: str


def create_voice_router(sqlite, redis_client, ws_server, ollama, voice_service) -> APIRouter:
    r = APIRouter()

    @r.post("/transcribe")
    async def transcribe_audio(audio: UploadFile = File(...)):
        if not voice_service or not voice_service.is_stt_available():
            return {"ok": False, "error": "Speech-to-text service not available"}

        try:
            audio_data = await audio.read()
            text = voice_service.transcribe(audio_data)
            return {"ok": True, "text": text, "success": True}
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return {"ok": False, "error": str(e)}

    @r.post("/synthesize")
    async def synthesize_speech(request: SynthesizeRequest):
        if not voice_service or not voice_service.is_tts_available():
            return {"ok": False, "error": "Text-to-speech service not available"}

        try:
            audio_data = voice_service.synthesize(request.message)
            return Response(content=audio_data, media_type="audio/wav")
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return {"ok": False, "error": str(e)}

    @r.post("/command")
    async def voice_command(audio: UploadFile = File(...), request: Request = None):
        """Full STT → LLM → executeIntent pipeline."""
        try:
            if not voice_service or not voice_service.is_stt_available():
                return {"ok": False, "error": "Voice service unavailable"}

            # Step 1: Transcribe
            audio_data = await audio.read()
            text = voice_service.transcribe(audio_data)

            if not text:
                return {
                    "ok": True,
                    "transcription": "",
                    "response": "I didn't catch that. Could you please repeat?",
                }

            # Step 2: Interpret with LLM
            intent = ollama.interpret_message(text, sqlite, ws_server)

            # Step 3: Execute
            mqtt = getattr(request.app.state, "mqtt", None) if request else None
            result = await execute_intent(
                intent, source="voice", message=text,
                sqlite=sqlite, redis=redis_client, mqtt=mqtt, ws=ws_server,
            )

            return {
                "ok": result["ok"],
                "transcription": text,
                "response": result["reply"],
                "action": result.get("action", {}).get("type"),
                "target": result.get("action", {}).get("target"),
                "value": result.get("action", {}).get("value"),
            }

        except Exception as e:
            logger.error(f"Error processing voice command: {e}")
            return {"ok": False, "error": "Voice service unavailable"}

    @r.get("/health")
    async def voice_health():
        return {
            "ok": True,
            "service": "voice",
            "stt_available": voice_service.is_stt_available() if voice_service else False,
            "tts_available": voice_service.is_tts_available() if voice_service else False,
            "llm_available": ollama.is_available() if ollama else False,
        }

    return r
