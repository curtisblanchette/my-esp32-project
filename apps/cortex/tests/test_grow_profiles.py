"""Tests for grow profiles and goals CRUD in SQLite."""

import time
import pytest


class TestProfiles:
    """Grow profile CRUD operations."""

    def test_create_profile(self, sqlite_db):
        profile = sqlite_db.insert_profile(
            location="zone-01",
            name="Northern Lights - Flower",
            strategy="precision",
            phase="flower",
            phase_start="2026-01-20",
        )
        assert profile["id"]
        assert profile["location"] == "zone-01"
        assert profile["name"] == "Northern Lights - Flower"
        assert profile["strategy"] == "precision"
        assert profile["phase"] == "flower"
        assert profile["phaseStart"] == "2026-01-20"
        assert profile["active"] is True

    def test_get_profile_by_id(self, sqlite_db):
        created = sqlite_db.insert_profile(location="zone-01", name="Test")
        fetched = sqlite_db.get_profile(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["name"] == "Test"

    def test_get_profile_not_found(self, sqlite_db):
        assert sqlite_db.get_profile("nonexistent") is None

    def test_get_profile_by_location(self, sqlite_db):
        sqlite_db.insert_profile(location="zone-01", name="Test Profile")
        profile = sqlite_db.get_profile_by_location("zone-01")
        assert profile is not None
        assert profile["location"] == "zone-01"

    def test_get_profile_by_location_only_active(self, sqlite_db):
        created = sqlite_db.insert_profile(location="zone-01", name="Old Profile")
        sqlite_db.update_profile(created["id"], active=False)
        assert sqlite_db.get_profile_by_location("zone-01") is None

    def test_get_profile_by_location_not_found(self, sqlite_db):
        assert sqlite_db.get_profile_by_location("nonexistent") is None

    def test_unique_location_constraint(self, sqlite_db):
        sqlite_db.insert_profile(location="zone-01", name="First")
        with pytest.raises(Exception):
            sqlite_db.insert_profile(location="zone-01", name="Second")

    def test_update_profile(self, sqlite_db):
        created = sqlite_db.insert_profile(
            location="zone-01", name="Old Name", strategy="balanced"
        )
        updated = sqlite_db.update_profile(
            created["id"], name="New Name", strategy="precision", phase="veg"
        )
        assert updated["name"] == "New Name"
        assert updated["strategy"] == "precision"
        assert updated["phase"] == "veg"

    def test_update_profile_not_found(self, sqlite_db):
        assert sqlite_db.update_profile("nonexistent", name="X") is None

    def test_update_profile_partial(self, sqlite_db):
        """Updating only one field doesn't affect others."""
        created = sqlite_db.insert_profile(
            location="zone-01", name="Test", strategy="balanced"
        )
        updated = sqlite_db.update_profile(created["id"], phase="flower")
        assert updated["name"] == "Test"
        assert updated["strategy"] == "balanced"
        assert updated["phase"] == "flower"

    def test_deactivate_profile(self, sqlite_db):
        created = sqlite_db.insert_profile(location="zone-01", name="Test")
        sqlite_db.update_profile(created["id"], active=False)
        profile = sqlite_db.get_profile(created["id"])
        assert profile["active"] is False

    def test_delete_profile(self, sqlite_db):
        created = sqlite_db.insert_profile(location="zone-01", name="Test")
        assert sqlite_db.delete_profile(created["id"]) is True
        assert sqlite_db.get_profile(created["id"]) is None

    def test_delete_profile_not_found(self, sqlite_db):
        assert sqlite_db.delete_profile("nonexistent") is False

    def test_delete_profile_cascades_goals(self, sqlite_db):
        """Deleting a profile should cascade-delete its goals."""
        profile = sqlite_db.insert_profile(location="zone-01", name="Test")
        sqlite_db.insert_goal(profile_id=profile["id"], metric="temp1", range_min=22.0, range_max=28.0)
        sqlite_db.insert_goal(profile_id=profile["id"], metric="hum1", range_min=45.0, range_max=65.0)
        assert len(sqlite_db.get_goals_for_profile(profile["id"])) == 2

        sqlite_db.delete_profile(profile["id"])
        assert len(sqlite_db.get_goals_for_profile(profile["id"])) == 0

    def test_get_all_profiles(self, sqlite_db):
        sqlite_db.insert_profile(location="zone-01", name="Profile A")
        sqlite_db.insert_profile(location="zone-02", name="Profile B")
        profiles = sqlite_db.get_all_profiles()
        assert len(profiles) == 2
        assert profiles[0]["location"] == "zone-01"
        assert profiles[1]["location"] == "zone-02"

    def test_default_strategy(self, sqlite_db):
        profile = sqlite_db.insert_profile(location="zone-01", name="Test")
        assert profile["strategy"] == "balanced"


class TestGoals:
    """Goal CRUD operations."""

    @pytest.fixture
    def profile(self, sqlite_db):
        return sqlite_db.insert_profile(location="zone-01", name="Test Profile")

    def test_create_sensor_goal(self, sqlite_db, profile):
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"],
            metric="temp1",
            metric_type="sensor",
            phase="flower",
            range_min=24.0,
            range_max=28.0,
            tolerance=1.0,
            priority=1.0,
        )
        assert goal["id"]
        assert goal["profileId"] == profile["id"]
        assert goal["metric"] == "temp1"
        assert goal["metricType"] == "sensor"
        assert goal["phase"] == "flower"
        assert goal["rangeMin"] == 24.0
        assert goal["rangeMax"] == 28.0
        assert goal["tolerance"] == 1.0
        assert goal["priority"] == 1.0

    def test_create_derived_goal(self, sqlite_db, profile):
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"],
            metric="vpd",
            metric_type="derived",
            range_min=1.0,
            range_max=1.3,
            priority=1.5,
        )
        assert goal["metricType"] == "derived"
        assert goal["priority"] == 1.5

    def test_create_relay_schedule_goal(self, sqlite_db, profile):
        schedule = {"06:00-22:00": {"expected": True}, "22:00-06:00": {"expected": False}}
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"],
            metric="relay6",
            metric_type="relay_schedule",
            schedule=schedule,
        )
        assert goal["metricType"] == "relay_schedule"
        assert goal["schedule"] == schedule

    def test_get_goal_by_id(self, sqlite_db, profile):
        created = sqlite_db.insert_goal(profile_id=profile["id"], metric="temp1", range_min=22.0)
        fetched = sqlite_db.get_goal(created["id"])
        assert fetched is not None
        assert fetched["metric"] == "temp1"

    def test_get_goal_not_found(self, sqlite_db):
        assert sqlite_db.get_goal("nonexistent") is None

    def test_update_goal(self, sqlite_db, profile):
        created = sqlite_db.insert_goal(
            profile_id=profile["id"], metric="temp1", range_min=22.0, range_max=28.0
        )
        updated = sqlite_db.update_goal(created["id"], range_min=24.0, range_max=26.0)
        assert updated["rangeMin"] == 24.0
        assert updated["rangeMax"] == 26.0

    def test_update_goal_not_found(self, sqlite_db):
        assert sqlite_db.update_goal("nonexistent", range_min=20.0) is None

    def test_delete_goal(self, sqlite_db, profile):
        created = sqlite_db.insert_goal(profile_id=profile["id"], metric="temp1")
        assert sqlite_db.delete_goal(created["id"]) is True
        assert sqlite_db.get_goal(created["id"]) is None

    def test_delete_goal_not_found(self, sqlite_db):
        assert sqlite_db.delete_goal("nonexistent") is False

    def test_get_goals_for_profile(self, sqlite_db, profile):
        sqlite_db.insert_goal(profile_id=profile["id"], metric="temp1", priority=1.0)
        sqlite_db.insert_goal(profile_id=profile["id"], metric="hum1", priority=2.0)
        sqlite_db.insert_goal(profile_id=profile["id"], metric="vpd", priority=1.5)

        goals = sqlite_db.get_goals_for_profile(profile["id"])
        assert len(goals) == 3
        # Ordered by priority DESC
        assert goals[0]["metric"] == "hum1"
        assert goals[1]["metric"] == "vpd"
        assert goals[2]["metric"] == "temp1"

    def test_get_goals_filtered_by_phase(self, sqlite_db, profile):
        sqlite_db.insert_goal(profile_id=profile["id"], metric="temp1", phase="flower")
        sqlite_db.insert_goal(profile_id=profile["id"], metric="hum1", phase="veg")
        sqlite_db.insert_goal(profile_id=profile["id"], metric="vpd")  # all phases

        flower_goals = sqlite_db.get_goals_for_profile(profile["id"], phase="flower")
        assert len(flower_goals) == 2  # flower + all-phases
        metrics = {g["metric"] for g in flower_goals}
        assert "temp1" in metrics
        assert "vpd" in metrics
        assert "hum1" not in metrics

    def test_delete_goals_for_profile(self, sqlite_db, profile):
        sqlite_db.insert_goal(profile_id=profile["id"], metric="temp1")
        sqlite_db.insert_goal(profile_id=profile["id"], metric="hum1")
        count = sqlite_db.delete_goals_for_profile(profile["id"])
        assert count == 2
        assert sqlite_db.get_goals_for_profile(profile["id"]) == []

    def test_goal_default_values(self, sqlite_db, profile):
        goal = sqlite_db.insert_goal(profile_id=profile["id"], metric="temp1")
        assert goal["metricType"] == "sensor"
        assert goal["tolerance"] == 0.0
        assert goal["priority"] == 1.0
        assert goal["phase"] is None
        assert goal["schedule"] is None

    def test_open_ended_range(self, sqlite_db, profile):
        """Goal with only range_min (no upper bound)."""
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"], metric="soil1", range_min=40.0
        )
        assert goal["rangeMin"] == 40.0
        assert goal["rangeMax"] is None

    def test_create_goal_with_time_window(self, sqlite_db, profile):
        """Goal with day/night time window stores and returns correctly."""
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"],
            metric="temp1",
            range_min=24.0,
            range_max=28.0,
            time_window_on_hour=6.0,
            time_window_off_hour=22.0,
        )
        assert goal["timeWindow"] is not None
        assert goal["timeWindow"]["onHour"] == 6.0
        assert goal["timeWindow"]["offHour"] == 22.0

    def test_create_goal_without_time_window(self, sqlite_db, profile):
        """Goal without time window has timeWindow=None."""
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"], metric="hum1", range_min=45.0,
        )
        assert goal["timeWindow"] is None

    def test_update_goal_set_time_window(self, sqlite_db, profile):
        """Adding time window to an existing goal."""
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"], metric="temp1", range_min=22.0,
        )
        assert goal["timeWindow"] is None

        updated = sqlite_db.update_goal(
            goal["id"],
            time_window_on_hour=22.0,
            time_window_off_hour=6.0,
        )
        assert updated["timeWindow"] is not None
        assert updated["timeWindow"]["onHour"] == 22.0
        assert updated["timeWindow"]["offHour"] == 6.0

    def test_update_goal_clear_time_window(self, sqlite_db, profile):
        """Clearing time window by setting to None."""
        goal = sqlite_db.insert_goal(
            profile_id=profile["id"],
            metric="temp1",
            time_window_on_hour=6.0,
            time_window_off_hour=22.0,
        )
        assert goal["timeWindow"] is not None

        updated = sqlite_db.update_goal(
            goal["id"],
            time_window_on_hour=None,
            time_window_off_hour=None,
        )
        assert updated["timeWindow"] is None


