"""Tests for location_goals SQLite CRUD."""

import pytest


class TestLocationGoals:
    """Tests for SqliteClient location_goals methods."""

    def test_upsert_and_get(self, sqlite_db):
        result = sqlite_db.upsert_location_goal("grow-tent", "tomatoes in veg stage")
        assert result["location"] == "grow-tent"
        assert result["goal"] == "tomatoes in veg stage"
        assert result["createdAt"] > 0

        fetched = sqlite_db.get_location_goal("grow-tent")
        assert fetched is not None
        assert fetched["location"] == "grow-tent"
        assert fetched["goal"] == "tomatoes in veg stage"

    def test_upsert_updates_existing(self, sqlite_db):
        sqlite_db.upsert_location_goal("grow-tent", "tomatoes in veg stage")
        sqlite_db.upsert_location_goal("grow-tent", "peppers in flowering stage")
        fetched = sqlite_db.get_location_goal("grow-tent")
        assert fetched["goal"] == "peppers in flowering stage"

    def test_get_nonexistent(self, sqlite_db):
        assert sqlite_db.get_location_goal("nonexistent") is None

    def test_get_all_goals(self, sqlite_db):
        sqlite_db.upsert_location_goal("grow-tent", "tomatoes")
        sqlite_db.upsert_location_goal("server-room", "keep cool")
        sqlite_db.upsert_location_goal("bedroom", "comfort")

        goals = sqlite_db.get_all_location_goals()
        assert len(goals) == 3
        # Ordered by location
        assert goals[0]["location"] == "bedroom"
        assert goals[1]["location"] == "grow-tent"
        assert goals[2]["location"] == "server-room"

    def test_get_all_empty(self, sqlite_db):
        assert sqlite_db.get_all_location_goals() == []

    def test_delete_goal(self, sqlite_db):
        sqlite_db.upsert_location_goal("grow-tent", "tomatoes")
        assert sqlite_db.delete_location_goal("grow-tent") is True
        assert sqlite_db.get_location_goal("grow-tent") is None

    def test_delete_nonexistent(self, sqlite_db):
        assert sqlite_db.delete_location_goal("nonexistent") is False
