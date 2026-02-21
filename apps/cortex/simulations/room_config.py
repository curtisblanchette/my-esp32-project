"""Room configuration loader for simulation environments.

Reads YAML room configuration files that define physical space parameters,
actuator specifications, ambient conditions, plant model, and initial
conditions. Provides ``load_room_config()`` for YAML files and
``default_room_config()`` for the built-in tent defaults.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

import yaml

from .fast_physics import FastPhysicsEngine, FastSubstrateConfig
from .physics import ActuatorSpec, PlantModel, SpaceConfig, ACTUATOR_SPECS, default_ambient_schedule

if TYPE_CHECKING:
    from .substrate_physics import SubstrateConfig, ContainerGeometry


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RoomConfig:
    """Parsed room configuration."""

    name: str
    space: SpaceConfig
    actuator_specs: dict[str, ActuatorSpec]
    ambient_schedule_fn: Callable[[float], float] | None
    ambient_temp: float
    plant: PlantModel
    initial_conditions: dict[str, Any] = field(default_factory=dict)
    ventilation_fn: Callable[[float], float] | None = None
    # Substrate physics (always present)
    substrate_fn: Callable | None = None
    substrate_config: SubstrateConfig | None = None
    substrate_container: ContainerGeometry | None = None

    def build_fast_engine(
        self,
        *,
        noise: bool = False,
        temperature: float | None = None,
        humidity: float | None = None,
        co2: float | None = None,
        vwc: float | None = None,
    ) -> FastPhysicsEngine:
        """Construct a FastPhysicsEngine from this room configuration.

        Maps substrate presets to simplified k_dry/wilting/stress/irrig/sat
        parameters.  Passes ventilation_fn and actuator_specs through.
        """
        ic = self.initial_conditions

        # Map detailed substrate config → FastSubstrateConfig
        fast_sub = _substrate_to_fast(self.substrate_config)

        # Average initial soil moisture for single-VWC engine
        soil_ic = ic.get("soil_moisture", [38.0])
        avg_vwc = sum(soil_ic) / len(soil_ic) if soil_ic else 38.0

        return FastPhysicsEngine(
            temperature=temperature if temperature is not None else ic.get("temperature", 24.0),
            humidity=humidity if humidity is not None else ic.get("humidity", 55.0),
            co2=co2 if co2 is not None else ic.get("co2", 420.0),
            vwc=vwc if vwc is not None else avg_vwc,
            ambient_temp=self.ambient_temp,
            ambient_schedule=self.ambient_schedule_fn,
            noise=noise,
            tent=self.space,
            plant=self.plant,
            actuator_specs=self.actuator_specs,
            ventilation_fn=self.ventilation_fn,
            substrate=fast_sub,
        )


# ---------------------------------------------------------------------------
# Default config (reproduces current hardcoded behavior)
# ---------------------------------------------------------------------------

def default_room_config() -> RoomConfig:
    """Return the built-in tent defaults — no YAML file needed.

    Default substrate is living soil (perlite/soil/castings) in raised beds,
    matching the user's actual setup.
    """
    from .substrate_physics import living_soil, bed_geometry, build_substrate_fn

    # Deep-copy the module-level ACTUATOR_SPECS so callers can mutate freely
    specs = {
        rid: ActuatorSpec(
            name=s.name,
            relay_id=s.relay_id,
            max_watts=s.max_watts,
            control_type=s.control_type,
        )
        for rid, s in ACTUATOR_SPECS.items()
    }
    substrate = living_soil()
    container = bed_geometry(length_cm=120.0, width_cm=60.0, depth_cm=30.0)
    return RoomConfig(
        name="Default Tent",
        space=SpaceConfig(),
        actuator_specs=specs,
        ambient_schedule_fn=default_ambient_schedule,
        ambient_temp=30.0,
        plant=PlantModel(),
        initial_conditions={
            "temperature": 24.0,
            "humidity": 55.0,
            "co2": 420.0,
            "soil_moisture": [38.0, 36.0, 40.0, 35.0],
        },
        substrate_fn=build_substrate_fn(substrate, container),
        substrate_config=substrate,
        substrate_container=container,
    )


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

_VALID_CONTROL_TYPES = {"binary", "variable"}
_VALID_PHASES = {"seedling", "veg", "flower", "late_flower", "dry", "cure"}
_VALID_SCHEDULES = {"sinusoidal", "fixed"}
_VALID_DUCT_MATERIALS = {"smooth", "flex"}
_VALID_SUBSTRATE_TYPES: set[str] | None = None  # lazy — populated on first use
_VALID_CONTAINER_TYPES = {"pot", "bed"}


def load_room_config(path: str | Path) -> RoomConfig:
    """Load a room YAML file and return a validated ``RoomConfig``."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Room config not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Room config must be a YAML mapping, got {type(raw).__name__}")

    name = raw.get("name", path.stem)

    # ── Space ──────────────────────────────────────────────────────────
    space = _parse_space(raw.get("space", {}))

    # ── Actuators ──────────────────────────────────────────────────────
    actuator_specs = _parse_actuators(raw.get("actuators", {}))

    # ── Ambient ────────────────────────────────────────────────────────
    ambient_schedule_fn, ambient_temp = _parse_ambient(raw.get("ambient", {}))

    # ── Plant ──────────────────────────────────────────────────────────
    plant = _parse_plant(raw.get("plant", {}))

    # ── Initial conditions ─────────────────────────────────────────────
    initial_conditions = _parse_initial_conditions(
        raw.get("initial_conditions", {}), space.num_pots,
    )

    # ── Ventilation (duct physics) ──────────────────────────────────────
    ventilation_fn = _parse_ventilation(raw.get("ventilation"), space.volume_m3)

    # ── Substrate (soil moisture physics) ─────────────────────────────
    substrate_fn, substrate_config, substrate_container = _parse_substrate(
        raw.get("substrate"),
    )

    return RoomConfig(
        name=name,
        space=space,
        actuator_specs=actuator_specs,
        ambient_schedule_fn=ambient_schedule_fn,
        ambient_temp=ambient_temp,
        plant=plant,
        initial_conditions=initial_conditions,
        ventilation_fn=ventilation_fn,
        substrate_fn=substrate_fn,
        substrate_config=substrate_config,
        substrate_container=substrate_container,
    )


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_space(data: dict) -> SpaceConfig:
    if not isinstance(data, dict):
        raise ValueError("'space' must be a mapping")
    kwargs: dict[str, Any] = {}
    field_types = {
        "floor_area_m2": float,
        "volume_m3": float,
        "ach_base": float,
        "ach_max": float,
        "num_pots": int,
        "max_ppfd": float,
        "co2_ambient": float,
        "h_ambient": float,
    }
    for key, typ in field_types.items():
        if key in data:
            kwargs[key] = typ(data[key])

    # Validate positive values
    for key in ("floor_area_m2", "volume_m3", "ach_max", "max_ppfd"):
        if key in kwargs and kwargs[key] <= 0:
            raise ValueError(f"space.{key} must be positive, got {kwargs[key]}")
    if "ach_base" in kwargs and kwargs["ach_base"] < 0:
        raise ValueError(f"space.ach_base must be non-negative, got {kwargs['ach_base']}")
    if "num_pots" in kwargs and kwargs["num_pots"] < 1:
        raise ValueError(f"space.num_pots must be >= 1, got {kwargs['num_pots']}")

    return SpaceConfig(**kwargs)