class TestHealth:
    """Ecosystem health storage."""

    def test_insert_and_query(self, sqlite_db):
        now = int(time.time() * 1000)
        sqlite_db.insert_health("zone-01", now, 0.85, {"goalScores": {"temp1": 1.0}})
        sqlite_db.insert_health("zone-01", now + 60000, 0.90, {"goalScores": {"temp1": 1.0}})

        results = sqlite_db.query_health("zone-01", since_ms=now - 1000, until_ms=now + 120000)
        assert len(results) == 2
        assert results[0]["score"] == 0.85
        assert results[1]["score"] == 0.90

    def test_query_with_range(self, sqlite_db):
        base = int(time.time() * 1000)
        for i in range(5):
            sqlite_db.insert_health("zone-01", base + i * 60000, 0.80 + i * 0.02, {})

        results = sqlite_db.query_health(
            "zone-01", since_ms=base + 60000, until_ms=base + 180000
        )
        assert len(results) == 3

    def test_get_latest_health(self, sqlite_db):
        now = int(time.time() * 1000)
        sqlite_db.insert_health("zone-01", now, 0.75, {"test": True})
        sqlite_db.insert_health("zone-01", now + 1000, 0.82, {"test": True})

        latest = sqlite_db.get_latest_health("zone-01")
        assert latest is not None
        assert latest["score"] == 0.82

    def test_get_latest_health_not_found(self, sqlite_db):
        assert sqlite_db.get_latest_health("nonexistent") is None

    def test_health_per_location(self, sqlite_db):
        now = int(time.time() * 1000)
        sqlite_db.insert_health("zone-01", now, 0.90, {})
        sqlite_db.insert_health("zone-02", now, 0.70, {})

        z1 = sqlite_db.query_health("zone-01", since_ms=now - 1000)
        z2 = sqlite_db.query_health("zone-02", since_ms=now - 1000)
        assert len(z1) == 1
        assert len(z2) == 1
        assert z1[0]["score"] == 0.90
        assert z2[0]["score"] == 0.70

    def test_health_detail_json(self, sqlite_db):
        now = int(time.time() * 1000)
        detail = {
            "goalScores": {"temp1": 1.0, "vpd": 0.85},
            "outOfRange": ["vpd"],
            "energy": {"totalWh": 12.5},
        }
        sqlite_db.insert_health("zone-01", now, 0.92, detail)
        result = sqlite_db.get_latest_health("zone-01")
        assert result["detail"]["goalScores"]["vpd"] == 0.85
        assert result["detail"]["energy"]["totalWh"] == 12.5
