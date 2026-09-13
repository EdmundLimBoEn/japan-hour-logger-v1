# Orange Pi deployment receipt

Verified on 13 September 2026 in Asia/Singapore. Target `radio-pi`, Ethernet `192.168.0.12`, Orange Pi 3B ARM64, Armbian Debian Trixie, Python 3.13.5.

## Installed artifact

Japan Hour Logger 1.1.0 reads `/var/lib/radio-ft8/ALL.TXT` and serves the [LAN dashboard](http://192.168.0.12:8080/). It stores observations and the source cursor in `/var/lib/japan-hour-logger/radio.db`.

The deployed wheel is `japan_hour_logger-1.1.0-py3-none-any.whl`. Its SHA-256 is `b28c1ad61d0c1e00abb01b40c546025f67a466fa7578b23013941ac215c7f3a8`. The source wheel is retained in `/root/japan-hour-logger-deploy` on the Pi.

The previous installation is preserved in `/opt/japan-hour-logger/before-20260913T114028Z-c5BpMu`. Before deployment, the logger state directory contained empty directories and no database. Old receiver archives and logs were left in place.

## Data and recovery evidence

| Check | Observed result |
| --- | --- |
| Local suite | 136 tests passed on Python 3.13. |
| Wheel installation | Built successfully. Installed checks passed on both Mac and Pi, including UDP compatibility, JSON, CSV, analytics, raw events, backup, and restart persistence. |
| First source catch-up | 31 complete real RX lines matched 31 observations exactly once. SQLite integrity passed. |
| New reception after startup | Logger started at 19:40:55 SGT. The 19:41:00 decode `BU2AD YC3BRJ OI62`, SNR -19 dB, DT 1.9, DF 1069 Hz, matched the database, JSON API, CSV, and dashboard at 21.074 MHz on 15m. |
| Actual process recovery | Killing logger PID 7360 caused systemd to restart it as PID 7912 at 19:41:48. `NRestarts=1`. All 34 source lines then matched 34 observations with no duplicates. |
| Reboot startup | A controlled reboot changed boot ID from `a53002c3-0e0e-4145-a17d-9595fbd2f22d` to `08aa179e-4c99-40b3-af90-31346d3e1ce3`. All four services started automatically after chrony synchronized. No failed units remained. |
| Reboot persistence | All 34 pre-reboot observations retained their IDs, timestamps, messages, and fingerprints. The complete pre-reboot log remained a byte-for-byte prefix of the current source. After reboot, 37 source lines matched 37 database observations exactly once. |
| Reception after reboot | The 19:43:30 SGT decode `UA0QNE YC3BRJ OI62`, SNR -12 dB, DT 1.9, DF 2697 Hz, reached SQLite, JSON, and CSV at 21.074 MHz. |
| Source permissions | Current log set to `0640 radio-ft8:radio-ft8`, matching the rotation policy. The service continued reading through `SupplementaryGroups=radio-ft8` with `ReadOnlyPaths=/var/lib/radio-ft8` and `PrivateDevices=true`. |
| Rotation on ARM64 | An isolated temporary source and database on the Pi passed repeated-line, partial-line restart, unread archive recovery, replacement-file, truncate/regrow, malformed/TX, and repeated-restart checks. Production logs were not truncated or force-rotated. |
| Daily backup | Manual execution of the real backup service succeeded. Backup `radio-20260913T114138.759563Z.db` passed SQLite integrity and contained the source cursor. The timer is enabled for 03:15 SGT plus up to 30 minutes of random delay. |
| Browser | The live dashboard showed FILE LIVE, ALL.TXT, 15m, 21.074 MHz, real station rows, and rendered charts. |

The new cursor migration also passed an upgrade test against an existing Alembic-managed database after normal application startup had already created the cursor table. Existing observations survived that upgrade.

## Receiver preservation

SHA-256 checks passed unchanged for `/etc/radio-ft8.conf`, all three receiver units, the `decode`, `receive`, and `usb_audio.py` scripts, and `ft8report`.

After reboot, PSK Reporter resumed as `9V1SWL/OJ11WH` with reporter ID `101869785`. At 19:42:51 it replayed three recent stations and successfully sent a 256-byte UDP datagram. This verifies the uploader still runs. Prior server-side verification was preserved, not repeated by this logger deployment.

The hardware watchdog remained active. The persistent journal retained the prior boot, chrony synchronized, and `radio-time-online.timer` remained active. Temperature was 39.4°C during the post-reboot checks.

A 30.3-second sample with the dashboard open measured logger CPU at 1.85% of one core and cgroup memory at 97.8 MiB. SDR, decoder, and reporter CPU were 8.77%, 83.07%, and 0.21% of one core. All process IDs remained stable during the sample. This is a short operational sample, not a long-term load test.

The live receiver remains receive-only at 21.074 MHz with SDR serial `00000001`, callsign `9V1SWL`, and grid `OJ11WH`. The logger uses the existing decode output. No SDR, desktop, VNC, noVNC, or SDR++ process was added.

The current source retained the complete byte prefix saved before deployment. The actual producer's `append_text` function opens, writes, flushes, and closes ALL.TXT for each decode. The existing rotation policy keeps an uncompressed first archive and restarts decoder/reporting services. Those behaviors match the follower's rename recovery.

## Limits

A quiet band can show WAITING FOR DECODES while the source remains healthy. The receipt proves ingestion of received stations, not future propagation or Japanese station availability.

An outage spanning multiple rotations or compression of an unfinished archive needs a history recovery review. Copytruncate can destroy bytes before any consumer reads them. Production remains on rename/create rotation.

The Orange Pi was tested physically. The same Python wheel and service layout target 64-bit Raspberry Pi OS Trixie, but a physical Raspberry Pi installation was not tested.

The earlier unclean shutdown's cause remains unproven. This work does not attribute it to overheating.

Operational commands and recovery steps are in [the headless guide](HEADLESS.md).
