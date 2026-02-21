"""Tests for the Cortex intelligence API routes."""

import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.cortex import create_cortex_router


def _seed_device(sqlite_db, device_id="esp32-test"):
    sqlite_db.upsert_device(
        id=device_id, location="room1",
        capabilities={"sensors": [{"id": "temp1", "type": "temperature"}], "actuators": []},
    )


@pytest.fixture
def cortex_client(sqlite_db):
    """Create a TestClient with the cortex router mounted."""
    _seed_device(sqlite_db)

    app = FastAPI()
    app.include_router(
        create_cortex_router(sqlite_db),
        prefix="/api/cortex",
    )
    return TestClient(app), sqlite_db


class TestCortexRoutes:

    def test_get_status(self, cortex_client):
        """Status endpoint returns overview."""
        client, _ = cortex_client
        r = client.get("/api/cortex/status")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True

    # ── Profile CRUD ────────────────────────────────────────────────

    def test_create_profile(self, cortex_client):
        """POST creates a new grow profile."""
        client, sqlite_db = cortex_client
        r = client.post("/api/cortex/profiles", json={
            "location": "tent-1",
            "name": "Test Profile",
            "strategy": "balanced",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["profile"]["location"] == "tent-1"
        assert data["profile"]["strategy"] == "balanced"
        assert data["profile"]["id"]

    def test_get_profiles(self, cortex_client):
        """GET returns all profiles."""
        client, sqlite_db = cortex_client
        sqlite_db.insert_profile(location="tent-1", name="Profile 1")
        sqlite_db.insert_profile(location="tent-2", name="Profile 2")

        r = client.get("/api/cortex/profiles")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["profiles"]) == 2

    def test_get_profile_by_id(self, cortex_client):
        """GET single profile by id."""
        client, sqlite_db = cortex_client
        p = sqlite_db.insert_profile(location="tent-1", name="Profile 1")

        r = client.get(f"/api/cortex/profiles/{p['id']}")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["profile"]["name"] == "Profile 1"

    def test_update_profile(self, cortex_client):
        """PUT updates a profile."""
        client, sqlite_db = cortex_client
        p = sqlite_db.insert_profile(location="tent-1", name="Original")

        r = client.put(f"/api/cortex/profiles/{p['id']}", json={
            "name": "Updated",
            "strategy": "precision",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["profile"]["name"] == "Updated"
        assert data["profile"]["strategy"] == "precision"

    def test_delete_profile(self, cortex_client):
        """DELETE removes a profile."""
        client, sqlite_db = cortex_client
        p = sqlite_db.insert_profile(location="tent-1", name="To Delete")

        r = client.delete(f"/api/cortex/profiles/{p['id']}")
        assert r.status_code == 200
        assert r.json()["ok"] is True

        assert sqlite_db.get_profile(p["id"]) is None

    # ── Goal CRUD ─────────────────────────────────────────────────

    def test_create_goal(self, cortex_client):
        """POST creates a goal for a profile."""
        client, sqlite_db = cortex_client
        p = sqlite_db.insert_profile(location="tent-1", name="Profile 1")

        r = client.post(f"/api/cortex/profiles/{p['id']}/goals", json={
            "metric": "temp1",
            "metricType": "sensor",
            "rangeMin": 22.0,
            "rangeMax": 28.0,
            "tolerance": 1.0,
            "priority": 1.0,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["goal"]["metric"] == "temp1"
        assert data["goal"]["rangeMin"] == 22.0

    def test_get_goals(self, cortex_client):
        """GET returns goals for a profile."""
        client, sqlite_db = cortex_client
        p = sqlite_db.insert_profile(location="tent-1", name="Profile 1")
        sqlite_db.insert_goal(profile_id=p["id"], metric="temp1", range_min=22, range_max=28)
        sqlite_db.insert_goal(profile_id=p["id"], metric="hum1", range_min=40, range_max=65)

        r = client.get(f"/api/cortex/profiles/{p['id']}/goals")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["goals"]) == 2

    def test_delete_goal(self, cortex_client):
        """DELETE removes a goal."""
        client, sqlite_db = cortex_client
        p = sqlite_db.insert_profile(location="tent-1", name="Profile 1")
        g = sqlite_db.insert_goal(profile_id=p["id"], metric="temp1", range_min=22, range_max=28)

        r = client.delete(f"/api/cortex/goals/{g['id']}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert sqlite_db.get_goal(g["id"]) is None

    # ── Room Config ──────────────────────────────────────────────────

    def test_room_config_unconfigured(self, cortex_client, monkeypatch):
        """Returns configured=false when ROOM_CONFIG_PATH is empty."""
        client, _ = cortex_client
        monkeypatch.setattr("src.api.cortex.ROOM_CONFIG_PATH", "")

        r = client.get("/api/cortex/room-config")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["configured"] is False
        assert data["config"] is None

    def test_room_config_valid_yaml(self, cortex_client, monkeypatch):
        """Returns parsed YAML when config file exists."""
        client, _ = cortex_client
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("name: Test Room\nspace:\n  volume_m3: 10.0\n")
            f.flush()
            monkeypatch.setattr("src.api.cortex.ROOM_CONFIG_PATH", f.name)

            r = client.get("/api/cortex/room-config")
            assert r.status_code == 200
            data = r.json()
            assert data["ok"] is True
            assert data["configured"] is True
            assert data["config"]["name"] == "Test Room"
            assert data["config"]["space"]["volume_m3"] == 10.0

        os.unlink(f.name)

    def test_room_config_missing_file(self, cortex_client, monkeypatch):
        """Returns configured=false when file doesn't exist."""
        client, _ = cortex_client
        monkeypatch.setattr("src.api.cortex.ROOM_CONFIG_PATH", "/nonexistent/room.yaml")

        r = client.get("/api/cortex/room-config")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["configured"] is False
