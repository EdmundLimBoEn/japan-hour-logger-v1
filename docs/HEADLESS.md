# Run the logger with the headless FT8 receiver

Open the [radio-pi dashboard](http://192.168.0.12:8080/) on your LAN. The logger consumes the receiver's ALL.TXT file and keeps its observations in SQLite on the SSD.

The receiver remains receive-only at 21.074 MHz on 15m. `/etc/radio-ft8.conf` owns callsign `9V1SWL`, grid `OJ11WH`, and SDR serial `00000001`. The logger does not configure or open the SDR.

## Install or update

Use Debian Trixie or 64-bit Raspberry Pi OS Trixie with Python 3.12 or newer and the `venv` module. Install the headless `radio-ft8` receiver first. Its `radio-ft8-decode`, `radio-ft8-sdr`, and `radio-ft8-report` services must already work.

The file follower starts at the beginning of the current log the first time it sees that source. Before installing against an existing experiment database, confirm that this same log has not already been imported. Do not use manual replay on a file already being followed. Restarting the follower uses its committed cursor and does not import earlier lines again.

Build the logger wheel on your development computer. Copy the wheel, `deploy/`, and `scripts/verify-headless.py` together to the Pi. From the copied directory, run as root:

```sh
deploy/install-headless.sh /absolute/path/to/japan_hour_logger-1.1.0-py3-none-any.whl
/opt/japan-hour-logger/venv/bin/python /opt/japan-hour-logger/verify-headless.py
```

The installer saves the previous virtual environment, config, and units in a timestamped `before-*` directory under `/opt/japan-hour-logger`. It backs up an existing standard-path database with the online backup command. It preserves the receiver configuration and logger settings other than `input`.

Set the input in `/etc/japan-hour-logger/receiver.yaml`:

```yaml
input:
  source: all_txt
  path: /var/lib/radio-ft8/ALL.TXT
  poll_seconds: 1.0
```

Keep the logger's `receiver.locator` equal to the verified receiver grid. Omit `input` to use the existing WSJT-X UDP setup on another computer.

## Check reception and startup

Connect over Ethernet if mDNS selects unreliable IPv6:

```sh
ssh -o HostKeyAlias=radio-pi.local root@192.168.0.12
```

Check the services and source:

```sh
systemctl is-active radio-ft8-sdr radio-ft8-decode radio-ft8-report japan-hour-logger
systemctl is-enabled japan-hour-logger japan-hour-logger-backup.timer
journalctl -u japan-hour-logger -n 30 --no-pager
tail /var/lib/radio-ft8/ALL.TXT
/opt/japan-hour-logger/venv/bin/python /opt/japan-hour-logger/verify-headless.py
```

The verifier compares complete real source lines with the database, API, CSV export, and analytics. It also checks SQLite integrity and dashboard assets. It does not inject test observations. A quiet band can leave the last decode old even while the input file is readable. Use the receiver service status and last decode time together.

Restart only the logger after changing its configuration:

```sh
systemctl restart japan-hour-logger
```

The logger unit has `PrivateDevices=true` and read-only access to `/var/lib/radio-ft8`. `SupplementaryGroups=radio-ft8` lets it read new logs created with mode `0640`. It does not depend on the removed `wsjtx.service`.

## Keep rotation and backups working

Keep `/etc/logrotate.d/radio-ft8` on its rename/create policy with `delaycompress`. The existing policy restarts the decoder and reporter to reopen the new file. The logger drains the previous file and follows the replacement. It can recover the saved inode from an uncompressed archive after a restart.

Live rename rotation uses POSIX filesystem behavior on Linux and macOS. On Windows, stop the follower before renaming its input file, then restart it. The Windows school setup continues to use UDP by default.

Do not switch production to `copytruncate`. Truncation detection protects normal recovery, but bytes lost by a copy/truncate race cannot be recovered by the logger. An outage spanning several rotations or compression of the unfinished archive needs a history recovery review. Keep all archives until that review is complete.

Run a backup and inspect the daily timer:

```sh
systemctl start japan-hour-logger-backup.service
systemctl list-timers japan-hour-logger-backup.timer
ls -lh /var/lib/japan-hour-logger/backups/
```

Backups include the source cursor because it is stored in the same SQLite database. Keep the decode archives with a database backup if you may need to catch up after restoring it. Never copy the live SQLite file directly while the service is running.

## Recover a failed update

Find the printed `before-*` path from the installer. Stop the logger and backup timer. Restore its saved `receiver.yaml` and systemd unit files to their original locations. Move the failed `/opt/japan-hour-logger/venv` to a new named directory, then extract `venv.tar.gz` under `/opt/japan-hour-logger`. Do not unpack over the failed environment because that leaves files from the failed update behind.

Run `systemctl daemon-reload`. Restore the enabled and active states recorded in `logger-enabled.txt`, `logger-active.txt`, `backup-enabled.txt`, and `backup-active.txt`. If the previous logger was disabled, keep it disabled until its old input is available.

Keep `/var/lib/japan-hour-logger` and `/var/lib/radio-ft8` intact. A code rollback does not require replacing or deleting observations. The receiver and PSK Reporter services can continue running throughout logger recovery.

The code uses portable Python and the same service layout on ARM64 Trixie. The Orange Pi deployment has a live verification record in [the deployment receipt](HEADLESS-VERIFICATION.md). A physical Raspberry Pi installation has not been tested.
