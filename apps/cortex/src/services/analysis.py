"""
Sensor data analysis utilities.

Ported from apps/api/src/routes/utils/analysis.ts — statistical analysis,
anomaly detection, trend detection, and formatting.
"""

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .redis_client import RedisReading
from .sqlite_client import TelemetryRow


@dataclass
class Stats:
    mean: float = 0.0
    std_dev: float = 0.0
    min: float = 0.0
    max: float = 0.0
    range: float = 0.0


@dataclass
class Anomaly:
    ts: int
    value: float
    type: str  # "spike" | "drop" | "outlier"
    deviation: float
    metric: str  # "temperature" | "humidity"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "value": self.value,
            "type": self.type,
            "deviation": self.deviation,
            "metric": self.metric,
        }


@dataclass
class SensorAnalysis:
    metric: str  # "temperature" | "humidity"
    stats: Stats = field(default_factory=Stats)
    trend: str = "stable"  # "rising" | "falling" | "stable"
    anomalies: list[Anomaly] = field(default_factory=list)
    fluctuation_score: float = 0.0  # 0-100

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "stats": {
                "mean": self.stats.mean,
                "stdDev": self.stats.std_dev,
                "min": self.stats.min,
                "max": self.stats.max,
                "range": self.stats.range,
            },
            "trend": self.trend,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "fluctuationScore": self.fluctuation_score,
        }


def calculate_stats(values: list[float]) -> Stats:
    if not values:
        return Stats()
    mean = sum(values) / len(values)
    squared_diffs = [(v - mean) ** 2 for v in values]
    std_dev = math.sqrt(sum(squared_diffs) / len(values))
    min_val = min(values)
    max_val = max(values)
    return Stats(mean=mean, std_dev=std_dev, min=min_val, max=max_val, range=max_val - min_val)


