"""Matplotlib visualization for simulation results.

Produces a multi-panel timeseries chart showing sensor readings with
actuator ON-periods as colored shaded regions. Also generates adaptive
learning comparison charts showing before/after rule improvements.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend for PNG output
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from .runner import SimulationResult, AdaptiveResult, MultiDayResult


# Actuator display config: (friendly_name, color, alpha)
ACTUATOR_STYLES: dict[str, tuple[str, str, float]] = {
    "fan": ("Fan", "#4A90D9", 0.15),
    "exhaust_fan": ("Exhaust Fan", "#D94A4A", 0.15),
    "humidifier": ("Humidifier", "#4AD9D9", 0.15),
    "dehumidifier": ("Dehumidifier", "#D9944A", 0.15),
    "irrigation": ("Irrigation", "#4AD94A", 0.15),
    "light": ("Grow Light", "#D9D94A", 0.15),
}


def _shade_actuator_periods(
    ax: plt.Axes,
    timestamps: list[float],
    states: list[bool],
    color: str,
    alpha: float,
) -> None:
    """Draw shaded regions where an actuator is ON."""
    if not timestamps or not states:
        return

    in_span = False
    span_start = 0.0

    for i, on in enumerate(states):
        if on and not in_span:
            span_start = timestamps[i]
            in_span = True
        elif not on and in_span:
            ax.axvspan(span_start, timestamps[i], color=color, alpha=alpha)
            in_span = False

    # Close final span if still ON
    if in_span:
        ax.axvspan(span_start, timestamps[-1], color=color, alpha=alpha)


def plot_simulation(
    result: SimulationResult,
    output_path: str | Path = "simulations/output/grow_tent_simulation.png",
    title: str | None = None,
) -> Path:
    """Generate a 4-panel timeseries chart and save as PNG.

    Panels:
      1. Temperature (°C) — fan, exhaust, light overlays
      2. Humidity (%) — humidifier, dehumidifier, exhaust overlays
      3. Soil Moisture (%, 4 lines) — irrigation overlay
      4. Light Intensity (lux) — light overlay

    Returns the output path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(4, 1, figsize=(16, 14), sharex=True)
    ts = result.timestamps

    if title is None:
        title = (
            f"Grow Tent Simulation — {result.duration_minutes}min, "
            f"{result.num_rules} rules, {len(result.events)} commands fired"
        )
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.98)

    # ── Panel 1: Temperature ──────────────────────────────────────────
    ax_temp = axes[0]
    ax_temp.plot(ts, result.readings["temp1"], color="#E74C3C", linewidth=1.2, label="temp1")
    ax_temp.set_ylabel("Temperature (°C)")
    ax_temp.grid(True, alpha=0.3)

    # Overlays: fan, exhaust, light
    for act_key in ("fan", "exhaust_fan", "light"):
        if act_key in result.actuators:
            label, color, alpha = ACTUATOR_STYLES[act_key]
            _shade_actuator_periods(ax_temp, ts, result.actuators[act_key], color, alpha)

    # Legend
    temp_handles = [
        plt.Line2D([0], [0], color="#E74C3C", linewidth=1.2, label="Temperature"),
    ]
    for act_key in ("fan", "exhaust_fan", "light"):
        label, color, _ = ACTUATOR_STYLES[act_key]
        temp_handles.append(mpatches.Patch(color=color, alpha=0.3, label=f"{label} ON"))
    ax_temp.legend(handles=temp_handles, loc="upper right", fontsize=8)

    # ── Panel 2: Humidity ─────────────────────────────────────────────
    ax_hum = axes[1]
    ax_hum.plot(ts, result.readings["hum1"], color="#3498DB", linewidth=1.2, label="hum1")
    ax_hum.set_ylabel("Humidity (%)")
    ax_hum.grid(True, alpha=0.3)

    for act_key in ("humidifier", "dehumidifier", "exhaust_fan"):
        if act_key in result.actuators:
            label, color, alpha = ACTUATOR_STYLES[act_key]
            _shade_actuator_periods(ax_hum, ts, result.actuators[act_key], color, alpha)

    hum_handles = [
        plt.Line2D([0], [0], color="#3498DB", linewidth=1.2, label="Humidity"),
    ]
    for act_key in ("humidifier", "dehumidifier", "exhaust_fan"):
        label, color, _ = ACTUATOR_STYLES[act_key]
        hum_handles.append(mpatches.Patch(color=color, alpha=0.3, label=f"{label} ON"))
    ax_hum.legend(handles=hum_handles, loc="upper right", fontsize=8)

    # ── Panel 3: Soil Moisture ────────────────────────────────────────
    ax_soil = axes[2]
    soil_colors = ["#27AE60", "#2ECC71", "#1ABC9C", "#16A085"]
    for i in range(4):
        key = f"soil{i + 1}"
        if key in result.readings:
            ax_soil.plot(
                ts, result.readings[key],
                color=soil_colors[i], linewidth=1.0,
                label=key, alpha=0.8,
            )
    ax_soil.set_ylabel("Soil Moisture (%)")
    ax_soil.grid(True, alpha=0.3)

    if "irrigation" in result.actuators:
        label, color, alpha = ACTUATOR_STYLES["irrigation"]
        _shade_actuator_periods(ax_soil, ts, result.actuators["irrigation"], color, alpha)

    soil_handles = [
        plt.Line2D([0], [0], color=soil_colors[i], linewidth=1.0, label=f"soil{i+1}")
        for i in range(4)
    ]
    label, color, _ = ACTUATOR_STYLES["irrigation"]
    soil_handles.append(mpatches.Patch(color=color, alpha=0.3, label=f"{label} ON"))
    ax_soil.legend(handles=soil_handles, loc="upper right", fontsize=8)

    # ── Panel 4: Light Intensity ──────────────────────────────────────
    ax_light = axes[3]
    ax_light.plot(ts, result.readings["light1"], color="#F39C12", linewidth=1.2, label="light1")
    ax_light.set_ylabel("Light (lux)")
    ax_light.set_xlabel("Elapsed Time (minutes)")
    ax_light.grid(True, alpha=0.3)

    if "light" in result.actuators:
        label, color, alpha = ACTUATOR_STYLES["light"]
        _shade_actuator_periods(ax_light, ts, result.actuators["light"], color, alpha)

    light_handles = [
        plt.Line2D([0], [0], color="#F39C12", linewidth=1.2, label="Light Intensity"),
    ]
    label, color, _ = ACTUATOR_STYLES["light"]
    light_handles.append(mpatches.Patch(color=color, alpha=0.3, label=f"{label} ON"))
    ax_light.legend(handles=light_handles, loc="upper right", fontsize=8)

    # ── Layout ────────────────────────────────────────────────────────
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_path


