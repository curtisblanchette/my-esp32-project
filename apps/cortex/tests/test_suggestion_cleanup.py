"""Tests for rejected suggestion purge logic and cleanup job."""

import asyncio
import time
import uuid

import pytest


@pytest.fixture(autouse=True)
def _ensure_suggestions_table(sqlite_db):
    """Create the cortex_suggestions table (normally done by RuleAdvisor)."""
    db = sqlite_db._get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cortex_suggestions (
            id TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            rule_name TEXT NOT NULL,
            field TEXT NOT NULL,
            current_value TEXT NOT NULL,
            suggested_value TEXT NOT NULL,
            reason TEXT NOT NULL,
            confidence REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_at INTEGER,
            outcome_sample_count INTEGER,
            observation_context TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_suggestions_status
            ON cortex_suggestions(status);
        CREATE INDEX IF NOT EXISTS idx_suggestions_rule
            ON cortex_suggestions(rule_name);
    """)
    db.commit()


class TestPurgeRejectedSuggestions:
    """Unit tests for SqliteClient.purge_rejected_suggestions."""

    def _insert_suggestion(self, sqlite_db, status="rejected", resolved_at_ms=None):
        """Helper to insert a suggestion with a given status and resolved_at."""
        suggestion_id = str(uuid.uuid4())
        now_ms = int(time.time() * 1000)
        db = sqlite_db._get_db()
        db.execute(
            "INSERT INTO cortex_suggestions "
            "(id, created_at, rule_name, field, current_value, suggested_value, "
            "reason, confidence, status, resolved_at, outcome_sample_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (suggestion_id, now_ms, "test_rule", "threshold", "25", "27",
             "test reason", 0.7, status, resolved_at_ms, 5),
        )
        db.commit()
        return suggestion_id

    def test_purges_old_rejected(self, sqlite_db):
        """Rejected suggestions resolved before cutoff are deleted."""
        old_ts = int(time.time() * 1000) - (8 * 24 * 60 * 60 * 1000)  # 8 days ago
        sid = self._insert_suggestion(sqlite_db, status="rejected", resolved_at_ms=old_ts)

        cutoff = int(time.time() * 1000) - (7 * 24 * 60 * 60 * 1000)  # 7 day cutoff
        purged = sqlite_db.purge_rejected_suggestions(cutoff)

        assert purged == 1
        assert sqlite_db.get_suggestion(sid) is None

    def test_keeps_recent_rejected(self, sqlite_db):
        """Rejected suggestions resolved within TTL are kept."""
        recent_ts = int(time.time() * 1000) - (1 * 24 * 60 * 60 * 1000)  # 1 day ago
        sid = self._insert_suggestion(sqlite_db, status="rejected", resolved_at_ms=recent_ts)

        cutoff = int(time.time() * 1000) - (7 * 24 * 60 * 60 * 1000)  # 7 day cutoff
        purged = sqlite_db.purge_rejected_suggestions(cutoff)

        assert purged == 0
        assert sqlite_db.get_suggestion(sid) is not None

    def test_does_not_purge_pending(self, sqlite_db):
        """Pending suggestions are never purged regardless of age."""
        old_ts = int(time.time() * 1000) - (30 * 24 * 60 * 60 * 1000)  # 30 days ago
        sid = self._insert_suggestion(sqlite_db, status="pending", resolved_at_ms=old_ts)

        cutoff = int(time.time() * 1000)  # cutoff = now (everything is old)
        purged = sqlite_db.purge_rejected_suggestions(cutoff)

        assert purged == 0
        assert sqlite_db.get_suggestion(sid) is not None

    def test_does_not_purge_applied(self, sqlite_db):
        """Applied suggestions are never purged regardless of age."""
        old_ts = int(time.time() * 1000) - (30 * 24 * 60 * 60 * 1000)
        sid = self._insert_suggestion(sqlite_db, status="applied", resolved_at_ms=old_ts)

        cutoff = int(time.time() * 1000)
        purged = sqlite_db.purge_rejected_suggestions(cutoff)

        assert purged == 0
        assert sqlite_db.get_suggestion(sid) is not None

    def test_purges_multiple_old_rejected(self, sqlite_db):
        """Multiple old rejected suggestions are purged in one call."""
        old_ts = int(time.time() * 1000) - (10 * 24 * 60 * 60 * 1000)
        for _ in range(3):
            self._insert_suggestion(sqlite_db, status="rejected", resolved_at_ms=old_ts)
        # Also add one recent rejected that should survive
        recent_ts = int(time.time() * 1000) - (1 * 60 * 60 * 1000)  # 1 hour ago
        self._insert_suggestion(sqlite_db, status="rejected", resolved_at_ms=recent_ts)

        cutoff = int(time.time() * 1000) - (7 * 24 * 60 * 60 * 1000)
        purged = sqlite_db.purge_rejected_suggestions(cutoff)

        assert purged == 3
        remaining = sqlite_db.get_suggestions(status="rejected")
        assert len(remaining) == 1

    def test_returns_zero_when_nothing_to_purge(self, sqlite_db):
        """Returns 0 when no rejected suggestions match the cutoff."""
        purged = sqlite_db.purge_rejected_suggestions(int(time.time() * 1000))
        assert purged == 0


class TestSuggestionCleanupJob:
    """Integration tests for the background cleanup job."""

    def test_job_calls_purge_with_correct_cutoff(self):
        """The cleanup job computes the correct cutoff and calls purge."""
        from unittest.mock import MagicMock

        mock_sqlite = MagicMock()
        mock_sqlite.purge_rejected_suggestions.return_value = 2

        ttl_s = 7 * 24 * 60 * 60  # 7 days

        from src.services.background_jobs import start_suggestion_cleanup_job

        async def run():
            # Patch sleep to break after first iteration
            original_sleep = asyncio.sleep
            call_count = 0

            async def limited_sleep(seconds):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    raise asyncio.CancelledError()
                await original_sleep(0)  # Don't actually wait

            import src.services.background_jobs as mod
            mod_sleep = asyncio.sleep
            asyncio.sleep = limited_sleep
            try:
                await start_suggestion_cleanup_job(mock_sqlite, ttl_s)
            except asyncio.CancelledError:
                pass
            finally:
                asyncio.sleep = mod_sleep

        asyncio.run(run())

        mock_sqlite.purge_rejected_suggestions.assert_called_once()
        cutoff_ms = mock_sqlite.purge_rejected_suggestions.call_args[0][0]
        expected_cutoff = int(time.time() * 1000) - (ttl_s * 1000)
        # Allow 5 second tolerance for test execution time
        assert abs(cutoff_ms - expected_cutoff) < 5000
