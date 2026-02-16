"""Grow tent simulation entry point.

Usage:
    cd apps/cortex && python -m simulations.grow_tent
    cd apps/cortex && python -m simulations.grow_tent --duration 360 --start-hour 6
    cd apps/cortex && python -m simulations.grow_tent --adaptive
    cd apps/cortex && python -m simulations.grow_tent --multi-day --suboptimal -v
"""

import argparse
import logging
import sys

from .charts import plot_simulation, plot_adaptive, plot_multi_day
from .environment import GrowTentEnvironment
from .runner import SimulationRunner, run_adaptive, run_multi_day
from .scenarios.grow_tent_rules import GROW_TENT_RULES
from .scenarios.suboptimal_rules import SUBOPTIMAL_RULES


def main() -> None:
    parser = argparse.ArgumentParser(description="Run grow tent simulation")
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
        "--output", type=str, default="simulations/output/grow_tent_simulation.png",
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
        "--adaptive", action="store_true",
        help="Run adaptive learning simulation (Phase 1 → analyze → Phase 2)",
    )
    parser.add_argument(
        "--multi-day", action="store_true",
        help="Run multi-day simulation with periodic Rule Advisor (72h, 12h checkpoints)",
    )
    parser.add_argument(
        "--suboptimal", action="store_true",
        help="Use deliberately suboptimal rules (for adaptive learning demo)",
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

    if args.multi_day:
        _run_multi_day(args)
    elif args.adaptive:
        _run_adaptive(args)
    else:
        _run_standard(args)


def _run_standard(args: argparse.Namespace) -> None:
    """Run a standard (non-adaptive) simulation."""
    env = GrowTentEnvironment(
        noise=not args.no_noise,
        ambient_temp=args.ambient_temp,
    )
    runner = SimulationRunner(
        environment=env,
        rules=GROW_TENT_RULES,
        duration_minutes=args.duration,
        step_seconds=args.step,
        start_hour=args.start_hour,
    )

    print(f"Running grow tent simulation: {args.duration}min, "
          f"{len(GROW_TENT_RULES)} rules, step={args.step}s, "
          f"start={args.start_hour}:00")

    result = runner.run()

    print(f"Simulation complete: {len(result.events)} commands fired")
    for evt in result.events:
        print(f"  [{evt.time_minutes:6.1f}m] {evt.rule_name}: "
              f"{evt.target}={'ON' if evt.value else 'OFF'}")

    output = plot_simulation(result, args.output)
    print(f"Chart saved to: {output}")


def _run_adaptive(args: argparse.Namespace) -> None:
    """Run the adaptive learning simulation with before/after comparison."""
    rules = SUBOPTIMAL_RULES if args.suboptimal else GROW_TENT_RULES
    label = "suboptimal" if args.suboptimal else "standard"

    print(f"Running adaptive learning simulation ({label} rules): "
          f"{args.duration}min, {len(rules)} rules, ambient={args.ambient_temp}°C")
    print()

    adaptive = run_adaptive(
        rules=rules,
        duration_minutes=args.duration,
        step_seconds=args.step,
        start_hour=args.start_hour,
        ambient_temp=args.ambient_temp,
    )

    print()
    print("=" * 60)
    print("ADAPTIVE LEARNING RESULTS")
    print("=" * 60)
    print(f"  Phase 1 outcomes: {len(adaptive.phase1.outcomes)}")
    print(f"  Phase 1 avg effectiveness: {adaptive.phase1_avg_effectiveness:+.3f}")
    print(f"  Suggestions generated: {len(adaptive.suggestions)}")
    for s in adaptive.suggestions:
        print(f"    {s['rule_name']}.{s['field']}: → {s['suggested_value']} "
              f"(confidence: {s['confidence']:.0%})")
        print(f"      Reason: {s['reason']}")
    print(f"  Phase 2 outcomes: {len(adaptive.phase2.outcomes)}")
    print(f"  Phase 2 avg effectiveness: {adaptive.phase2_avg_effectiveness:+.3f}")
    print(f"  Improvement: {adaptive.improvement:+.3f}")
    print("=" * 60)

    # Generate both charts
    output_dir = "simulations/output"
    p1_path = plot_simulation(
        adaptive.phase1,
        f"{output_dir}/adaptive_phase1.png",
        title="Phase 1 — Original Rules",
    )
    p2_path = plot_simulation(
        adaptive.phase2,
        f"{output_dir}/adaptive_phase2.png",
        title="Phase 2 — Adjusted Rules",
    )
    cmp_path = plot_adaptive(adaptive, f"{output_dir}/adaptive_learning.png")

    print(f"\nCharts saved:")
    print(f"  Phase 1: {p1_path}")
    print(f"  Phase 2: {p2_path}")
    print(f"  Comparison: {cmp_path}")


def _run_multi_day(args: argparse.Namespace) -> None:
    """Run the multi-day adaptive simulation with periodic Rule Advisor checkpoints."""
    rules = SUBOPTIMAL_RULES if args.suboptimal else GROW_TENT_RULES
    label = "suboptimal" if args.suboptimal else "standard"

    total_minutes = args.duration if args.duration != 180 else 4320  # 72h default
    checkpoint_interval = 720  # 12h

    total_hours = total_minutes / 60
    num_phases = total_minutes // checkpoint_interval

    print(f"Running multi-day simulation ({label} rules): "
          f"{total_hours:.0f}h, {num_phases} phases of "
          f"{checkpoint_interval // 60}h each")
    print()

    result = run_multi_day(
        rules=rules,
        total_duration_minutes=total_minutes,
        checkpoint_interval_minutes=checkpoint_interval,
        step_seconds=args.step,
        start_hour=args.start_hour,
        use_ambient_schedule=True,
    )

    # ── Print Report ─────────────────────────────────────────────────
    print()
    w = 66
    print("┌" + "─" * w + "┐")
    print(f"│{'MULTI-DAY ADAPTIVE LEARNING REPORT':^{w}}│")
    print(f"│{f'{total_hours:.0f}h · {result.num_checkpoints} phases · {checkpoint_interval // 60}h intervals':^{w}}│")
    print("├──────┬──────────┬───────────┬────────────┬" + "─" * 19 + "┤")
    print(f"│{'Phase':^6}│{'Duration':^10}│{'Commands':^11}│{'Avg Effect':^12}│{'Suggestions':^19}│")
    print("├──────┼──────────┼───────────┼────────────┼" + "─" * 19 + "┤")

    for phase in result.phases:
        start_h = phase.start_minutes / 60
        end_h = phase.end_minutes / 60
        n_cmds = len(phase.events)
        eff = phase.avg_effectiveness
        n_gen = phase.num_suggestions_generated
        n_app = phase.num_suggestions_applied

        sugg_str = f"{n_gen} gen, {n_app} applied" if n_gen > 0 else "0 generated"

        print(
            f"│{phase.phase_num:^6}│"
            f"{f'{start_h:.0f}-{end_h:.0f}h':^10}│"
            f"{n_cmds:^11}│"
            f"{eff:^+12.3f}│"
            f"{sugg_str:^19}│"
        )

    print("├──────┴──────────┴───────────┴────────────┴" + "─" * 19 + "┤")

    first_eff = result.effectiveness_trajectory[0] if result.effectiveness_trajectory else 0
    last_eff = result.effectiveness_trajectory[-1] if result.effectiveness_trajectory else 0
    summary = (
        f"Overall: {first_eff:+.3f} → {last_eff:+.3f} "
        f"({result.overall_improvement:+.3f})"
    )
    print(f"│{summary:^{w}}│")
    totals = f"Total: {result.total_suggestions} suggestions, {result.total_applied} applied"
    print(f"│{totals:^{w}}│")
    print("└" + "─" * w + "┘")

    # ── Generate Chart ───────────────────────────────────────────────
    output_dir = "simulations/output"
    chart_path = plot_multi_day(result, f"{output_dir}/multi_day_adaptive.png")
    print(f"\nChart saved to: {chart_path}")


if __name__ == "__main__":
    main()
