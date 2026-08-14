# ruuvix

Scans [RuuviTag](https://ruuvi.com/) BLE sensors and writes their measurements
to an InfluxDB 1.x database, on a schedule. Runs as a Docker container.

## How it works

BLE scanning goes through the host's own BlueZ stack over D-Bus (via
`ruuvitag_sensor`'s `bleak` backend), not raw Bluetooth device passthrough.
That means the container just needs access to the host's D-Bus socket and
BlueZ setup — it never touches `/dev/` Bluetooth device nodes directly. The
container runs with `--privileged --network host` mainly to keep this simple
and match a Bluetooth setup that's been running reliably for a long time; a
future hardening pass could try narrowing those, since the D-Bus/bleak path
likely doesn't strictly need them.

## Prerequisites

- A Linux host with Bluetooth hardware, reachable by RuuviTag(s) nearby.
- An existing InfluxDB 1.x instance (host:port) you can point this at. This
  project does not bundle or manage InfluxDB itself.
- `sudo` access (the installer installs Docker and bluez, and edits
  `/etc/bluetooth/main.conf`).

## Quick start

```bash
git clone <this-repo-url> ruuvix
cd ruuvix
less install.sh   # read it before running — it needs root for Docker/bluez
./install.sh
```

The installer will:

1. Install Docker (via Docker's official `get.docker.com` script) if it's
   not already present.
2. Install `bluez` and tune `/etc/bluetooth/main.conf` for tag scanning
   (`Experimental = true`, `DisablePlugins = pnat`), then restart the
   `bluetooth` service.
3. Create `app/config.json`, `app/ruuvi-names.properties`, and
   `app/ruuvi-corrections.json` from their `.example` templates, prompting
   you for your InfluxDB `host:port`.
4. Build and start the container with `docker compose up -d --build`.

Re-running `./install.sh` is safe — every step skips work that's already
been done.

Non-interactive installs (e.g. scripted provisioning) can pass `-y`/`--yes`
and set `RUUVIX_INFLUXDB=host:port` to skip prompts.

## Configuring your tags

Nothing gets written to InfluxDB for a tag until it has a name. Discover the
MAC addresses of tags nearby:

```bash
docker compose run --rm ruuvix --find
```

Add each one to `app/ruuvi-names.properties` as `MAC=Name`, then:

```bash
docker compose restart
```

## Calibrating tags

If you have multiple tags you'd like to read the same as each other (e.g.
sitting next to one another), calibrate them:

```bash
docker compose run --rm ruuvix --calibrate <name1> <name2> ...
```

This writes per-tag offsets to `app/ruuvi-corrections.json`, applied to all
future measurements.

## First-time database setup

**One-time, manual, and destructive** — this drops and recreates the
`ruuvix` InfluxDB database, so only run it once against a fresh InfluxDB
instance, never as a routine step (a second run will delete your history):

```bash
docker compose run --rm ruuvix --init
```

## Operating

```bash
docker compose logs -f      # tail logs
docker compose restart      # apply config changes
docker compose down         # stop
```

`app/config.json` fields:

| field | meaning |
|---|---|
| `influxdb` | `host:port` of your InfluxDB instance |
| `scan_rate` | seconds between measurements |
| `retention` | InfluxDB retention policy duration, e.g. `53w` |
| `debug` | verbose logging |

## Using plain `docker run` instead of docker compose

`install.sh` and `docker-compose.yml` are the recommended path, but the
container is just as easy to run directly if you'd rather skip compose.
`app/config.json` etc. still need to exist first — copy the `.example`
files yourself, or run `install.sh` once (which also sets up Docker/bluez
and starts things via compose — `docker compose down` afterwards if you'd
rather manage the container with plain `docker run`).

Required packages (`install.sh` installs these for you; here's the manual
equivalent on Debian/Ubuntu):

- `docker.io` — Docker engine and CLI
- `bluez` — the host Bluetooth stack `ruuvitag_sensor` talks to over D-Bus

```bash
sudo apt-get update
sudo apt-get install -y docker.io bluez
```

```bash
# build the image (rerun after changing requirements.txt)
docker build -t ruuvix .

# run it
docker run -d \
  --name ruuvix \
  --restart unless-stopped \
  --network host \
  --uts host \
  --privileged \
  --user "$(id -u):$(id -g)" \
  -v "$(pwd)/app:/app" \
  -v /var/run/dbus/:/var/run/dbus/:z \
  -e RUUVI_BLE_ADAPTER=bleak \
  ruuvix --run
```

`--network host` and `--uts host` give the container the host's D-Bus access
and hostname (the latter keeps the InfluxDB `client` tag consistent).
`--user` avoids root-owned files showing up under `app/`.

One-off commands (find tags, calibrate, init) work the same way, just with
`run --rm` instead of `run -d`:

```bash
docker run --rm -v "$(pwd)/app:/app" -v /var/run/dbus/:/var/run/dbus/:z \
  --network host --privileged ruuvix --find
```

## File layout

```
install.sh              bootstraps Docker/bluez/config and starts the stack
Dockerfile               builds the Python runtime only — no app code baked in
docker-compose.yml        runs the container, bind-mounting ./app
requirements.txt          pinned Python dependencies
app/
  ruuvix.py                the collector itself
  config.json.example       -> app/config.json        (gitignored, real config)
  ruuvi-names.properties.example -> app/ruuvi-names.properties (gitignored)
  ruuvi-corrections.json.example -> app/ruuvi-corrections.json (gitignored)
```

`app/` is bind-mounted into the container at `/app`, so editing `ruuvix.py`
or any config file takes effect on the next `docker compose restart` — no
image rebuild needed.
