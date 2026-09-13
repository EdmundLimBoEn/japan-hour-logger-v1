const charts = {};

function fmtHz(hz) {
  if (hz == null) return "--";
  return (hz / 1e6).toFixed(3) + " MHz";
}
function fmtAge(sec) {
  if (sec == null) return "never";
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  return (sec / 3600).toFixed(1) + "h";
}
function pct(v) {
  if (v == null) return "--";
  return (v * 100).toFixed(1) + "%";
}
function n(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return "--";
  return Number(v).toFixed(digits);
}

function tickClocks() {
  const now = new Date();
  document.getElementById("clock-utc").textContent = now.toISOString().slice(11, 19);
  document.getElementById("clock-sgt").textContent = now.toLocaleTimeString("en-SG", {
    timeZone: "Asia/Singapore",
    hour12: false,
  });
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(url + " " + res.status);
  return res.json();
}

function upsertChart(id, spec) {
  const ctx = document.getElementById(id);
  if (!ctx) return;
  if (charts[id]) {
    charts[id].data = spec.data;
    charts[id].options = spec.options;
    charts[id].update();
    return;
  }
  charts[id] = new Chart(ctx, spec);
}

const darkOpts = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { labels: { color: "#7fa38f" } } },
  scales: {
    x: { ticks: { color: "#7fa38f" }, grid: { color: "#1e3b2f" } },
    y: { ticks: { color: "#7fa38f" }, grid: { color: "#1e3b2f" } },
  },
};

async function refreshStatus() {
  const s = await getJson("/api/status");
  const led = document.getElementById("led");
  led.className = "led " + (s.udp_recently_seen ? "ok" : s.last_decode_at ? "warn" : "off");
  document.getElementById("f-online").textContent = s.udp_recently_seen ? "UDP LIVE" : s.last_decode_at ? "DATA" : "IDLE";
  document.getElementById("f-udp").textContent = s.udp_bound || LOGGER_META.udp;
  document.getElementById("f-band").textContent = s.band || "--";
  document.getElementById("f-dial").textContent = fmtHz(s.dial_frequency_hz);
  document.getElementById("f-age").textContent = fmtAge(s.last_decode_age_seconds);
  document.getElementById("f-uptime").textContent = fmtAge(s.uptime_seconds);
  document.getElementById("f-today").textContent = s.decodes_today;
  document.getElementById("f-today-ja").textContent = s.japan_decodes_today;
  document.getElementById("f-15").textContent = s.decodes_15m + " / JA " + s.japan_decodes_15m;
  const mb = s.db_size_bytes != null ? (s.db_size_bytes / 1024 / 1024).toFixed(2) + " MB" : "--";
  document.getElementById("f-db").textContent = (s.db_writable ? "ok · " : "ERR · ") + mb;
}

async function refreshJapan() {
  const summary = await getJson("/api/stats/summary");
  const jh = await getJson("/api/stats/japan-hour?bucket=15");
  document.getElementById("j-stations").textContent = summary.japan_unique_callsigns;
  document.getElementById("j-snr").textContent = n(summary.median_japan_snr) + " dB";
  document.getElementById("j-rel").textContent = n(summary.japan_relative_snr) + " dB";
  document.getElementById("j-dist").textContent = n(summary.median_japan_distance_km, 0) + " km";
  document.getElementById("j-share").textContent = pct(summary.japan_decode_share);
  document.getElementById("j-peak").textContent = jh.peak_local ? jh.peak_local.local_hour : "--";

  const labels = jh.buckets.map((b) => b.local_hour);
  upsertChart("c-activity", {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "All", data: jh.buckets.map((b) => b.total_decodes), backgroundColor: "#1f6" },
        { label: "Japan", data: jh.buckets.map((b) => b.japan_decodes), backgroundColor: "#fc4" },
      ],
    },
    options: darkOpts,
  });
  upsertChart("c-unique", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Unique", data: jh.buckets.map((b) => b.unique_callsigns), borderColor: "#6fe7ff", tension: 0.2 },
        { label: "Japan unique", data: jh.buckets.map((b) => b.japan_unique_callsigns), borderColor: "#fc4", tension: 0.2 },
      ],
    },
    options: darkOpts,
  });
  upsertChart("c-share", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Japan share", data: jh.buckets.map((b) => (b.japan_decode_share || 0) * 100), borderColor: "#fc4", yAxisID: "y" },
        { label: "Japan decodes", data: jh.buckets.map((b) => b.japan_decodes), borderColor: "#3dff8a", yAxisID: "y1" },
      ],
    },
    options: {
      ...darkOpts,
      scales: {
        ...darkOpts.scales,
        y1: { position: "right", ticks: { color: "#7fa38f" }, grid: { drawOnChartArea: false } },
      },
    },
  });
  upsertChart("c-snr", {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Median all", data: jh.buckets.map((b) => b.median_all_snr), borderColor: "#6fe7ff" },
        { label: "Median Japan", data: jh.buckets.map((b) => b.median_japan_snr), borderColor: "#fc4" },
      ],
    },
    options: darkOpts,
  });

  const dist = await getJson("/api/stats/distance");
  upsertChart("c-dist", {
    type: "bar",
    data: {
      labels: dist.items.map((d) => d.bucket_km),
      datasets: [{ label: "Decodes", data: dist.items.map((d) => d.count), backgroundColor: "#3dff8a" }],
    },
    options: darkOpts,
  });
  const countries = await getJson("/api/stats/countries");
  upsertChart("c-countries", {
    type: "bar",
    data: {
      labels: countries.items.map((c) => c.country),
      datasets: [{ label: "Decodes", data: countries.items.map((c) => c.decodes), backgroundColor: "#6fe7ff" }],
    },
    options: { ...darkOpts, indexAxis: "y" },
  });
}

async function refreshLatest() {
  const data = await getJson("/api/observations/latest?limit=30");
  const body = document.getElementById("latest");
  const rows = data.items.map((r) => {
    const row = document.createElement("tr");
    if (r.is_japan) row.className = "japan";
    const values = [
      r.timestamp_local ? r.timestamp_local.slice(11, 19) : "",
      r.timestamp_utc ? r.timestamp_utc.slice(11, 19) : "",
      r.snr_db,
      r.df,
      r.tx_callsign,
      r.tx_grid,
      r.country,
      r.distance_km,
      r.raw_message,
    ];
    row.append(
      ...values.map((value) => {
        const cell = document.createElement("td");
        cell.textContent = value ?? "";
        return cell;
      }),
    );
    return row;
  });
  body.replaceChildren(...rows);
}

async function tickFast() {
  tickClocks();
  try {
    await refreshStatus();
    await refreshLatest();
  } catch (err) {
    document.getElementById("led").className = "led off";
  }
}
async function tickSlow() {
  try {
    await refreshJapan();
  } catch (_err) {
    /* keep last charts */
  }
}

tickFast();
tickSlow();
setInterval(tickFast, 5000);
setInterval(tickSlow, 15000);
