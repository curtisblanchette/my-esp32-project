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
    sessionId: str | None = None


def create_chat_router(sqlite, redis_client, ws_server, ollama, data_reader=None) -> APIRouter:
    r = APIRouter()

    @r.post("")
    async def chat(body: ChatRequest, request: Request):
        try:
            mqtt = getattr(request.app.state, "mqtt", None)
            session_store = getattr(request.app.state, "chat_session_store", None)
            rule_generator = getattr(request.app.state, "rule_generator", None)
            engine = getattr(request.app.state, "engine", None)

            # Get conversation history if session is active
            conversation_history = None
            if session_store and body.sessionId:
                conversation_history = session_store.get_conversation_context(body.sessionId)
                session_store.add_message(body.sessionId, "user", body.message)

            intent = ollama.interpret_message(
                body.message, sqlite, ws_server,
                conversation_history=conversation_history,
            )
            result = await execute_intent(
                intent, source="chat", message=body.message,
                sqlite=sqlite, redis=redis_client, mqtt=mqtt, ws=ws_server,
                device_id=body.deviceId, location=body.location,
                data_reader=data_reader,
                session_store=session_store, session_id=body.sessionId,
                rule_generator=rule_generator, engine=engine,
            )

            # Record assistant reply in session
            if session_store and body.sessionId and result.get("reply"):
                session_store.add_message(body.sessionId, "assistant", result["reply"])

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
        session_store = getattr(request.app.state, "chat_session_store", None)
        rule_generator = getattr(request.app.state, "rule_generator", None)
        engine = getattr(request.app.state, "engine", None)

        # Get conversation history if session is active
        conversation_history = None
        if session_store and body.sessionId:
            conversation_history = session_store.get_conversation_context(body.sessionId)
            session_store.add_message(body.sessionId, "user", body.message)

        async def generate():
            try:
                async for chunk in ollama.interpret_message_stream(
                    body.message, sqlite, ws_server,
                    conversation_history=conversation_history,
                ):
                    if chunk["type"] == "token":
                        yield f"data: {json.dumps({'type': 'token', 'token': chunk['token']})}\n\n"
                    elif chunk["type"] == "done":
                        result = await execute_intent(
                            chunk["intent"], source="chat", message=body.message,
                            sqlite=sqlite, redis=redis_client, mqtt=mqtt, ws=ws_server,
                            device_id=body.deviceId, location=body.location,
                            data_reader=data_reader,
                            session_store=session_store, session_id=body.sessionId,
                            rule_generator=rule_generator, engine=engine,
                        )

                        # Record assistant reply in session
                        if session_store and body.sessionId and result.get("reply"):
                            session_store.add_message(body.sessionId, "assistant", result["reply"])

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
