"""Tests for room configuration loading, validation, and integration."""

import math
import textwrap
from pathlib import Path

import pytest
import yaml

from simulations.physics import (
    ACTUATOR_SPECS,
    ActuatorSpec,
    PhysicsEngine,
    PlantModel,
    SpaceConfig,
)
from simulations.room_config import (
    RoomConfig,
    default_room_config,
    load_room_config,
)


ROOMS_DIR = Path(__file__).resolve().parent.parent / "simulations" / "rooms"


# ---------------------------------------------------------------------------
# Default room config
# ---------------------------------------------------------------------------


class TestDefaultRoomConfig:
    def test_returns_room_config(self):
        room = default_room_config()
        assert isinstance(room, RoomConfig)
        assert room.name == "Default Tent"

    def test_space_matches_defaults(self):
        room = default_room_config()
        defaults = SpaceConfig()
        assert room.space.floor_area_m2 == defaults.floor_area_m2
        assert room.space.volume_m3 == defaults.volume_m3
        assert room.space.ach_base == defaults.ach_base
        assert room.space.ach_max == defaults.ach_max
        assert room.space.num_pots == defaults.num_pots
        assert room.space.max_ppfd == defaults.max_ppfd

    def test_actuator_specs_match_defaults(self):
        room = default_room_config()
        assert set(room.actuator_specs.keys()) == set(ACTUATOR_SPECS.keys())
        for rid, spec in room.actuator_specs.items():
            orig = ACTUATOR_SPECS[rid]
            assert spec.name == orig.name
            assert spec.max_watts == orig.max_watts
            assert spec.control_type == orig.control_type

    def test_actuator_specs_are_independent_copies(self):
        """Mutating room config specs should NOT affect module-level ACTUATOR_SPECS."""
        room = default_room_config()
        room.actuator_specs["relay1"].control_type = "variable"
        assert ACTUATOR_SPECS["relay1"].control_type == "binary"

    def test_ambient_schedule_is_default(self):
        room = default_room_config()
        assert room.ambient_schedule_fn is not None
        # Check peak at 14:00 (should be ~32°C)
        temp_14 = room.ambient_schedule_fn(14.0)
        assert 31.5 < temp_14 < 32.5

    def test_plant_is_default_veg(self):
        room = default_room_config()
        assert room.plant.phase == "veg"
        assert room.plant.lai == 3.0

    def test_initial_conditions(self):
        room = default_room_config()
        assert room.initial_conditions["temperature"] == 24.0
        assert room.initial_conditions["humidity"] == 55.0
        assert room.initial_conditions["co2"] == 420.0
        assert len(room.initial_conditions["soil_moisture"]) == 4


# ---------------------------------------------------------------------------
# YAML loading — preset files
# ---------------------------------------------------------------------------


class TestLoadPresetFiles:
    def test_load_tent_4x4(self):
        room = load_room_config(ROOMS_DIR / "tent_4x4.yaml")
        assert room.name == "4x4 Grow Tent"
        assert room.space.floor_area_m2 == 1.44
        assert room.space.volume_m3 == 2.88
        assert room.space.num_pots == 4
        assert len(room.actuator_specs) == 7
        assert all(s.control_type == "binary" for s in room.actuator_specs.values())

    def test_load_commercial_10x10(self):
        room = load_room_config(ROOMS_DIR / "commercial_10x10.yaml")
        assert room.name == "10x10 Commercial Room"
        assert room.space.floor_area_m2 == 100.0
        assert room.space.volume_m3 == 300.0
        assert room.space.num_pots == 50
        # Variable actuators
        assert room.actuator_specs["relay2"].control_type == "variable"
        assert room.actuator_specs["relay4"].control_type == "variable"
        assert room.actuator_specs["relay6"].control_type == "variable"
        # Binary actuators
        assert room.actuator_specs["relay1"].control_type == "binary"
        assert room.actuator_specs["relay5"].control_type == "binary"

    def test_load_warehouse(self):
        room = load_room_config(ROOMS_DIR / "warehouse.yaml")
        assert room.name == "Warehouse Facility"
        assert room.space.floor_area_m2 == 600.0
        assert room.space.num_pots == 200
        # Fixed ambient — no schedule
        assert room.ambient_schedule_fn is None
        assert room.ambient_temp == 25.0

    def test_commercial_soil_moisture_replicated(self):
        """Single soil_moisture value replicates for all pots."""
        room = load_room_config(ROOMS_DIR / "commercial_10x10.yaml")
        sm = room.initial_conditions.get("soil_moisture", [])
        assert len(sm) == 50
        assert all(v == 42.0 for v in sm)


