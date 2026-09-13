# Set up the school logger

This guide gets Japan Hour Logger collecting real FT8 decodes on the school's Windows computer. The same computer runs the receiver software, WSJT-X, and the logger. A Linux setup is included below.

The signal follows this path:

```text
Antenna and receiver or SDR
    -> receiver audio
    -> WSJT-X decodes FT8
    -> UDP on 127.0.0.1:2237
    -> Japan Hour Logger saves data/radio.db
    -> browser at http://127.0.0.1:8080
```

The logger starts at the UDP step. It cannot install an SDR driver, route audio, tune a receiver, or decode FT8. Keep the school's working receiver and audio setup. The hardware model and audio devices determine those instructions.

## Before you start

Have these ready on the computer you will leave at school:

- A working receiver and antenna, with audio reaching WSJT-X.
- Git, Python 3.12 or newer, and WSJT-X.
- An internet connection for the clone and first package installation.
- A local folder that your account can write to.
- Mains power and enough free disk space for the experiment.
- The school's actual Maidenhead locator, if known.

Use a local disk folder such as your user folder's `radio` directory. Keep the working folder outside OneDrive, a network share, and a USB drive. Copy completed backups to those locations instead. Paths with spaces are supported, but keep the folder name short.

If the school blocks software installation or time synchronization, have the school technician enable those before the session. The logger itself runs as your normal user.

## Install on Windows

### 1. Install the prerequisites

