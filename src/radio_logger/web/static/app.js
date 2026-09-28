const charts = {};
const palette = { all: "#62d6bd", japan: "#f2bf69", blue: "#8dbbf1", muted: "#9bb1c4", grid: "#294157" };
let selectedHours = 24;
let selectedBucket = 15;
let selectedTimezone = "Asia/Singapore";
let chartController = null;

function fmtHz(hz) { return hz == null ? "--" : (hz / 1e6).toFixed(3) + " MHz"; }
function fmtAge(sec) {
  if (sec == null) return "never";
  if (sec < 60) return Math.max(0, Math.round(sec)) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  if (sec < 86400) return (sec / 3600).toFixed(1) + "h";
  return (sec / 86400).toFixed(1) + "d";
}
function pct(v) { return v == null ? "--" : (v * 100).toFixed(1) + "%"; }
function n(v, digits = 1) { return v == null || Number.isNaN(v) ? "--" : Number(v).toFixed(digits); }
function count(v) { return v == null ? "--" : Number(v).toLocaleString("en-SG"); }
function measure(v, unit, digits = 1) { return v == null ? "--" : n(v, digits) + " " + unit; }
function put(id, value) { document.getElementById(id).textContent = value; }
function localTime(value, zone, date = false) {
  return new Date(value).toLocaleString("en-GB", {
    timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    ...(date ? { day: "2-digit", month: "short" } : {}),
  });
}
function zoneLabel(zone) { return zone === "Asia/Singapore" ? "SGT" : zone; }
function tickClocks() {
  const now = new Date();
  put("clock-utc", now.toISOString().slice(11, 19));
  put("clock-sgt", now.toLocaleTimeString("en-SG", { timeZone: "Asia/Singapore", hour12: false }));
}
async function getJson(url, signal) {
  const timeout = globalThis.AbortSignal?.timeout(30000);
  const requestSignal = signal && timeout && globalThis.AbortSignal?.any
    ? AbortSignal.any([signal, timeout]) : signal || timeout;
  const res = await fetch(url, { cache: "no-store", signal: requestSignal });
  if (!res.ok) throw new Error(url + " " + res.status);
  return res.json();
}
function showNotice(id, message) {
  const element = document.getElementById(id);
  element.textContent = message;
  element.hidden = !message;
}
function upsertChart(id, spec) {
  const canvas = document.getElementById(id);
  if (charts[id]) {
    charts[id].data = spec.data;
    charts[id].options = spec.options;
    charts[id].update("none");
  } else {
    charts[id] = new Chart(canvas, spec);
  }
}
function chartOptions({ unit = "Decodes", percentage = false, signal = false, horizontal = false } = {}) {
  const valueAxis = {
    beginAtZero: !signal,
    ...(percentage ? { min: 0, max: 100 } : {}),
    title: { display: true, text: unit, color: palette.muted, font: { size: 10 } },
    ticks: { color: palette.muted, font: { size: 10 }, ...(signal ? {} : { precision: 0 }) },
    grid: { color: palette.grid }, border: { display: false },
  };
  const categoryAxis = {
    ticks: { color: palette.muted, font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 9 },
    grid: { display: false }, border: { display: false },
  };
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    elements: {
      point: {
        radius(context) {
          const data = context.dataset.data;
          const index = context.dataIndex;
          return data[index] != null && data[index - 1] == null && data[index + 1] == null ? 3 : 0;
        },
        hitRadius: 10, hoverRadius: 4,
      },
      line: { borderWidth: 2, tension: 0 },
    },
    plugins: {
      legend: { position: "bottom", align: "start", labels: { color: palette.muted, boxWidth: 10, boxHeight: 10, padding: 18, font: { size: 11 } } },
      tooltip: { backgroundColor: "#0b1420", titleColor: "#e2edf5", bodyColor: "#e2edf5", borderColor: palette.grid, borderWidth: 1, padding: 12 },
    },
    indexAxis: horizontal ? "y" : "x",
    scales: horizontal ? { x: valueAxis, y: categoryAxis } : { x: categoryAxis, y: valueAxis },
  };
}
function line(label, values, color) { return { label, data: values, borderColor: color, backgroundColor: color, spanGaps: false }; }
function bar(label, values, color) { return { label, data: values, backgroundColor: color, borderRadius: 2, maxBarThickness: 26 }; }

