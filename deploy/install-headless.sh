#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ] || [ "$#" -ne 1 ]; then
    echo "Usage as root: deploy/install-headless.sh /absolute/path/to/logger.whl" >&2
    exit 1
fi

wheel=$(realpath "$1")
deployment=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test -f "$wheel"
getent group radio-ft8 >/dev/null
test -d /var/lib/radio-ft8
python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer required"'

if ! id radio-logger >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/japan-hour-logger --shell /usr/sbin/nologin radio-logger
fi
install -d -o radio-logger -g radio-logger -m 0750 /var/lib/japan-hour-logger
install -d -o root -g radio-logger -m 0750 /etc/japan-hour-logger
install -d -m 0755 /opt/japan-hour-logger

stamp=$(date -u +%Y%m%dT%H%M%SZ)
rollback=$(mktemp -d "/opt/japan-hour-logger/before-${stamp}-XXXXXX")
echo "Saving the previous installation in $rollback"
trap 'result=$?; if [ "$result" -ne 0 ]; then echo "Installation failed. Saved files are in $rollback. See docs/HEADLESS.md to recover; receiver services were not changed." >&2; fi' 0
for unit in japan-hour-logger.service japan-hour-logger-backup.service japan-hour-logger-backup.timer; do
    if [ -f "/etc/systemd/system/$unit" ]; then
        cp -p "/etc/systemd/system/$unit" "$rollback/"
    fi
done
if [ -f /etc/japan-hour-logger/receiver.yaml ]; then
    cp -p /etc/japan-hour-logger/receiver.yaml "$rollback/receiver.yaml"
fi
systemctl is-enabled japan-hour-logger.service > "$rollback/logger-enabled.txt" 2>/dev/null || true
systemctl is-active japan-hour-logger.service > "$rollback/logger-active.txt" 2>/dev/null || true
systemctl is-enabled japan-hour-logger-backup.timer > "$rollback/backup-enabled.txt" 2>/dev/null || true
systemctl is-active japan-hour-logger-backup.timer > "$rollback/backup-active.txt" 2>/dev/null || true

for unit in japan-hour-logger-backup.timer japan-hour-logger-backup.service japan-hour-logger.service; do
    if systemctl cat "$unit" >/dev/null 2>&1; then
        systemctl stop "$unit"
    fi
done
if [ -d /opt/japan-hour-logger/venv ]; then
    tar -C /opt/japan-hour-logger -czf "$rollback/venv.tar.gz" venv
fi
if [ -x /opt/japan-hour-logger/venv/bin/radio-logger ] && [ -f /etc/japan-hour-logger/receiver.yaml ]; then
    if [ -f /var/lib/japan-hour-logger/radio.db ]; then
        runuser -u radio-logger -- /opt/japan-hour-logger/venv/bin/radio-logger db-backup --config /etc/japan-hour-logger/receiver.yaml
    fi
fi
if [ ! -x /opt/japan-hour-logger/venv/bin/python ]; then
    python3 -m venv /opt/japan-hour-logger/venv
fi
/opt/japan-hour-logger/venv/bin/python -m pip install "$wheel"
/opt/japan-hour-logger/venv/bin/python -m pip install --no-deps --force-reinstall "$wheel"
/opt/japan-hour-logger/venv/bin/python -m pip check

/opt/japan-hour-logger/venv/bin/python - "$deployment/receiver.yaml" <<'PY'
import os
import shutil
import sys
from pathlib import Path

import yaml

path = Path('/etc/japan-hour-logger/receiver.yaml')
if not path.exists():
    shutil.copyfile(sys.argv[1], path)
data = yaml.safe_load(path.read_text())
data['input'] = {'source': 'all_txt', 'path': '/var/lib/radio-ft8/ALL.TXT', 'poll_seconds': 1.0}
temporary = path.with_suffix('.yaml.new')
temporary.write_text(yaml.safe_dump(data, sort_keys=False))
shutil.chown(temporary, user='root', group='radio-logger')
temporary.chmod(0o640)
os.replace(temporary, path)
PY

install -m 0644 "$deployment"/systemd/japan-hour-logger* /etc/systemd/system/
install -m 0755 "$deployment/../scripts/verify-headless.py" /opt/japan-hour-logger/verify-headless.py
systemctl daemon-reload
systemctl enable --now japan-hour-logger.service japan-hour-logger-backup.timer
echo "Saved the previous installation in $rollback"
echo "Verify: /opt/japan-hour-logger/venv/bin/python /opt/japan-hour-logger/verify-headless.py"
