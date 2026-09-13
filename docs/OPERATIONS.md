# Run and maintain the school logger

Complete [the setup guide](SETUP.md) before collecting observations. Run the commands below from the cloned `japan-hour-logger-v1` folder. Windows examples use PowerShell. Linux examples use a regular terminal.

## Start a collection session

1. Connect mains power and confirm the clock is synchronized.
2. Start the receiver software with the usual audio route.
3. Start WSJT-X with **FT8** and **Monitor** enabled.
4. Keep transmission disabled.
5. Start Japan Hour Logger.

On Windows, double-click **Start Logger.cmd**, or run:

```powershell
& '.\Start Logger.cmd'
```

On Linux, run:

```bash
.venv/bin/python -m radio_logger run --config config/receiver.yaml
```

Open [the dashboard](http://127.0.0.1:8080). Confirm that **Latest decodes** receives the same new messages as WSJT-X and that **DB** says `ok`. Leave the logger terminal open. The browser can be closed.

Start one logger instance for this receiver. Another program such as GridTracker or another logger may already own UDP port `2237`. See [Resolve a port conflict](#resolve-a-port-conflict) if startup fails.

The logger has no automatic restart in this desktop setup. After a reboot, sign in and repeat these steps.

## Stop collection

Press **Ctrl+C** in the logger terminal. Wait for the process to stop before shutting down the computer. On Windows, answer `Y` if the terminal asks whether to terminate the batch job.

Stop WSJT-X and the receiver software when reception is finished. Existing observations remain on disk.

## Check the current state

Open [logger status](http://127.0.0.1:8080/api/status) while the logger runs. A healthy local service reports `ok: true`, `db_writable: true`, and no active `storage_error` or `udp_error`. `last_error` keeps the most recent historical issue for diagnosis, so it can remain after recovery. Real reception increases `session_decodes` and updates `last_decode_at`.

For stored totals, run the following on Windows:

```powershell
.\.venv\Scripts\python.exe -m radio_logger status --config config/receiver.yaml
```

On Linux, run:

```bash
.venv/bin/python -m radio_logger status --config config/receiver.yaml
```

This reports the selected database, receiver, total decodes, and recent rows. A local `status` command can initialize a missing database, so use the live URL when checking whether an already running service is reachable.

To check the software without adding test observations, run **Check Setup.cmd** on Windows. On Linux, run `.venv/bin/python scripts/verify-install.py`. The installation check uses an isolated temporary database.

## Back up the observations

Keep a backup outside the receiver computer after each collection day and before any update. A backup on the same disk does not protect against losing that disk.

On Windows, double-click **Backup Data.cmd**, or run:

```powershell
& '.\Backup Data.cmd'
```

The helper creates a database backup and a matching effective configuration copy under `data/backups`. The copy includes `RADIO_LOGGER_*` overrides and keeps relative storage paths portable. The helper prints both paths. The database backup is safe while the logger runs.

On Linux, use SQLite's online backup command while the logger runs, or after stopping it:

```bash
.venv/bin/python -m radio_logger db-backup --config config/receiver.yaml
```

The command prints the new `.db` file under `data/backups`. Copy that exact backup file and the configuration copy to your backup drive or storage folder. On Linux, copy `config/receiver.yaml` separately. To archive the raw events as well, stop the logger and copy `data/raw/wsjtx`. Save the application's revision with:

```text
git rev-parse HEAD
```

Keep WSJT-X's own `ALL.TXT` separately if you need its independent reception history. You can find its directory through WSJT-X's **File > Open log directory** menu.

Do not copy only the live `data/radio.db` while collection is running. SQLite may have recent observations in `radio.db-wal`. The backup command captures a consistent database through SQLite's backup API. See [SQLite's backup documentation](https://www.sqlite.org/backup.html).

Open the copied backup read-only to verify it. Set `backup` to the exact copied file path. On Windows, run:

```powershell
$backup = 'D:\radio-backups\your-backup-file.db'
.\.venv\Scripts\python.exe -c "import pathlib,sqlite3,sys; p=pathlib.Path(sys.argv[1]).resolve(); c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0]); print('observations:',c.execute('SELECT COUNT(*) FROM observations').fetchone()[0]); c.close()" "$backup"
```

On Linux, run:

```bash
backup='/path/to/copied-backup.db'
.venv/bin/python -c "import pathlib,sqlite3,sys; p=pathlib.Path(sys.argv[1]).resolve(); c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0]); print('observations:',c.execute('SELECT COUNT(*) FROM observations').fetchone()[0]); c.close()" "$backup"
```

Expect `ok` and the observation count. Keep the original backup if verification fails. Do not replace the working database with an unverified copy.

## Export observations for a spreadsheet

On Windows, double-click **Export CSV.cmd**. The helper writes a new timestamped file under `data/exports` and prints its path.

On either operating system, open [Download CSV](http://127.0.0.1:8080/api/export/csv) while the logger runs. This exports all stored observations to a browser download.

To export a specific period, use `--from` and `--to`. The start is included and the end is excluded. Include the time-zone offset so the date is unambiguous.

For the Singapore school day of 14 September 2026, run the following on Windows:

```powershell
.\.venv\Scripts\python.exe -m radio_logger export --config config/receiver.yaml --from 2026-09-14T00:00:00+08:00 --to 2026-09-15T00:00:00+08:00 --output data/exports/school-2026-09-14.csv
```

On Linux, run:

```bash
.venv/bin/python -m radio_logger export --config config/receiver.yaml --from 2026-09-14T00:00:00+08:00 --to 2026-09-15T00:00:00+08:00 --output data/exports/school-2026-09-14.csv
```

Change the dates and filename for a different day. An existing CSV at the requested output path is overwritten. Keep a different filename for each export you want to retain.

A CSV is for analysis. Keep the database backup as well, because the CSV does not preserve the whole database.

## Update the application

1. Make and verify a backup.
2. Stop the logger.
3. Record `git rev-parse HEAD` with the backup.
4. Run `git status --short`.

If you have local code changes, resolve those before continuing. For a change only to `config/receiver.yaml`, save a copy outside the repository. If Git refuses to pull because that file would be overwritten, stop and keep your copy. Do not use `git reset --hard` to force the update.

On Windows, run:

```powershell
git pull --ff-only
& '.\Setup Windows.cmd'
& '.\Check Setup.cmd'
& '.\Start Logger.cmd'
```

On Linux, run:

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install .
.venv/bin/python scripts/verify-install.py
.venv/bin/python -m radio_logger run --config config/receiver.yaml
```

Run each line only after the previous command succeeds. Installation requires internet access. Recheck the configured locator, stored counts, and a new real decode after the update.

If a check fails, keep the logger stopped and preserve the error text. Keep both the pre-update backup and the current data folder. Do not solve an update failure by deleting data or replaying the whole history.

## Restore a backup or move to another computer

Use a fresh clone in a new directory to avoid overwriting the existing database.

1. Stop the old logger if it still runs.
2. Verify the backup's integrity and record its observation count.
3. Follow [the setup guide](SETUP.md) in a new folder or on the new computer. Stop before the first real logger start.
4. Copy the backed-up YAML configuration into the new clone as `config/receiver.yaml`. Rename the timestamped configuration copy from **Backup Data.cmd** to `receiver.yaml`.
5. Create a `data` folder in the new clone if it is missing.
6. Copy the verified backup into that folder with the name `radio.db`.
7. Copy the archived raw files into `data/raw/wsjtx` if needed.

If `data/radio.db` already exists in the destination, stop. Pick a new clone directory instead of replacing it. Do not copy `radio.db-wal` or `radio.db-shm` from a different database beside the restored backup.

For the default setup, confirm that the new `receiver.yaml` still uses `sqlite:///data/radio.db` and local relative paths. Replace old machine-specific absolute paths before starting. Keep the receiver ID if this is the same station and experiment.

Run the local `status` command and compare the count with the verified backup. Start the new logger, confirm old rows are present, then check a new WSJT-X decode. Keep the original folder and backup until the restored installation has been verified.

Install a new `.venv` on the destination. Do not copy a Windows virtual environment to Linux or move an existing virtual environment between directories.

## Import old WSJT-X history once

The logger does not automatically import decodes from before it started. Normal setup starts with a clean observation database.

If you need historical reception, take a backup first. Use a separate clone and database to inspect the import before adding anything to the experiment. Stop WSJT-X before copying `ALL.TXT` so the source cannot change during the copy.

In the separate clone, run the following on Windows:

```powershell
.\.venv\Scripts\python.exe -m radio_logger replay 'C:\radio-import\ALL.TXT' --config config/receiver.yaml
```

On Linux, run:

```bash
.venv/bin/python -m radio_logger replay '/path/to/copied/ALL.TXT' --config config/receiver.yaml
```

Record the source file, its dates, the import time, and the printed observation count. Import each file once. Replaying it again adds observations again, and overlaps with live collection can double-count receptions. The short UDP duplicate check is not an import-history check.

Do not run `seed` or `simulate` against the school's real database. Use the isolated installation check for software testing.

## Fix an installation problem

If `git`, `py`, or `python` is not recognized, close the terminal and open it again after installation. Check the prerequisite version commands in [the setup guide](SETUP.md#1-install-the-prerequisites).

If Python opens the Microsoft Store or reports an older version, install an actual Python 3.12 or newer runtime. Run `py -3.12 --version` before retrying setup. Do not change PowerShell's execution policy for these CMD helpers.

If setup reports a download, certificate, or proxy error, check the school internet connection and proxy settings. Preserve the full error for the technician. Do not disable TLS verification. First installation needs package downloads even when you already have the source code.

If Python reports a missing `venv` or `ensurepip` module on Linux, install the matching distribution package, such as `python3.12-venv` on Ubuntu 24.04.

If you see access denied or database write errors, check free disk space and the folder's write permission. Run from your own local folder, outside OneDrive and `Program Files`. Keep your existing data intact when moving the installation.

## Resolve a port conflict

An occupied HTTP port can produce `address already in use` or Windows error `10048`. First check whether your logger is already running at [the dashboard](http://127.0.0.1:8080). Reuse that window instead of starting another copy.

To inspect owners on Windows, run:

```powershell
Get-NetUDPEndpoint -LocalPort 2237
Get-NetTCPConnection -LocalPort 8080 -State Listen
```

Use the returned `OwningProcess` value with `Get-Process -Id NUMBER`. On Linux, run `ss -lupn` for UDP listeners and `ss -ltpn` for TCP listeners.

Do not kill an unidentified process. If another application needs UDP `2237`, choose a free UDP port such as `2238` and set it in both `config/receiver.yaml` and WSJT-X Reporting. Restart the logger. WSJT-X sends to the configured destination, so changing the destination can stop reports to another logger.

If only HTTP `8080` is occupied, change `http.port` to a free port such as `8081`, restart, and open `http://127.0.0.1:8081`.

Keep `udp.host` and `http.host` at `127.0.0.1` for a computer running all the software. No router port forwarding is needed. If school security software blocks local traffic, ask the technician to allow only the required loopback traffic for WSJT-X and the repository's Python executable. Do not disable the firewall or expose these ports to the school network.

## Fix missing decodes or empty charts

If WSJT-X has no new **Band Activity**, inspect the receiver, audio selection, FT8 mode, Monitor state, and clock synchronization. See the [WSJT-X user guide](https://wsjt.sourceforge.io/wsjtx-main_en.html) for receiver-side settings.

If WSJT-X decodes but the logger does not, confirm the UDP destination and port match. Restart WSJT-X once after the logger is running so it sends current status. Inspect `last_error` at [logger status](http://127.0.0.1:8080/api/status) and the terminal error text.

If Band or Dial is blank, wait for a WSJT-X status report. Verify the dial frequency in WSJT-X. The logger cannot infer the receiver's frequency from audio alone.

If old rows appear but charts are empty, check their dates. Dashboard charts and **Today** use the current Singapore day. **Latest decodes** can show earlier history. The dashboard has no historical date picker. Use the CSV export or the CLI for an older period:

```text
radio-logger analyze japan-hour --from 2026-09-14T00:00:00+08:00 --to 2026-09-15T00:00:00+08:00 --bucket 15
```

Use `.\.venv\Scripts\python.exe -m radio_logger` on Windows or `.venv/bin/python -m radio_logger` on Linux in place of `radio-logger` if the environment is not activated.

If distance is blank, confirm the receiver locator is real and the transmitting station has a known grid. A country can be identified without a grid. The logger leaves unknown distances blank.

If Japan totals are zero while other decodes arrive, keep collecting. Software cannot guarantee Japanese stations are receivable during a particular period.

If the dashboard says **IDLE** or stops saying **UDP LIVE**, inspect new rows as well. The live indicator expires after 60 seconds without any packet. Heartbeats and decodes have different timing.

If the browser page fails, confirm the terminal is still running and use `http`, not `https`. Open [health](http://127.0.0.1:8080/health). After installation, the dashboard's scripts and charts are served locally and do not need a chart CDN.

## Respond to collection errors

Keep the console visible during collection. The dashboard reports database errors, disconnected HTTP, queued packets, and lost packets or writes. Stale charts show a warning. A valid heartbeat shows that WSJT-X is reachable but does not prove that audio is being decoded. Confirm new rows too.

If **Lost packets / writes** increases, preserve WSJT-X `ALL.TXT` and the logger's raw event files. Check disk space, disk health, write permissions, and other applications locking SQLite. Storage retries are bounded. Queued packets stay in memory until processed, so a forced process exit or power loss can lose packets that have not committed. Normal Ctrl+C drains the queue before closing the database and can take longer on a slow disk.

Do not reimport a whole overlapping history into the experiment. Replaying a file remains an append operation. Inspect the missing time interval in an isolated database, then import only records known to be absent. UDP packets lost before reaching the logger exist only in WSJT-X's independent log, if that log was enabled. The logger excludes UDP replay and WAV-file playback from live observations.

The logger retries an unexpectedly closed UDP socket once per second. A port already occupied at startup produces an error instead of competing for packets. A second collector, history importer, seed command, or simulator cannot write while a live logger holds the database lock. Backup and export remain available during collection.

SQLite uses WAL with FULL synchronization. Backups become final `.db` files only after verification and flush. CSV exports replace their destination only after the complete export succeeds. None of these protects against a failed physical disk, so keep verified backups on another device.

Setup preserves an unusable Python environment as `.venv.broken-*` before rebuilding it. Keep the old environment until the replacement passes **Check Setup.cmd**. If Setup cannot safely inspect an existing installation because a dependency is missing, close every logger window, rename `.venv` to an unused backup name, and run **Setup Windows.cmd** again. Keep `config` and `data`. Always stop the logger before updating, especially when upgrading a release that predates the database lock.
