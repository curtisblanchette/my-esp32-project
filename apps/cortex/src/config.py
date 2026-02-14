import os
from dotenv import load_dotenv

load_dotenv()

# MQTT Configuration
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

# Ollama Configuration
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "phi3:mini")

# API Configuration (for fetching context)
API_URL = os.getenv("API_URL", "http://localhost:3000")

# Rules Configuration
RULES_PATH = os.getenv("RULES_PATH", "config/rules.yaml")

# Device defaults
DEFAULT_DEVICE_ID = os.getenv("DEFAULT_DEVICE_ID", "esp32-1")
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "garage")

# Voice Configuration
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15")

# Kokoro TTS Configuration
KOKORO_MODEL_PATH = os.getenv("KOKORO_MODEL_PATH", "models/kokoro-v1.0.onnx")
KOKORO_VOICES_PATH = os.getenv("KOKORO_VOICES_PATH", "models/voices-v1.0.bin")
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.0"))
KOKORO_LANG = os.getenv("KOKORO_LANG", "en-us")

# HTTP API Configuration
HTTP_PORT = int(os.getenv("HTTP_PORT", "8000"))