async function refreshStatus() {
  const s = await getJson("/api/status");
  const issues = [];
  if (!s.db_writable) issues.push("Database is not writable. Collection needs attention.");
  if (s.storage_error) issues.push("Storage error: " + s.storage_error);
  if (s.udp_error) issues.push("UDP error: " + s.udp_error);
  if (s.storage_failures || s.dropped_datagrams) issues.push("Some observations were lost this session. Preserve WSJT-X ALL.TXT and check the console before importing missing history.");
  if (s.input_error) issues.push("Input error: " + s.input_error);
  if (s.input_recovery_warning) issues.push(s.input_recovery_warning);
  if (s.last_error) issues.push("Last issue: " + s.last_error);
  showNotice("runtime-error", issues.join(" "));
  const led = document.getElementById("led");
  const fileInput = s.input_source === "all_txt";
  const inputReady = fileInput ? s.input_readable : s.udp_recently_seen;
  const input = document.getElementById("f-udp");
  input.textContent = fileInput ? "ALL.TXT" : s.udp_bound || LOGGER_META.udp;
  input.title = fileInput ? s.input_path || "" : "";
  if (!s.ok) {
    led.className = "led warn";
    put("f-online", fileInput && !inputReady ? "FILE ERROR" : "ERROR");
  } else if (fileInput) {
    led.className = "led " + (s.online ? "ok" : "warn");
    put("f-online", s.input_backlog_bytes > 0 ? "CATCHING UP" : s.online ? "FILE LIVE" : "WAITING FOR DECODES");
  } else {
    led.className = "led " + (s.udp_recently_seen ? "ok" : "off");
    put("f-online", s.udp_recently_seen ? "UDP LIVE" : "WAITING FOR WSJT-X");
  }
  put("f-band", s.band || "--");
  put("f-dial", fmtHz(s.dial_frequency_hz));
  put("f-age", fmtAge(s.last_decode_age_seconds));
  put("f-uptime", fmtAge(s.uptime_seconds));
  put("f-queue", count(s.queue_depth));
  put("f-loss", count(s.dropped_datagrams) + " / " + count(s.storage_failures));
  put("f-today", count(s.decodes_today));
  put("f-today-ja", count(s.japan_decodes_today));
  put("today-zone", zoneLabel(s.display_timezone || "Asia/Singapore"));
  put("f-15", count(s.decodes_15m) + " / " + count(s.japan_decodes_15m) + " Japan");
  const mb = s.db_size_bytes != null ? (s.db_size_bytes / 1024 / 1024).toFixed(1) + " MB" : "--";
  put("f-db", (s.db_writable ? "OK · " : "ERROR · ") + mb);
}

async function refreshTotals() {
  const data = await getJson("/api/stats/totals");
  put("total-decodes", count(data.total_decodes));
  put("total-callsigns", count(data.unique_callsigns));
  put("total-japan", count(data.japan_decodes));
  put("total-japan-callsigns", count(data.japan_unique_callsigns));
  put("total-countries", count(data.unique_countries));
  put("collection-since", data.first_decode_at
    ? "Collecting since " + new Date(data.first_decode_at).toLocaleDateString("en-GB", { timeZone: "Asia/Singapore", day: "numeric", month: "short", year: "numeric" })
    : "Waiting for the first decode");
}

