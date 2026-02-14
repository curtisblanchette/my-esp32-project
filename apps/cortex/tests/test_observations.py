"""Unit tests for the observations API endpoint."""

import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.observations import create_observations_router, OBSERVATION_CATEGORIES


@pytest.fixture
def app_with_observations(sqlite_db):
    """Create a FastAPI app with the observations router mounted."""
    ws_server = AsyncMock()
    ws_server.broadcast_event = AsyncMock()

    app = FastAPI()
    app.include_router(
        create_observations_router(sqlite_db, ws_server),
        prefix="/api/observations",
    )

    return app, sqlite_db, ws_server


@pytest.fixture
def client(app_with_observations):
    app, _, _ = app_with_observations
    return TestClient(app)


@pytest.fixture
def device_id(app_with_observations):
    """Insert a test device and return its id."""
    _, sqlite_db, _ = app_with_observations
    sqlite_db.upsert_device(id="esp32-test", location="room1")
    return "esp32-test"


class TestLogObservation:
    def test_happy_path(self, client, device_id):
        r = client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "mold",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["event"]["eventType"] == "observation"
        assert body["event"]["source"] == "human"
        assert body["event"]["deviceId"] == device_id
        assert body["event"]["data"]["category"] == "mold"
        assert body["event"]["data"]["notes"] is None

    def test_happy_path_with_notes(self, client, device_id):
        r = client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "pests",
            "notes": "Aphids on lower leaves",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["event"]["data"]["category"] == "pests"
        assert body["event"]["data"]["notes"] == "Aphids on lower leaves"

    def test_invalid_category(self, client, device_id):
        r = client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "banana",
        })
        body = r.json()
        assert body["ok"] is False
        assert "Invalid category" in body["error"]

    def test_unknown_device(self, client):
        r = client.post("/api/observations", json={
            "deviceId": "nonexistent-device",
            "category": "mold",
        })
        body = r.json()
        assert body["ok"] is False
        assert "Unknown device" in body["error"]

    def test_general_requires_notes(self, client, device_id):
        r = client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "general",
        })
        body = r.json()
        assert body["ok"] is False
        assert "require notes" in body["error"].lower()

    def test_general_with_notes(self, client, device_id):
        r = client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "general",
            "notes": "Topped the plant today",
        })
        assert r.json()["ok"] is True
        assert r.json()["event"]["data"]["notes"] == "Topped the plant today"

    def test_empty_notes_treated_as_none(self, client, device_id):
        r = client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "wilting",
            "notes": "   ",
        })
        assert r.json()["ok"] is True
        assert r.json()["event"]["data"]["notes"] is None

    def test_observation_stored_in_events_table(self, app_with_observations, client, device_id):
        _, sqlite_db, _ = app_with_observations
        client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "needs_water",
        })

        events = sqlite_db.query_events(
            since_ms=0,
            event_type="observation",
        )
        assert len(events) == 1
        assert events[0].event_type == "observation"
        assert events[0].source == "human"
        assert events[0].device_id == device_id
        payload = events[0].payload
        assert payload["category"] == "needs_water"

    def test_broadcast_called(self, app_with_observations, client, device_id):
        _, _, ws_server = app_with_observations
        client.post("/api/observations", json={
            "deviceId": device_id,
            "category": "harvest_ready",
        })
        ws_server.broadcast_event.assert_called_once()
        event_dict = ws_server.broadcast_event.call_args[0][0]
        assert event_dict["eventType"] == "observation"

    def test_all_categories_accepted(self, client, device_id):
        for cat in OBSERVATION_CATEGORIES:
            payload = {"deviceId": device_id, "category": cat}
            if cat == "general":
                payload["notes"] = "test note"
            r = client.post("/api/observations", json=payload)
            assert r.json()["ok"] is True, f"Category {cat} should be accepted"
