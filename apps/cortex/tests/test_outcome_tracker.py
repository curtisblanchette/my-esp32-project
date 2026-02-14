"""Tests for the OutcomeTracker — Phase 2: outcome tracking and effectiveness scoring."""

import time

import pytest

from src.models.command import Command, CommandAck
from src.models.telemetry import TelemetryMessage, Reading
from src.services.outcome_tracker import OutcomeTracker


# ── Helpers ───────────────────────────────────────────────────────────

def _make_command(
    device_id="esp32-test",
    location="room1",
    target="relay1",
    action="set",
    value=True,
    reason="Temperature rising — preemptive cooling",
    correlation_id="test-cmd-001",
):
    cmd = Command(
        device_id=device_id,
        location=location,
        target=target,
        action=action,
        value=value,
        reason=reason,
    )
    cmd.correlation_id = correlation_id
    return cmd


def _make_telemetry(device_id="esp32-test", temp=25.0, humidity=55.0, ts=None):
    return TelemetryMessage(
        version=1,
        ts=ts or int(time.time() * 1000),
        device_id=device_id,
        location="room1",
        readings=[
            Reading(id="temp1", value=temp, unit="C"),
            Reading(id="hum1", value=humidity, unit="%"),
        ],
    )


def _make_ack(correlation_id="test-cmd-001", status="executed"):
    return CommandAck(
        correlation_id=correlation_id,
        status=status,
        target="relay1",
        actual_value=True,
    )


class MockDataReader:
    """Mock DataReader that returns controlled readings."""

    def __init__(self):
        self._readings = []

    def set_current(self, temp, humidity, device_id="esp32-test"):
        from src.services.data_reader import MergedReading
        self._readings = [
            MergedReading(
                ts=int(time.time() * 1000),
                temp=temp,
                humidity=humidity,
                device_id=device_id,
            )
        ]

    def get_recent_readings(self, window_minutes=30, device_id=None):
        return self._readings

    def get_readings(self, since_ms=0, until_ms=None, device_id=None, limit=5000):
        return self._readings

    def extract_metric(self, readings, metric):
        values = []
        timestamps = []
        for r in readings:
            if metric == "temperature":
                values.append(r.temp)
            elif metric == "humidity":
                values.append(r.humidity)
            timestamps.append(r.ts)
        return values, timestamps


@pytest.fixture
def mock_data_reader():
    return MockDataReader()


@pytest.fixture
def tracker(sqlite_db, mock_data_reader):
    return OutcomeTracker(sqlite_db, mock_data_reader)


# ── TestInferTargetMetric ─────────────────────────────────────────────

class TestInferTargetMetric:
    def test_temperature_keywords(self):
        assert OutcomeTracker._infer_target_metric("temp rising fast") == "temperature"
        assert OutcomeTracker._infer_target_metric("Too hot in the room") == "temperature"
        assert OutcomeTracker._infer_target_metric("Cooling needed") == "temperature"
        assert OutcomeTracker._infer_target_metric("Temperature above threshold") == "temperature"
        assert OutcomeTracker._infer_target_metric("Heat wave detected") == "temperature"

    def test_humidity_keywords(self):
        assert OutcomeTracker._infer_target_metric("humidity too high") == "humidity"
        assert OutcomeTracker._infer_target_metric("Too dry in grow room") == "humidity"
        assert OutcomeTracker._infer_target_metric("Moist conditions detected") == "humidity"

    def test_default_fallback(self):
        assert OutcomeTracker._infer_target_metric(None) == "temperature"
        assert OutcomeTracker._infer_target_metric("") == "temperature"
        assert OutcomeTracker._infer_target_metric("unknown reason") == "temperature"

    def test_humidity_checked_before_temperature(self):
        # "humidity" should win even if "temp" is also in the string
        # because humidity keywords are checked first
        assert OutcomeTracker._infer_target_metric("humidity and temp both high") == "humidity"


# ── TestInferDesiredDirection ─────────────────────────────────────────

