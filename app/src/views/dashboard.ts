export const dashboardHtml = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Sensor Dashboard</title>
    <style>
      :root {
        color-scheme: light dark;
      }
      body {
        font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji",
          "Segoe UI Emoji";
        margin: 0;
        padding: 24px;
        display: grid;
        place-items: center;
      }
      .card {
        width: min(720px, 100%);
        border: 1px solid rgba(127, 127, 127, 0.3);
        border-radius: 16px;
        padding: 20px;
      }
      h1 {
        margin: 0 0 12px;
        font-size: 20px;
      }
      .grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        margin-top: 12px;
      }
      .metric {
        padding: 16px;
        border-radius: 12px;
        border: 1px solid rgba(127, 127, 127, 0.25);
      }
      .label {
        opacity: 0.75;
        font-size: 12px;
        margin-bottom: 6px;
      }
      .value {
        font-size: 28px;
        font-weight: 700;
      }
      .meta {
        margin-top: 12px;
        font-size: 12px;
        opacity: 0.75;
      }
      code {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Sensor Dashboard</h1>
      <div class="grid">
        <div class="metric">
          <div class="label">Temperature</div>
          <div class="value" id="temp">--</div>
        </div>
        <div class="metric">
          <div class="label">Humidity</div>
          <div class="value" id="humidity">--</div>
        </div>
      </div>
      <div class="meta" id="meta">Waiting for first reading...</div>
    </div>

    <script>
      const tempEl = document.getElementById('temp');
      const humidityEl = document.getElementById('humidity');
      const metaEl = document.getElementById('meta');

      function fmtTime(ms) {
        const d = new Date(ms);
        return d.toLocaleString();
      }

      async function refresh() {
        try {
          const r = await fetch('/api/latest', { cache: 'no-store' });
          const data = await r.json();
          const latest = data.latest;

          if (!latest) {
            tempEl.textContent = '--';
            humidityEl.textContent = '--';
            metaEl.textContent = 'Waiting for first reading...';
            return;
          }

          tempEl.textContent = String(latest.temp);
          humidityEl.textContent = String(latest.humidity);
          metaEl.textContent =
            'Last update: ' +
            fmtTime(latest.updatedAt) +
            (latest.sourceIp ? ' | Source: ' + latest.sourceIp : '');
        } catch (e) {
          metaEl.textContent = 'Error fetching latest reading.';
        }
      }

      refresh();
      setInterval(refresh, 2000);
    </script>
  </body>
</html>`;
