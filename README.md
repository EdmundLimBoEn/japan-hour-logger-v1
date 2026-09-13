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

## Set up the school computer

Start with the [step-by-step Windows and Linux setup guide](docs/SETUP.md). It covers Python, Git, WSJT-X, receiver audio, clock synchronization, and a live reception check. Use [daily operation and troubleshooting](docs/OPERATIONS.md) for backups, exports, updates, and moving to another machine.

The default setup runs the receiver software, WSJT-X, and this logger on the same computer. Data stays on that computer. First installation needs internet access. The installed dashboard and charts run locally.

With Git and Python 3.12 or newer installed, run these commands in Windows PowerShell:

```powershell
git clone https://github.com/EdmundLimBoEn/japan-hour-logger-v1.git
Set-Location .\japan-hour-logger-v1
& '.\Setup Windows.cmd'
notepad .\config\receiver.yaml
& '.\Start Logger.cmd'
```

Set the school's verified `receiver.locator` in the configuration file. Leave `TODO` if it is unknown. Distance and bearing stay empty until the locator is known.

After installation, the Windows helpers can be double-clicked in File Explorer:

| File | Action |
| --- | --- |
| `Setup Windows.cmd` | Install or update the environment and check it. Preserve configuration and data. |
| `Start Logger.cmd` | Run the logger and open the dashboard. Keep the terminal open. |
| `Check Setup.cmd` | Check the configuration and test with isolated temporary data. |
| `Backup Data.cmd` | Create a SQLite backup and configuration copy. |
| `Export CSV.cmd` | Export all observations to a new timestamped CSV. |

On Linux with Python 3.12 and its `venv` module installed:

```bash
git clone https://github.com/EdmundLimBoEn/japan-hour-logger-v1.git
cd japan-hour-logger-v1
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install .
.venv/bin/python scripts/verify-install.py
.venv/bin/python -m radio_logger run --config config/receiver.yaml
```

Open [the dashboard](http://127.0.0.1:8080) on the receiver computer. Configure WSJT-X as below and check that new real decodes appear. Press Ctrl+C in the terminal to stop the logger.

The self-test checks UDP, persistence, analytics, CSV export, backups, and local dashboard assets without writing sample rows to the real database. Do not use `seed` or `simulate` against school observations.

Environment variables override YAML, which overrides built-in defaults. Use single-underscore names such as `RADIO_LOGGER_UDP_PORT` and `RADIO_LOGGER_RECEIVER_LOCATOR`. Double-underscore names such as `RADIO_LOGGER_UDP__PORT` remain supported. Set `RADIO_LOGGER_CONFIG` to select a different YAML file for direct CLI commands. See `.env.example` for names. The application does not automatically load a `.env` file.

The remaining examples use `radio-logger`. Without activating the virtual environment, replace it with `.\.venv\Scripts\python.exe -m radio_logger` on Windows or `.venv/bin/python -m radio_logger` on Linux.

## WSJT-X UDP setup

In WSJT-X: **File → Settings → Reporting**.

1. Set **UDP Server** to `127.0.0.1`.
2. Set **UDP Server port number** to `2237`.
3. Leave **Accept UDP requests** disabled. That option allows external programs to send control commands to WSJT-X. WSJT-X sends decode reports with the option disabled.
4. Leave the logger running before or after WSJT-X. The logger consumes Heartbeat, Status, Decode, Clear, and Close packets. Decode packets become observations.

Multicast is optional in `config/receiver.yaml` (`udp.multicast: true` and a multicast group as `host`). Default is unicast localhost.

## Replay ALL.TXT

Import a historical file once. Repeated imports and overlap with live collection add duplicate observations. Follow the [history import procedure](docs/OPERATIONS.md#import-old-wsjtx-history-once) before writing to an experiment database.

```bash
radio-logger replay /path/to/ALL.TXT
radio-logger replay /path/to/ALL.TXT --speed 1      # original timing
radio-logger replay events.jsonl
```

Replay uses the same `NormalizedDecode` path as UDP. A copy of ALL.TXT is kept under `data/raw/wsjtx/YYYY/MM/` when possible.

## Export CSV

```bash
radio-logger export --format csv --from 2026-09-11T00:00:00+00:00 --to 2026-09-12T00:00:00+00:00
```

Writes `data/exports/observations.csv` unless `--output` is set. The same export is at `GET /api/export/csv`.

## Pull data to a Mac

Run analysis on a local snapshot so the Raspberry Pi only collects data and serves the dashboard:

```bash
scripts/pull-radio-data
```

The helper asks the live logger for an online SQLite backup, downloads only that new backup, and verifies it with `PRAGMA integrity_check`. It updates `~/Documents/radio-data/radio-pi/latest.db` and incrementally copies raw logs without deleting files on either computer. Override either endpoint when needed:

```bash
scripts/pull-radio-data --remote root@radio-pi.local --destination ~/Documents/radio-data/radio-pi
```

Open the local database read-only with SQLite:

```bash
sqlite3 -readonly ~/Documents/radio-data/radio-pi/latest.db
```

Python can open the same snapshot without write access:

```python
import sqlite3
from pathlib import Path

database = Path("~/Documents/radio-data/radio-pi/latest.db").expanduser()
connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
```

Do not run the ingestor against this snapshot. The Pi remains the only writer to the live database.

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

Use `simulate` only with an isolated test database.

```bash
radio-logger db-info
radio-logger db-backup
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

For development on Linux or macOS:

```bash
.venv/bin/python -m pip install ".[dev]"
.venv/bin/python -m pytest
```

On Windows, use `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`.

Coverage includes Maidenhead/distance/bearing, DXCC prefixes (9V JA VK YB BY HL HS DU W/K and portables), FT8 messages, UDP sample packets, and DB preservation of repeated callsigns.

DXCC prefix data is a bundled AD1C-format `cty.dat` (via the DJ1YFK `dxcc` distribution). Country names follow that file. Drop a newer `cty.dat` into `src/radio_logger/resources/cty.dat` to update.

## Deploy on radio-pi

This is the optional Linux service layout for a separate collection appliance. For the Windows school computer or a Linux desktop, use [the setup guide](docs/SETUP.md) instead.

The production layout keeps the wheel and virtual environment under `/opt/japan-hour-logger`. Configuration lives at `/etc/japan-hour-logger/receiver.yaml`. SQLite data, raw WSJT-X events, exports, and backups live under `/var/lib/japan-hour-logger` and belong to the `radio-logger` service account.

Install `deploy/receiver.yaml` and the units under `deploy/systemd/` as root. Build a virtual environment with Python 3.12 or newer, then install the wheel into that environment. Enable both `japan-hour-logger.service` and `japan-hour-logger-backup.timer`.

For the first deployment, run `deploy/import-existing-all-txt.sh` before you start or enable the logger service. The script takes an immutable copy of WSJT-X `ALL.TXT`, imports that copy once, starts the live UDP listener, and writes an import marker. It refuses to run when the marker or the production database exists. Run the script just after an FT8 decode cycle. If `ALL.TXT` grows before the HTTP health check passes, the script leaves a `review-required` marker instead of claiming a clean cutover.

The logger listens for WSJT-X UDP packets on `127.0.0.1:2237`. It serves the dashboard and API on port 8080 on every network interface. The backup timer uses SQLite's online backup API every day and keeps every backup.