class TestInferDesiredDirection:
    def test_decrease_keywords(self):
        assert OutcomeTracker._infer_desired_direction("Cooling the room", True) == "decrease"
        assert OutcomeTracker._infer_desired_direction("Lower temperature needed", True) == "decrease"
        assert OutcomeTracker._infer_desired_direction("Save energy mode", False) == "decrease"
        assert OutcomeTracker._infer_desired_direction("Reduce humidity", True) == "decrease"
        assert OutcomeTracker._infer_desired_direction("Too hot, drop temp", True) == "decrease"

    def test_increase_keywords(self):
        assert OutcomeTracker._infer_desired_direction("Heating up", True) == "increase"
        assert OutcomeTracker._infer_desired_direction("Warm the room", True) == "increase"
        assert OutcomeTracker._infer_desired_direction("Raise temperature", True) == "increase"
        assert OutcomeTracker._infer_desired_direction("Too cold outside", True) == "increase"

    def test_fallback_from_value(self):
        # No keywords — fall back to value heuristic
        assert OutcomeTracker._infer_desired_direction("Activating relay", True) == "decrease"
        assert OutcomeTracker._infer_desired_direction("Deactivating relay", False) == "increase"

    def test_none_reason(self):
        assert OutcomeTracker._infer_desired_direction(None, True) == "decrease"
        assert OutcomeTracker._infer_desired_direction(None, False) == "increase"


# ── TestScoreOutcome ──────────────────────────────────────────────────

