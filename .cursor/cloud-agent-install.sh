#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null; then
  curl -fsSL https://astral.sh/uv/install.sh | sh
fi
export PATH="${HOME}/.local/bin:${PATH}"

uv python install 3.14

if [[ ! -d .venv ]]; then
  uv venv --python 3.14 .venv
fi

uv pip install -r requirements_test.txt

if ! command -v docker >/dev/null || ! command -v fuse-overlayfs >/dev/null; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold" \
    docker.io fuse3 fuse-overlayfs
fi