# ---------------------------------------------------------------------------
# Ambient schedule
# ---------------------------------------------------------------------------


class TestAmbientSchedule:
    def test_sinusoidal_peak_at_peak_hour(self):
        room = load_room_config(ROOMS_DIR / "tent_4x4.yaml")
        assert room.ambient_schedule_fn is not None
        # Peak should be at peak_hour (14.0)
        temp_peak = room.ambient_schedule_fn(14.0)
        assert 31.5 < temp_peak < 32.5  # 26 + 6 = 32

    def test_sinusoidal_trough(self):
        room = load_room_config(ROOMS_DIR / "tent_4x4.yaml")
        # Trough 12h after peak → hour 2
        temp_trough = room.ambient_schedule_fn(2.0)
        assert 19.5 < temp_trough < 20.5  # 26 - 6 = 20

    def test_sinusoidal_mean_at_crossing(self):
        room = load_room_config(ROOMS_DIR / "tent_4x4.yaml")
        # Mean at 8:00 and 20:00 (quarter points)
        temp_8 = room.ambient_schedule_fn(8.0)
        assert 25.5 < temp_8 < 26.5

    def test_fixed_schedule_returns_none(self):
        room = load_room_config(ROOMS_DIR / "warehouse.yaml")
        assert room.ambient_schedule_fn is None
        assert room.ambient_temp == 25.0

    def test_custom_sinusoidal_params(self, tmp_path):
        config = {
            "name": "Custom",
            "ambient": {
                "schedule": "sinusoidal",
                "mean_temp": 30.0,
                "amplitude": 4.0,
                "peak_hour": 16.0,
            },
        }
        p = tmp_path / "custom.yaml"
        p.write_text(yaml.dump(config))
        room = load_room_config(p)
        # Peak at hour 16
        temp_peak = room.ambient_schedule_fn(16.0)
        assert 33.5 < temp_peak < 34.5  # 30 + 4 = 34


# ---------------------------------------------------------------------------
# Plant model
# ---------------------------------------------------------------------------


class TestPlantModel:
    def test_phase_defaults_applied(self, tmp_path):
        config = {"name": "Test", "plant": {"phase": "flower"}}
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump(config))
        room = load_room_config(p)
        assert room.plant.phase == "flower"
        assert room.plant.lai == 4.5
        assert room.plant.pmax == 30.0

    def test_explicit_overrides(self, tmp_path):
        config = {
            "name": "Test",
            "plant": {"phase": "veg", "lai": 5.0, "pmax": 35.0},
        }
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump(config))
        room = load_room_config(p)
        assert room.plant.lai == 5.0
        assert room.plant.pmax == 35.0
        # Non-overridden field keeps phase default
        assert room.plant.g0 == 0.01

    def test_no_plant_section_uses_default(self, tmp_path):
        config = {"name": "Test"}
        p = tmp_path / "test.yaml"
        p.write_text(yaml.dump(config))
        room = load_room_config(p)
        assert room.plant.phase == "veg"
        assert room.plant.lai == 3.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_room_config("/nonexistent/path.yaml")

    def test_invalid_yaml_type(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("just a string")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_room_config(p)

    def test_negative_floor_area(self, tmp_path):
        config = {"name": "Bad", "space": {"floor_area_m2": -10.0}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="floor_area_m2"):
            load_room_config(p)

    def test_zero_volume(self, tmp_path):
        config = {"name": "Bad", "space": {"volume_m3": 0}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="volume_m3"):
            load_room_config(p)

    def test_zero_pots(self, tmp_path):
        config = {"name": "Bad", "space": {"num_pots": 0}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="num_pots"):
            load_room_config(p)

    def test_invalid_control_type(self, tmp_path):
        config = {
            "name": "Bad",
            "actuators": {
                "relay1": {"name": "fan", "max_watts": 45, "control_type": "stepped"},
            },
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="control_type"):
            load_room_config(p)

    def test_invalid_phase(self, tmp_path):
        config = {"name": "Bad", "plant": {"phase": "harvest"}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="plant.phase"):
            load_room_config(p)

    def test_invalid_schedule(self, tmp_path):
        config = {"name": "Bad", "ambient": {"schedule": "custom"}}
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="ambient.schedule"):
            load_room_config(p)

    def test_actuator_missing_name(self, tmp_path):
        config = {
            "name": "Bad",
            "actuators": {"relay1": {"max_watts": 45}},
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="missing required 'name'"):
            load_room_config(p)


