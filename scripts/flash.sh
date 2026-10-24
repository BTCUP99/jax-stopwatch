#!/bin/zsh
# Flash the Jax firmware to an M5Stack StopWatch over USB.
#   scripts/flash.sh                    update: download the release app image and flash it
#   scripts/flash.sh --first-install    first time: upstream factory image (bootloader, partitions,
#                                       built-in face images), then the Jax app on top
#   scripts/flash.sh path/to/app.bin    flash a local app image (e.g. your own build)
# Updates write only the app partition (0x10000): Wi-Fi pairing and uploaded avatars stay.
# --first-install erases the whole flash. Back up the factory firmware first (docs/setup.md).
set -euo pipefail
cd "${0:A:h}/.."
VERSION=0.3.0
UPSTREAM_FACTORY=https://github.com/Corvelis/stackchan-pet-fw/releases/download/v0.4.1/stackchan_stopwatch_factory.bin
UPSTREAM_FACTORY_SHA256=ee7aee7648a0e60fb348cd85c6017c362e4d8fb3cf8b2bbbe64888c2e47061f3

first_install=0
IMAGE=
for arg in "$@"; do
  case "$arg" in
    --first-install) first_install=1 ;;
    *) IMAGE=$arg ;;
  esac
done

mkdir -p firmware-dist
if [[ -z "$IMAGE" ]]; then
  IMAGE=firmware-dist/jax-stopwatch-$VERSION.bin
  if [[ ! -f "$IMAGE" ]]; then
    base=https://github.com/BTCUP99/jax-stopwatch/releases/download/v$VERSION
    curl -fL -o "$IMAGE" "$base/jax-stopwatch-$VERSION.bin"
    curl -fL -o "$IMAGE.sha256" "$base/jax-stopwatch-$VERSION.bin.sha256"
  fi
  (cd firmware-dist && shasum -a 256 -c "${IMAGE:t}.sha256")
fi
if (( first_install )); then
  FACTORY=firmware-dist/stackchan_stopwatch_factory-v0.4.1.bin
  [[ -f "$FACTORY" ]] || curl -fL -o "$FACTORY" "$UPSTREAM_FACTORY"
  echo "$UPSTREAM_FACTORY_SHA256  $FACTORY" | shasum -a 256 -c
fi

ports=(${(f)"$(.venv/bin/python -c 'from serial.tools import list_ports
for p in list_ports.comports():
    if p.vid == 0x303A and p.pid == 0x1001: print(p.device)')"})
if (( ${#ports} != 1 )); then
  echo "Connect exactly one StopWatch over USB (found ${#ports})." >&2
  exit 1
fi
esptool=(uv run --with esptool esptool --chip esp32s3 --port "${ports[1]}" --baud 921600)

# The bridge holds the serial port; stop it for the flash and bring it back afterwards.
was_running=0
if .venv/bin/python src/manage.py status | grep -q '"phase"'; then
  was_running=1
  .venv/bin/python src/manage.py stop
fi
if (( first_install )); then
  read -q "?This erases the whole StopWatch flash. Continue? [y/N] " || { echo; exit 1; }
  echo
  $esptool write-flash 0x0 "$FACTORY"
  sleep 3
fi
$esptool write-flash 0x10000 "$IMAGE"
if (( was_running )); then
  sleep 3
  # Hand the bridge back to the login agent when autostart is on, so crash restarts keep working.
  launchctl kickstart "gui/$(id -u)/io.github.jax-stopwatch.bridge" 2>/dev/null || .venv/bin/python src/manage.py start
fi
