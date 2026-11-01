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
