#!/usr/bin/env bash
set -euo pipefail

# Builds and pushes the multi-arch (amd64 + arm64) image to Docker Hub.
# Both architectures have prebuilt wheels for every dependency, so this
# needs no compiling under QEMU emulation and can run from any one machine
# -- no need to also build natively on a Raspberry Pi.

BUILDER=ruuvix-builder
docker buildx inspect "$BUILDER" >/dev/null 2>&1 || docker buildx create --name "$BUILDER" --use
docker buildx use "$BUILDER"

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t dreamr/ruuvix:latest \
  --push \
  .
