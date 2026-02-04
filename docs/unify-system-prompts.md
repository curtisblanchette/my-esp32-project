# Plan: Unify User-Facing System Prompts

## Goal
Ensure all user-facing interfaces (web chat, voice commands) use the same full-featured system prompt from the Node.js API, while keeping the background automation prompt separate and optimization-focused.

## Current State
Three separate system prompts exist:
1. **`apps/api/src/services/ollama.ts:59-112`** — Full-featured, dynamic (devices from DB, current readings, all intents)
2. **`apps/ai/src/api.py:259-266`** — Hardcoded, limited (only relay1)
3. **`apps/ai/src/services/ollama_client.py:16-32`** — Conservative, automation-focused (keep as-is)

## Change

### Update Python `api.py` to delegate to Node.js API

**File:** `apps/ai/src/api.py`

Replace `_process_with_llm()` function (lines 255-286) to call Node.js API instead of Ollama directly:

```python
def _process_with_llm(user_message: str) -> dict:
    """Process user message via Node.js API for consistent prompt handling."""
    import httpx
    from .config import API_URL  # Already exists: http://localhost:3000

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{API_URL}/api/chat",
                json={"message": user_message},
            )
            response.raise_for_status()
            data = response.json()

            # Map Node.js response format to Python API format
            action_data = data.get("action", {})
            return {
                "action": action_data.get("type") if action_data else "none",
                "target": action_data.get("target") if action_data else None,
                "value": action_data.get("value") if action_data else None,
                "response": data.get("reply", ""),
            }
    except Exception as e:
        logger.error(f"Error calling Node.js API: {e}")
        return {"action": "none", "response": "I'm having trouble processing that right now."}
```

**Note:** `API_URL` already exists in `apps/ai/src/config.py:17` — no config changes needed.

### Keep `ollama_client.py` unchanged

The background automation prompt stays separate and optimization-focused for autonomous decisions.

## Files to Modify
- `apps/ai/src/api.py` — Replace `_process_with_llm()` function

## Latency Impact
- Added network hop: ~1-5ms
- Node.js processing: ~5-20ms
- Ollama inference: ~500-3000ms (unchanged)
- **Total overhead: ~1-2%** — negligible

## Verification
1. Start services: `docker compose up -d` and Python AI service
2. Test web chat: Send message via web dashboard, verify response
3. Test voice/Python chat:
   ```bash
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "turn on the light"}'
   ```
4. Verify both return consistent responses with full device awareness
5. Test background automation still works independently (send telemetry via MQTT)