"""Environment simulation entry point.

Usage:
    cd apps/cortex && python -m simulations.simulate -v
    cd apps/cortex && python -m simulations.simulate --room simulations/rooms/commercial_10x10.yaml -v
    cd apps/cortex && python -m simulations.simulate --duration 360 --start-hour 6
    cd apps/cortex && python -m simulations.simulate --multi-day -v
    cd apps/cortex && python -m simulations.simulate --multi-day --fast-physics -v
    cd apps/cortex && python -m simulations.simulate --fast-physics -v
    cd apps/cortex && python -m simulations.simulate --horizon 30 --w-energy 0.1 -v
"""

import argparse
import logging

from .charts import plot_mpc_simulation, plot_multi_day
from .physics import PhysicsEngine
from .room_config import load_room_config, default_room_config, RoomConfig
from .runner import run_multi_day, run_mpc
from .scenarios.default import (
    PROFILE, GOALS, MPC_CONFIG, VARIABLE_OVERRIDES,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run environment simulation")
    # ── Room Configuration ────────────────────────────────────────────
    parser.add_argument(
        "--room", type=str, default=None,
        help="Path to room config YAML (default: built-in tent)",
    )
    parser.add_argument(
        "--duration", type=int, default=180,
        help="Simulation duration in minutes (default: 180)",
    )
    parser.add_argument(
        "--step", type=int, default=30,
        help="Timestep in seconds (default: 30)",
    )
    parser.add_argument(
        "--start-hour", type=int, default=8,
        help="Simulation start hour (0-23, default: 8)",
    )
    parser.add_argument(
        "--output", type=str, default="simulations/output/simulation.png",
        help="Output PNG path",
    )
    parser.add_argument(
        "--no-noise", action="store_true",
        help="Disable sensor noise for cleaner charts",
    )
    parser.add_argument(
        "--ambient-temp", type=float, default=30.0,
        help="Outside ambient temperature in °C (default: 30.0)",
    )
    parser.add_argument(
        "--multi-day", action="store_true",
        help="Run multi-day simulation with periodic checkpoints (96h, 12h checkpoints)",
    )
    parser.add_argument(
        "--strategy", type=str, default="balanced",
        choices=["precision", "balanced", "efficiency"],
        help="Strategy for goal-aware filtering (default: balanced)",
    )
    # ── Actuator Control ─────────────────────────────────────────────
    parser.add_argument(
        "--variable", action="store_true",
        help="Upgrade select actuators to variable (0-10V) control (EC fans, dimmable LEDs)",
    )
    # ── MPC ──────────────────────────────────────────────────────────
    parser.add_argument(
        "--fast-physics", action="store_true",
        help="Use FastPhysicsEngine for MPC (10-15× faster solves)",
    )
    parser.add_argument(
        "--horizon", type=float, default=None,
        help="MPC prediction horizon in minutes (default: 15)",
    )
    parser.add_argument(
        "--w-energy", type=float, default=None,
        help="MPC energy cost weight (default: 0.05)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    # ── Load room configuration ────────────────────────────────────────
    room = load_room_config(args.room) if args.room else default_room_config()
    print(f"Room: {room.name}")

    # Apply actuator control type overrides to the room's specs
    if args.variable:
        for relay_id, overrides in VARIABLE_OVERRIDES.items():
            if relay_id in room.actuator_specs:
                for fld, value in overrides.items():
                    setattr(room.actuator_specs[relay_id], fld, value)
        print(f"Variable control: {', '.join(f'{room.actuator_specs[r].name}' for r in VARIABLE_OVERRIDES if r in room.actuator_specs)}")

    if args.multi_day:
        _run_multi_day(args, room)
    else:
        _run_mpc(args, room)


def _build_mpc_config(args: argparse.Namespace, volume_m3: float = 2.88) -> dict:
    """Build MPC config dict from CLI args, with auto-scaling for room volume.

    Auto-scaling adjusts horizon and cost weights for larger rooms.
    Explicit CLI overrides (--horizon, --w-energy) take precedence.
    """
    from .state_planner import MPCConfig

    # Start from defaults and auto-scale for volume
    base = MPCConfig(**MPC_CONFIG)
    scaled = MPCConfig.auto_scale(base, volume_m3)

    cfg = {
        "horizon_minutes": scaled.horizon_minutes,
        "control_interval_s": scaled.control_interval_s,
        "w_goal": scaled.w_goal,
        "w_energy": scaled.w_energy,
        "w_rate": scaled.w_rate,
        "method": scaled.method,
        "light_schedule": scaled.light_schedule,
    }

    # CLI overrides take precedence over auto-scaling
    if args.horizon is not None:
        cfg["horizon_minutes"] = args.horizon
    if args.w_energy is not None:
        cfg["w_energy"] = args.w_energy
    return cfg


# ── MPC ──────────────────────────────────────────────────────────────


def _run_mpc(args: argparse.Namespace, room: RoomConfig) -> None:
    """Run an MPC-controlled simulation."""
    profile = dict(PROFILE)
    profile["strategy"] = args.strategy
    goals = list(GOALS)
    mpc_cfg = _build_mpc_config(args, room.space.volume_m3)

    use_fast = getattr(args, "fast_physics", False)
    engine_label = "fast physics" if use_fast else "full physics"
    print(f"Running MPC simulation: {args.duration}min, "
          f"horizon={mpc_cfg.get('horizon_minutes', 15):.0f}min, "
          f"strategy={args.strategy}, engine={engine_label}, "
          f"start={args.start_hour}:00")

    result = run_mpc(
        goals=goals,
        profile=profile,
        mpc_config=mpc_cfg,
        duration_minutes=args.duration,
        step_seconds=args.step,
        start_hour=args.start_hour,
        actuator_specs=room.actuator_specs,
        ventilation_fn=room.ventilation_fn,
        substrate_fn=room.substrate_fn,
        substrate_config=room.substrate_config,
        substrate_container=room.substrate_container,
        space=room.space,
        plant=room.plant,
        ambient_temp=room.ambient_temp,
        ambient_schedule=room.ambient_schedule_fn,
        initial_conditions=room.initial_conditions,
        fast_physics=use_fast,
    )

    _print_mpc_report(result)

    output_dir = "simulations/output"
    chart_path = plot_mpc_simulation(result, f"{output_dir}/mpc_simulation.png", goals=goals)
    print(f"\nChart saved to: {chart_path}")


def _print_mpc_report(result) -> None:
    """Print MPC simulation report."""
    w = 62
    print()
    print("┌" + "─" * w + "┐")
    print(f"│{'MPC STATE PLANNER REPORT':^{w}}│")
    print("├" + "─" * w + "┤")

    avg_solve = sum(result.solve_times_ms) / len(result.solve_times_ms)
    max_solve = max(result.solve_times_ms)
    print(f"│  Avg solve time: {avg_solve:.1f}ms  (max: {max_solve:.1f}ms){' ' * (w - 51)}│")
    print(f"│  Avg health: {result.avg_health:.1%}{' ' * (w - 22)}│")
    print(f"│  Total energy: {result.total_energy_wh:.0f} Wh{' ' * (w - 25)}│")

    if result.compliance:
        print("├" + "─" * w + "┤")
        print(f"│  {'Metric':<12} {'Compliance':>12}  {'':>{w - 30}}│")
        for metric, pct in sorted(result.compliance.items()):
            bar = "█" * int(pct * 20)
            print(f"│  {metric:<12} {pct:>11.1%}  {bar:<{w - 30}}│")

    if result.energy_breakdown:
        print("├" + "─" * w + "┤")
        print(f"│  {'Actuator':<16} {'Energy (Wh)':>12}  {'':>{w - 34}}│")
        for name, wh in sorted(result.energy_breakdown.items(), key=lambda x: -x[1]):
            if wh > 0:
                print(f"│  {name:<16} {wh:>11.1f}  {'':>{w - 34}}│")

    print("└" + "─" * w + "┘")


def _run_multi_day(args: argparse.Namespace, room: RoomConfig) -> None:
    """Run the multi-day MPC simulation with periodic compliance checkpoints."""
    profile = dict(PROFILE)
    profile["strategy"] = args.strategy
    goals = list(GOALS)
    mpc_cfg = _build_mpc_config(args, room.space.volume_m3)

    total_minutes = args.duration if args.duration != 180 else 5760  # 96h default
    checkpoint_interval = 720  # 12h

    total_hours = total_minutes / 60
    num_phases = total_minutes // checkpoint_interval

    use_fast = getattr(args, "fast_physics", False)
    engine_label = "fast" if use_fast else "full"

    print(f"Running multi-day MPC simulation: "
          f"{total_hours:.0f}h, {num_phases} phases of "
          f"{checkpoint_interval // 60}h each, "
          f"strategy={args.strategy}, engine={engine_label}, "
          f"horizon={mpc_cfg.get('horizon_minutes', 15)}min")
    print()

    result = run_multi_day(
        goals=goals,
        profile=profile,
        mpc_config=mpc_cfg,
        total_duration_minutes=total_minutes,
        checkpoint_interval_minutes=checkpoint_interval,
        step_seconds=args.step,
        start_hour=args.start_hour,
        actuator_specs=room.actuator_specs,
        ventilation_fn=room.ventilation_fn,
        substrate_fn=room.substrate_fn,
        substrate_config=room.substrate_config,
        substrate_container=room.substrate_container,
        space=room.space,
        plant=room.plant,
        ambient_temp=room.ambient_temp,
        ambient_schedule=room.ambient_schedule_fn,
        initial_conditions=room.initial_conditions,
        fast_physics=use_fast,
    )

    # ── Print Report ─────────────────────────────────────────────────
    print()
    w = 76
    print("┌" + "─" * w + "┐")
    print(f"│{'MULTI-DAY MPC REPORT':^{w}}│")
    sub = f"{total_hours:.0f}h · {result.num_checkpoints} phases · {checkpoint_interval // 60}h intervals · energy={result.total_energy_wh:.0f}Wh"
    print(f"│{sub:^{w}}│")
    print("├──────┬──────────┬────────────┬────────────┬──────────────┬───────────┤")
    print(f"│{'Phase':^6}│{'Duration':^10}│{'Compliance':^12}│{'Energy(Wh)':^12}│{'Solve(ms)':^14}│{'Avg Cost':^11}│")
    print("├──────┼──────────┼────────────┼────────────┼──────────────┼───────────┤")

    for phase in result.phases:
        start_h = phase.start_minutes / 60
        end_h = phase.end_minutes / 60

        print(
            f"│{phase.phase_num:^6}│"
            f"{f'{start_h:.0f}-{end_h:.0f}h':^10}│"
            f"{f'{phase.avg_compliance:.1%}':^12}│"
            f"{phase.energy_wh:^12.0f}│"
            f"{phase.avg_solve_time_ms:^14.1f}│"
            f"{phase.avg_cost:^11.4f}│"
        )

    print("├──────┴──────────┴────────────┴────────────┴──────────────┴───────────┤")

    # Overall compliance per metric
    if result.compliance:
        comp_parts = [f"{m}: {v:.1%}" for m, v in sorted(result.compliance.items())]
        comp_line = "Compliance: " + ", ".join(comp_parts)
        print(f"│{comp_line:^{w}}│")

    first_comp = result.effectiveness_trajectory[0] if result.effectiveness_trajectory else 0
    last_comp = result.effectiveness_trajectory[-1] if result.effectiveness_trajectory else 0
    summary = (
        f"Avg compliance: {first_comp:.1%} → {last_comp:.1%} "
        f"(delta: {result.overall_improvement:+.4f})"
    )
    print(f"│{summary:^{w}}│")
    print("└" + "─" * w + "┘")

    # ── Generate Chart ───────────────────────────────────────────────
    output_dir = "simulations/output"
    chart_path = plot_multi_day(
        result, f"{output_dir}/multi_day_mpc.png",
        goals=goals,
        actuator_specs=room.actuator_specs,
        space=room.space,
        room_name=room.name,
    )
    print(f"\nChart saved to: {chart_path}")


if __name__ == "__main__":
    main()