def analyze_metric(
    metric_name: str,
    values: list[float],
    timestamps: list[int],
) -> SensorAnalysis:
    """Analyze a single metric for stats, anomalies, trend, and volatility."""
    stats = calculate_stats(values)
    anomalies: list[Anomaly] = []

    # Detect outliers (> 2 std deviations from mean)
    for i, val in enumerate(values):
        deviation = abs(val - stats.mean) / (stats.std_dev or 1)
        if deviation > 2:
            anomalies.append(Anomaly(
                ts=timestamps[i], value=val, type="outlier",
                deviation=deviation, metric=metric_name,
            ))

    # Detect spikes/drops (rapid changes between consecutive readings)
    change_threshold = stats.std_dev * 1.5 or stats.range * 0.1
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        if abs(change) > change_threshold:
            anomalies.append(Anomaly(
                ts=timestamps[i], value=values[i],
                type="spike" if change > 0 else "drop",
                deviation=abs(change), metric=metric_name,
            ))

    # Calculate trend (compare first quarter average to last quarter average)
    quarter_len = max(1, len(values) // 4)
    first_quarter = values[:quarter_len]
    last_quarter = values[-quarter_len:]
    first_avg = sum(first_quarter) / len(first_quarter)
    last_avg = sum(last_quarter) / len(last_quarter)
    trend_threshold = stats.std_dev * 0.5 or 0.5
    trend = "stable"
    if last_avg - first_avg > trend_threshold:
        trend = "rising"
    elif first_avg - last_avg > trend_threshold:
        trend = "falling"

    # Fluctuation score (coefficient of variation, normalized to 0-100)
    cv = (stats.std_dev / abs(stats.mean)) * 100 if stats.mean != 0 else 0
    fluctuation_score = min(100, cv * 10)

    return SensorAnalysis(
        metric=metric_name, stats=stats, trend=trend,
        anomalies=anomalies, fluctuation_score=fluctuation_score,
    )


def analyze_sensor_data(
    metric: str,
    redis_readings: list[RedisReading],
    sqlite_readings: list[TelemetryRow],
) -> dict[str, Any]:
    """Analyze sensor data from both Redis and SQLite sources."""
    # Merge and sort by timestamp
    all_readings: list[dict] = []
    for r in sqlite_readings:
        all_readings.append({"ts": r.ts, "temp": r.temp, "humidity": r.humidity})
    for r in redis_readings:
        all_readings.append({"ts": r.ts, "temp": r.temp, "humidity": r.humidity})

    all_readings.sort(key=lambda r: r["ts"])

    if not all_readings:
        return {"dataPoints": 0}

    result: dict[str, Any] = {"dataPoints": len(all_readings)}

    if metric in ("temperature", "all"):
        temps = [r["temp"] for r in all_readings]
        timestamps = [r["ts"] for r in all_readings]
        result["temperature"] = analyze_metric("temperature", temps, timestamps)

    if metric in ("humidity", "all"):
        humidities = [r["humidity"] for r in all_readings]
        timestamps = [r["ts"] for r in all_readings]
        result["humidity"] = analyze_metric("humidity", humidities, timestamps)

    return result


# ── Timeframe Parsing ────────────────────────────────────────────────

def parse_timeframe(timeframe: str) -> int:
    """Parse timeframe string (e.g., '24h', '7d') to milliseconds."""
    match = re.match(r"^(\d+)(h|d)$", timeframe)
    if not match:
        return 24 * 60 * 60 * 1000  # default 24h
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return value * 60 * 60 * 1000
    if unit == "d":
        return value * 24 * 60 * 60 * 1000
    return 24 * 60 * 60 * 1000


# ── Formatting ───────────────────────────────────────────────────────

def format_analysis_reply(
    reply: str,
    summary: str | None,
    analysis: dict[str, Any],
) -> str:
    """Format analysis results for display, matching Node.js output."""
    parts: list[str] = [reply]

    if analysis.get("dataPoints", 0) == 0:
        parts.append("\n\nNo sensor data found in this timeframe.")
        return "".join(parts)

    parts.append(f"\n\n<detail>\n📊 Analyzed {analysis['dataPoints']} readings")

    def format_metric(data: SensorAnalysis) -> None:
        unit = "°C" if data.metric == "temperature" else "%"
        icon = "🌡️" if data.metric == "temperature" else "💧"

        parts.append(f"\n\n**{icon} {data.metric.capitalize()}:**")
        parts.append(f"• Range: {data.stats.min:.1f}{unit} - {data.stats.max:.1f}{unit}")
        parts.append(f"• Average: {data.stats.mean:.1f}{unit} (±{data.stats.std_dev:.2f})")

        trend_icon = "📈 Rising" if data.trend == "rising" else "📉 Falling" if data.trend == "falling" else "➡️ Stable"
        parts.append(f"• Trend: {trend_icon}")

        volatility = "Low" if data.fluctuation_score < 20 else "Moderate" if data.fluctuation_score < 50 else "High"
        parts.append(f"• Volatility: {volatility} ({data.fluctuation_score:.0f}/100)")

        if data.anomalies:
            spikes = sum(1 for a in data.anomalies if a.type == "spike")
            drops = sum(1 for a in data.anomalies if a.type == "drop")
            outliers = sum(1 for a in data.anomalies if a.type == "outlier")

            parts.append("\n⚠️ **Anomalies detected:**")
            if spikes > 0:
                parts.append(f"  • {spikes} spike{'s' if spikes > 1 else ''}")
            if drops > 0:
                parts.append(f"  • {drops} drop{'s' if drops > 1 else ''}")
            if outliers > 0:
                parts.append(f"  • {outliers} outlier{'s' if outliers > 1 else ''}")

            recent = data.anomalies[-3:]
            for a in recent:
                from datetime import datetime
                t = datetime.fromtimestamp(a.ts / 1000).strftime("%I:%M %p")
                parts.append(f"  → {t}: {a.value:.1f}{unit} ({a.type})")
        else:
            parts.append("\n✅ No anomalies detected")

    if "temperature" in analysis and isinstance(analysis["temperature"], SensorAnalysis):
        format_metric(analysis["temperature"])
    if "humidity" in analysis and isinstance(analysis["humidity"], SensorAnalysis):
        format_metric(analysis["humidity"])

    parts.append("</detail>")

    if summary:
        parts.append(f"\n{summary}")

    return "\n".join(parts)


def format_history_reply(
    reply: str,
    summary: str | None,
    commands: list[dict] | None,
    events: list[dict] | None,
    category: str,
) -> str:
    """Format history results for display, matching Node.js output."""
    parts: list[str] = [reply]
    parts.append("\n\n<detail>")

    if commands and len(commands) > 0:
        parts.append(f"\n\n**Commands ({len(commands)}):**")
        for cmd in commands[:10]:
            from datetime import datetime
            t = datetime.fromtimestamp(cmd["ts"] / 1000).strftime("%I:%M %p")
            status = "✓" if cmd["status"] == "acked" else "✗" if cmd["status"] == "failed" else "⏳"
            parts.append(f"{status} {t}: {cmd['target']} → {cmd['value']} ({cmd['source']})")
        if len(commands) > 10:
            parts.append(f"... and {len(commands) - 10} more")
    elif category in ("commands", "all"):
        parts.append("\n\nNo commands found in this timeframe.")

    if events and len(events) > 0:
        parts.append(f"\n\n**Events ({len(events)}):**")
        for evt in events[:10]:
            from datetime import datetime
            t = datetime.fromtimestamp(evt["ts"] / 1000).strftime("%I:%M %p")
            parts.append(f"• {t}: {evt['eventType']} ({evt['deviceId']})")
        if len(events) > 10:
            parts.append(f"... and {len(events) - 10} more")
    elif category in ("events", "all"):
        parts.append("\n\nNo events found in this timeframe.")

    parts.append("</detail>")

    if summary:
        parts.append(f"\n{summary}")

    return "\n".join(parts)
