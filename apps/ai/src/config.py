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