# ---------------------------------------------------------------------------
# PhysicsEngine instance specs integration
# ---------------------------------------------------------------------------


class TestPhysicsEngineInstanceSpecs:
    def test_custom_specs_used_by_set_actuator(self):
        """PhysicsEngine with custom specs uses those for quantization."""
        custom_specs = {
            "relay1": ActuatorSpec("fan", "relay1", 100, "variable"),
            "relay2": ActuatorSpec("exhaust_fan", "relay2", 200, "variable"),
        }
        env = PhysicsEngine(actuator_specs=custom_specs)
        env.set_actuator("relay1", 0.75)
        # Variable control → should preserve 0.75
        assert env.fan == 0.75

    def test_default_specs_snap_binary(self):
        """Default (no custom specs) still snaps binary actuators."""
        env = PhysicsEngine()
        env.set_actuator("relay1", 0.75)
        # Binary → snaps to 1.0
        assert env.fan == 1.0

    def test_get_power_uses_instance_specs(self):
        """Power calculation uses instance specs, not module-level."""
        custom_specs = {
            "relay1": ActuatorSpec("fan", "relay1", 1000, "variable"),
        }
        env = PhysicsEngine(actuator_specs=custom_specs)
        env.fan = 0.5
        assert env.get_power_watts() == 500.0

    def test_get_power_breakdown_uses_instance_specs(self):
        custom_specs = {
            "relay1": ActuatorSpec("fan", "relay1", 1000, "variable"),
            "relay6": ActuatorSpec("light", "relay6", 2000, "variable"),
        }
        env = PhysicsEngine(actuator_specs=custom_specs)
        env.fan = 0.5
        env.light = 1.0
        breakdown = env.get_power_breakdown()
        assert breakdown["fan"] == 500.0
        assert breakdown["light"] == 2000.0


# ---------------------------------------------------------------------------
# MPC integration with custom specs
# ---------------------------------------------------------------------------


class TestMPCWithCustomSpecs:
    def test_mpc_uses_custom_wattage(self):
        """MPCPlanner with custom specs computes energy cost from those wattages."""
        import numpy as np
        from simulations.state_planner import (
            ACTUATOR_NAMES,
            GoalSpec,
            MPCConfig,
            MPCPlanner,
            NUM_ACTUATORS,
        )

        custom_specs = {
            f"relay{i+1}": ActuatorSpec(name, f"relay{i+1}", 1000, "variable")
            for i, name in enumerate(ACTUATOR_NAMES)
        }
        goals = [GoalSpec("temp1", 24.0, 20.0, 28.0, 1.0, 1.0)]
        cfg = MPCConfig(horizon_minutes=5.0)

        planner_custom = MPCPlanner(config=cfg, goals=goals, actuator_specs=custom_specs)
        planner_default = MPCPlanner(config=cfg, goals=goals)

        # With all actuators at equal intensity, custom planner should
        # compute different energy costs due to different wattages
        n_int = cfg.num_control_intervals
        u = np.full(n_int * NUM_ACTUATORS, 0.5)

        custom_energy = planner_custom._energy_cost(u)
        default_energy = planner_default._energy_cost(u)

        # Both should return the same NORMALIZED cost since all actuators
        # have equal weight — the normalization cancels out. But the raw
        # watts differ (1000 each vs varied), so the planner is using its
        # own watts vector.
        assert planner_custom._max_power == 7000.0  # 7 * 1000
        assert planner_default._max_power != 7000.0


# ---------------------------------------------------------------------------
# Integration: load YAML → run short simulation
# ---------------------------------------------------------------------------