function historyRequest(now = new Date()) {
  const until = new Date(now);
  const since = new Date(until.getTime() - selectedHours * 3600000);
  const params = new URLSearchParams({ since: since.toISOString(), until: until.toISOString() });
  return { since, until, params, hours: selectedHours, bucket: selectedBucket, zone: selectedTimezone };
}
function renderHistory(jh, dist, countries, request) {
  const { since, until, hours, bucket, zone } = request;
  const summary = jh.totals;
  const period = hours === 168 ? "7 days" : hours + " hours";
  put("analysis-title", "The last " + period);
  put("range-caption", localTime(since, zone, true) + " → " + localTime(until, zone, true) + " " + zoneLabel(zone) + " · " + bucket + "-minute intervals");
  put("window-decodes", count(summary.total_decodes));
  put("window-callsigns", count(summary.unique_callsigns));
  put("j-stations", count(summary.japan_unique_callsigns));
  put("j-snr", measure(summary.median_japan_snr, "dB"));
  put("j-rel", measure(summary.japan_relative_snr, "dB"));
  put("j-dist", summary.median_japan_distance_km == null ? "--" : count(Math.round(summary.median_japan_distance_km)) + " km");
  put("j-share", pct(summary.japan_decode_share));
  put("j-peak", jh.peak_local ? localTime(jh.peak_local.bucket_start_utc, zone, true) + " " + zoneLabel(zone) : "No Japan decodes");
  document.getElementById("empty-history").hidden = summary.total_decodes !== 0;
  document.getElementById("export-csv").href = "/api/export/csv?" + request.params;

  const labels = jh.buckets.map((b) => [localTime(b.bucket_start_utc, zone), new Date(b.bucket_start_utc).toLocaleDateString("en-GB", { timeZone: zone, day: "2-digit", month: "short" })]);
  const values = (field) => jh.buckets.map((b) => b[field]);
  function timeOptions(settings) {
    const opts = chartOptions(settings);
    opts.plugins.tooltip.callbacks = {
      title(items) {
        if (!items.length) return "";
        const start = new Date(jh.buckets[items[0].dataIndex].bucket_start_utc).getTime();
        const left = new Date(Math.max(start, since.getTime()));
        const right = new Date(Math.min(start + bucket * 60000, until.getTime()));
        return localTime(left, zone, true) + " – " + localTime(right, zone) + " " + zoneLabel(zone);
      },
    };
    return opts;
  }
  const activityOptions = timeOptions();
  activityOptions.scales.x.stacked = true;
  activityOptions.scales.y.stacked = true;
  upsertChart("c-activity", { type: "bar", data: { labels, datasets: [
    bar("Other countries / unknown", jh.buckets.map((b) => b.total_decodes - b.japan_decodes), palette.all),
    bar("Japan", values("japan_decodes"), palette.japan),
  ] }, options: activityOptions });
  upsertChart("c-unique", { type: "line", data: { labels, datasets: [
    line("All callsigns", values("unique_callsigns"), palette.blue), line("Japan", values("japan_unique_callsigns"), palette.japan),
  ] }, options: timeOptions({ unit: "Callsigns" }) });
  upsertChart("c-snr", { type: "line", data: { labels, datasets: [
    line("All decodes", values("median_all_snr"), palette.blue), line("Japan", values("median_japan_snr"), palette.japan),
  ] }, options: timeOptions({ unit: "SNR · dB", signal: true }) });
  upsertChart("c-share", { type: "line", data: { labels, datasets: [
    line("Japan share (%)", jh.buckets.map((b) => b.japan_decode_share == null ? null : b.japan_decode_share * 100), palette.japan),
  ] }, options: timeOptions({ unit: "% of decodes", percentage: true }) });

  const hourly = zone === "UTC" ? jh.time_of_day_utc : jh.time_of_day_local;
  put("hourly-caption", "Clock hour in " + zoneLabel(zone) + " · summed across the last " + period);
  const hourlyOptions = chartOptions();
  hourlyOptions.scales.x.stacked = true;
  hourlyOptions.scales.y.stacked = true;
  hourlyOptions.scales.x.ticks.maxTicksLimit = 12;
  upsertChart("c-hourly", { type: "bar", data: { labels: hourly.map((b) => b.hour), datasets: [
    bar("Other countries / unknown", hourly.map((b) => b.decodes - b.japan_decodes), palette.all),
    bar("Japan", hourly.map((b) => b.japan_decodes), palette.japan),
  ] }, options: hourlyOptions });
  upsertChart("c-dist", { type: "bar", data: { labels: dist.items.map((d) => d.bucket_km.replace("-", "–")), datasets: [
    bar("Decodes", dist.items.map((d) => d.count), palette.all),
  ] }, options: chartOptions() });
  const ranked = countries.items.slice(0, 12);
  const countryOptions = chartOptions({ horizontal: true });
  countryOptions.scales.y.ticks.maxTicksLimit = 12;
  upsertChart("c-countries", { type: "bar", data: { labels: ranked.map((c) => c.country), datasets: [
    { ...bar("Decodes", ranked.map((c) => c.decodes), palette.blue), backgroundColor: ranked.map((c) => c.country === "Japan" ? palette.japan : palette.blue) },
  ] }, options: countryOptions });
  document.getElementById("empty-countries").hidden = ranked.length !== 0;
  document.getElementById("empty-distance").hidden = dist.items.some((d) => d.count > 0);
}