class TestScoreOutcome:
    def test_perfect_decrease(self):
        # pre=26, post_5m=24 → delta=-2, desired=decrease, scale=2
        # score = -(-2)/2 = 1.0
        score = OutcomeTracker._score_outcome(
            pre_value=26.0,
            post_snapshots={300: 24.0},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score == pytest.approx(1.0)

    def test_perfect_increase(self):
        # pre=20, post_5m=22 → delta=+2, desired=increase, scale=2
        # score = +2/2 = 1.0
        score = OutcomeTracker._score_outcome(
            pre_value=20.0,
            post_snapshots={300: 22.0},
            desired_direction="increase",
            scale_factor=2.0,
        )
        assert score == pytest.approx(1.0)

    def test_no_effect(self):
        score = OutcomeTracker._score_outcome(
            pre_value=25.0,
            post_snapshots={60: 25.0, 300: 25.0, 600: 25.0},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score == pytest.approx(0.0)

    def test_made_worse(self):
        # Wanted decrease but temp went up
        score = OutcomeTracker._score_outcome(
            pre_value=25.0,
            post_snapshots={300: 27.0},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score < 0
        assert score == pytest.approx(-1.0)

    def test_clamp_range(self):
        # Extreme values should clamp to [-1, 1]
        score = OutcomeTracker._score_outcome(
            pre_value=30.0,
            post_snapshots={300: 20.0},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score == 1.0

        score = OutcomeTracker._score_outcome(
            pre_value=20.0,
            post_snapshots={300: 30.0},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score == -1.0

    def test_weighted_intervals(self):
        # All three intervals present: 1m=20%, 5m=50%, 10m=30%
        # pre=25, post_1m=24.5 (delta=-0.5), post_5m=24 (delta=-1), post_10m=23.5 (delta=-1.5)
        # Desired=decrease, scale=2
        # 1m_score = 0.5/2 = 0.25, weighted = 0.25*0.20 = 0.050
        # 5m_score = 1.0/2 = 0.50, weighted = 0.50*0.50 = 0.250
        # 10m_score= 1.5/2 = 0.75, weighted = 0.75*0.30 = 0.225
        # Total = 0.525, total_weight = 1.0
        score = OutcomeTracker._score_outcome(
            pre_value=25.0,
            post_snapshots={60: 24.5, 300: 24.0, 600: 23.5},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score == pytest.approx(0.525)

    def test_missing_intervals(self):
        # Only 5m available — should normalize by actual weight used
        # 5m weight = 0.50, score = 1.0/2 = 0.5
        # normalized = (0.5 * 0.50) / 0.50 = 0.5
        score = OutcomeTracker._score_outcome(
            pre_value=25.0,
            post_snapshots={300: 24.0},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score == pytest.approx(0.5)

    def test_empty_snapshots(self):
        score = OutcomeTracker._score_outcome(
            pre_value=25.0,
            post_snapshots={},
            desired_direction="decrease",
            scale_factor=2.0,
        )
        assert score == 0.0

    def test_humidity_scale_factor(self):
        # 5% drop in humidity with scale=5: score = 5/5 = 1.0
        score = OutcomeTracker._score_outcome(
            pre_value=65.0,
            post_snapshots={300: 60.0},
            desired_direction="decrease",
            scale_factor=5.0,
        )
        assert score == pytest.approx(1.0)


# ── TestOutcomeTrackerLifecycle ───────────────────────────────────────

class TestOutcomeTrackerLifecycle:
    def test_track_command_creates_pending(self, tracker):
        cmd = _make_command()
        telemetry = _make_telemetry(temp=26.0, humidity=58.0)

        tracker.track_command(cmd, telemetry)

        assert "test-cmd-001" in tracker._pending
        pending = tracker._pending["test-cmd-001"]
        assert pending.pre_snapshot.values["temp1"] == 26.0
        assert pending.pre_snapshot.values["hum1"] == 58.0
        assert pending.target_metric == "temperature"
        assert pending.desired_direction == "decrease"

    def test_handle_ack_marks_acked(self, tracker):
        cmd = _make_command()
        telemetry = _make_telemetry(temp=26.0)
        tracker.track_command(cmd, telemetry)

        ack = _make_ack(status="executed")
        tracker.handle_ack(ack)

        pending = tracker._pending["test-cmd-001"]
        assert pending.acked is True
        assert pending.ack_status == "executed"

    def test_failed_ack_scores_zero(self, tracker):
        cmd = _make_command()
        telemetry = _make_telemetry(temp=26.0)
        tracker.track_command(cmd, telemetry)

        ack = _make_ack(status="rejected")
        tracker.handle_ack(ack)

        # Should be removed from pending and stored in SQLite
        assert "test-cmd-001" not in tracker._pending

        # Check stored outcome
        db = tracker._sqlite._get_db()
        row = db.execute(
            "SELECT effectiveness, ack_status FROM cortex_outcomes WHERE correlation_id = ?",
            ("test-cmd-001",),
        ).fetchone()
        assert row is not None
        assert row["effectiveness"] == 0.0
        assert row["ack_status"] == "rejected"

    def test_check_outcomes_collects_snapshots(self, tracker, mock_data_reader):
        # Create a command that was issued 70 seconds ago (past first interval)
        cmd = _make_command()
        telemetry = _make_telemetry(temp=26.0)
        tracker.track_command(cmd, telemetry)

        # Backdate the command timestamp to 70 seconds ago
        pending = tracker._pending["test-cmd-001"]
        pending.command_ts = int((time.time() - 70) * 1000)

        # Set current readings for the check
        mock_data_reader.set_current(temp=25.5, humidity=57.0)

        completed = tracker.check_outcomes()

        # Should have collected 1m snapshot but not yet completed
        assert len(completed) == 0  # not all intervals done yet
        assert 60 in pending.post_snapshots
        assert pending.post_snapshots[60].values["temp1"] == 25.5

    def test_full_lifecycle(self, tracker, mock_data_reader):
        """Track → ack → 3 interval checks → scored and stored."""
        cmd = _make_command()
        telemetry = _make_telemetry(temp=26.0, humidity=58.0)

        # Step 1: Track
        tracker.track_command(cmd, telemetry)

        # Step 2: Ack
        tracker.handle_ack(_make_ack(status="executed"))

        pending = tracker._pending["test-cmd-001"]

        # Step 3: Simulate 1-minute check
        pending.command_ts = int((time.time() - 70) * 1000)
        mock_data_reader.set_current(temp=25.5, humidity=57.5)
        tracker.check_outcomes()
        assert pending.next_check_idx == 1

        # Step 4: Simulate 5-minute check
        pending.command_ts = int((time.time() - 310) * 1000)
        mock_data_reader.set_current(temp=24.5, humidity=56.0)
        tracker.check_outcomes()
        assert pending.next_check_idx == 2

        # Step 5: Simulate 10-minute check
        pending.command_ts = int((time.time() - 610) * 1000)
        mock_data_reader.set_current(temp=24.0, humidity=55.0)
        completed = tracker.check_outcomes()

        assert len(completed) == 1
        record = completed[0]
        assert record.correlation_id == "test-cmd-001"
        assert record.ack_status == "executed"
        assert record.target_metric == "temperature"
        assert record.pre_value == 26.0
        assert record.post_1m == 25.5
        assert record.post_5m == 24.5
        assert record.post_10m == 24.0
        assert record.effectiveness > 0  # temperature dropped as desired

        # Removed from pending
        assert "test-cmd-001" not in tracker._pending

        # Stored in SQLite
        db = tracker._sqlite._get_db()
        row = db.execute(
            "SELECT * FROM cortex_outcomes WHERE correlation_id = ?",
            ("test-cmd-001",),
        ).fetchone()
        assert row is not None
        assert row["effectiveness"] == record.effectiveness

    def test_no_ack_still_tracks(self, tracker, mock_data_reader):
        """Commands without ack can still complete interval checks."""
        cmd = _make_command()
        telemetry = _make_telemetry(temp=26.0)
        tracker.track_command(cmd, telemetry)

        # Skip ack, go straight to checks
        pending = tracker._pending["test-cmd-001"]
        pending.command_ts = int((time.time() - 610) * 1000)
        mock_data_reader.set_current(temp=24.0, humidity=55.0)

        # Fast-forward through all intervals
        for _ in range(3):
            tracker.check_outcomes()

        # Should be completed (all 3 intervals checked even without ack)
        assert "test-cmd-001" not in tracker._pending

        db = tracker._sqlite._get_db()
        row = db.execute(
            "SELECT ack_status FROM cortex_outcomes WHERE correlation_id = ?",
            ("test-cmd-001",),
        ).fetchone()
        assert row is not None
        assert row["ack_status"] == "unknown"

    def test_get_effectiveness_summary(self, tracker):
        """Multiple stored outcomes produce correct aggregate summary."""
        # Manually insert some outcome records
        db = tracker._sqlite._get_db()
        now = int(time.time() * 1000)
        for i, eff in enumerate([0.8, 0.6, 0.4, -0.2]):
            db.execute(
                "INSERT INTO cortex_outcomes "
                "(correlation_id, device_id, target, action, value, reason, "
                "command_ts, ack_status, target_metric, desired_direction, "
                "pre_value, post_1m, post_5m, post_10m, effectiveness, scored_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"cmd-{i}", "esp32-test", "relay1", "set", "true",
                    "temp rising", now - (i * 60000), "executed",
                    "temperature", "decrease", 26.0, 25.5, 24.5, 24.0,
                    eff, now,
                ),
            )
        db.commit()

        summary = tracker.get_effectiveness_summary("esp32-test", "relay1")

        assert summary is not None
        assert summary["sample_count"] == 4
        assert summary["avg_score"] == pytest.approx(0.4, abs=0.01)
        # 3 out of 4 have effectiveness > 0.1
        assert summary["success_rate"] == pytest.approx(0.75)

    def test_pending_not_in_summary(self, tracker):
        """Pending (incomplete) outcomes are not included in summary."""
        cmd = _make_command()
        telemetry = _make_telemetry(temp=26.0)
        tracker.track_command(cmd, telemetry)

        summary = tracker.get_effectiveness_summary("esp32-test", "relay1")
        assert summary is None  # no completed outcomes yet

    def test_query_outcomes(self, tracker):
        """Query stored outcomes for API."""
        db = tracker._sqlite._get_db()
        now = int(time.time() * 1000)
        db.execute(
            "INSERT INTO cortex_outcomes "
            "(correlation_id, device_id, target, action, value, reason, "
            "command_ts, ack_status, target_metric, desired_direction, "
            "pre_value, post_1m, post_5m, post_10m, effectiveness, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cmd-q1", "esp32-test", "relay1", "set", '"true"',
                "temp rising", now, "executed",
                "temperature", "decrease", 26.0, 25.5, 24.5, 24.0,
                0.75, now,
            ),
        )
        db.commit()

        results = tracker.query_outcomes(device_id="esp32-test")
        assert len(results) == 1
        assert results[0]["correlationId"] == "cmd-q1"
        assert results[0]["effectiveness"] == 0.75

    def test_track_ignores_command_without_correlation_id(self, tracker):
        """Commands without correlation_id are not tracked."""
        cmd = Command(
            device_id="esp32-test",
            location="room1",
            target="relay1",
            action="set",
            value=True,
            reason="test",
        )
        cmd.correlation_id = None  # explicitly set to None

        telemetry = _make_telemetry(temp=25.0)
        tracker.track_command(cmd, telemetry)

        assert len(tracker._pending) == 0

    def test_humidity_outcome_tracking(self, tracker, mock_data_reader):
        """Verify humidity-related commands are tracked with correct metric."""
        cmd = _make_command(
            reason="Humidity too high, ventilating",
            correlation_id="hum-cmd-001",
        )
        telemetry = _make_telemetry(temp=24.0, humidity=70.0)

        tracker.track_command(cmd, telemetry)
        pending = tracker._pending["hum-cmd-001"]

        assert pending.target_metric == "humidity"
        assert pending.desired_direction == "decrease"