def _parse_actuators(data: dict) -> dict[str, ActuatorSpec]:
    if not isinstance(data, dict):
        raise ValueError("'actuators' must be a mapping")
    if not data:
        # No actuators section → copy module defaults
        return {
            rid: ActuatorSpec(
                name=s.name, relay_id=s.relay_id,
                max_watts=s.max_watts, control_type=s.control_type,
                humidify_g_per_min=s.humidify_g_per_min,
                dehumidify_g_per_min=s.dehumidify_g_per_min,
            )
            for rid, s in ACTUATOR_SPECS.items()
        }

    specs: dict[str, ActuatorSpec] = {}
    for relay_id, spec_data in data.items():
        if not isinstance(spec_data, dict):
            raise ValueError(f"actuators.{relay_id} must be a mapping")
        name = spec_data.get("name")
        if not name:
            raise ValueError(f"actuators.{relay_id} missing required 'name'")
        max_watts = spec_data.get("max_watts", 0)
        control_type = spec_data.get("control_type", "binary")
        if control_type not in _VALID_CONTROL_TYPES:
            raise ValueError(
                f"actuators.{relay_id}.control_type must be one of "
                f"{_VALID_CONTROL_TYPES}, got '{control_type}'"
            )
        humidify = spec_data.get("humidify_g_per_min")
        dehumidify = spec_data.get("dehumidify_g_per_min")
        specs[relay_id] = ActuatorSpec(
            name=str(name),
            relay_id=str(relay_id),
            max_watts=float(max_watts),
            control_type=str(control_type),
            humidify_g_per_min=float(humidify) if humidify is not None else None,
            dehumidify_g_per_min=float(dehumidify) if dehumidify is not None else None,
        )
    return specs