async function refreshLatest() {
  const data = await getJson("/api/observations/latest?limit=30");
  const body = document.getElementById("latest");
  const rows = data.items.map((r) => {
    const row = document.createElement("tr");
    if (r.is_japan) row.className = "japan";
    const values = [
      r.timestamp_utc ? new Date(r.timestamp_utc).toLocaleTimeString("en-GB", { timeZone: "Asia/Singapore", hourCycle: "h23" }) : "",
      r.timestamp_utc ? r.timestamp_utc.slice(11, 19) : "", r.snr_db, r.df, r.tx_callsign, r.tx_grid, r.country,
      r.distance_km == null ? "" : Math.round(r.distance_km), r.raw_message,
    ];
    row.append(...values.map((value) => {
      const cell = document.createElement("td");
      cell.textContent = value ?? "";
      if (r.timestamp_utc) cell.title = r.timestamp_utc;
      return cell;
    }));
    return row;
  });
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.textContent = "No decodes stored yet. Check the receiver input and wait for reception.";
    row.append(cell);
    rows.push(row);
  }
  body.replaceChildren(...rows);
}

function dash(value) { return value == null || value === "" ? "--" : String(value); }
function scaleLabel(block, letter) {
  if (!block || block.level == null) return letter + "?";
  return letter + block.level;
}
function clockPhrase(iso, zone) {
  return iso ? localTime(iso, zone) + " " + zoneLabel(zone) : "--";
}
function renderPropagation(data) {
  const space = data.space_weather || {};
  put("wx-sfi", dash(space.sfi));
  put("wx-kp", space.kp == null ? "--" : n(space.kp, 2));
  put("wx-wind", measure(space.solar_wind_kms, "km/s", 0));
  put("wx-bz", space.bz_nt == null ? "--" : n(space.bz_nt, 1) + " nT");
  put("wx-xray", dash(space.xray_class));
  put("wx-scales", [scaleLabel(space.radio_blackout, "R"), scaleLabel(space.geomagnetic, "G"), scaleLabel(space.proton, "S")].join("  "));
  const wxNote = space.stale ? "Showing the last NOAA update. A refresh failed." : (space.fetched_at ? "Updated " + localTime(space.fetched_at, "UTC") + " UTC" : "");
  put("wx-updated", wxNote);
  const solar = data.solar || {};
  const state = solar.state === "day" ? "Day" : solar.state === "twilight" ? "Twilight" : solar.state === "night" ? "Night" : "--";
  const until = solar.minutes_to_next == null ? "" : " · " + (solar.next_event === "sunset" ? "sunset" : "sunrise") + " in " + solar.minutes_to_next + " min";
  put("solar-state", state + (solar.elevation_deg == null ? "" : "  " + n(solar.elevation_deg, 1) + "°") + until);
  const where = data.location && data.location.label ? data.location.label : "the receiver";
  put("solar-detail", "Sunrise " + clockPhrase(solar.sunrise_utc, "Asia/Singapore") + " · sunset " + clockPhrase(solar.sunset_utc, "Asia/Singapore") + " · " + where);
  const alert = document.getElementById("prop-alert");
  alert.hidden = !data.alert;
  alert.textContent = data.alert ? data.alert.text : "";
  const heard = data.heard || {};
  const tuned = heard.band || (heard.bands && heard.bands[0] && heard.bands[0].band);
  const dial = heard.dial_hz == null ? "" : (heard.dial_hz / 1e6).toFixed(3) + " MHz";
  const band = (heard.bands || [])[0];
  const condition = document.getElementById("heard-condition");
  if (!band) {
    condition.textContent = "--";
    condition.className = "heard-condition";
    put("heard-detail", "");
  } else {
    const arrow = band.trend === "up" ? "↑" : band.trend === "down" ? "↓" : "→";
    condition.textContent = (band.condition || "quiet") + " " + arrow;
    condition.className = "heard-condition cond-" + (band.condition || "quiet");
    const where = (band.regions || []).join(", ");
    put("heard-detail", [
      count(band.unique_calls_15m) + " calls",
      band.median_snr == null ? null : n(band.median_snr, 0) + " dB",
      where || null,
    ].filter(Boolean).join(" · "));
  }
  put("heard-hint", tuned
    ? tuned + (dial ? " · " + dial : "") + " · last 15 minutes against the 15 before"
    : "This receiver listens on one frequency. No decodes yet.");
  const ham = data.hamqsl || {};
  const groups = { day: {}, night: {} };
  const names = [];
  (ham.bands || []).forEach((item) => {
    if (!item.name || !item.rating) return;
    const slot = item.time === "night" ? groups.night : groups.day;
    if (!names.includes(item.name)) names.push(item.name);
    slot[item.name] = item.rating;
  });
  const ratings = names.map((name) => {
    const day = groups.day[name];
    const night = groups.night[name];
    return name + (day ? " day " + day : "") + (night ? " night " + night : "");
  });
  if (ham.sunspots) ratings.push("Sunspots " + ham.sunspots);
  const fallback = document.getElementById("hamqsl-bands");
  fallback.textContent = ratings.join("   ");
  const hamImage = document.getElementById("img-bands");
  hamImage.hidden = false;
  fallback.hidden = true;
  hamImage.onerror = () => { hamImage.hidden = true; fallback.hidden = false; };
  put("hamqsl-updated", ham.updated ? ham.updated.trim() : (ham.error ? "Unavailable" : ""));
  fillPairs("noaa-detail", [
    ["SFI", dash(space.sfi)],
    ["Kp", space.kp == null ? "--" : n(space.kp, 2)],
    ["Ap", dash(space.ap)],
    ["Wind", space.solar_wind_kms == null ? "--" : n(space.solar_wind_kms, 0) + " km/s"],
    ["Bz", space.bz_nt == null ? "--" : n(space.bz_nt, 1) + " nT"],
    ["Bt", space.bt_nt == null ? "--" : n(space.bt_nt, 1) + " nT"],
    ["X-ray", dash(space.xray_class)],
    ["R", scaleLabel(space.radio_blackout, "R")],
    ["S", scaleLabel(space.proton, "S")],
    ["G", scaleLabel(space.geomagnetic, "G")],
  ]);
  fillPairs("hamqsl-index", [
    ["SFI", dash(ham.solar_flux)],
    ["A", dash(ham.a_index)],
    ["K", dash(ham.k_index)],
    ["X-ray", dash(ham.xray)],
    ["Sunspots", dash(ham.sunspots)],
    ["Wind", dash(ham.solar_wind)],
    ["Mag", dash(ham.magnetic_field)],
    ["Protons", dash(ham.proton_flux)],
    ["Electrons", dash(ham.electron_flux)],
    ["Aurora", dash(ham.aurora)],
  ]);
  renderBandTable("hamqsl-band-table", names, groups);
  const measured = (heard.bands || [])[0];
  fillPairs("heard-full", measured ? [
    ["Band", measured.band],
    ["Calls", count(measured.unique_calls_15m)],
    ["Previous", count(measured.unique_calls_previous_15m)],
    ["Decodes", count(measured.decodes_15m)],
    ["SNR", measured.median_snr == null ? "--" : n(measured.median_snr, 0) + " dB"],
    ["SNR range", measured.min_snr == null ? "--" : n(measured.min_snr, 0) + " to " + n(measured.max_snr, 0)],
    ["Furthest", measured.max_distance_km == null ? "--" : count(measured.max_distance_km) + " km"],
    ["Where", (measured.regions || []).join(", ") || "--"],
  ] : [["Band", "--"]]);
}