class TestRoomConfigIntegration:
    def test_tent_yaml_produces_identical_results(self):
        """Loading tent_4x4.yaml should produce the same simulation as default_room_config()."""
        from simulations.room_config import default_room_config

        room = load_room_config(ROOMS_DIR / "tent_4x4.yaml")
        defaults = default_room_config()

        # Build env from YAML room config
        env_room = PhysicsEngine(
            noise=False,
            ambient_temp=room.ambient_temp,
            ambient_schedule=room.ambient_schedule_fn,
            tent=room.space,
            plant=room.plant,
            actuator_specs=room.actuator_specs,
            substrate_fn=room.substrate_fn,
            substrate_config=room.substrate_config,
            substrate_container=room.substrate_container,
            temperature=room.initial_conditions["temperature"],
            humidity=room.initial_conditions["humidity"],
            co2=room.initial_conditions["co2"],
            soil_moisture=list(room.initial_conditions["soil_moisture"]),
        )

        # Build env from default_room_config()
        ic = defaults.initial_conditions
        env_default = PhysicsEngine(
            noise=False,
            ambient_temp=defaults.ambient_temp,
            ambient_schedule=defaults.ambient_schedule_fn,
            tent=defaults.space,
            plant=defaults.plant,
            actuator_specs=defaults.actuator_specs,
            substrate_fn=defaults.substrate_fn,
            substrate_config=defaults.substrate_config,
            substrate_container=defaults.substrate_container,
            temperature=ic["temperature"],
            humidity=ic["humidity"],
            co2=ic["co2"],
            soil_moisture=list(ic["soil_moisture"]),
        )

        # Run both for 10 steps
        for _ in range(10):
            env_room.step(30.0, current_hour=12.0)
            env_default.step(30.0, current_hour=12.0)

        # Should produce identical readings
        r1 = env_room.get_readings()
        r2 = env_default.get_readings()
        for key in r1:
            assert abs(r1[key] - r2[key]) < 0.01, f"{key}: {r1[key]} != {r2[key]}"

    def test_commercial_room_runs_without_error(self):
        """Smoke test: commercial room config loads and simulates."""
        room = load_room_config(ROOMS_DIR / "commercial_10x10.yaml")
        env = PhysicsEngine(
            noise=False,
            ambient_temp=room.ambient_temp,
            ambient_schedule=room.ambient_schedule_fn,
            tent=room.space,
            plant=room.plant,
            actuator_specs=room.actuator_specs,
        )
        # Run 5 steps without error
        for _ in range(5):
            env.step(30.0, current_hour=12.0)
        readings = env.get_readings()
        assert "temp1" in readings
        assert "co2_1" in readings

    def test_warehouse_fixed_ambient(self):
        """Warehouse uses fixed ambient (no schedule)."""
        room = load_room_config(ROOMS_DIR / "warehouse.yaml")
        env = PhysicsEngine(
            noise=False,
            ambient_temp=room.ambient_temp,
            ambient_schedule=room.ambient_schedule_fn,
            tent=room.space,
            plant=room.plant,
            actuator_specs=room.actuator_specs,
        )
        # Step a few times — temperature should drift toward fixed ambient (25°C)
        for _ in range(20):
            env.step(30.0, current_hour=12.0)
        # Initial was 22°C, ambient is 25°C → temp should have risen
        assert env.temperature > 22.0


# ---------------------------------------------------------------------------
# Ventilation (duct physics) parsing
# ---------------------------------------------------------------------------


class TestVentilationParsing:
    def test_no_ventilation_section_returns_none(self, tmp_path):
        """No ventilation section → ventilation_fn is None."""
        config = {"name": "No Vent"}
        p = tmp_path / "no_vent.yaml"
        p.write_text(yaml.dump(config))
        room = load_room_config(p)
        assert room.ventilation_fn is None

    def test_valid_ventilation_section_builds_callable(self, tmp_path):
        config = {
            "name": "Vent Test",
            "space": {"volume_m3": 10.0},
            "ventilation": {
                "exhaust": {"rated_cfm": 400, "max_static_pressure": 1.5},
                "duct": {"diameter_in": 6, "length_ft": 10},
            },
        }
        p = tmp_path / "vent.yaml"
        p.write_text(yaml.dump(config))
        room = load_room_config(p)
        assert room.ventilation_fn is not None
        assert callable(room.ventilation_fn)
        # Should return a positive ACH at full speed
        ach = room.ventilation_fn(1.0)
        assert ach > 0

    def test_ventilation_uses_room_volume(self, tmp_path):
        """ACH should differ for different room volumes with same fan."""
        base = {
            "ventilation": {
                "exhaust": {"rated_cfm": 400, "max_static_pressure": 1.5},
                "duct": {"diameter_in": 6, "length_ft": 10},
            },
        }
        # Small room
        small = {**base, "name": "Small", "space": {"volume_m3": 10.0}}
        p1 = tmp_path / "small.yaml"
        p1.write_text(yaml.dump(small))
        r1 = load_room_config(p1)

        # Big room
        big = {**base, "name": "Big", "space": {"volume_m3": 300.0}}
        p2 = tmp_path / "big.yaml"
        p2.write_text(yaml.dump(big))
        r2 = load_room_config(p2)

        assert r1.ventilation_fn(1.0) > r2.ventilation_fn(1.0)

    def test_default_room_config_no_ventilation(self):
        """Default tent config has no ventilation (uses linear ACH model)."""
        room = default_room_config()
        assert room.ventilation_fn is None

    def test_tent_yaml_no_ventilation(self):
        """tent_4x4.yaml has no ventilation section."""
        room = load_room_config(ROOMS_DIR / "tent_4x4.yaml")
        assert room.ventilation_fn is None

    def test_commercial_has_ventilation(self):
        """commercial_10x10.yaml has ventilation section."""
        room = load_room_config(ROOMS_DIR / "commercial_10x10.yaml")
        assert room.ventilation_fn is not None
        ach = room.ventilation_fn(1.0)
        assert 1.0 < ach < 15.0

    def test_warehouse_has_ventilation(self):
        """warehouse.yaml has ventilation section."""
        room = load_room_config(ROOMS_DIR / "warehouse.yaml")
        assert room.ventilation_fn is not None
        ach = room.ventilation_fn(1.0)
        assert 1.0 < ach < 10.0


