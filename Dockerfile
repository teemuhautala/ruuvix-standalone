FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# app code and config are bind-mounted at runtime (see docker-compose.yml),
# not baked into the image, so edits never require a rebuild.
ENTRYPOINT ["python", "ruuvix.py"]
CMD ["--run"]
