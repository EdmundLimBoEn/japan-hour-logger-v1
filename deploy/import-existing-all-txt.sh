#!/bin/sh
set -eu

source_file=${1:-/home/edmund/.local/share/WSJT-X/ALL.TXT}
state_dir=/var/lib/japan-hour-logger/imports
marker=$state_dir/all-txt-v1.state
snapshot=$state_dir/ALL-before-udp.txt
config=/etc/japan-hour-logger/receiver.yaml
logger=/opt/japan-hour-logger/venv/bin/radio-logger

if [ "$(id -u)" -ne 0 ]; then
  echo "run this command as root" >&2
  exit 1
fi
if [ ! -r "$source_file" ]; then
  echo "cannot read $source_file" >&2
  exit 1
fi
if [ -e "$marker" ]; then
  echo "historical import already attempted: $(cat "$marker")" >&2
  exit 1
fi
if systemctl is-active --quiet japan-hour-logger.service; then
  echo "japan-hour-logger.service must be stopped before the historical import" >&2
  exit 1
fi
if [ -e /var/lib/japan-hour-logger/radio.db ]; then
  echo "radio.db already exists; refusing a replay that could duplicate observations" >&2
  exit 1
fi

install -d -o radio-logger -g radio-logger -m 0750 "$state_dir"
printf 'started %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$marker"
chown radio-logger:radio-logger "$marker"
chmod 0640 "$marker"

previous_size=-1
stable=false
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  current_size=$(stat -c %s "$source_file")
  if [ "$current_size" = "$previous_size" ]; then
    stable=true
    break
  fi
  previous_size=$current_size
  sleep 1
done
if [ "$stable" != true ]; then
  printf 'failed %s source-not-stable-after-30s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$marker"
  echo "ALL.TXT did not remain stable for one second" >&2
  exit 1
fi

install -o radio-logger -g radio-logger -m 0440 "$source_file" "$snapshot"
runuser -u radio-logger -- "$logger" replay "$snapshot" --config "$config" --speed 0
systemctl start japan-hour-logger.service

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8080/health >/dev/null

snapshot_size=$(stat -c %s "$snapshot")
source_size=$(stat -c %s "$source_file")
if [ "$source_size" != "$snapshot_size" ]; then
  printf 'review-required %s source-grew-during-cutover snapshot=%s source=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$snapshot_size" "$source_size" > "$marker"
  echo "ALL.TXT grew during cutover; inspect the cutover interval before declaring success" >&2
  exit 1
fi

snapshot_sha256=$(sha256sum "$snapshot" | awk '{print $1}')
printf 'complete %s bytes=%s sha256=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$snapshot_size" "$snapshot_sha256" > "$marker"
chown radio-logger:radio-logger "$marker"
chmod 0440 "$marker"