def _parse_ambient(data: dict) -> tuple[Callable[[float], float] | None, float]:
    """Return (schedule_fn, static_temp)."""
    if not isinstance(data, dict) or not data:
        return default_ambient_schedule, 30.0

    schedule_type = data.get("schedule", "sinusoidal")
    if schedule_type not in _VALID_SCHEDULES:
        raise ValueError(
            f"ambient.schedule must be one of {_VALID_SCHEDULES}, got '{schedule_type}'"
        )

    static_temp = float(data.get("temp", 30.0))

    if schedule_type == "fixed":
        return None, static_temp

    # Sinusoidal schedule
    mean_temp = float(data.get("mean_temp", 26.0))
    amplitude = float(data.get("amplitude", 6.0))
    peak_hour = float(data.get("peak_hour", 14.0))

    # Phase offset: peak_hour corresponds to sin peak (π/2)
    # sin(2π(h - offset)/24) = 1 when h = peak_hour
    # → offset = peak_hour - 6
    offset = peak_hour - 6.0

    def _schedule(hour: float, _m=mean_temp, _a=amplitude, _o=offset) -> float:
        return _m + _a * math.sin(2 * math.pi * (hour - _o) / 24.0)

    return _schedule, static_temp


def _parse_plant(data: dict) -> PlantModel:
    if not isinstance(data, dict) or not data:
        return PlantModel()

    phase = data.get("phase", "veg")
    if phase not in _VALID_PHASES:
        raise ValueError(f"plant.phase must be one of {_VALID_PHASES}, got '{phase}'")

    # Start with phase-appropriate defaults
    plant = PlantModel.for_phase(phase)

    # Overlay explicit overrides
    override_fields = {"lai", "pmax", "alpha", "t_opt", "t_sigma", "km_co2", "g0", "m_bb"}
    for key in override_fields:
        if key in data:
            setattr(plant, key, float(data[key]))

    return plant


