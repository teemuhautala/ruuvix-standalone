# ruuvix

Scans [RuuviTag](https://ruuvi.com/) BLE sensors and writes their
measurements to an InfluxDB 1.x database, on a schedule.

BLE scanning goes through the host's own BlueZ stack over D-Bus, so the
container never needs raw Bluetooth device access — just the host's D-Bus
socket mounted in.

Full documentation, `docker-compose.yml`, the install script, and source are
on GitHub: **https://github.com/teemuhautala/ruuvix-standalone**

## Quick start

You need a Linux host with Bluetooth hardware and `bluez` installed, and an
existing InfluxDB 1.x instance to point this at.

Grab the config templates from the repo first, since the image expects
`config.json`, `ruuvi-names.properties`, and `ruuvi-corrections.json` to
exist in the directory you mount to `/app`:

```bash
mkdir -p ruuvix/app && cd ruuvix
curl -fsSL https://raw.githubusercontent.com/teemuhautala/ruuvix-standalone/main/app/config.json.example -o app/config.json
curl -fsSL https://raw.githubusercontent.com/teemuhautala/ruuvix-standalone/main/app/ruuvi-names.properties.example -o app/ruuvi-names.properties
curl -fsSL https://raw.githubusercontent.com/teemuhautala/ruuvix-standalone/main/app/ruuvi-corrections.json.example -o app/ruuvi-corrections.json
```

Edit `app/config.json` to point `influxdb` at your `host:port` — both parts
required, no `http://` scheme and no trailing `/` (e.g. `192.168.1.10:8086`,
not `192.168.1.10` or `http://192.168.1.10:8086/`) — then:

```bash
docker pull dreamr/ruuvix

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
  dreamr/ruuvix --run
```

No `--privileged` or `--network host` needed — the container only talks to
the host's already-running `bluetoothd` as a D-Bus client over the mounted
socket above.

Discover nearby tags and add them to `app/ruuvi-names.properties`:

```bash
docker run --rm --cap-drop ALL --security-opt no-new-privileges:true \
  --user "$(id -u):$(id -g)" \
  -v "$(pwd)/app:/app" -v /var/run/dbus/:/var/run/dbus/:z \
  dreamr/ruuvix --find
```

See the [GitHub repo](https://github.com/teemuhautala/ruuvix-standalone) for
the `install.sh` bootstrap script, `docker-compose.yml`, calibration,
first-time InfluxDB setup, and troubleshooting (including the broader
`--privileged --network host` fallback if your host's D-Bus/BlueZ setup
needs it).

## Tags

- `latest` — tracks the `main` branch of the GitHub repo. Publish this as a
  multi-platform image for `linux/amd64`, `linux/arm64` (64-bit Raspberry Pi
  OS), and `linux/arm/v7` (32-bit Raspberry Pi OS); Docker will then pull the
  matching variant automatically. See the repository README for the Buildx
  command.

## Source

[github.com/teemuhautala/ruuvix-standalone](https://github.com/teemuhautala/ruuvix-standalone)
