# Plan: Streaming TTS for Reduced Latency

## Problem

Large TTS responses take 4+ seconds to synthesize before any audio plays. Users must wait for the entire synthesis to complete before hearing anything.

**Current latency measurements:**
| Message Length | Synthesis Time |
|----------------|----------------|
| ~12 chars | 0.36s |
| ~90 chars | 1.39s |
| ~300 chars | 4.14s |

## Solution

Use kokoro-onnx's built-in `create_stream()` async generator to stream audio chunks as they're synthesized. Audio starts playing after the first chunk (~0.3-0.5s) while the rest continues generating.

**Key discovery:** `kokoro-onnx` v0.5.0 already includes streaming support via `create_stream()` - no manual sentence splitting needed.

---

## Files to Modify

### 1. `apps/ai/src/services/voice_service.py`

Add async streaming method:

```python
async def synthesize_stream(self, text: str):
    """
    Stream audio chunks as they're synthesized.

    Yields:
        tuple[bytes, int]: (PCM audio chunk, sample_rate)
    """
    kokoro = self._load_kokoro_model()
    if kokoro is None:
        raise RuntimeError("Kokoro model not loaded")

    async for samples, sample_rate in kokoro.create_stream(
        text,
        voice=self.kokoro_voice,
        speed=self.kokoro_speed,
        lang=self.kokoro_lang,
    ):
        # Convert float32 to int16 PCM
        audio_int16 = (samples * 32767).astype(np.int16)
        yield audio_int16.tobytes(), sample_rate
```

### 2. `apps/ai/src/api.py`

Add streaming endpoint:

```python
from fastapi.responses import StreamingResponse

@app.post("/voice/synthesize-stream")
async def synthesize_speech_streaming(request: ChatRequest):
    """
    Stream TTS audio as chunks are synthesized.

    Returns chunked audio/wav with each chunk as a complete mini-WAV.
    """
    if not voice_service or not voice_service.is_tts_available():
        raise HTTPException(status_code=503, detail="TTS not available")

    async def audio_generator():
        async for pcm_chunk, sample_rate in voice_service.synthesize_stream(request.message):
            # Wrap each chunk in WAV header for browser compatibility
            wav_chunk = _pcm_to_wav(pcm_chunk, sample_rate)
            yield wav_chunk

    return StreamingResponse(
        audio_generator(),
        media_type="audio/wav",
        headers={"X-Content-Type-Options": "nosniff"}
    )

def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
    """Wrap PCM data in WAV header."""
    import struct

    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_data)

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16,
        1,  # PCM
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size
    )
    return header + pcm_data
```

### 3. `apps/web/src/api.ts`

Add streaming client function:

```typescript
export async function synthesizeSpeechStream(
  text: string,
  onChunk: (audioBlob: Blob) => void
): Promise<void> {
  const response = await fetch(`${getApiBase()}/api/voice/synthesize-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
  });

  if (!response.ok) {
    throw new Error(`TTS stream failed: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  // WAV files are self-delimiting (header contains size)
  // Read and parse WAV chunks
  let buffer = new Uint8Array(0);

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    // Append to buffer
    const newBuffer = new Uint8Array(buffer.length + value.length);
    newBuffer.set(buffer);
    newBuffer.set(value, buffer.length);
    buffer = newBuffer;

    // Try to extract complete WAV chunks
    while (buffer.length >= 44) {
      // Read WAV header to get chunk size
      const dataView = new DataView(buffer.buffer);
      const chunkSize = dataView.getUint32(4, true) + 8; // RIFF size + 8

      if (buffer.length >= chunkSize) {
        const wavChunk = buffer.slice(0, chunkSize);
        buffer = buffer.slice(chunkSize);
        onChunk(new Blob([wavChunk], { type: "audio/wav" }));
      } else {
        break;
      }
    }
  }
}
```

### 4. `apps/web/src/components/ChatInput.tsx`

Update `speakResponse` to use streaming:

```typescript
const speakResponse = useCallback(async (text: string, messageId: number) => {
  if (playingMessageId === messageId) {
    stopAudio();
    return;
  }

  setPlayingMessageId(messageId);

  const audioQueue: Blob[] = [];
  let isPlaying = false;
  let currentAudio: HTMLAudioElement | null = null;

  const playNext = () => {
    if (audioQueue.length === 0) {
      isPlaying = false;
      return;
    }

    isPlaying = true;
    const blob = audioQueue.shift()!;
    const url = URL.createObjectURL(blob);
    currentAudio = new Audio(url);
    currentAudio.onended = () => {
      URL.revokeObjectURL(url);
      playNext();
    };
    currentAudio.play();
  };

  try {
    await synthesizeSpeechStream(text, (chunk) => {
      audioQueue.push(chunk);
      if (!isPlaying) playNext();
    });
  } catch (err) {
    console.error("TTS stream error:", err);
  } finally {
    // Wait for queue to drain
    while (audioQueue.length > 0 || isPlaying) {
      await new Promise(r => setTimeout(r, 100));
    }
    setPlayingMessageId(null);
  }
}, [playingMessageId, stopAudio]);
```

---

## Architecture

```
User Request
    ↓
FastAPI /voice/synthesize-stream
    ↓
voice_service.synthesize_stream()
    ↓
kokoro.create_stream() ──→ yields audio chunks as synthesized
    ↓
StreamingResponse
    ↓
Browser fetch() with ReadableStream
    ↓
Audio queue → plays chunks sequentially
```

---

## Verification

1. Restart AI service after changes
2. Test streaming endpoint directly:
   ```bash
   curl -X POST http://localhost:8000/voice/synthesize-stream \
     -H "Content-Type: application/json" \
     -d '{"message": "This is a long message to test streaming. It should start playing before the entire message is synthesized."}' \
     --output stream_test.wav
   ```
3. Test in browser via ChatInput
4. Compare time-to-first-audio between `/synthesize` and `/synthesize-stream`

**Expected improvement:** First audio plays in ~0.3-0.5s instead of waiting 4+ seconds for long messages.

---

## Fallback

Keep existing `/voice/synthesize` endpoint unchanged for compatibility. The streaming endpoint is additive.