def _parse_initial_conditions(data: dict, num_pots: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    result: dict[str, Any] = {}

    for key in ("temperature", "humidity", "co2", "light_intensity"):
        if key in data:
            result[key] = float(data[key])

    if "soil_moisture" in data:
        sm = data["soil_moisture"]
        if isinstance(sm, list):
            result["soil_moisture"] = [float(v) for v in sm]
        else:
            # Single value → replicate for all pots
            result["soil_moisture"] = [float(sm)] * num_pots

    return result


def _parse_ventilation(
    data: dict | None,
    volume_m3: float,
) -> Callable[[float], float] | None:
    """Parse optional ``ventilation:`` section into a duct-physics closure.

    Returns ``None`` when no ventilation section is present — the physics
    engine falls back to the linear ``ach_base + ach_max × fan`` model.
    """
    if not data or not isinstance(data, dict):
        return None

    from .duct_physics import build_ventilation_fn

    # ── Exhaust fan specs ────────────────────────────────────────────
    exhaust = data.get("exhaust", {})
    if not isinstance(exhaust, dict):
        raise ValueError("ventilation.exhaust must be a mapping")

    rated_cfm = exhaust.get("rated_cfm")
    if rated_cfm is None:
        raise ValueError("ventilation.exhaust.rated_cfm is required")
    rated_cfm = float(rated_cfm)
    if rated_cfm <= 0:
        raise ValueError(f"ventilation.exhaust.rated_cfm must be positive, got {rated_cfm}")

    max_sp = exhaust.get("max_static_pressure")
    if max_sp is None:
        raise ValueError("ventilation.exhaust.max_static_pressure is required")
    max_sp = float(max_sp)
    if max_sp <= 0:
        raise ValueError(
            f"ventilation.exhaust.max_static_pressure must be positive, got {max_sp}"
        )

    # ── Duct geometry ────────────────────────────────────────────────
    duct = data.get("duct", {})
    if not isinstance(duct, dict):
        raise ValueError("ventilation.duct must be a mapping")

    diameter_in = float(duct.get("diameter_in", 6))
    if diameter_in <= 0:
        raise ValueError(f"ventilation.duct.diameter_in must be positive, got {diameter_in}")

    length_ft = float(duct.get("length_ft", 10))
    if length_ft <= 0:
        raise ValueError(f"ventilation.duct.length_ft must be positive, got {length_ft}")

    material = duct.get("material", "flex")
    if material not in _VALID_DUCT_MATERIALS:
        raise ValueError(
            f"ventilation.duct.material must be one of {_VALID_DUCT_MATERIALS}, got '{material}'"
        )

    elbows_90 = int(duct.get("elbows_90", 0))
    has_carbon_filter = bool(duct.get("has_carbon_filter", False))

    # ── Passive ACH ──────────────────────────────────────────────────
    passive_ach = float(data.get("passive_ach", 0.5))

    return build_ventilation_fn(
        rated_cfm=rated_cfm,
        max_static_pressure_inwc=max_sp,
        diameter_in=diameter_in,
        length_ft=length_ft,
        material=material,
        elbows_90=elbows_90,
        has_carbon_filter=has_carbon_filter,
        passive_ach=passive_ach,
        volume_m3=volume_m3,
    )


def _parse_substrate(
    data: dict | None,
) -> tuple[Callable | None, SubstrateConfig | None, ContainerGeometry | None]:
    """Parse ``substrate:`` section into substrate physics objects.

    When no substrate section is present, returns defaults (living soil + bed).
    This ensures the substrate model is always active.
    """
    from .substrate_physics import (
        SubstrateConfig, build_substrate_fn,
        living_soil, default_container, SUBSTRATE_PRESETS,
    )

    global _VALID_SUBSTRATE_TYPES
    if _VALID_SUBSTRATE_TYPES is None:
        _VALID_SUBSTRATE_TYPES = set(SUBSTRATE_PRESETS.keys()) | {"custom"}

    # No section → use defaults (living soil in bed)
    if not data or not isinstance(data, dict):
        substrate = living_soil()
        container = default_container()
        return build_substrate_fn(substrate, container), substrate, container

    # ── Medium ────────────────────────────────────────────────────────
    medium = data.get("medium", "living_soil")
    if medium not in _VALID_SUBSTRATE_TYPES:
        raise ValueError(
            f"substrate.medium must be one of {_VALID_SUBSTRATE_TYPES}, got '{medium}'"
        )

    if medium == "custom":
        substrate = _parse_custom_substrate(data)
    else:
        substrate = SUBSTRATE_PRESETS[medium]()
        # Allow per-field overrides on top of preset
        overrides = {}
        override_fields = {
            "saturation_vwc", "field_capacity_vwc", "stress_onset_vwc",
            "wilting_point_vwc", "k_dry", "infiltration_rate",
            "drainage_rate", "surface_evap_factor",
        }
        for fld in override_fields:
            if fld in data:
                overrides[fld] = float(data[fld])
        if overrides:
            # Create a new SubstrateConfig with overrides applied
            from dataclasses import asdict
            cfg = asdict(substrate)
            cfg.update(overrides)
            substrate = SubstrateConfig(**cfg)

    # ── Container geometry ────────────────────────────────────────────
    container_data = data.get("container", {})
    if isinstance(container_data, dict) and container_data:
        container = _parse_container(container_data)
    else:
        container = default_container()

    return build_substrate_fn(substrate, container), substrate, container


def _parse_custom_substrate(data: dict) -> SubstrateConfig:
    """Parse a fully custom substrate definition (all fields required)."""
    from .substrate_physics import SubstrateConfig

    required = [
        "saturation_vwc", "field_capacity_vwc", "stress_onset_vwc",
        "wilting_point_vwc", "k_dry", "infiltration_rate",
        "drainage_rate", "surface_evap_factor",
    ]
    for fld in required:
        if fld not in data:
            raise ValueError(f"substrate.{fld} is required for medium=custom")

    return SubstrateConfig(
        name="custom",
        saturation_vwc=float(data["saturation_vwc"]),
        field_capacity_vwc=float(data["field_capacity_vwc"]),
        stress_onset_vwc=float(data["stress_onset_vwc"]),
        wilting_point_vwc=float(data["wilting_point_vwc"]),
        k_dry=float(data["k_dry"]),
        infiltration_rate=float(data["infiltration_rate"]),
        drainage_rate=float(data["drainage_rate"]),
        surface_evap_factor=float(data["surface_evap_factor"]),
    )


def _parse_container(data: dict) -> ContainerGeometry:
    """Parse a container geometry definition."""
    from .substrate_physics import pot_geometry, bed_geometry

    ctype = data.get("type", "pot")
    if ctype not in _VALID_CONTAINER_TYPES:
        raise ValueError(
            f"substrate.container.type must be one of {_VALID_CONTAINER_TYPES}, got '{ctype}'"
        )

    if ctype == "pot":
        diameter = float(data.get("diameter_cm", 30.0))
        height = float(data.get("height_cm", 30.0))
        if diameter <= 0 or height <= 0:
            raise ValueError("substrate.container dimensions must be positive")
        return pot_geometry(diameter, height)
    else:  # bed
        length = float(data.get("length_cm", 120.0))
        width = float(data.get("width_cm", 60.0))
        depth = float(data.get("depth_cm", 30.0))
        if length <= 0 or width <= 0 or depth <= 0:
            raise ValueError("substrate.container dimensions must be positive")
        return bed_geometry(length, width, depth)


# ---------------------------------------------------------------------------
# Substrate → FastSubstrateConfig mapping
# ---------------------------------------------------------------------------

def _substrate_to_fast(config: SubstrateConfig | None) -> FastSubstrateConfig:
    """Map a detailed SubstrateConfig to simplified FastSubstrateConfig.

    Extracts the fields relevant to the fast engine's linear dry-back ODE.
    When no substrate config is provided, returns sensible defaults.
    """
    if config is None:
        return FastSubstrateConfig()

    return FastSubstrateConfig(
        k_dry=config.k_dry,
        wilting_vwc=config.wilting_point_vwc,
        stress_onset_vwc=config.stress_onset_vwc,
        irrig_rate=config.infiltration_rate,
        sat_vwc=config.saturation_vwc,
    )