class TestVentilationValidation:
    def test_missing_rated_cfm(self, tmp_path):
        config = {
            "name": "Bad",
            "ventilation": {
                "exhaust": {"max_static_pressure": 1.5},
                "duct": {"diameter_in": 6, "length_ft": 10},
            },
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="rated_cfm"):
            load_room_config(p)

    def test_negative_rated_cfm(self, tmp_path):
        config = {
            "name": "Bad",
            "ventilation": {
                "exhaust": {"rated_cfm": -100, "max_static_pressure": 1.5},
                "duct": {"diameter_in": 6, "length_ft": 10},
            },
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="rated_cfm.*positive"):
            load_room_config(p)

    def test_missing_max_static_pressure(self, tmp_path):
        config = {
            "name": "Bad",
            "ventilation": {
                "exhaust": {"rated_cfm": 400},
                "duct": {"diameter_in": 6, "length_ft": 10},
            },
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="max_static_pressure"):
            load_room_config(p)

    def test_invalid_duct_material(self, tmp_path):
        config = {
            "name": "Bad",
            "ventilation": {
                "exhaust": {"rated_cfm": 400, "max_static_pressure": 1.5},
                "duct": {"diameter_in": 6, "length_ft": 10, "material": "cardboard"},
            },
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="material"):
            load_room_config(p)

    def test_negative_duct_diameter(self, tmp_path):
        config = {
            "name": "Bad",
            "ventilation": {
                "exhaust": {"rated_cfm": 400, "max_static_pressure": 1.5},
                "duct": {"diameter_in": -6, "length_ft": 10},
            },
        }
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.dump(config))
        with pytest.raises(ValueError, match="diameter_in.*positive"):
            load_room_config(p)


# ---------------------------------------------------------------------------
# PhysicsEngine ventilation_fn integration
# ---------------------------------------------------------------------------


