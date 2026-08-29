# ruuvix

Scans [RuuviTag](https://ruuvi.com/) BLE sensors and writes their measurements
to an InfluxDB 1.x database, on a schedule. Runs as a Docker container.

## How it works

BLE scanning goes through the host's own BlueZ stack over D-Bus (via
`ruuvitag_sensor`'s `bleak` backend), not raw Bluetooth device passthrough.
That means the container just needs access to the host's D-Bus socket — it
never touches `/dev/` Bluetooth device nodes or opens raw sockets itself, so
it doesn't need `--privileged` or `--network host`. The default setup drops
all Linux capabilities (`cap_drop: [ALL]`) and runs as an unprivileged
container user; `--network host`/`--privileged` are kept only as a
documented, commented-out fallback in `docker-compose.yml` in case some
environment's D-Bus/BlueZ setup needs the extra access — if scanning finds
no tags under the default settings, try that fallback before assuming
something else is wrong.

## Prerequisites

- A Linux host with Bluetooth hardware, reachable by RuuviTag(s) nearby.
- On Raspberry Pi: a Pi 2 or newer (including Zero 2 W), running either
  64-bit Raspberry Pi OS (`arm64`) or 32-bit Raspberry Pi OS (`arm/v7`).
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

## Raspberry Pi

No separate Pi Dockerfile is needed. The official Python base image is
multi-architecture, and this project's Dockerfile handles the one dependency
that must be compiled on 32-bit ARM. On a Pi, the normal quick start builds
for the Pi's native architecture automatically:

```bash
./install.sh
```

For a manual install, use the same command as on any other host:

```bash
docker compose up -d --build
```

The first 32-bit build can take several minutes because `dbus-fast` must be
compiled. The compiler is removed from the finished image. On 64-bit Pi OS,
pip uses the published ARM64 wheel instead.

To build Pi images on another machine, use a Buildx builder with emulation
enabled. `--push` is required for a multi-platform result because the normal
Docker image store cannot load a manifest containing multiple architectures:

```bash
docker buildx build \
  --platform linux/arm64,linux/arm/v7 \
  -t your-dockerhub-user/ruuvix:latest \
  --push .
```

To publish one tag that also retains the existing PC image, include amd64:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t your-dockerhub-user/ruuvix:latest \
  --push .
```

The original Pi Zero and Pi 1 use ARMv6 and are not included in the supported
platforms. Pi Zero 2 W and Pi 2 or newer are supported.

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
  --uts host \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --user "$(id -u):$(id -g)" \
  -v "$(pwd)/app:/app" \
  -v /var/run/dbus/:/var/run/dbus/:z \
  -e RUUVI_BLE_ADAPTER=bleak \
  ruuvix --run
```

`--uts host` shares the host's hostname (keeps the InfluxDB `client` tag
consistent — it has nothing to do with privilege). `--cap-drop ALL` and
`--security-opt no-new-privileges:true` strip the container down to no Linux
capabilities at all, since BLE scanning happens on the host's `bluetoothd`
and this container is just a D-Bus client talking to it over the mounted
socket. `--user` avoids root-owned files showing up under `app/`.

One-off commands (find tags, calibrate, init) work the same way, just with
`run --rm` instead of `run -d`:

```bash
docker run --rm --cap-drop ALL --security-opt no-new-privileges:true \
  --user "$(id -u):$(id -g)" \
  -v "$(pwd)/app:/app" -v /var/run/dbus/:/var/run/dbus/:z \
  ruuvix --find
```

**If scanning finds no tags** under these locked-down settings, your
environment's D-Bus/BlueZ setup may need more access than this project's own
test hardware did. Fall back to the previously proven-working, broader
settings by dropping `--cap-drop`/`--security-opt` and adding back
`--network host --privileged`.

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
