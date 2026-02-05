import { getReadingsInRange } from "../../lib/redis.js";
import { queryHistoryRaw, queryCommands, queryEvents } from "../../lib/sqlite.js";
import type { OllamaIntent } from "../../services/ollama.js";
import { parseTimeframe } from "./timeframe.js";

// Statistical helpers for sensor analysis
function calculateStats(values: number[]) {
  if (values.length === 0) return { mean: 0, stdDev: 0, min: 0, max: 0, range: 0 };
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const squaredDiffs = values.map((v) => Math.pow(v - mean, 2));
  const stdDev = Math.sqrt(squaredDiffs.reduce((a, b) => a + b, 0) / values.length);
  const min = Math.min(...values);
  const max = Math.max(...values);
  return { mean, stdDev, min, max, range: max - min };
}

export type Anomaly = {
  ts: number;
  value: number;
  type: "spike" | "drop" | "outlier";
  deviation: number;
  metric: "temperature" | "humidity";
};

export type SensorAnalysis = {
  metric: "temperature" | "humidity";
  stats: ReturnType<typeof calculateStats>;
  trend: "rising" | "falling" | "stable";
  anomalies: Anomaly[];
  fluctuationScore: number; // 0-100, higher = more volatile
};

// Analyze sensor readings for patterns and anomalies
export async function analyzeSensorData(
  intent: Extract<OllamaIntent, { intent: "analyze" }>
): Promise<{ temperature?: SensorAnalysis; humidity?: SensorAnalysis; dataPoints: number }> {
  const sinceMs = Date.now() - parseTimeframe(intent.timeframe);
  const untilMs = Date.now();
  const metric = intent.metric ?? "all";

  // Fetch data from both Redis (hot) and SQLite (cold)
  const redisReadings = await getReadingsInRange(sinceMs, untilMs);
  const sqliteReadings = queryHistoryRaw({ sinceMs, untilMs, limit: 5000 });

  // Merge and sort by timestamp
  const allReadings = [...sqliteReadings, ...redisReadings];
  allReadings.sort((a, b) => a.ts - b.ts);

  if (allReadings.length === 0) {
    return { dataPoints: 0 };
  }

  const result: { temperature?: SensorAnalysis; humidity?: SensorAnalysis; dataPoints: number } = {
    dataPoints: allReadings.length,
  };

  const analyzeMetric = (
    metricName: "temperature" | "humidity",
    values: number[],
    timestamps: number[]
  ): SensorAnalysis => {
    const stats = calculateStats(values);
    const anomalies: Anomaly[] = [];

    // Detect outliers (> 2 std deviations from mean)
    for (let i = 0; i < values.length; i++) {
      const deviation = Math.abs(values[i] - stats.mean) / (stats.stdDev || 1);
      if (deviation > 2) {
        anomalies.push({
          ts: timestamps[i],
          value: values[i],
          type: "outlier",
          deviation,
          metric: metricName,
        });
      }
    }

    // Detect spikes/drops (rapid changes between consecutive readings)
    const changeThreshold = stats.stdDev * 1.5 || stats.range * 0.1;
    for (let i = 1; i < values.length; i++) {
      const change = values[i] - values[i - 1];
      if (Math.abs(change) > changeThreshold) {
        anomalies.push({
          ts: timestamps[i],
          value: values[i],
          type: change > 0 ? "spike" : "drop",
          deviation: Math.abs(change),
          metric: metricName,
        });
      }
    }

    // Calculate trend (compare first quarter average to last quarter average)
    const quarterLen = Math.floor(values.length / 4) || 1;
    const firstQuarter = values.slice(0, quarterLen);
    const lastQuarter = values.slice(-quarterLen);
    const firstAvg = firstQuarter.reduce((a, b) => a + b, 0) / firstQuarter.length;
    const lastAvg = lastQuarter.reduce((a, b) => a + b, 0) / lastQuarter.length;
    const trendThreshold = stats.stdDev * 0.5 || 0.5;
    let trend: "rising" | "falling" | "stable" = "stable";
    if (lastAvg - firstAvg > trendThreshold) trend = "rising";
    else if (firstAvg - lastAvg > trendThreshold) trend = "falling";

    // Calculate fluctuation score (coefficient of variation, normalized to 0-100)
    const cv = stats.mean !== 0 ? (stats.stdDev / Math.abs(stats.mean)) * 100 : 0;
    const fluctuationScore = Math.min(100, cv * 10); // Scale for readability

    return { metric: metricName, stats, trend, anomalies, fluctuationScore };
  };

  if (metric === "temperature" || metric === "all") {
    const temps = allReadings.map((r) => r.temp);
    const timestamps = allReadings.map((r) => r.ts);
    result.temperature = analyzeMetric("temperature", temps, timestamps);
  }

  if (metric === "humidity" || metric === "all") {
    const humidities = allReadings.map((r) => r.humidity);
    const timestamps = allReadings.map((r) => r.ts);
    result.humidity = analyzeMetric("humidity", humidities, timestamps);
  }

  return result;
}

