# Fast Physics Engine for MPC

## Context

MPC solve times are ~1 second for a simple tent (2.88 m³, 15-min horizon, 6 intervals). The bottleneck is the physics engine: SLSQP runs ~3,000 physics evaluations per solve (30 steps × ~100 iterations with finite-difference gradients over 42 decision variables). Each step costs ~150µs due to Ball-Berry stomatal conductance, 3-surface photosynthesis, per-pot substrate physics with Q10 evaporation, and repeated Tetens `exp()` calls for psychrometric conversions.

The [hardware requirements doc](cortex-mpc-hardware-requirements.md) defines a much simpler model: 4 coupled ODEs (temperature, absolute humidity, CO2, substrate moisture), with transpiration as a simple disturbance function — not a biophysics simulation. MPC re-calibrates from real sensors every 30 seconds, so model speed matters more than model fidelity.

**Goal**: Reduce per-step cost from ~150µs to ~15µs → solve time from ~1s to ~100ms.

---

## What Changes

### New: `simulations/fast_physics.py` — FastPhysicsEngine

Same interface as `PhysicsEngine` (`step()`, `save_state()`, `restore_state()`, `calibrate()`, `set_actuator()`, `get_readings()`), but:

**Dropped** (expensive, unnecessary for MPC):
- Ball-Berry stomatal conductance (`g_s = g_0 + m_bb × P × h_s / c_s`)
- 3-surface photosynthesis (rectangular hyperbola × Michaelis-Menten × Gaussian × VPD stress)
- Per-pot substrate (4× Q10 evaporation, exponential dry-back, Penman-Monteith humidity, drainage)
- Leaf temperature as computed ODE
- Repeated Tetens `exp()` calls in the hot loop (4-8 per step)

**Replaced with**:
- **Transpiration**: `k_transp × VPD × LAI × light_frac × vwc_available` — one multiply chain
- **CO2 uptake**: `k_co2_uptake × light_frac` — proportional to light only
- **Substrate**: Single average VWC, linear dry-back: `dVWC = -k_dry × (VWC - wilting) + irrig`
- **SVP lookup table**: Pre-computed Tetens at integer temps 10–50°C, linear interpolation (zero `exp()`)
- **Internal absolute humidity** (g/m³): eliminates RH↔AH conversions, converts only in `get_readings()`

**State snapshot** (save/restore for MPC): flat tuple of 13 floats — zero list allocation, zero deepcopy overhead.

### ODEs (4 coupled equations)

```
dT/dt   = k_drift·(T_amb - T) + ACH/60·(T_amb - T) + k_light·light
          - k_fan·fan - k_transp·E_transp - k_humid·humid + k_dehum·dehum - k_hvac·hvac

dw/dt   = ACH/60·(w_amb - w) + E_transp/V + k_humid_add·humid
          - k_dehum_rm·dehum - k_hvac_dehum·hvac + k_soil·(VWC - wilting)·VPD

dCO2/dt = k_inject·co2_inj - k_uptake·light_frac + ACH/60·(CO2_amb - CO2) + respiration

dVWC/dt = -k_dry·(VWC - wilting) - root_uptake + k_irrig·irrigation
```

Where `E_transp = k_transp × VPD × LAI × light_frac × vwc_avail` (no Ball-Berry).

### Performance Budget

| Component | Current | Fast | Speedup |
|-----------|---------|------|---------|
| SVP (Tetens exp) ×4 | 20µs | 2µs (lookup) | 10× |
| Photosynthesis (exp+hyperbola) | 30µs | 2µs (linear) | 15× |
| Transpiration (Ball-Berry) | 15µs | 2µs (multiply) | 7× |
| Substrate ×4 pots (Q10 pow) | 60µs | 3µs (single linear) | 20× |
| RH↔AH conversions (exp ×3) | 15µs | 0µs (internal AH) | ∞ |
| **Total per step** | **~150µs** | **~10-15µs** | **10-15×** |
| **3000 steps/solve** | **~450ms** | **~30-45ms** | **10-15×** |

---

## Implementation Steps

### 1. Create `simulations/fast_physics.py`
- `FastPhysicsEngine` dataclass with same public interface as `PhysicsEngine`
- SVP lookup table (41 entries, 10-50°C) built in `__post_init__`
- `_svp_fast()` — linear interpolation, no `exp()`
- `_w_to_rh()`, `_rh_to_w()` — psychrometric conversions using lookup
- Pre-computed thermal/humidity/CO2 coefficients from room config (same first-principles as current engine)
- `step()` — 4 ODEs, ~20 multiplies, ~10 adds, 1 SVP lookup
- `save_state()` / `restore_state()` — flat 13-float tuple
- `get_readings()` — converts internal AH→RH for output, computes VPD and leaf temp offset
- `calibrate()`, `set_actuator()`, `get_actuator_states()`, `get_power_watts()`
- Transpiration coefficient `k_transp` calibrated to match Ball-Berry at reference conditions (~0.72)

### 2. Create `tests/test_fast_physics.py`
- SVP lookup accuracy vs Tetens (max error <0.5% over 15-45°C)
- Psychrometric roundtrip (w→RH→w identity)
- Direction tests: fan cools, humidifier raises humidity, CO2 injector raises CO2, irrigation raises VWC
- save_state/restore_state roundtrip
- Benchmark: 3000 `step()` calls < 50ms
- Regression vs `PhysicsEngine`: same initial conditions, compare after 30 min (within ~10-15%)

### 3. Add `build_fast_engine()` to `room_config.py`
- `RoomConfig.build_fast_engine()` constructs `FastPhysicsEngine` from room parameters
- Maps substrate presets to simplified `k_dry`/`wilting_vwc`/`stress_onset_vwc`/`irrig_rate`/`sat_vwc`
- Passes ventilation_fn closure through (already fast)
- Passes actuator specs through for quantization

### 4. Wire into MPC planner and runner
- `state_planner.py`: no changes needed (duck-typed interface)
- `runner.py` `run_mpc()`: accept `fast_physics=True` flag, use `build_fast_engine()` instead of `build_engine()`
- `simulate.py`: add `--fast-physics` CLI flag

### 5. Tests for MPC integration
- Run MPC solve with `FastPhysicsEngine`, assert solve time < 200ms (cold) / < 50ms (warm)
- Compare MPC trajectory: fast vs full physics, verify similar control decisions

---

## Files Modified/Created

| File | Action |
|------|--------|
| `simulations/fast_physics.py` | **New** — FastPhysicsEngine |
| `tests/test_fast_physics.py` | **New** — unit tests + benchmark |
| `simulations/room_config.py` | **Modified** — add `build_fast_engine()` |
| `simulations/runner.py` | **Modified** — `run_mpc(fast_physics=True)` option |
| `simulations/simulate.py` | **Modified** — `--fast-physics` CLI flag |

Existing `PhysicsEngine` stays untouched — used for detailed simulations and visualization.

---

## Verification

1. `pytest tests/test_fast_physics.py -v` — all direction/accuracy/benchmark tests pass
2. `pytest tests/test_state_planner.py -v` — existing MPC tests still pass
3. `python -m simulations.simulate --mpc --fast-physics -v` — MPC solve times < 200ms, reasonable compliance
4. Compare output: `--mpc` vs `--mpc --fast-physics` — similar health scores, similar actuator decisions