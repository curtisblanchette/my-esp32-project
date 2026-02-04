"""
Shared services and state for the AI orchestrator.

Provides a singleton container for services (like OllamaClient) that need
to be shared across components (e.g., between main.py orchestrator and api.py HTTP endpoints).
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ollama_client import OllamaClient

logger = logging.getLogger(__name__)


@dataclass
class SharedState:
    """Shared application state across components."""
    running: bool = False
    devices: dict = field(default_factory=dict)
    recent_commands: list = field(default_factory=list)


class SharedServices:
    """
    Singleton container for shared services.

    This allows the orchestrator (main.py) to initialize services like OllamaClient
    once, and have them accessible from the HTTP API endpoints (api.py).
    """

    _instance: "SharedServices | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.state = SharedState()
        self._ollama: "OllamaClient | None" = None
        self._initialized = True
        logger.debug("SharedServices initialized")

    @property
    def ollama(self) -> "OllamaClient | None":
        """Get the shared Ollama client."""
        return self._ollama

    def init_ollama(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> "OllamaClient":
        """
        Initialize the shared Ollama client.

        If already initialized, returns the existing instance.
        If base_url/model not provided, uses defaults from config.
        """
        if self._ollama is not None:
            return self._ollama

        from ..config import OLLAMA_URL, OLLAMA_MODEL
        from .ollama_client import OllamaClient

        self._ollama = OllamaClient(
            base_url=base_url or OLLAMA_URL,
            model=model or OLLAMA_MODEL,
        )
        logger.info(f"Ollama client initialized: {self._ollama.base_url} ({self._ollama.model})")
        return self._ollama

    def close(self):
        """Clean up shared resources."""
        if self._ollama:
            self._ollama.close()
            self._ollama = None
        logger.debug("SharedServices closed")


def get_shared_services() -> SharedServices:
    """Get the shared services singleton."""
    return SharedServices()
