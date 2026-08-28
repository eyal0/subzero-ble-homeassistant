#!/usr/bin/env bash
# Run hassfest against the subzero_ble integration, the same check the
# "Validate" GitHub workflow runs. Requires scripts/setup-dev-env.sh to have
# created the venv and the home-assistant/core checkout.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SUBZERO_DEV_VENV:-$HOME/ha-venv}"
CORE_DIR="${SUBZERO_DEV_CORE:-$HOME/home-assistant-core}"

if [ ! -x "$VENV/bin/python" ] || [ ! -d "$CORE_DIR" ]; then
  echo "Dev environment missing. Run scripts/setup-dev-env.sh first." >&2
  exit 1
fi

cd "$CORE_DIR"
exec "$VENV/bin/python" -m script.hassfest \
  --integration-path "$REPO_ROOT/custom_components/subzero_ble" "$@"