Install [Git for Windows](https://git-scm.com/install/windows). Keep the option that makes Git available from the command line.

Install Python through the [official Python Windows download page](https://www.python.org/downloads/windows/). With the Python Install Manager, open PowerShell and run:

```powershell
py install 3.12
```

If Python 3.12 is already installed through the older Python installer, skip that command. Both the current manager and the older launcher support `py -3.12`. See [Python's Windows installation instructions](https://docs.python.org/3/using/windows.html).

Close PowerShell after installation. Open a new PowerShell window from the Start menu and run:

```powershell
git --version
py -3.12 --version
```

Expect a Git version and `Python 3.12.x`. If `py` is unavailable but `python --version` shows Python 3.12 or newer, the setup helper can use `python`.

For the Windows 11 deployment, install the 64-bit Windows build of [WSJT-X Improved Plus](https://sourceforge.net/projects/wsjt-x-improved/files/WSJT-X_v3.2.0/). The release checked on 13 September 2026 is `wsjtx-3.2.0-win64_improved_PLUS_260908.exe`. Use 64-bit x64 Python 3.12, 3.13, or 3.14 with the pinned packages. Native Windows ARM64 Python is not covered by this installation.

### 2. Clone and install the logger

Run each line in PowerShell. Wait for a command to finish before running the next.

```powershell
New-Item -ItemType Directory -Force "$HOME\radio" | Out-Null
Set-Location "$HOME\radio"
git clone https://github.com/EdmundLimBoEn/japan-hour-logger-v1.git
Set-Location .\japan-hour-logger-v1
& '.\Setup Windows.cmd'
```

The final command creates `.venv`, installs the Python packages, and checks the installation. Wait for a successful result before continuing. The helper preserves `data` and `config\receiver.yaml` when you run it again.

You can also open the cloned folder in File Explorer and double-click **Setup Windows.cmd**. These helpers do not require a PowerShell execution-policy change.

### 3. Set the receiver details

Open the configuration file:

```powershell
notepad .\config\receiver.yaml
```

Under `receiver`, replace `locator: TODO` with the school's verified Maidenhead locator. Keep `TODO` if the locator is unknown. The logger still records decodes and countries, but cannot calculate distance or bearing.

Keep the remaining defaults for this setup:

```yaml
receiver:
  id: school-9v
  name: Japan Hour Receiver
  locator: TODO
  timezone: Asia/Singapore
udp:
  host: 127.0.0.1
  port: 2237
  multicast: false
http:
  host: 127.0.0.1
  port: 8080
```

This is an excerpt, not a replacement for the whole file. Keep the database, paths, and analytics sections already in the file. Preserve indentation with spaces. Save the file and close Notepad.

Do not copy a sample callsign or grid into the real station settings. Use the station's actual details. This logger does not need your own callsign to receive UDP reports.

### 4. Run the installation check

```powershell
& '.\Check Setup.cmd'
```

Expect a successful check. It tests the logger with temporary data and leaves the real observation database alone. A passing check proves the software path works. The live receiver check comes later.

Continue with [Prepare the receiver computer](#prepare-the-receiver-computer).

## Install on Linux

Run the receiver software, WSJT-X, and the logger on the same Linux computer. Use a regular terminal in your desktop session.

### 1. Check Python and Git

```bash
git --version
python3.12 --version
```

On Ubuntu 24.04, install the missing prerequisites with:

```bash
sudo apt update
sudo apt install git python3.12 python3.12-venv
```

Ubuntu 24.04 provides Python 3.12. Other distributions have different package names and available versions. See [Ubuntu's Python versions](https://ubuntu.com/developers/docs/reference/availability/python/) and [Python setup instructions](https://ubuntu.com/developers/docs/howto/python-setup/).

If your installed `python3` is 3.12 or newer, use `python3` instead of `python3.12` in the next step. Do not replace the operating system's Python or run `sudo pip`.

### 2. Clone and install

```bash
mkdir -p "$HOME/radio"
cd "$HOME/radio"
git clone https://github.com/EdmundLimBoEn/japan-hour-logger-v1.git
cd japan-hour-logger-v1
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install .
.venv/bin/python scripts/verify-install.py
```

Expect the installation check to pass. It uses temporary data. No virtual-environment activation is needed because each command names its Python executable.

Open `config/receiver.yaml` in your text editor. Set the school's verified locator as described in the Windows section. Keep both hosts at `127.0.0.1`, UDP port `2237`, and HTTP port `8080`.

Install WSJT-X from the [official downloads](https://wsjt.sourceforge.io/downloads.html) or your distribution's package manager if it is not already available.

## Prepare the receiver computer

### 1. Synchronize the clock

On Windows, open **Settings > Time & language > Date & time**. Enable **Set time automatically** and use **Sync now**. Confirm that the displayed date, time, and Singapore time zone are correct. See [Microsoft's date and time settings](https://support.microsoft.com/en-us/windows/experience/personalization/set-time-date-and-time-zone-settings-in-windows).

On Linux, enable automatic time synchronization in the desktop's date and time settings. On a system with `timedatectl`, inspect the result with:

```bash
timedatectl status
```

Look for `System clock synchronized: yes`. If it says `no`, fix synchronization before taking measurements. WSJT-X needs an accurate clock. The logger stores UTC timestamps and displays Singapore time, so leave the computer on its normal local time zone.

### 2. Keep the session running

Connect mains power. In the computer's power settings, prevent sleep or hibernation while plugged in for the collection period. The display may turn off. Keep a laptop lid open unless its lid-close action is configured to keep the computer awake.

Finish any pending restart before collection. After a reboot, sign in and start the receiver software, WSJT-X, and the logger again. This setup does not install an automatic background service.

### 3. Confirm that WSJT-X receives real decodes

Open the school's receiver software and WSJT-X. In WSJT-X, select **FT8**. In **File > Settings > Audio**, select the input that carries receiver audio. Use the school's working audio-device choice.

Keep **Enable Tx** off. Do not use **Tune**, automatic transmit features, or double-click decoded stations. Turn **Monitor** on and wait for real messages in **Band Activity**. WSJT-X documents these controls in its [user guide](https://wsjt.sourceforge.io/wsjtx-main_en.html).

Confirm that the dial frequency shown in WSJT-X matches the actual receiver frequency. The logger uses the WSJT-X frequency report to label each observation's band.

If **Band Activity** stays empty, fix the receiver, audio input, clock, or FT8 mode first. The logger cannot produce decodes when WSJT-X has none.

### 4. Send WSJT-X reports to the logger

In WSJT-X, open **File > Settings > Reporting** and set:

- Primary **UDP Server** to `127.0.0.1`.
- **UDP Server port number** to `2237`.
- **Accept UDP requests** to unchecked.

In Improved Plus, do not use **Secondary UDP Server** for this logger. That setting sends logged contacts rather than received decodes. Disable receive filters for the experiment because filtered messages can be omitted from UDP. No **Broadcast decodes** checkbox is required in this release. These behaviors were checked against the [Improved Plus release source](https://sourceforge.net/projects/wsjt-x-improved/files/WSJT-X_v3.2.0/Source%20code/wsjtx-3.2.0_improved_PLUS_260908.tgz/download).

Click **OK**. The UDP server sends decoded messages and program status to other software. Accepting UDP requests is for incoming control commands and is not needed here. See the [WSJT-X Reporting settings](https://wsjt.sourceforge.io/wsjtx-main_en.html#_reporting).

Leave existing WSJT-X logs in place. A QSO log is not required for this logger to collect received FT8 messages.

## Start the logger and prove live collection

On Windows, double-click **Start Logger.cmd**, or run:

```powershell
& '.\Start Logger.cmd'
```

On Linux, run from the cloned folder:

```bash
.venv/bin/python -m radio_logger run --config config/receiver.yaml
```

Keep that window open. Open [the dashboard](http://127.0.0.1:8080) on the same computer. The Windows helper opens it after startup succeeds.

Check these results in order:

1. The dashboard opens and shows the receiver name.
2. The **DB** field starts with `ok`.
3. WSJT-X continues to show new messages in **Band Activity**.
4. Within the next few receive cycles, matching messages appear under **Latest decodes**.
5. **Today > Decodes** and **15 min** increase while WSJT-X receives messages.
6. **Band** and **Dial** match the WSJT-X display after a status report arrives.
7. **Lost packets / writes** stays at `0 / 0` and no error banner appears.

The dashboard refreshes status and recent rows every five seconds. Charts refresh every fifteen seconds. A country or distance may be blank when the decode lacks enough information.

For the underlying health result, open [logger status](http://127.0.0.1:8080/api/status). Check `ok: true` and `db_writable: true`. `storage_error` and `udp_error` show active faults. `last_error` keeps the most recent historical issue for diagnosis, so it can remain after recovery. After live reception, `session_decodes` should increase. `udp_recently_seen` means a valid packet arrived in the last 60 seconds, so it can turn false during quiet periods.

Press **Ctrl+C** in the logger window to stop it. On Windows, answer `Y` if the terminal asks whether to terminate the batch job. Start the logger again. Confirm that the previous real decodes remain and that new ones arrive.

Create a backup using [the backup instructions](OPERATIONS.md#back-up-the-observations) before leaving the setup session.

Keep the receiver software, WSJT-X, and the logger open during collection. Closing the browser is fine. Closing the logger window stops collection. Continue with [daily operation and troubleshooting](OPERATIONS.md).

## Windows 11 acceptance checklist

Before leaving the computer collecting, complete each check:

- [ ] Run **Check Setup.cmd** and keep its PASS result.
- [ ] Record the installed WSJT-X Improved Plus version and `git rev-parse HEAD`.
- [ ] Confirm Windows clock synchronization, mains power, and sleep disabled while plugged in.
- [ ] Confirm primary UDP `127.0.0.1:2237`, FT8 monitoring, receive filters disabled, and transmit disabled.
- [ ] Confirm real WSJT-X messages appear on the dashboard, with the correct band and increasing counts.
- [ ] Change bands, wait for fresh status, and verify new observations have the new frequency.
- [ ] Close and restart WSJT-X. Confirm new decodes resume without restarting the logger.
- [ ] Stop the logger with Ctrl+C and let queued packets finish. Start it again and verify old rows and new reception.
- [ ] Run **Backup Data.cmd**, verify the copied backup, and copy it to another drive.
- [ ] Locate WSJT-X **File > Open log directory** and preserve `ALL.TXT` as an independent reception record.

After a power cut or Windows restart, sign in and reopen the receiver software, WSJT-X Improved Plus, and **Start Logger.cmd**. This repository does not install automatic login, restart Windows services, or control the receiver.
