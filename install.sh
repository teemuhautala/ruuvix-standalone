#!/usr/bin/env bash
# Installs and starts ruuvix: installs Docker (if missing) via Docker's
# official get.docker.com script, configures the host's BlueZ stack for
# RuuviTag scanning, bootstraps local config files, and starts the
# container via docker compose.
#
# Prefer downloading and reading this script before running it, since it
# needs root for the Docker install and the bluetooth config:
#   curl -fsSL <repo-url>/install.sh -o install.sh
#   less install.sh
#   ./install.sh
#
# Re-running is safe: every step is idempotent and skips work already done.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
  esac
done

confirm() {
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    return 0
  fi
  read -r -p "$1 [Enter to continue, Ctrl-C to abort] " _ < /dev/tty
}

echo "== ruuvix installer =="
echo "This will, as needed:"
echo "  1. install Docker via https://get.docker.com (runs as root)"
echo "  2. install/configure bluez on the host and restart the bluetooth service"
echo "  3. create local config files under ./app from the .example templates"
echo "  4. build and start the ruuvix container via docker compose"
confirm "Continue?"

# ---------------------------------------------------------------------------
# 1. Docker
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found."
  confirm "About to download and run https://get.docker.com as root. Continue?"
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
else
  echo "Docker already installed, skipping."
fi

if ! groups "$USER" | grep -qw docker; then
  echo "Adding $USER to the docker group..."
  sudo usermod -aG docker "$USER"
  cat <<'EOF'

You were just added to the "docker" group, but that only takes effect in a
new login session. Log out and back in (or run `newgrp docker`), then re-run
this script:

  ./install.sh
EOF
  exit 0
fi

# ---------------------------------------------------------------------------
# 2. Host bluetooth setup (best-effort, OS-agnostic beyond the apt install)
# ---------------------------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
  if ! dpkg -s bluez >/dev/null 2>&1; then
    echo "Installing bluez..."
    sudo apt-get update -y
    sudo apt-get install -y bluez
  else
    echo "bluez already installed, skipping."
  fi
else
  echo "No apt-get found: please make sure bluez and D-Bus are installed and running."
fi

MAIN_CONF="/etc/bluetooth/main.conf"
if [[ -f "$MAIN_CONF" ]]; then
  if ! grep -q "# BEGIN ruuvix" "$MAIN_CONF" 2>/dev/null; then
    echo "Tuning $MAIN_CONF for RuuviTag scanning..."
    sudo tee -a "$MAIN_CONF" >/dev/null <<'EOF'

# BEGIN ruuvix
[General]
Experimental = true
DisablePlugins = pnat
# END ruuvix
EOF
  else
    echo "$MAIN_CONF already tuned for ruuvix, skipping."
  fi

  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl restart bluetooth || echo "Warning: could not restart bluetooth service, you may need to do this manually."
  fi
else
  echo "Warning: $MAIN_CONF not found, skipping bluetooth tuning. Make sure BlueZ is installed."
fi

# ---------------------------------------------------------------------------
# 3. Config bootstrap
# ---------------------------------------------------------------------------
NEW_CONFIG=0
if [[ ! -f app/config.json ]]; then
  cp app/config.json.example app/config.json
  NEW_CONFIG=1
fi
[[ -f app/ruuvi-names.properties ]] || cp app/ruuvi-names.properties.example app/ruuvi-names.properties
[[ -f app/ruuvi-corrections.json ]] || cp app/ruuvi-corrections.json.example app/ruuvi-corrections.json

if [[ "$NEW_CONFIG" -eq 1 ]]; then
  INFLUXDB_HOST="${RUUVIX_INFLUXDB:-}"
  while [[ ! "$INFLUXDB_HOST" =~ ^[^:[:space:]]+:[0-9]+$ ]]; do
    if [[ -n "$INFLUXDB_HOST" ]]; then
      echo "Invalid format, expected host:port (e.g. 192.168.1.10:8086), got: $INFLUXDB_HOST"
    fi
    read -r -p "InfluxDB host:port (e.g. 192.168.1.10:8086): " INFLUXDB_HOST < /dev/tty
  done
  python3 - "$INFLUXDB_HOST" <<'EOF'
import json, sys
path = "app/config.json"
with open(path) as f:
    conf = json.load(f)
conf["influxdb"] = sys.argv[1]
with open(path, "w") as f:
    json.dump(conf, f, indent=4)
    f.write("\n")
EOF
  echo "Wrote InfluxDB target ($INFLUXDB_HOST) to app/config.json."
else
  echo "app/config.json already exists, leaving it untouched."
fi

if [[ ! -f .env ]]; then
  cat > .env <<EOF
PUID=$(id -u)
PGID=$(id -g)
EOF
fi

# ---------------------------------------------------------------------------
# 4. Build and start
# ---------------------------------------------------------------------------
echo "Building and starting ruuvix..."
docker compose up -d --build

cat <<'EOF'

== ruuvix is running ==

Useful next steps:
  Discover nearby tag MACs:
    docker compose run --rm ruuvix --find

  Add discovered tags to app/ruuvi-names.properties (MAC=Name), then restart:
    docker compose restart

  Calibrate tags that should read the same as each other:
    docker compose run --rm ruuvix --calibrate <name1> <name2> ...

  View logs:
    docker compose logs -f

  ONE-TIME, MANUAL, DESTRUCTIVE: create/reset the InfluxDB "ruuvix" database.
  This drops the database if it already exists, so only run it once against
  a fresh InfluxDB instance, never as a routine step:
    docker compose run --rm ruuvix --init
EOF
