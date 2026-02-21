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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

# Storage Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
SQLITE_PATH = os.getenv("SQLITE_PATH", "data/telemetry.sqlite")
SQLITE_JOURNAL_MODE = os.getenv("SQLITE_JOURNAL_MODE", "WAL")

# Rules Configuration
RULES_PATH = os.getenv("RULES_PATH", "config/rules.yaml")

# MPC Room Configuration (optional — enables MPC controller when set)
ROOM_CONFIG_PATH = os.getenv("ROOM_CONFIG_PATH", "")
# Device defaults
DEFAULT_DEVICE_ID = os.getenv("DEFAULT_DEVICE_ID", "esp32-1")
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "room1")

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
