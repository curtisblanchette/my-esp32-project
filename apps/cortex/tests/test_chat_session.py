"""Tests for ChatSessionStore."""

import time

import pytest

from src.services.chat_session import ChatSession, ChatSessionStore


class TestChatSessionStore:
    """Tests for the in-memory chat session store."""

    def test_get_or_create_new_session(self):
        store = ChatSessionStore()
        session = store.get_or_create("sess-1")
        assert isinstance(session, ChatSession)
        assert session.session_id == "sess-1"
        assert session.location is None
        assert session.goal is None
        assert session.proposed_rules is None
        assert session.messages == []

    def test_get_or_create_returns_existing(self):
        store = ChatSessionStore()
        s1 = store.get_or_create("sess-1")
        s1.location = "grow-tent"
        s2 = store.get_or_create("sess-1")
        assert s2 is s1
        assert s2.location == "grow-tent"

    def test_add_message(self):
        store = ChatSessionStore()
        store.add_message("sess-1", "user", "hello")
        store.add_message("sess-1", "assistant", "hi there")
        ctx = store.get_conversation_context("sess-1")
        assert len(ctx) == 2
        assert ctx[0] == {"role": "user", "content": "hello"}
        assert ctx[1] == {"role": "assistant", "content": "hi there"}

    def test_message_cap_at_max(self):
        store = ChatSessionStore()
        store.MAX_MESSAGES = 3
        for i in range(5):
            store.add_message("sess-1", "user", f"msg-{i}")
        ctx = store.get_conversation_context("sess-1")
        assert len(ctx) == 3
        assert ctx[0]["content"] == "msg-2"
        assert ctx[2]["content"] == "msg-4"

    def test_get_conversation_context_nonexistent(self):
        store = ChatSessionStore()
        ctx = store.get_conversation_context("nonexistent")
        assert ctx == []

    def test_set_and_get_proposed_rules(self):
        store = ChatSessionStore()
        rules = [{"name": "rule1"}, {"name": "rule2"}]
        store.set_proposed_rules("sess-1", rules)
        result = store.get_proposed_rules("sess-1")
        assert result == rules

    def test_get_proposed_rules_nonexistent(self):
        store = ChatSessionStore()
        assert store.get_proposed_rules("nonexistent") is None

    def test_clear_proposed_rules(self):
        store = ChatSessionStore()
        store.set_proposed_rules("sess-1", [{"name": "rule1"}])
        store.clear_proposed_rules("sess-1")
        assert store.get_proposed_rules("sess-1") is None

    def test_clear_proposed_rules_nonexistent(self):
        store = ChatSessionStore()
        store.clear_proposed_rules("nonexistent")  # Should not raise

    def test_ttl_expiration(self):
        store = ChatSessionStore()
        store.TTL_SECONDS = 0  # Expire immediately
        store.get_or_create("sess-1")
        # Wait a tiny bit so the session is older than TTL
        time.sleep(0.01)
        # Getting a different session triggers cleanup
        store.get_or_create("sess-2")
        # sess-1 should be cleaned up, creating a new one
        session = store.get_or_create("sess-1")
        assert session.messages == []  # Fresh session

    def test_cleanup_expired(self):
        store = ChatSessionStore()
        store.TTL_SECONDS = 0
        store.get_or_create("sess-1")
        store.get_or_create("sess-2")
        time.sleep(0.01)
        store._cleanup_expired()
        # Both should be gone — new sessions created
        s1 = store.get_or_create("sess-1")
        assert s1.messages == []

    def test_session_updated_at_refreshes(self):
        store = ChatSessionStore()
        session = store.get_or_create("sess-1")
        original = session.updated_at
        time.sleep(0.01)
        store.add_message("sess-1", "user", "msg")
        assert session.updated_at > original
