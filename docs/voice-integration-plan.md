# Voice Integration Plan

Integrating voice capabilities into the IoT system using Vosk (STT) and Piper (TTS).

## Architecture Overview

Four main components:

1. **Node/Express API** (TypeScript) - existing API layer
2. **Python AI app** - rules engine with Ollama
3. **Vosk** - speech-to-text processing
4. **Piper** - text-to-speech synthesis

## Integration Approach

### Option 1: Python-centric processing (recommended)

- Add Vosk and Piper to existing Python app since they're both Python-native
- Node API receives audio, forwards to Python service
- Python handles: audio → Vosk → Ollama → Piper → audio response
- Return synthesized audio to Node API for delivery

### Option 2: Distributed processing

- Run Vosk and Piper as separate microservices
- Node orchestrates the pipeline
- More complexity but better separation of concerns

## Communication Flow

```
Client → [Node API] → [Python AI Service]
                         ↓
                      [Vosk STT]
                         ↓
                      [Ollama]
                         ↓
                      [Piper TTS]
                         ↓
         [Node API] ← [Audio Response]
```

## Technical Implementation

### Python side

- Install `vosk` and `piper-tts` packages
- Download Vosk model (lightweight: vosk-model-small-en-us-0.15, ~40MB)
- Download Piper voice model
- Create endpoints for audio processing
- Use asyncio for concurrent processing

### Node/Express side

- Add routes for voice interactions (`/api/voice/command`)
- Handle multipart audio uploads (multer middleware)
- Stream audio responses back to client
- Use `node-fetch` or `axios` to communicate with Python service

### Docker setup

- Extend Python container to include Vosk/Piper dependencies
- Mount model directories as volumes (they're large, cache them)
- Ensure proper network configuration between containers

## Key Considerations

- **Audio format**: Use WAV 16kHz mono for Vosk (convert if needed)
- **Latency**: Vosk is fast, but Ollama inference might be slow depending on model size
- **Model selection**: Use smaller Ollama models (7B params) for faster response
- **Streaming**: Consider streaming Ollama responses and synthesizing incrementally
- **Error handling**: Timeout handling for Ollama, fallback for unclear speech

## Implementation Priority

1. Set up Vosk in Python container with basic transcription endpoint
2. Test audio pipeline (record → transcribe → verify)
3. Connect Vosk output to Ollama
4. Add Piper for TTS
5. Create Node endpoints with proper audio handling
6. Add WebSocket support for real-time streaming (optional enhancement)