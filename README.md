# Japan Hour Logger V1

Receive-only FT8 propagation logger for a school Japan Hour experiment.

WSJT-X decodes arrive over UDP (or ALL.TXT replay), every decode is stored as its own SQLite row, then the logger enriches callsign/country/grid/distance and serves Japan Hour analytics on a local dashboard.

This program does not talk to an SDR, does not decode FT8, and does not transmit. It starts at the WSJT-X data-output layer.

Owner: Edmund Lim ([EdmundLimBoEn](https://github.com/EdmundLimBoEn)).

## What it stores

Every decode is a row. The same station heard 20 times is 20 observations. The logger never dedupes by callsign.

The only suppression is a short network-duplicate window: identical `instance id + decode time + SNR + DT + DF + message` within about 2 seconds (WSJT-X often emits the same UDP packet more than once).

UTC is authoritative. Asia/Singapore is used for local display and Japan Hour charts. Raw message text is always kept, plus the normalized fields.

Grids are never invented. Country may come from the callsign prefix. A grid is stored only from the FT8 message, the station cache (a grid heard earlier from that station), or an explicit external/config locator, and `grid_source` records which.

If enrichment fails, the decode is still stored. Unknown FT8 text becomes `message_type=unknown` and does not crash the logger.

## Install

Python 3.12+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Or `pip install -r requirements.txt` after the editable install has written the lock-ish requirements file.

Copy `config/receiver.yaml` if you want a local override. Set the school Maidenhead locator. Leave `TODO` until you know it; country/DXCC still work, distance/bearing stay empty.

```yaml
receiver:
  id: school-9v
  name: Japan Hour Receiver
  locator: TODO          # e.g. OJ11  — do not invent a grid
  timezone: Asia/Singapore
udp:
  host: 127.0.0.1
  port: 2237
```

Environment variables (`RADIO_LOGGER_UDP_PORT`, `RADIO_LOGGER_RECEIVER_LOCATOR`, …) override YAML. See `.env.example`.

## Run logger + API/dashboard

```bash
radio-logger run
```

- UDP listener: `127.0.0.1:2237`
- Dashboard and API: http://127.0.0.1:8080

Without WSJT-X, seed fixture data so the dashboard is not empty:

```bash
radio-logger seed
radio-logger run --no-udp
```

`radio-logger status` prints local DB counts. `radio-logger status --url http://127.0.0.1:8080` queries a running instance.

## WSJT-X UDP setup

In WSJT-X: **File → Settings → Reporting**.

1. Enable **Accept UDP requests** / UDP Server.
2. UDP Server: `127.0.0.1`
3. UDP Server port number: `2237`
4. Leave the logger running before or after WSJT-X. Heartbeat, Status, Decode, Clear, and Close are consumed. Decode packets become observations.

Multicast is optional in `config/receiver.yaml` (`udp.multicast: true` and a multicast group as `host`). Default is unicast localhost.

## Replay ALL.TXT

```bash
radio-logger replay /path/to/ALL.TXT
radio-logger replay /path/to/ALL.TXT --speed 1      # original timing
radio-logger replay tests/fixtures/sample_all.txt --speed 0
radio-logger replay events.jsonl
```

Replay uses the same `NormalizedDecode` path as UDP. A copy of ALL.TXT is kept under `data/raw/wsjtx/YYYY/MM/` when possible.

## Export CSV

```bash
radio-logger export --format csv --from 2026-09-11T00:00:00+00:00 --to 2026-09-12T00:00:00+00:00
```

Writes `data/exports/observations.csv` unless `--output` is set. The same export is at `GET /api/export/csv`.

## Analyze Japan Hour

```bash
radio-logger analyze japan-hour --bucket 15
```

Buckets are 5, 15, 30, or 60 minutes (default 15). Each bucket reports:

- `total_decodes`, `unique_callsigns`, `unique_countries`
- Japan counterparts (`japan_decodes`, `japan_unique_callsigns`, …)
- `japan_decode_share`, `japan_unique_station_share`
- median/mean/max/min Japan SNR, `median_all_snr`
- **`japan_relative_snr` = median Japan SNR − median non-Japan SNR**
- median/max/min Japan distance

Charts default to SGT. UTC hour-of-day is included in the API payload (`/api/stats/japan-hour`).

Japan DXCC entities (config `japan.dxcc_entities`): Japan, Ogasawara, Minami Torishima.

## Other CLI

```bash
radio-logger db-info
radio-logger db-backup
radio-logger simulate --japan-spike
```

`--japan-spike` writes a burst of Japan activity for analytics QA. It is not on-air data.

## Example observation

`CQ JA1XYZ PM95` becomes:

| Field | Value |
| --- | --- |
| `tx_callsign` | JA1XYZ |
| `message_type` | cq |
| `is_cq` | true |
| `tx_grid` | PM95 |
| `grid_source` | message |
| `country` | Japan |
| `dxcc` | JA |
| `continent` | AS |
| `is_japan` | true |
| `band` | 20m (when dial is 14.074 MHz) |

Distance and bearing are filled only when `receiver.locator` is a real Maidenhead grid (for example `OJ11`).

## Data layout

```
data/radio.db
data/raw/wsjtx/YYYY/MM/          # ALL.TXT copies, JSONL raw events
data/exports/
data/backups/                    # SQLite online backup via radio-logger db-backup
```

Schema (Alembic): `observations`, `stations`, `sessions`, `receivers`, `app_events`. Every observation has `receiver_id`. Core tables are not named `japan_ft8_*`.

```bash
alembic upgrade head
```

`radio-logger run` also creates tables if they are missing.

## API

| Method | Path |
| --- | --- |
| GET | `/api/status` |
| GET | `/api/observations` |
| GET | `/api/observations/latest` |
| GET | `/api/stations` |
| GET | `/api/stations/{call}/history` |
| GET | `/api/stats/summary` |
| GET | `/api/stats/countries` |
| GET | `/api/stats/distance` |
| GET | `/api/stats/snr` |
| GET | `/api/stats/japan-hour` |
| GET | `/api/export/csv` |
| GET | `/health` |

Status includes UDP recently seen, last decode, decodes/15m, DB writable/size, uptime, dial/band.

## Tests

```bash
pytest
```

Coverage includes Maidenhead/distance/bearing, DXCC prefixes (9V JA VK YB BY HL HS DU W/K and portables), FT8 messages, UDP sample packets, and DB preservation of repeated callsigns.

DXCC prefix data is a bundled AD1C-format `cty.dat` (via the DJ1YFK `dxcc` distribution). Country names follow that file. Drop a newer `cty.dat` into `src/radio_logger/resources/cty.dat` to update.

SDRplay / RSP duo control, DSP, FT8 decode, TX, cloud deploy, ML, map. No fridge or Mac ping.
