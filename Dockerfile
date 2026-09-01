FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt /requirements.txt

# dbus-fast publishes wheels for amd64 and arm64, so this never needs to
# compile anything from source (32-bit ARMv7 isn't supported).
RUN pip install --no-cache-dir -r /requirements.txt

# app code and config are bind-mounted at runtime (see docker-compose.yml),
# not baked into the image, so edits never require a rebuild.
ENTRYPOINT ["python", "ruuvix.py"]
CMD ["--run"]
