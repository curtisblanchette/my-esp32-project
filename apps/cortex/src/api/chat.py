"""
Chat routes: POST /api/chat, POST /api/chat/stream, GET /api/chat/health

Ported from apps/api/src/routes/chat.ts
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services.intent_executor import execute_intent

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    deviceId: str | None = None
    location: str | None = None


def create_chat_router(sqlite, redis_client, ws_server, ollama) -> APIRouter:
    r = APIRouter()

    @r.post("")
    async def chat(body: ChatRequest, request: Request):
        try:
            mqtt = getattr(request.app.state, "mqtt", None)
            intent = ollama.interpret_message(body.message, sqlite, ws_server)
            result = await execute_intent(
                intent, source="chat", message=body.message,
                sqlite=sqlite, redis=redis_client, mqtt=mqtt, ws=ws_server,
                device_id=body.deviceId, location=body.location,
            )

            if not result["ok"] and intent.get("intent") == "command":
                return {"ok": False, "error": "MQTT client not connected", **result}

            return result

        except Exception as e:
            logger.error(f"Error processing chat message: {e}")
            error_msg = str(e)
            if "fetch failed" in error_msg or "ECONNREFUSED" in error_msg or "ConnectError" in error_msg:
                return {
                    "ok": False,
                    "error": "AI service unavailable",
                    "reply": "The AI assistant is currently unavailable. Please try again later.",
                }
            return {
                "ok": False,
                "error": "Failed to process chat message",
                "reply": "Something went wrong. Please try again.",
            }

    @r.post("/stream")
    async def chat_stream(body: ChatRequest, request: Request):
        mqtt = getattr(request.app.state, "mqtt", None)

        async def generate():
            try:
                async for chunk in ollama.interpret_message_stream(body.message, sqlite, ws_server):
                    if chunk["type"] == "token":
                        yield f"data: {json.dumps({'type': 'token', 'token': chunk['token']})}\n\n"
                    elif chunk["type"] == "done":
                        result = await execute_intent(
                            chunk["intent"], source="chat", message=body.message,
                            sqlite=sqlite, redis=redis_client, mqtt=mqtt, ws=ws_server,
                            device_id=body.deviceId, location=body.location,
                        )
                        yield f"data: {json.dumps({'type': 'done', **result})}\n\n"
            except Exception as e:
                logger.error(f"Error in streaming chat: {e}")
                error_msg = str(e)
                if "fetch failed" in error_msg or "ECONNREFUSED" in error_msg or "ConnectError" in error_msg:
                    yield f"data: {json.dumps({'type': 'error', 'error': 'AI service unavailable', 'reply': 'The AI assistant is currently unavailable. Please try again later.'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Failed to process chat message', 'reply': 'Something went wrong. Please try again.'})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @r.get("/health")
    async def chat_health():
        healthy = ollama.is_available()
        return {"ok": healthy, "service": "ollama"}

    return r
