"""Matplotlib visualization for MPC simulation results.

Produces multi-panel timeseries charts showing sensor readings with
actuator intensity heatmaps, cost decomposition, and compliance tracking.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend for PNG output
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from .runner import MultiDayResult, MPCSimulationResult


# Actuator display config: (friendly_name, color, alpha)
ACTUATOR_STYLES: dict[str, tuple[str, str, float]] = {
    "fan": ("Fan", "#4A90D9", 0.15),
    "exhaust_fan": ("Exhaust Fan", "#D94A4A", 0.15),
    "humidifier": ("Humidifier", "#4AD9D9", 0.15),
    "dehumidifier": ("Dehumidifier", "#D9944A", 0.15),
    "irrigation": ("Irrigation", "#4AD94A", 0.15),
    "light": ("Grow Light", "#D9D94A", 0.15),
    "co2_injector": ("CO\u2082 Injector", "#8B4513", 0.15),
    "hvac": ("HVAC", "#5B9BD5", 0.15),
}


def _goal_range(goals: list[dict], metric: str) -> tuple[float, float] | None:
    """Extract the outer envelope of target ranges for a metric across all time windows.

    Returns (min_of_rangeMins, max_of_rangeMaxs) or None if no matching goals.
    """
    lo_vals = []
    hi_vals = []
    for g in goals:
        if g.get("metric") == metric:
            if g.get("rangeMin") is not None:
                lo_vals.append(g["rangeMin"])
            if g.get("rangeMax") is not None:
                hi_vals.append(g["rangeMax"])
    if lo_vals and hi_vals:
        return (min(lo_vals), max(hi_vals))
    return None


def _add_target_band(
    ax: plt.Axes,
    lo: float,
    hi: float,
    color: str,
    *,
    fill_alpha: float = 0.05,
    line_alpha: float = 0.3,
    label: str | None = None,
) -> None:
    """Add target range shading with boundary lines to an axis."""
    ax.axhspan(lo, hi, color=color, alpha=fill_alpha, label=label)
    ax.axhline(y=lo, color=color, linestyle=":", alpha=line_alpha, linewidth=0.8)
    ax.axhline(y=hi, color=color, linestyle=":", alpha=line_alpha, linewidth=0.8)


def _add_checkpoints_and_phases(
    ax: plt.Axes,
    checkpoint_hours: list[float],
    phases: list,
    phase_colors: list,
) -> None:
    """Add checkpoint markers and phase shading to an axis."""
    for ch in checkpoint_hours:
        ax.axvline(x=ch, color="#7F8C8D", linestyle="--", alpha=0.6, linewidth=0.8)
    for i, phase in enumerate(phases):
        ax.axvspan(
            phase.start_minutes / 60.0, phase.end_minutes / 60.0,
            color=phase_colors[i], alpha=0.08,
        )


def _build_phase_legend(
    sensor_label: str,
    sensor_color: str,
    phases: list,
    phase_colors: list,
    extra_handles: list | None = None,
) -> list:
    """Build a legend with sensor line, checkpoint markers, and phase bands."""
    handles = [
        plt.Line2D([0], [0], color=sensor_color, linewidth=0.8, label=sensor_label),
        plt.Line2D([0], [0], color="#7F8C8D", linestyle="--", alpha=0.6, label="Checkpoint"),
    ]
    if extra_handles:
        handles.extend(extra_handles)
    for i, phase in enumerate(phases):
        handles.append(
            mpatches.Patch(color=phase_colors[i], alpha=0.25, label=f"Phase {phase.phase_num}")
        )
    return handles


def plot_multi_day(
    result: MultiDayResult,
    output_path: str | Path = "simulations/output/multi_day_mpc.png",
    goals: list[dict] | None = None,
    actuator_specs: dict | None = None,
    space: "SpaceConfig | None" = None,
    room_name: str | None = None,
) -> Path:
    """Generate a multi-panel chart for multi-day MPC simulation.

    Panels (dynamic — CO2/VPD added when data present):
      Row 0: Temperature timeline | Humidity timeline
      Row 1: CO₂ timeline (opt)  | VPD timeline (opt)
      Row N: Actuator intensity heatmap (full width)
      Last:  Compliance bars      | Equipment config

    Returns the output path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    has_co2 = "co2_1" in result.readings and len(result.readings["co2_1"]) > 0
    has_vpd = "vpd1" in result.readings and len(result.readings["vpd1"]) > 0
    has_extra_row = has_co2 or has_vpd
    # sensor rows + heatmap row + bottom row
    n_rows = (2 if has_extra_row else 1) + 1 + 1

    fig = plt.figure(figsize=(20, 5 * n_rows))
    gs = fig.add_gridspec(n_rows, 2, hspace=0.35, wspace=0.2)

    duration_h = result.total_duration_minutes / 60
    title_parts = [f"Multi-Day MPC — {duration_h:.0f}h"]
    if room_name:
        title_parts[0] = f"{room_name} MPC — {duration_h:.0f}h"
    title_parts.append(
        f"{result.num_checkpoints} phases, "
        f"energy={result.total_energy_wh:.0f}Wh"
    )
    fig.suptitle(
        ", ".join(title_parts),
        fontsize=14, fontweight="bold", y=0.98,
    )

    ts = result.timestamps
    ts_hours = [t / 60.0 for t in ts]

    checkpoint_hours = [p.end_minutes / 60.0 for p in result.phases[:-1]]

    # Phase colors — RdYlGn gradient across all phases
    n_phases = len(result.phases)
    phase_colors = list(plt.cm.RdYlGn(np.linspace(0.15, 0.85, max(n_phases, 2))))

    row = 0

    # ── Row 0, Col 0: Temperature Timeline ─────────────────────────────
    ax_temp = fig.add_subplot(gs[row, 0])
    if "temp1" in result.readings:
        ax_temp.plot(ts_hours, result.readings["temp1"],
                     color="#E74C3C", linewidth=0.8, alpha=0.8)
    temp_range = _goal_range(goals, "temp1") if goals else None
    if temp_range:
        _add_target_band(ax_temp, temp_range[0], temp_range[1], "#E74C3C")
    _add_checkpoints_and_phases(ax_temp, checkpoint_hours, result.phases, phase_colors)
    ax_temp.set_ylabel("Temperature (°C)")
    ax_temp.set_xlabel("Time (hours)")
    ax_temp.set_title("Temperature with Checkpoint Markers")
    ax_temp.grid(True, alpha=0.3)
    temp_extra = [mpatches.Patch(color="#E74C3C", alpha=0.1, label=f"Target ({temp_range[0]:.0f}–{temp_range[1]:.0f}°C)")] if temp_range else None
    ax_temp.legend(
        handles=_build_phase_legend("Temperature", "#E74C3C", result.phases, phase_colors, temp_extra),
        loc="upper right", fontsize=7, ncol=2,
    )

    # ── Row 0, Col 1: Humidity Timeline ────────────────────────────────
    ax_hum = fig.add_subplot(gs[row, 1])
    if "hum1" in result.readings:
        ax_hum.plot(ts_hours, result.readings["hum1"],
                    color="#3498DB", linewidth=0.8, alpha=0.8)
    hum_range = _goal_range(goals, "hum1") if goals else None
    if hum_range:
        _add_target_band(ax_hum, hum_range[0], hum_range[1], "#3498DB")
    _add_checkpoints_and_phases(ax_hum, checkpoint_hours, result.phases, phase_colors)
    ax_hum.set_ylabel("Humidity (%)")
    ax_hum.set_xlabel("Time (hours)")
    ax_hum.set_title("Humidity with Checkpoint Markers")
    ax_hum.grid(True, alpha=0.3)
    hum_extra = [mpatches.Patch(color="#3498DB", alpha=0.1, label=f"Target ({hum_range[0]:.0f}–{hum_range[1]:.0f}%)")] if hum_range else None
    ax_hum.legend(
        handles=_build_phase_legend("Humidity", "#3498DB", result.phases, phase_colors, hum_extra),
        loc="upper right", fontsize=7, ncol=2,
    )

    row += 1

    # ── Row 1 (optional): CO₂ and VPD Timelines ───────────────────────
    if has_extra_row:
        # CO₂ panel
        ax_co2 = fig.add_subplot(gs[row, 0])
        if has_co2:
            ax_co2.plot(ts_hours, result.readings["co2_1"],
                        color="#8B4513", linewidth=0.8, alpha=0.8)
            _add_target_band(ax_co2, 800, 1200, "#8B4513")
        _add_checkpoints_and_phases(ax_co2, checkpoint_hours, result.phases, phase_colors)
        ax_co2.set_ylabel("CO\u2082 (ppm)")
        ax_co2.set_xlabel("Time (hours)")
        ax_co2.set_title("CO\u2082 with Checkpoint Markers")
        ax_co2.grid(True, alpha=0.3)
        co2_extra = [mpatches.Patch(color="#8B4513", alpha=0.1, label="Target (800\u20131200)")]
        ax_co2.legend(
            handles=_build_phase_legend("CO\u2082", "#8B4513", result.phases, phase_colors, co2_extra),
            loc="upper right", fontsize=7, ncol=2,
        )

        # VPD panel
        ax_vpd = fig.add_subplot(gs[row, 1])
        if has_vpd:
            ax_vpd.plot(ts_hours, result.readings["vpd1"],
                        color="#6C3483", linewidth=0.8, alpha=0.8)
            _add_target_band(ax_vpd, 0.8, 1.4, "#6C3483")
        _add_checkpoints_and_phases(ax_vpd, checkpoint_hours, result.phases, phase_colors)
        ax_vpd.set_ylabel("VPD (kPa)")
        ax_vpd.set_xlabel("Time (hours)")
        ax_vpd.set_title("VPD with Checkpoint Markers")
        ax_vpd.grid(True, alpha=0.3)
        vpd_extra = [mpatches.Patch(color="#6C3483", alpha=0.1, label="Target (0.8\u20131.4 kPa)")]
        ax_vpd.legend(
            handles=_build_phase_legend("VPD", "#6C3483", result.phases, phase_colors, vpd_extra),
            loc="upper right", fontsize=7, ncol=2,
        )

        row += 1

    # ── Actuator Intensity Heatmap (full width) ────────────────────────
    ax_heat = fig.add_subplot(gs[row, :])
    # Use result's actual actuator set (dynamic — includes hvac when present)
    heat_names = list(result.intensities.keys()) if result.intensities else list(_MPC_ACTUATOR_COLORS.keys())
    heatmap_data = []
    labels = []
    for name in heat_names:
        vals = result.intensities.get(name, [0.0] * len(ts))
        heatmap_data.append(vals)
        style_name = ACTUATOR_STYLES.get(name, (name, "#888", 0.15))[0]
        labels.append(style_name)

    heatmap_arr = np.array(heatmap_data)
    im = ax_heat.imshow(
        heatmap_arr,
        aspect="auto",
        cmap="YlOrRd",
        vmin=0.0,
        vmax=1.0,
        extent=[ts_hours[0], ts_hours[-1], -0.5, len(heat_names) - 0.5],
        origin="lower",
        interpolation="nearest",
    )
    # Add checkpoint lines to heatmap
    for ch in checkpoint_hours:
        ax_heat.axvline(x=ch, color="#7F8C8D", linestyle="--", alpha=0.6, linewidth=0.8)
    ax_heat.set_yticks(range(len(labels)))
    ax_heat.set_yticklabels(labels, fontsize=9)
    ax_heat.set_title("Actuator Intensities (0.0 – 1.0)", fontsize=10)
    ax_heat.set_xlabel("Time (hours)")
    fig.colorbar(im, ax=ax_heat, shrink=0.6, label="Intensity")

    row += 1

    # ── Last Row: Compliance Bars | Equipment Config ───────────────────
    ax_comp = fig.add_subplot(gs[row, 0])
    phase_labels = [f"Phase {p.phase_num}" for p in result.phases]
    comp_values = [p.avg_compliance * 100 for p in result.phases]
    bar_colors_comp = [phase_colors[i] for i in range(len(comp_values))]

    bars = ax_comp.bar(range(len(comp_values)), comp_values, color=bar_colors_comp, edgecolor="white")

    for i, bar in enumerate(bars):
        h = bar.get_height()
        ax_comp.annotate(
            f"{h:.1f}%",
            xy=(bar.get_x() + bar.get_width() / 2, h),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center", va="bottom",
            fontsize=9, fontweight="bold",
        )

    ax_comp.set_xticks(range(len(phase_labels)))
    ax_comp.set_xticklabels(phase_labels, fontsize=9)
    ax_comp.set_ylabel("Avg Compliance (%)")
    ax_comp.set_title("Compliance Trajectory Across Phases")
    ax_comp.set_ylim(0, 105)
    ax_comp.grid(True, alpha=0.3, axis="y")

    # ── Last Row: Equipment Configuration ────────────────────────────
    ax_equip = fig.add_subplot(gs[row, 1])
    ax_equip.axis("off")

    if actuator_specs:
        lines = []
        if room_name:
            lines.append(f"Room: {room_name}")
        if space:
            lines.append(f"Volume: {space.volume_m3:.0f} m\u00b3  |  Floor: {space.floor_area_m2:.0f} m\u00b2  |  Pots: {space.num_pots}")
        lines.append("")
        lines.append(f"{'Actuator':<18} {'Watts':>6}  {'Control':<9} {'Extra'}")
        lines.append("\u2500" * 55)
        for relay_id in sorted(actuator_specs):
            spec = actuator_specs[relay_id]
            extra = ""
            if spec.humidify_g_per_min:
                extra = f"+{spec.humidify_g_per_min:.0f} g/min"
            elif spec.dehumidify_g_per_min:
                extra = f"-{spec.dehumidify_g_per_min:.0f} g/min"
            lines.append(
                f"{spec.name:<18} {spec.max_watts:>6.0f}W  {spec.control_type:<9} {extra}"
            )
        # Add energy breakdown
        if result.energy_breakdown:
            lines.append("")
            lines.append(f"{'Energy Breakdown':<18} {'Wh':>8}")
            lines.append("\u2500" * 30)
            for name, wh in sorted(result.energy_breakdown.items(), key=lambda x: -x[1]):
                if wh > 0:
                    lines.append(f"{name:<18} {wh:>8.1f}")
            lines.append(f"{'Total':<18} {result.total_energy_wh:>8.1f}")
        ax_equip.text(
            0.05, 0.95, "\n".join(lines),
            transform=ax_equip.transAxes,
            fontsize=10, fontfamily="monospace",
            verticalalignment="top",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#F8F9FA", edgecolor="#DEE2E6", alpha=0.9),
        )
        ax_equip.set_title("Equipment Configuration", fontsize=11, fontweight="bold")

    # ── Layout ─────────────────────────────────────────────────────────
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_path