// Format analysis results for display
export function formatAnalysisReply(
  intent: Extract<OllamaIntent, { intent: "analyze" }>,
  analysis: Awaited<ReturnType<typeof analyzeSensorData>>
): string {
  const parts: string[] = [intent.reply];

  if (analysis.dataPoints === 0) {
    parts.push("\n\nNo sensor data found in this timeframe.");
    return parts.join("");
  }

  parts.push(`\n\n📊 Analyzed ${analysis.dataPoints} readings`);

  const formatMetricAnalysis = (data: SensorAnalysis) => {
    const unit = data.metric === "temperature" ? "°C" : "%";
    const icon = data.metric === "temperature" ? "🌡️" : "💧";

    parts.push(`\n\n**${icon} ${data.metric.charAt(0).toUpperCase() + data.metric.slice(1)}:**`);
    parts.push(`• Range: ${data.stats.min.toFixed(1)}${unit} - ${data.stats.max.toFixed(1)}${unit}`);
    parts.push(`• Average: ${data.stats.mean.toFixed(1)}${unit} (±${data.stats.stdDev.toFixed(2)})`);
    parts.push(`• Trend: ${data.trend === "rising" ? "📈 Rising" : data.trend === "falling" ? "📉 Falling" : "➡️ Stable"}`);
    parts.push(`• Volatility: ${data.fluctuationScore < 20 ? "Low" : data.fluctuationScore < 50 ? "Moderate" : "High"} (${data.fluctuationScore.toFixed(0)}/100)`);

    if (data.anomalies.length > 0) {
      const spikes = data.anomalies.filter((a) => a.type === "spike").length;
      const drops = data.anomalies.filter((a) => a.type === "drop").length;
      const outliers = data.anomalies.filter((a) => a.type === "outlier").length;

      parts.push(`\n⚠️ **Anomalies detected:**`);
      if (spikes > 0) parts.push(`  • ${spikes} spike${spikes > 1 ? "s" : ""}`);
      if (drops > 0) parts.push(`  • ${drops} drop${drops > 1 ? "s" : ""}`);
      if (outliers > 0) parts.push(`  • ${outliers} outlier${outliers > 1 ? "s" : ""}`);

      // Show most recent anomalies
      const recentAnomalies = data.anomalies.slice(-3);
      for (const a of recentAnomalies) {
        const time = new Date(a.ts).toLocaleTimeString();
        parts.push(`  → ${time}: ${a.value.toFixed(1)}${unit} (${a.type})`);
      }
    } else {
      parts.push(`\n✅ No anomalies detected`);
    }
  };

  if (analysis.temperature) formatMetricAnalysis(analysis.temperature);
  if (analysis.humidity) formatMetricAnalysis(analysis.humidity);

  return parts.join("\n");
}

// Helper to fetch history based on intent
export function fetchHistory(intent: Extract<OllamaIntent, { intent: "history" }>) {
  const sinceMs = Date.now() - parseTimeframe(intent.timeframe);
  const category = intent.category ?? "all";

  const result: {
    commands?: ReturnType<typeof queryCommands>;
    events?: ReturnType<typeof queryEvents>;
  } = {};

  if (category === "commands" || category === "all") {
    result.commands = queryCommands({ sinceMs, limit: 50 });
  }
  if (category === "events" || category === "all") {
    result.events = queryEvents({ sinceMs, limit: 50 });
  }

  return result;
}

// Format history for display
export function formatHistoryReply(
  intent: Extract<OllamaIntent, { intent: "history" }>,
  history: ReturnType<typeof fetchHistory>
): string {
  const parts: string[] = [intent.reply];

  if (history.commands && history.commands.length > 0) {
    parts.push(`\n\n**Commands (${history.commands.length}):**`);
    for (const cmd of history.commands.slice(0, 10)) {
      const time = new Date(cmd.ts).toLocaleTimeString();
      const status = cmd.status === "acked" ? "✓" : cmd.status === "failed" ? "✗" : "⏳";
      parts.push(`${status} ${time}: ${cmd.target} → ${cmd.value} (${cmd.source})`);
    }
    if (history.commands.length > 10) {
      parts.push(`... and ${history.commands.length - 10} more`);
    }
  } else if (intent.category === "commands" || intent.category === "all") {
    parts.push("\n\nNo commands found in this timeframe.");
  }

  if (history.events && history.events.length > 0) {
    parts.push(`\n\n**Events (${history.events.length}):**`);
    for (const evt of history.events.slice(0, 10)) {
      const time = new Date(evt.ts).toLocaleTimeString();
      parts.push(`• ${time}: ${evt.eventType} (${evt.deviceId})`);
    }
    if (history.events.length > 10) {
      parts.push(`... and ${history.events.length - 10} more`);
    }
  } else if (intent.category === "events" || intent.category === "all") {
    parts.push("\n\nNo events found in this timeframe.");
  }

  return parts.join("\n");
}