class TestPhysicsEngineVentilation:
    def test_ventilation_fn_used_when_provided(self):
        """PhysicsEngine with ventilation_fn uses it for air exchange."""
        from simulations.duct_physics import build_ventilation_fn

        fn = build_ventilation_fn(
            rated_cfm=400, max_static_pressure_inwc=1.5,
            diameter_in=6, length_ft=10, volume_m3=4.32,
        )

        env = PhysicsEngine(
            noise=False, co2=800.0,
            ventilation_fn=fn,
        )
        env.exhaust_fan = 1.0

        # Step — CO2 should drop due to air exchange
        initial_co2 = env.co2
        for _ in range(5):
            env.step(30.0, current_hour=12.0)
        assert env.co2 < initial_co2

    def test_no_ventilation_fn_uses_linear_fallback(self):
        """PhysicsEngine without ventilation_fn uses linear ACH model."""
        env = PhysicsEngine(
            noise=False, co2=800.0,
            ventilation_fn=None,
        )
        env.exhaust_fan = 1.0

        initial_co2 = env.co2
        for _ in range(5):
            env.step(30.0, current_hour=12.0)
        # Should still drop CO2 via linear model
        assert env.co2 < initial_co2

    def test_ventilation_fn_vs_linear_different_rates(self):
        """Duct physics and linear models should produce different CO2 trajectories.

        Uses a larger room (300 m³) where the duct physics constraints
        (system resistance, carbon filter) are more pronounced than the
        abstract linear ach_max model.
        """
        from simulations.duct_physics import build_ventilation_fn

        space = SpaceConfig(
            floor_area_m2=100.0, volume_m3=300.0,
            ach_base=0.3, ach_max=12.0,
        )
        fn = build_ventilation_fn(
            rated_cfm=800, max_static_pressure_inwc=1.8,
            diameter_in=10, length_ft=20, material="smooth",
            elbows_90=2, has_carbon_filter=True,
            passive_ach=0.3, volume_m3=300.0,
        )

        env_duct = PhysicsEngine(noise=False, co2=800.0, tent=space, ventilation_fn=fn)
        env_linear = PhysicsEngine(noise=False, co2=800.0, tent=space, ventilation_fn=None)

        env_duct.exhaust_fan = 1.0
        env_linear.exhaust_fan = 1.0

        for _ in range(20):
            env_duct.step(30.0, current_hour=12.0)
            env_linear.step(30.0, current_hour=12.0)

        # Both should have lower CO2, but at different rates
        assert env_duct.co2 < 800.0
        assert env_linear.co2 < 800.0
        # The duct physics model (with real system resistance + carbon filter)
        # should deliver less airflow than the ideal linear ach_max=12 model
        assert env_duct.co2 != pytest.approx(env_linear.co2, rel=0.01)


# ---------------------------------------------------------------------------
# Substrate parsing
# ---------------------------------------------------------------------------