# ── MPC Charts ──────────────────────────────────────────────────────

# Actuator colors for the intensity heatmap
_MPC_ACTUATOR_COLORS: dict[str, str] = {
    "fan": "#4A90D9",
    "exhaust_fan": "#D94A4A",
    "humidifier": "#4AD9D9",
    "dehumidifier": "#D9944A",
    "irrigation": "#4AD94A",
    "light": "#D9D94A",
    "co2_injector": "#8B4513",
    "hvac": "#5B9BD5",
}


def plot_mpc_simulation(
    result: MPCSimulationResult,
    output_path: str | Path = "simulations/output/mpc_simulation.png",
    title: str | None = None,
    goals: list[dict] | None = None,
) -> Path:
    """Generate an MPC simulation chart with sensor panels, intensity heatmap, and diagnostics.

    Panels:
      Row 0: Temperature + Humidity (side by side)
      Row 1: CO2 + VPD (if data present)
      Row 2: Actuator intensity heatmap (7 actuators)
      Row 3: MPC cost breakdown + solve time
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    has_co2 = "co2_1" in result.readings and len(result.readings.get("co2_1", [])) > 0
    has_vpd = "vpd1" in result.readings and len(result.readings.get("vpd1", [])) > 0

    n_rows = 3  # sensors, heatmap, diagnostics
    if has_co2 or has_vpd:
        n_rows = 4  # + CO2/VPD row

    fig = plt.figure(figsize=(16, 3.5 * n_rows + 1.5))
    gs = fig.add_gridspec(n_rows, 2, hspace=0.35, wspace=0.25)

    ts = result.timestamps
    ts_h = [t / 60.0 for t in ts]

    if title is None:
        title = (
            f"MPC State Planner — {result.duration_minutes}min, "
            f"avg health={result.avg_health:.1%}, "
            f"energy={result.total_energy_wh:.0f}Wh"
        )
    fig.suptitle(title, fontsize=14, fontweight="bold", y=0.98)

    row = 0

    # ── Panel: Temperature ──────────────────────────────────────────
    ax_temp = fig.add_subplot(gs[row, 0])
    ax_temp.plot(ts_h, result.readings["temp1"], color="#E74C3C", linewidth=1.2)
    temp_range = _goal_range(goals, "temp1") if goals else None
    if temp_range:
        _add_target_band(ax_temp, temp_range[0], temp_range[1], "#E74C3C", fill_alpha=0.08, label=f"Target {temp_range[0]:.0f}–{temp_range[1]:.0f}°C")
    ax_temp.set_ylabel("Temperature (°C)")
    ax_temp.set_title("Temperature", fontsize=10)
    ax_temp.grid(True, alpha=0.3)
    if temp_range:
        ax_temp.legend(fontsize=8)

    # ── Panel: Humidity ─────────────────────────────────────────────
    ax_hum = fig.add_subplot(gs[row, 1])
    ax_hum.plot(ts_h, result.readings["hum1"], color="#3498DB", linewidth=1.2)
    hum_range = _goal_range(goals, "hum1") if goals else None
    if hum_range:
        _add_target_band(ax_hum, hum_range[0], hum_range[1], "#3498DB", fill_alpha=0.08, label=f"Target {hum_range[0]:.0f}–{hum_range[1]:.0f}%")
    ax_hum.set_ylabel("Humidity (%)")
    ax_hum.set_title("Humidity", fontsize=10)
    ax_hum.grid(True, alpha=0.3)
    if hum_range:
        ax_hum.legend(fontsize=8)

    row += 1

    # ── Panel: CO2 + VPD ────────────────────────────────────────────
    if has_co2 or has_vpd:
        if has_co2:
            ax_co2 = fig.add_subplot(gs[row, 0])
            ax_co2.plot(ts_h, result.readings["co2_1"], color="#8B4513", linewidth=1.2)
            _add_target_band(ax_co2, 800, 1200, "#8B4513", fill_alpha=0.08, label="Target 800–1200")
            ax_co2.set_ylabel("CO₂ (ppm)")
            ax_co2.set_title("CO₂", fontsize=10)
            ax_co2.grid(True, alpha=0.3)
            ax_co2.legend(fontsize=8)

        if has_vpd:
            ax_vpd = fig.add_subplot(gs[row, 1])
            ax_vpd.plot(ts_h, result.readings["vpd1"], color="#6C3483", linewidth=1.2)
            _add_target_band(ax_vpd, 0.8, 1.4, "#6C3483", fill_alpha=0.08, label="Target 0.8–1.4")
            ax_vpd.set_ylabel("VPD (kPa)")
            ax_vpd.set_title("VPD", fontsize=10)
            ax_vpd.grid(True, alpha=0.3)
            ax_vpd.legend(fontsize=8)

        row += 1

    # ── Panel: Actuator Intensity Heatmap ───────────────────────────
    ax_heat = fig.add_subplot(gs[row, :])
    # Use result's actual actuator set (dynamic — includes hvac when present)
    heat_names = list(result.intensities.keys()) if result.intensities else list(_MPC_ACTUATOR_COLORS.keys())
    # Build heatmap matrix: rows = actuators (bottom to top), cols = time
    heatmap_data = []
    labels = []
    for name in heat_names:
        vals = result.intensities.get(name, [0.0] * len(ts))
        heatmap_data.append(vals)
        style_name = ACTUATOR_STYLES.get(name, (name, "#888", 0.15))[0]
        labels.append(style_name)

    heatmap_arr = np.array(heatmap_data)
    im = ax_heat.imshow(
        heatmap_arr,
        aspect="auto",
        cmap="YlOrRd",
        vmin=0.0,
        vmax=1.0,
        extent=[ts_h[0], ts_h[-1], -0.5, len(heat_names) - 0.5],
        origin="lower",
        interpolation="nearest",
    )
    ax_heat.set_yticks(range(len(labels)))
    ax_heat.set_yticklabels(labels, fontsize=9)
    ax_heat.set_title("Actuator Intensities (0.0 – 1.0)", fontsize=10)
    ax_heat.set_xlabel("Time (hours)")
    fig.colorbar(im, ax=ax_heat, shrink=0.6, label="Intensity")

    row += 1

    # ── Panel: MPC Diagnostics ──────────────────────────────────────
    ax_cost = fig.add_subplot(gs[row, 0])
    ax_cost.fill_between(ts_h, 0, result.goal_costs, color="#E74C3C", alpha=0.4, label="Goal")
    goal_top = result.goal_costs
    energy_top = [g + e for g, e in zip(result.goal_costs, result.energy_costs)]
    ax_cost.fill_between(ts_h, goal_top, energy_top, color="#F39C12", alpha=0.4, label="Energy")
    rate_top = [et + r for et, r in zip(energy_top, result.rate_costs)]
    ax_cost.fill_between(ts_h, energy_top, rate_top, color="#3498DB", alpha=0.4, label="Rate")
    ax_cost.set_ylabel("Cost")
    ax_cost.set_title("Cost Decomposition", fontsize=10)
    ax_cost.set_xlabel("Time (hours)")
    ax_cost.legend(fontsize=8)
    ax_cost.grid(True, alpha=0.3)

    ax_solve = fig.add_subplot(gs[row, 1])
    ax_solve.plot(ts_h, result.solve_times_ms, color="#2ECC71", linewidth=0.8, alpha=0.7)
    avg_ms = sum(result.solve_times_ms) / max(len(result.solve_times_ms), 1)
    ax_solve.axhline(y=avg_ms, color="#27AE60", linestyle="--", linewidth=1, label=f"Avg: {avg_ms:.1f}ms")
    ax_solve.set_ylabel("Solve Time (ms)")
    ax_solve.set_title("MPC Solve Time", fontsize=10)
    ax_solve.set_xlabel("Time (hours)")
    ax_solve.legend(fontsize=8)
    ax_solve.grid(True, alpha=0.3)

    # ── Save ────────────────────────────────────────────────────────
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_path
