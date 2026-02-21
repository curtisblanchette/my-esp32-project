"""In-memory chat session store for multi-turn conversation context."""

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ChatSession:
    """A single chat conversation session."""

    session_id: str
    messages: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ChatSessionStore:
    """In-memory chat session store with TTL-based expiration."""

    TTL_SECONDS = 600  # 10 minutes of inactivity
    MAX_MESSAGES = 10  # conversation context window

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    def get_or_create(self, session_id: str) -> ChatSession:
        """Get an existing session or create a new one."""
        self._cleanup_expired()
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.updated_at = time.time()
            return session
        session = ChatSession(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Add a message to the session history, trimming to MAX_MESSAGES."""
        session = self.get_or_create(session_id)
        session.messages.append({"role": role, "content": content})
        if len(session.messages) > self.MAX_MESSAGES:
            session.messages = session.messages[-self.MAX_MESSAGES :]
        session.updated_at = time.time()

    def get_conversation_context(self, session_id: str) -> list[dict]:
        """Get the conversation history for a session."""
        if session_id not in self._sessions:
            return []
        session = self._sessions[session_id]
        session.updated_at = time.time()
        return list(session.messages)

    def _cleanup_expired(self) -> None:
        """Remove sessions that have exceeded the TTL."""
        now = time.time()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.updated_at > self.TTL_SECONDS
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired chat sessions")