class TestSubstrateParsing:
    """Tests for substrate section parsing in room YAML files."""

    def test_default_room_has_substrate(self):
        room = default_room_config()
        assert room.substrate_fn is not None
        assert room.substrate_config is not None
        assert room.substrate_container is not None

    def test_default_room_living_soil(self):
        room = default_room_config()
        assert room.substrate_config.name == "living_soil"

    def test_tent_yaml_has_substrate(self):
        room = load_room_config(ROOMS_DIR / "tent_4x4.yaml")
        assert room.substrate_fn is not None
        assert room.substrate_config.name == "living_soil"
        assert room.substrate_container is not None

    def test_commercial_yaml_coco(self):
        room = load_room_config(ROOMS_DIR / "commercial_10x10.yaml")
        assert room.substrate_config.name == "coco_perlite_70_30"

    def test_warehouse_yaml_rockwool(self):
        room = load_room_config(ROOMS_DIR / "warehouse.yaml")
        assert room.substrate_config.name == "rockwool"

    def test_no_substrate_section_uses_defaults(self, tmp_path):
        """YAML without substrate section gets living soil defaults."""
        yaml_content = textwrap.dedent("""\
            name: "Bare Room"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            """)
        p = tmp_path / "bare.yaml"
        p.write_text(yaml_content)
        room = load_room_config(p)
        assert room.substrate_fn is not None
        assert room.substrate_config is not None

    def test_preset_override_field(self, tmp_path):
        """Override a single field on top of a preset."""
        yaml_content = textwrap.dedent("""\
            name: "Custom k_dry"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            substrate:
              medium: coco_perlite_70_30
              k_dry: 0.006
            """)
        p = tmp_path / "override.yaml"
        p.write_text(yaml_content)
        room = load_room_config(p)
        assert room.substrate_config.k_dry == 0.006
        # Other fields stay at preset defaults
        assert room.substrate_config.saturation_vwc == 72.0

    def test_pot_container_parsing(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            name: "Pot Room"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            substrate:
              medium: coco_perlite_70_30
              container:
                type: pot
                diameter_cm: 20
                height_cm: 20
            """)
        p = tmp_path / "pot.yaml"
        p.write_text(yaml_content)
        room = load_room_config(p)
        assert room.substrate_container.volume_liters > 0
        assert room.substrate_container.surface_area_m2 > 0

    def test_bed_container_parsing(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            name: "Bed Room"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            substrate:
              medium: living_soil
              container:
                type: bed
                length_cm: 120
                width_cm: 60
                depth_cm: 30
            """)
        p = tmp_path / "bed.yaml"
        p.write_text(yaml_content)
        room = load_room_config(p)
        assert room.substrate_container.volume_liters > 0

    def test_invalid_medium_raises(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            name: "Bad"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            substrate:
              medium: peat_moss
            """)
        p = tmp_path / "bad.yaml"
        p.write_text(yaml_content)
        with pytest.raises(ValueError, match="substrate.medium"):
            load_room_config(p)

    def test_invalid_container_type_raises(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            name: "Bad"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            substrate:
              medium: rockwool
              container:
                type: bucket
            """)
        p = tmp_path / "bad.yaml"
        p.write_text(yaml_content)
        with pytest.raises(ValueError, match="substrate.container.type"):
            load_room_config(p)

    def test_custom_medium_requires_all_fields(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            name: "Bad"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            substrate:
              medium: custom
              saturation_vwc: 70
            """)
        p = tmp_path / "bad.yaml"
        p.write_text(yaml_content)
        with pytest.raises(ValueError, match="required for medium=custom"):
            load_room_config(p)

    def test_custom_medium_full(self, tmp_path):
        yaml_content = textwrap.dedent("""\
            name: "Custom"
            space:
              volume_m3: 10.0
              floor_area_m2: 5.0
            substrate:
              medium: custom
              saturation_vwc: 70
              field_capacity_vwc: 50
              stress_onset_vwc: 25
              wilting_point_vwc: 10
              k_dry: 0.01
              infiltration_rate: 4.0
              drainage_rate: 0.2
              surface_evap_factor: 0.6
            """)
        p = tmp_path / "custom.yaml"
        p.write_text(yaml_content)
        room = load_room_config(p)
        assert room.substrate_config.name == "custom"
        assert room.substrate_config.saturation_vwc == 70.0
        assert room.substrate_config.k_dry == 0.01


# ---------------------------------------------------------------------------
# FastPhysicsEngine builder
# ---------------------------------------------------------------------------

class TestBuildFastEngine:
    """Tests for RoomConfig.build_fast_engine()."""

    def test_default_room_builds_fast_engine(self):
        room = default_room_config()
        eng = room.build_fast_engine()
        from simulations.fast_physics import FastPhysicsEngine
        assert isinstance(eng, FastPhysicsEngine)

    def test_fast_engine_uses_room_initial_conditions(self):
        room = default_room_config()
        eng = room.build_fast_engine()
        ic = room.initial_conditions
        assert eng.temperature == ic.get("temperature", 24.0)
        assert eng.co2 == ic.get("co2", 420.0)

    def test_fast_engine_averages_soil_moisture(self):
        room = default_room_config()
        eng = room.build_fast_engine()
        soil = room.initial_conditions.get("soil_moisture", [38.0])
        expected_avg = sum(soil) / len(soil)
        assert eng.vwc == pytest.approx(expected_avg, abs=0.1)

    def test_fast_engine_maps_substrate_config(self):
        room = default_room_config()
        eng = room.build_fast_engine()
        assert eng.substrate.wilting_vwc == room.substrate_config.wilting_point_vwc
        assert eng.substrate.sat_vwc == room.substrate_config.saturation_vwc
        assert eng.substrate.k_dry == room.substrate_config.k_dry

    def test_fast_engine_uses_actuator_specs(self):
        room = default_room_config()
        eng = room.build_fast_engine()
        assert "fan" in eng._spec_by_name
        assert eng._spec_by_name["fan"].max_watts == room.actuator_specs["relay1"].max_watts

    def test_fast_engine_uses_ventilation_fn(self):
        """Rooms with duct physics should pass ventilation_fn through."""
        commercial = ROOMS_DIR / "commercial_10x10.yaml"
        if commercial.exists():
            room = load_room_config(commercial)
            eng = room.build_fast_engine()
            assert eng.ventilation_fn is not None

    def test_fast_engine_can_step(self):
        room = default_room_config()
        eng = room.build_fast_engine()
        readings_before = eng.get_readings()
        eng.light = 1.0
        for _ in range(10):
            eng.step(30.0, current_hour=12.0)
        readings_after = eng.get_readings()
        # Temperature should change with light on
        assert readings_after["temp1"] != readings_before["temp1"]

    def test_fast_engine_override_initial_conditions(self):
        room = default_room_config()
        eng = room.build_fast_engine(temperature=30.0, humidity=70.0, vwc=50.0)
        assert eng.temperature == 30.0
        assert eng.humidity == pytest.approx(70.0, abs=0.1)
        assert eng.vwc == 50.0
