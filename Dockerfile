FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt /requirements.txt

# dbus-fast publishes wheels for amd64 and arm64, but not for 32-bit ARMv7.
# Install a temporary compiler only on armhf so Raspberry Pi OS (32-bit) can
# build that dependency from source without making the other images larger.
RUN set -eux; \
    architecture="$(dpkg --print-architecture)"; \
    if [ "$architecture" = "armhf" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends build-essential; \
    fi; \
    pip install --no-cache-dir -r /requirements.txt; \
    if [ "$architecture" = "armhf" ]; then \
        apt-get purge -y --auto-remove build-essential; \
        rm -rf /var/lib/apt/lists/*; \
    fi

# app code and config are bind-mounted at runtime (see docker-compose.yml),
# not baked into the image, so edits never require a rebuild.
ENTRYPOINT ["python", "ruuvix.py"]
CMD ["--run"]
