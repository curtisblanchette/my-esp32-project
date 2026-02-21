# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. Directory-specific details live in child `CLAUDE.md` files (loaded automatically by Claude Code for agents working in those directories).

## Project Overview

IoT telemetry dashboard for ESP32 sensor monitoring with relay control.

```
ESP32 (MicroPython) → MQTT → Cortex (Python/FastAPI) → Redis (HOT) + SQLite (COLD)
                         ↓                           → WebSocket → React Dashboard
                   MPC Control Plane (OSQP/SLSQP) + EKF State Estimation + Ollama LLM
                         ↓
                   MQTT Commands → ESP32
```

## Monorepo Structure

- `apps/cortex/` — Python/FastAPI unified backend (REST API, WebSocket, MQTT, MPC control, voice, LLM)
- `apps/cortex/src/mpc/` — OSQP-based MPC: 7-state thermodynamic model, QP solver, EKF estimator, compliance tracker
- `apps/cortex/src/control/` — Control loop infrastructure: sensor fusion, actuator interface, fail-safe manager
- `apps/cortex/simulations/` — Environment simulation framework (physics engine, runner, charts, adaptive learning)
- `apps/web/` — React/Vite dashboard with Chart.js visualizations
- `device/` — MicroPython code for ESP32 sensors
- `tools/` — Device management shell scripts

## Service Management

```bash
./tools/start.sh              # Start all services (Ollama, Cortex, Docker stack)
./tools/stop.sh               # Stop all services
docker compose up -d          # Start containerized services (Web, Redis, Mosquitto)
./tools/flash.sh <device-id>  # Upload MicroPython code to ESP32
./tools/flash.sh --list       # List registered devices
./tools/repl.sh               # Serial console monitor
./tools/reset.sh              # Soft reset device
```

## MQTT Topics

```
home/{location}/{deviceId}/telemetry  — Sensor readings (Device → Server)
home/{location}/{deviceId}/command    — Commands to device (Server → Device)
home/{location}/{deviceId}/ack        — Command acknowledgments (Device → Server)
home/_registry/{deviceId}/birth       — Device registration (Device → Server)
home/_registry/{deviceId}/will        — Device offline (LWT, Broker → Server)
```

## Chat & Voice `<detail>` Tag Protocol

Chat responses for `analyze` and `history` intents use `<detail>` tags to separate display-only content from TTS-spoken content:

```
intent.reply              ← spoken by TTS
<detail>
📊 detailed data...       ← display only (stripped by TTS)
</detail>
intent.summary            ← spoken by TTS
```

- Backend: LLM produces `summary` field for `history`/`analyze` intents
- Frontend: `stripDetail()` removes `<detail>` blocks before TTS; `formatMessage()` strips tag markers for display

## Claude Skills

Project-specific skills in `.claude/skills/`:

| Skill | Description |
|-------|-------------|
| `/commit` | Create git commits with conventional commit messages |
| `/pr` | Create pull requests with formatted title and description |
| `/release` | Create git tags and GitHub releases with auto-generated notes |
| `/docs` | Update README.md and CLAUDE.md to reflect code changes |

## Preferences

- You ALWAYS work on plans in /docs, not the global ~/.claude/plans directory. Your plan files must have descriptive names.
- Prefer `docker compose ...` over `docker-compose ...`
- Use the latest installation instructions for libraries and packages. Ensure compatibility with system dependencies. Always prefer latest versions.
- All code additions must include tests. Unit tests go in `apps/cortex/tests/` using pytest. Run `pytest tests/ -m "not e2e"` to verify before committing.
- Tests must cover both happy paths and edge cases (empty inputs, boundary values, error conditions).
- When modifying existing code, run the full unit test suite first to establish a baseline, then again after changes to catch regressions.

## Autonomous Plan Execution

When working through a multi-phase plan (e.g., `docs/mycelium-cortex-plan.md`), you may continue executing subsequent phases without waiting for human approval **provided all of the following are true**:
1. All unit tests pass (`pytest tests/ -m "not e2e"`) with zero regressions
2. Documentation (README.md, CLAUDE.md) has been audited and updated via `/docs`
3. You are not deviating from the approved plan — no architectural changes, no new dependencies, no scope creep

If any of these conditions fail, stop and ask before proceeding.

## Post-Implementation Workflow

After completing a plan or significant implementation work, run `/docs` to update README.md and CLAUDE.md. This ensures documentation stays in sync with code changes.