function ratingClass(word) {
  const key = String(word || "").toLowerCase();
  if (key === "good" || key === "excellent") return "cond-good";
  if (key === "fair") return "cond-fair";
  if (key === "poor") return "cond-poor";
  return "";
}

function renderBandTable(id, names, groups) {
  const table = document.createElement("table");
  table.className = "band-ref";
  const head = document.createElement("tr");
  ["Band", "Day", "Night"].forEach((label) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    head.append(cell);
  });
  const thead = document.createElement("thead");
  thead.append(head);
  const body = document.createElement("tbody");
  names.forEach((name) => {
    const row = document.createElement("tr");
    const band = document.createElement("td");
    band.textContent = name;
    row.append(band);
    ["day", "night"].forEach((when) => {
      const cell = document.createElement("td");
      const rating = groups[when][name] || "--";
      cell.textContent = rating;
      cell.className = ratingClass(rating);
      row.append(cell);
    });
    body.append(row);
  });
  table.append(thead, body);
  document.getElementById(id).replaceChildren(table);
}

function fillPairs(id, pairs) {
  const list = document.getElementById(id);
  list.replaceChildren(...pairs.map(([label, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const data = document.createElement("dd");
    term.textContent = label;
    data.textContent = value;
    row.append(term, data);
    return row;
  }));
}

let fastPending = false;
let totalsPending = false;
let propagationPending = false;
async function tickFast() {
  tickClocks();
  if (fastPending) return;
  fastPending = true;
  try {
    await Promise.all([refreshStatus(), refreshLatest()]);
    showNotice("connection-error", "");
  } catch (_err) {
    document.getElementById("led").className = "led off";
    put("f-online", "DISCONNECTED");
    showNotice("connection-error", "Cannot reach the logger. Displayed values may be old. Check its console. Retrying automatically.");
  } finally { fastPending = false; }
}
async function tickTotals() {
  if (totalsPending) return;
  totalsPending = true;
  try {
    await refreshTotals();
    showNotice("totals-error", "");
  } catch (_err) {
    showNotice("totals-error", "All-time totals could not refresh. Displayed totals may be old. Retrying automatically.");
  } finally { totalsPending = false; }
}
async function tickSlow(force = false) {
  if (chartController && !force) return;
  if (chartController) chartController.abort();
  const controller = new AbortController();
  chartController = controller;
  const request = historyRequest();
  const params = request.params.toString();
  put("chart-updated", "Loading selected period…");
  document.getElementById("analysis").setAttribute("aria-busy", "true");
  try {
    const [jh, dist, countries] = await Promise.all([
      getJson("/api/stats/japan-hour?" + params + "&bucket=" + request.bucket + "&tz=" + encodeURIComponent(request.zone), controller.signal),
      getJson("/api/stats/distance?" + params, controller.signal),
      getJson("/api/stats/countries?" + params, controller.signal),
    ]);
    if (controller.signal.aborted || chartController !== controller) return;
    renderHistory(jh, dist, countries, request);
    showNotice("chart-error", "");
    put("chart-updated", "Updated " + localTime(new Date(), request.zone) + " " + zoneLabel(request.zone) + " · refreshes every 30s");
  } catch (_err) {
    if (controller.signal.aborted || chartController !== controller) return;
    showNotice("chart-error", "Charts could not refresh. Any displayed charts and period summary are from the previous successful update. Retrying automatically.");
    put("chart-updated", "Refresh failed");
  } finally {
    if (chartController === controller) {
      chartController = null;
      document.getElementById("analysis").setAttribute("aria-busy", "false");
    }
  }
}
function setupControls() {
  document.querySelectorAll("[data-hours]").forEach((button) => button.addEventListener("click", () => {
    selectedHours = Number(button.dataset.hours);
    document.querySelectorAll("[data-hours]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    selectedBucket = selectedHours === 168 ? 60 : selectedHours === 48 ? 30 : 15;
    document.getElementById("bucket-size").value = String(selectedBucket);
    tickSlow(true);
  }));
  document.getElementById("bucket-size").addEventListener("change", (event) => { selectedBucket = Number(event.target.value); tickSlow(true); });
  document.getElementById("chart-timezone").addEventListener("change", (event) => { selectedTimezone = event.target.value; tickSlow(true); });
}

async function refreshPropagation() {
  if (propagationPending) return;
  propagationPending = true;
  try {
    renderPropagation(await getJson("/api/propagation"));
    showNotice("prop-error", "");
  } catch (_err) {
    showNotice("prop-error", "Propagation numbers could not refresh. Anything still on screen is from the last successful update.");
  } finally { propagationPending = false; }
}

tickFast();
tickTotals();
tickSlow();
refreshPropagation();
setupControls();
setInterval(tickClocks, 1000);
setInterval(tickFast, 5000);
setInterval(tickSlow, 30000);
setInterval(tickTotals, 60000);
setInterval(refreshPropagation, 60000);