def plot_adaptive(
    adaptive: AdaptiveResult,
    output_path: str | Path = "simulations/output/adaptive_learning.png",
) -> Path:
    """Generate a multi-panel comparison chart for adaptive learning.

    Panels:
      1. Temperature comparison (Phase 1 vs Phase 2)
      2. Humidity comparison (Phase 1 vs Phase 2)
      3. Per-command effectiveness scatter (Phase 1 vs Phase 2)
      4. Summary bar chart (avg effectiveness, event counts, suggestions)

    Returns the output path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(18, 12))
    fig.suptitle(
        f"Adaptive Learning — Rule Advisor Improvement\n"
        f"Phase 1 avg: {adaptive.phase1_avg_effectiveness:+.3f} → "
        f"Phase 2 avg: {adaptive.phase2_avg_effectiveness:+.3f} "
        f"({adaptive.improvement:+.3f} improvement)",
        fontsize=14, fontweight="bold", y=0.98,
    )

    p1 = adaptive.phase1
    p2 = adaptive.phase2

    # ── Panel 1: Temperature Comparison ────────────────────────────────
    ax_temp = axes[0, 0]
    if "temp1" in p1.readings and "temp1" in p2.readings:
        ax_temp.plot(p1.timestamps, p1.readings["temp1"],
                     color="#E74C3C", linewidth=1.0, alpha=0.6, label="Phase 1")
        ax_temp.plot(p2.timestamps, p2.readings["temp1"],
                     color="#2ECC71", linewidth=1.2, label="Phase 2 (adjusted)")
    ax_temp.set_ylabel("Temperature (°C)")
    ax_temp.set_xlabel("Elapsed Time (minutes)")
    ax_temp.set_title("Temperature: Before vs After")
    ax_temp.legend(fontsize=9)
    ax_temp.grid(True, alpha=0.3)

    # ── Panel 2: Humidity Comparison ───────────────────────────────────
    ax_hum = axes[0, 1]
    if "hum1" in p1.readings and "hum1" in p2.readings:
        ax_hum.plot(p1.timestamps, p1.readings["hum1"],
                    color="#3498DB", linewidth=1.0, alpha=0.6, label="Phase 1")
        ax_hum.plot(p2.timestamps, p2.readings["hum1"],
                    color="#9B59B6", linewidth=1.2, label="Phase 2 (adjusted)")
    ax_hum.set_ylabel("Humidity (%)")
    ax_hum.set_xlabel("Elapsed Time (minutes)")
    ax_hum.set_title("Humidity: Before vs After")
    ax_hum.legend(fontsize=9)
    ax_hum.grid(True, alpha=0.3)

    # ── Panel 3: Effectiveness Scatter ─────────────────────────────────
    ax_eff = axes[1, 0]

    if p1.outcomes:
        p1_times = [o.time_minutes for o in p1.outcomes]
        p1_scores = [o.effectiveness for o in p1.outcomes]
        ax_eff.scatter(p1_times, p1_scores, color="#E74C3C", alpha=0.6,
                       s=40, label=f"Phase 1 (avg: {adaptive.phase1_avg_effectiveness:+.2f})",
                       zorder=3)

    if p2.outcomes:
        p2_times = [o.time_minutes for o in p2.outcomes]
        p2_scores = [o.effectiveness for o in p2.outcomes]
        ax_eff.scatter(p2_times, p2_scores, color="#2ECC71", alpha=0.6,
                       s=40, label=f"Phase 2 (avg: {adaptive.phase2_avg_effectiveness:+.2f})",
                       marker="D", zorder=3)

    ax_eff.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    ax_eff.set_ylabel("Effectiveness Score")
    ax_eff.set_xlabel("Elapsed Time (minutes)")
    ax_eff.set_title("Command Effectiveness Over Time")
    ax_eff.set_ylim(-1.1, 1.1)
    ax_eff.legend(fontsize=9)
    ax_eff.grid(True, alpha=0.3)

    # ── Panel 4: Summary Bar Chart ─────────────────────────────────────
    ax_bar = axes[1, 1]

    categories = ["Avg\nEffectiveness", "Commands\nFired", "Positive\nOutcomes"]
    p1_positive = sum(1 for o in p1.outcomes if o.effectiveness > 0.1) if p1.outcomes else 0
    p2_positive = sum(1 for o in p2.outcomes if o.effectiveness > 0.1) if p2.outcomes else 0

    p1_vals = [adaptive.phase1_avg_effectiveness, len(p1.events), p1_positive]
    p2_vals = [adaptive.phase2_avg_effectiveness, len(p2.events), p2_positive]

    x = np.arange(len(categories))
    width = 0.35

    bars1 = ax_bar.bar(x - width / 2, p1_vals, width, label="Phase 1",
                       color="#E74C3C", alpha=0.7)
    bars2 = ax_bar.bar(x + width / 2, p2_vals, width, label="Phase 2",
                       color="#2ECC71", alpha=0.7)

    ax_bar.set_xticks(x)
    ax_bar.set_xticklabels(categories)
    ax_bar.set_title(f"Summary ({len(adaptive.suggestions)} suggestions applied)")
    ax_bar.legend(fontsize=9)
    ax_bar.grid(True, alpha=0.3, axis="y")

    # Add value labels on bars
    for bar in bars1:
        h = bar.get_height()
        ax_bar.annotate(f"{h:.2f}" if abs(h) < 10 else f"{int(h)}",
                        xy=(bar.get_x() + bar.get_width() / 2, h),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8)
    for bar in bars2:
        h = bar.get_height()
        ax_bar.annotate(f"{h:.2f}" if abs(h) < 10 else f"{int(h)}",
                        xy=(bar.get_x() + bar.get_width() / 2, h),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8)

    # ── Layout ────────────────────────────────────────────────────────
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_path


def plot_multi_day(
    result: MultiDayResult,
    output_path: str | Path = "simulations/output/multi_day_adaptive.png",
) -> Path:
    """Generate a multi-panel chart for multi-day adaptive simulation.

    Panels:
      1. Full temperature timeline with checkpoint markers
      2. Full humidity timeline with checkpoint markers
      3. Effectiveness trajectory (bar per phase)
      4. Cumulative suggestions and improvement

    Returns the output path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(20, 12))

    duration_h = result.total_duration_minutes / 60
    fig.suptitle(
        f"Multi-Day Adaptive Learning — {duration_h:.0f}h, "
        f"{result.num_checkpoints} phases, "
        f"overall improvement: {result.overall_improvement:+.3f}",
        fontsize=14, fontweight="bold", y=0.98,
    )

    ts = result.timestamps
    # Convert to hours for readability
    ts_hours = [t / 60.0 for t in ts]

    # Checkpoint boundaries in hours
    checkpoint_hours = [p.end_minutes / 60.0 for p in result.phases[:-1]]

    # Phase colors (gradient from red to green)
    n_phases = len(result.phases)
    phase_colors = plt.cm.RdYlGn(np.linspace(0.15, 0.85, max(n_phases, 2)))

    # ── Panel 1: Temperature Timeline ──────────────────────────────────
    ax_temp = axes[0, 0]
    if "temp1" in result.readings:
        ax_temp.plot(ts_hours, result.readings["temp1"],
                     color="#E74C3C", linewidth=0.8, alpha=0.8)
    for ch in checkpoint_hours:
        ax_temp.axvline(x=ch, color="#7F8C8D", linestyle="--", alpha=0.6, linewidth=0.8)
    # Shade phases
    for i, phase in enumerate(result.phases):
        ax_temp.axvspan(
            phase.start_minutes / 60.0, phase.end_minutes / 60.0,
            color=phase_colors[i], alpha=0.08,
        )
    ax_temp.set_ylabel("Temperature (°C)")
    ax_temp.set_xlabel("Time (hours)")
    ax_temp.set_title("Temperature with Checkpoint Markers")
    ax_temp.grid(True, alpha=0.3)

    # ── Panel 2: Humidity Timeline ─────────────────────────────────────
    ax_hum = axes[0, 1]
    if "hum1" in result.readings:
        ax_hum.plot(ts_hours, result.readings["hum1"],
                    color="#3498DB", linewidth=0.8, alpha=0.8)
    for ch in checkpoint_hours:
        ax_hum.axvline(x=ch, color="#7F8C8D", linestyle="--", alpha=0.6, linewidth=0.8)
    for i, phase in enumerate(result.phases):
        ax_hum.axvspan(
            phase.start_minutes / 60.0, phase.end_minutes / 60.0,
            color=phase_colors[i], alpha=0.08,
        )
    ax_hum.set_ylabel("Humidity (%)")
    ax_hum.set_xlabel("Time (hours)")
    ax_hum.set_title("Humidity with Checkpoint Markers")
    ax_hum.grid(True, alpha=0.3)

    # ── Panel 3: Effectiveness Trajectory ──────────────────────────────
    ax_eff = axes[1, 0]
    phase_labels = [f"Phase {p.phase_num}" for p in result.phases]
    eff_values = result.effectiveness_trajectory
    bar_colors = [phase_colors[i] for i in range(len(eff_values))]

    bars = ax_eff.bar(range(len(eff_values)), eff_values, color=bar_colors, edgecolor="white")
    ax_eff.axhline(y=0, color="gray", linestyle="--", alpha=0.5)

    # Value labels
    for i, bar in enumerate(bars):
        h = bar.get_height()
        ax_eff.annotate(
            f"{h:+.3f}",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3 if h >= 0 else -12),
            textcoords="offset points",
            ha="center", va="bottom" if h >= 0 else "top",
            fontsize=9, fontweight="bold",
        )

    ax_eff.set_xticks(range(len(phase_labels)))
    ax_eff.set_xticklabels(phase_labels, fontsize=9)
    ax_eff.set_ylabel("Avg Effectiveness")
    ax_eff.set_title("Effectiveness Trajectory Across Phases")
    ax_eff.set_ylim(min(min(eff_values) - 0.15, -0.3), max(max(eff_values) + 0.15, 0.3))
    ax_eff.grid(True, alpha=0.3, axis="y")

    # ── Panel 4: Suggestions & Improvement Summary ─────────────────────
    ax_summary = axes[1, 1]

    # Stacked bar: suggestions generated vs applied per phase
    x = np.arange(n_phases)
    generated = [p.num_suggestions_generated for p in result.phases]
    applied = [p.num_suggestions_applied for p in result.phases]

    ax_summary.bar(x, generated, width=0.5, color="#3498DB", alpha=0.7, label="Generated")
    ax_summary.bar(x, applied, width=0.5, color="#2ECC71", alpha=0.9, label="Applied")

    # Overlay effectiveness line
    ax2 = ax_summary.twinx()
    ax2.plot(x, eff_values, color="#E74C3C", marker="o", linewidth=2, label="Effectiveness")
    ax2.set_ylabel("Avg Effectiveness", color="#E74C3C")
    ax2.tick_params(axis="y", labelcolor="#E74C3C")
    ax2.axhline(y=0, color="#E74C3C", linestyle=":", alpha=0.3)

    ax_summary.set_xticks(x)
    ax_summary.set_xticklabels(phase_labels, fontsize=9)
    ax_summary.set_ylabel("Suggestion Count")
    ax_summary.set_title(
        f"Suggestions Over Time — "
        f"{result.total_suggestions} total, {result.total_applied} applied"
    )
    ax_summary.legend(loc="upper left", fontsize=9)
    ax2.legend(loc="upper right", fontsize=9)
    ax_summary.grid(True, alpha=0.3, axis="y")

    # ── Layout ─────────────────────────────────────────────────────────
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_path
