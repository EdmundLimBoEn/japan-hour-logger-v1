from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js is needed for the DOM harness"
)

_HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(process.argv[2]);
const fixedNow = '2026-09-27T02:17:42.000Z';
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : [fixedNow])); }
  static now() { return Date.parse(fixedNow); }
}
class Element {
  constructor(id) {
    this.id = id;
    this.textContent = '';
    this.hidden = false;
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.children = [];
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  dispatch(name) { return this.listeners[name]({ target: this }); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, new Element(id));
  return elements.get(id);
}
const buttons = [24, 48, 168].map((hours) => {
  const button = new Element('range-' + hours);
  button.dataset.hours = String(hours);
  return button;
});
const renderedCharts = new Map();
class Chart {
  constructor(canvas, spec) {
    this.type = spec.type;
    this.data = spec.data;
    this.options = spec.options;
    this.updates = [];
    renderedCharts.set(canvas.id, this);
  }
  update(mode) { this.updates.push(mode); }
}
const context = {
  document: {
    getElementById: element,
    querySelectorAll: () => buttons,
    createElement: (tag) => new Element(tag),
  },
  Chart, Date: FixedDate, URLSearchParams, AbortController, AbortSignal,
  LOGGER_META: { udp: '127.0.0.1:2237' },
  console,
  fetch: async () => { throw new Error('Unexpected request'); },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8').split('tickFast();')[0], context);
const run = (source) => vm.runInContext(source, context);
const plain = (value) => JSON.parse(JSON.stringify(value));
const response = (data) => ({ ok: true, json: async () => data });
const flush = () => new Promise((resolve) => setImmediate(resolve));
function history(total = 10) {
  const rows = [
    ['2026-09-26T15:45:00Z', 0, 0, null, null],
    ['2026-09-26T16:00:00Z', 6, 0, 0, -12],
    ['2026-09-26T16:15:00Z', 4, 2, 0.5, -5],
  ];
  return {
    totals: {
      total_decodes: total, unique_callsigns: 7, japan_unique_callsigns: 2,
      japan_decode_share: 0.2, median_japan_snr: -5, japan_relative_snr: 7,
      median_japan_distance_km: 5312,
    },
    buckets: rows.map(([timestamp, all, japan, share, snr]) => ({
      bucket_start_utc: timestamp, total_decodes: all, japan_decodes: japan,
      unique_callsigns: all, japan_unique_callsigns: japan,
      japan_decode_share: share, median_all_snr: snr,
      median_japan_snr: japan ? snr : null,
    })),
    peak_local: { bucket_start_utc: '2026-09-26T16:15:00Z' },
    time_of_day_utc: Array.from({ length: 24 }, (_, hour) => ({
      hour, decodes: hour === 16 ? 10 : 0, japan_decodes: hour === 16 ? 2 : 0,
    })),
    time_of_day_local: Array.from({ length: 24 }, (_, hour) => ({
      hour, decodes: hour === 0 ? 10 : 0, japan_decodes: hour === 0 ? 2 : 0,
    })),
  };
}
const distance = { items: [{ bucket_km: '5000-6000', count: 10 }] };
const countries = { items: [{ country: 'Japan', decodes: 2 }] };
function serveHistory(data = history()) {
  const requests = [];
  context.fetch = async (url) => {
    requests.push(new URL(url, 'http://logger.test'));
    if (url.startsWith('/api/stats/japan-hour?')) return response(data);
    if (url.startsWith('/api/stats/distance?')) return response(distance);
    if (url.startsWith('/api/stats/countries?')) return response(countries);
    throw new Error('Unexpected request: ' + url);
  };
  return requests;
}
"""


def _run_dashboard(script: str, **payload) -> None:
    app_js = Path("src/radio_logger/web/static/app.js").resolve()
    result = subprocess.run(
        [
            "node",
            "-e",
            _HARNESS + script + "\nmain().catch((error) => { console.error(error); process.exitCode = 1; });",
            str(app_js),
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("hours", "bucket", "since"),
    [
        (24, 15, "2026-09-26T02:17:42.000Z"),
        (48, 30, "2026-09-25T02:17:42.000Z"),
        (168, 60, "2026-09-20T02:17:42.000Z"),
    ],
)
def test_history_controls_share_exact_rolling_bounds_with_charts_and_csv(hours, bucket, since):
    _run_dashboard(
        r"""
async function main() {
  const requests = serveHistory();
  context.setupControls();
  const selected = buttons.find((button) => Number(button.dataset.hours) === input.hours);
  selected.dispatch('click');
  await flush();
  assert.equal(requests.length, 3);
  assert.deepEqual(requests.map((url) => url.pathname).sort(), [
    '/api/stats/countries', '/api/stats/distance', '/api/stats/japan-hour',
  ]);
  const exported = new URL(element('export-csv').href, 'http://logger.test');
  assert.equal(exported.pathname, '/api/export/csv');
  for (const url of [...requests, exported]) {
    assert.equal(url.searchParams.get('since'), input.since);
    assert.equal(url.searchParams.get('until'), fixedNow);
  }
  const chartRequest = requests.find((url) => url.pathname.endsWith('japan-hour'));
  assert.equal(chartRequest.searchParams.get('bucket'), String(input.bucket));
  assert.equal(element('bucket-size').value, String(input.bucket));
  assert.equal(element('analysis-title').textContent,
    'The last ' + (input.hours === 168 ? '7 days' : input.hours + ' hours'));
  assert.equal(element('analysis').attributes['aria-busy'], 'false');
  for (const button of buttons) {
    assert.equal(button.attributes['aria-pressed'], String(button === selected));
  }
}
""",
        hours=hours,
        bucket=bucket,
        since=since,
    )


def test_empty_intervals_remain_gaps_while_observed_zero_japan_share_is_zero():
    _run_dashboard(
        r"""
async function main() {
  context.renderHistory(history(), distance, countries, context.historyRequest());
  const share = renderedCharts.get('c-share');
  assert.deepEqual(plain(share.data.datasets[0].data), [null, 0, 50]);
  assert.equal(share.data.datasets[0].spanGaps, false);
  assert.equal(share.options.scales.y.min, 0);
  assert.equal(share.options.scales.y.max, 100);
  const snr = renderedCharts.get('c-snr');
  assert.deepEqual(plain(snr.data.datasets[0].data), [null, -12, -5]);
  assert.deepEqual(plain(snr.data.datasets[1].data), [null, null, -5]);
  assert.equal(snr.options.scales.y.beginAtZero, false);
  assert.equal(snr.data.datasets[1].spanGaps, false);
  const activity = renderedCharts.get('c-activity');
  assert.deepEqual(plain(activity.data.datasets.map((dataset) => dataset.data)), [
    [0, 6, 2], [0, 0, 2],
  ]);
  assert.equal(activity.options.scales.y.stacked, true);
}
"""
    )


def test_isolated_signal_and_share_measurements_have_visible_points():
    _run_dashboard(
        r"""
async function main() {
  const data = history();
  data.buckets = [null, -5, null, 0, null, -10, -11].map((snr, index) => ({
    ...data.buckets[0],
    bucket_start_utc: new Date(Date.parse('2026-09-26T16:00:00Z') + index * 900000)
      .toISOString(),
    median_japan_snr: snr,
    japan_decode_share: snr == null ? null : Math.abs(snr) / 20,
  }));
  context.renderHistory(data, distance, countries, context.historyRequest());
  for (const [chartId, datasetIndex] of [['c-snr', 1], ['c-share', 0]]) {
    const chart = renderedCharts.get(chartId);
    const dataset = chart.data.datasets[datasetIndex];
    const radius = chart.options.elements.point.radius;
    const at = (index) => typeof radius === 'function'
      ? radius({ dataset, dataIndex: index }) : radius;
    assert.equal(dataset.spanGaps, false);
    assert.ok(at(1) > 0, 'An isolated measurement needs a visible marker');
    assert.ok(at(3) > 0, 'An isolated zero is still a measured value');
    assert.equal(at(2), 0, 'An empty interval must not acquire a marker');
    assert.equal(at(5), 0, 'Connected measurements remain a clean line');
  }
}
"""
    )


def test_timezone_changes_dates_clock_hour_histogram_and_request_timezone():
    _run_dashboard(
        r"""
async function main() {
  const requests = serveHistory();
  context.setupControls();
  await context.tickSlow();
  assert.deepEqual(plain(renderedCharts.get('c-activity').data.labels), [
    ['23:45', '26 Sept'], ['00:00', '27 Sept'], ['00:15', '27 Sept'],
  ]);
  assert.equal(renderedCharts.get('c-hourly').data.datasets[1].data[0], 2);
  assert.equal(renderedCharts.get('c-hourly').data.labels.length, 24);
  assert.match(element('j-peak').textContent, /27 Sept.*00:15 SGT/);
  const timezone = element('chart-timezone');
  timezone.value = 'UTC';
  timezone.dispatch('change');
  await flush();
  assert.deepEqual(plain(renderedCharts.get('c-activity').data.labels), [
    ['15:45', '26 Sept'], ['16:00', '26 Sept'], ['16:15', '26 Sept'],
  ]);
  assert.equal(renderedCharts.get('c-hourly').data.datasets[1].data[0], 0);
  assert.equal(renderedCharts.get('c-hourly').data.datasets[1].data[16], 2);
  assert.match(element('hourly-caption').textContent, /UTC/);
  assert.match(element('j-peak').textContent, /26 Sept.*16:15 UTC/);
  assert.equal(requests[3].searchParams.get('tz'), 'UTC');
  assert.deepEqual(renderedCharts.get('c-activity').updates, ['none']);
}
"""
    )


def test_tooltip_clips_partial_buckets_to_selected_bounds():
    _run_dashboard(
        r"""
async function main() {
  const request = context.historyRequest();
  request.since = new Date('2026-09-26T15:52:00Z');
  request.until = new Date('2026-09-26T16:18:00Z');
  request.zone = 'UTC';
  context.renderHistory(history(), distance, countries, request);
  const title = renderedCharts.get('c-activity').options.plugins.tooltip.callbacks.title;
  assert.equal(title([]), '');
  assert.match(title([{ dataIndex: 0 }]), /15:52 – 16:00 UTC$/);
  assert.match(title([{ dataIndex: 2 }]), /16:15 – 16:18 UTC$/);
}
"""
    )


@pytest.mark.parametrize("stale_failure", [False, True])
def test_aborted_history_cannot_overwrite_latest_selection_or_clear_its_loading(stale_failure):
    _run_dashboard(
        r"""
async function main() {
  const pending = [];
  context.fetch = (url, options) => new Promise((resolve, reject) => {
    pending.push({ url, signal: options.signal, resolve, reject });
  });
  const oldRefresh = context.tickSlow();
  await context.tickSlow();
  assert.equal(pending.length, 3, 'A timer must not duplicate an in-flight request');
  run('selectedHours = 168; selectedBucket = 60;');
  const newRefresh = context.tickSlow(true);
  assert.equal(pending.length, 6);
  assert.ok(pending.slice(0, 3).every((request) => request.signal.aborted));
  assert.ok(pending.slice(3).every((request) => !request.signal.aborted));
  if (input.stale_failure) {
    pending[0].reject(new Error('Late network failure'));
  } else {
    pending[0].resolve(response(history(111)));
  }
  pending[1].resolve(response(distance));
  pending[2].resolve(response(countries));
  await oldRefresh;
  assert.equal(renderedCharts.size, 0);
  assert.equal(element('chart-error').textContent, '');
  assert.equal(element('analysis').attributes['aria-busy'], 'true');
  assert.equal(element('chart-updated').textContent, 'Loading selected period…');
  pending[3].resolve(response(history(222)));
  pending[4].resolve(response(distance));
  pending[5].resolve(response(countries));
  await newRefresh;
  assert.equal(element('window-decodes').textContent, '222');
  assert.equal(element('analysis-title').textContent, 'The last 7 days');
  assert.equal(element('analysis').attributes['aria-busy'], 'false');
  assert.equal(new URL(element('export-csv').href, 'http://logger.test')
    .searchParams.get('since'), '2026-09-20T02:17:42.000Z');
}
""",
        stale_failure=stale_failure,
    )


def test_totals_refresh_independently_and_survive_a_later_refresh_failure():
    _run_dashboard(
        r"""
async function main() {
  const historyRequests = serveHistory();
  const fetchHistory = context.fetch;
  const totalsRequests = [];
  context.fetch = (url, options) => {
    if (url === '/api/stats/totals') {
      return new Promise((resolve, reject) => totalsRequests.push({ resolve, reject }));
    }
    return fetchHistory(url, options);
  };
  const refreshingTotals = context.tickTotals();
  await context.tickTotals();
  assert.equal(totalsRequests.length, 1, 'Totals requests must not overlap');
  await context.tickSlow();
  assert.equal(element('window-decodes').textContent, '10');
  assert.equal(historyRequests.length, 3);
  totalsRequests[0].resolve(response({
    total_decodes: 12345, unique_callsigns: 345, japan_decodes: 987,
    japan_unique_callsigns: 65, unique_countries: 32,
    first_decode_at: '2026-09-01T18:00:00Z',
  }));
  await refreshingTotals;
  assert.equal(element('total-decodes').textContent, '12,345');
  assert.equal(element('total-callsigns').textContent, '345');
  assert.equal(element('total-japan').textContent, '987');
  assert.equal(element('total-japan-callsigns').textContent, '65');
  assert.equal(element('total-countries').textContent, '32');
  assert.equal(element('collection-since').textContent, 'Collecting since 2 Sept 2026');
  run('selectedHours = 48;');
  await context.tickSlow(true);
  assert.equal(totalsRequests.length, 1, 'Changing history must not refetch lifetime totals');
  assert.equal(element('total-decodes').textContent, '12,345');
  const failedRefresh = context.tickTotals();
  totalsRequests[1].reject(new Error('Totals offline'));
  await failedRefresh;
  assert.equal(element('total-decodes').textContent, '12,345');
  assert.equal(element('total-callsigns').textContent, '345');
  assert.equal(element('totals-error').hidden, false);
  assert.match(element('totals-error').textContent, /All-time totals could not refresh/);
  assert.equal(element('chart-error').textContent, '');
  assert.equal(element('analysis-title').textContent, 'The last 48 hours');
}
"""
    )
