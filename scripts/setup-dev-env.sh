#!/usr/bin/env bash
# Set up a local Home Assistant that loads the subzero_ble integration.
#
# Home Assistant is not importable from a normal editor Python: it needs
# Python 3.14 and it is never listed in this repo's (empty) runtime
# requirements. This script builds an isolated venv with the right Python and
# the Home Assistant packages the integration depends on, then wires the repo's
# custom_components into a Home Assistant config directory so you can run the
# real thing (`hass -c "$HA_CONFIG"`).
#
# It is idempotent: re-running only does the work that is missing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Everything lives outside the checkout so a fresh clone (or a Cloud Agent that
# re-checks-out /workspace on every boot) does not clobber it.
VENV="${SUBZERO_DEV_VENV:-$HOME/ha-venv}"
HA_CONFIG="${SUBZERO_DEV_HA_CONFIG:-$HOME/ha-config}"
CORE_DIR="${SUBZERO_DEV_CORE:-$HOME/home-assistant-core}"
PYTHON_VERSION="3.14"

export PATH="$HOME/.local/bin:$PATH"

# /usr/bin/c++ is clang on the base image and cannot find libstdc++, which
# breaks Home Assistant's native build-from-source deps (pymicro-vad,
# pyspeex-noise, ...). Force gcc/g++, which have the matching headers.
export CC="${CC:-gcc}" CXX="${CXX:-g++}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

log "Ensuring system packages are installed"
# build-essential  : gcc/g++ so Home Assistant's build-from-source deps compile
#                    (the image's default /usr/bin/c++ is clang without libstdc++)
# libturbojpeg     : used by the camera base platform Home Assistant always loads
NEEDED_PKGS=()
command -v gcc >/dev/null 2>&1 && command -v g++ >/dev/null 2>&1 || NEEDED_PKGS+=(build-essential)
dpkg -s libturbojpeg >/dev/null 2>&1 || NEEDED_PKGS+=(libturbojpeg)
if [ "${#NEEDED_PKGS[@]}" -gt 0 ]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${NEEDED_PKGS[@]}"
  else
    echo "WARNING: cannot install ${NEEDED_PKGS[*]} (no passwordless sudo)." >&2
  fi
fi

log "Ensuring uv is installed"
if ! command -v uv >/dev/null 2>&1; then
  pip3 install --user uv
fi
uv --version

log "Ensuring CPython ${PYTHON_VERSION} is available (Home Assistant needs >=3.14.2)"
uv python install "$PYTHON_VERSION"

log "Creating the Home Assistant venv at ${VENV}"
if [ ! -x "$VENV/bin/python" ]; then
  uv venv --python "$PYTHON_VERSION" "$VENV"
fi

log "Installing Home Assistant"
# Unpinned so the venv tracks the current release; the tested versions are
# captured by the environment snapshot/build.
VIRTUAL_ENV="$VENV" uv pip install --upgrade homeassistant

HA_VERSION="$("$VENV/bin/python" -c 'import homeassistant.const as c; print(c.__version__)')"
log "Home Assistant ${HA_VERSION} installed"

log "Installing the Home Assistant components the integration runs on"
# Pull the exact pinned requirements Home Assistant declares for the components
# we actually start, so the versions always match the installed core:
#   - bluetooth       : the BLE stack the integration is built on
#   - frontend        : the web UI used to add and exercise the integration
#   - assist_pipeline : a default base platform; installing its deps keeps the
#                       boot log clean even though the integration never uses it
"$VENV/bin/python" - "$REPO_ROOT" <<'PY' | VIRTUAL_ENV="$VENV" uv pip install --upgrade -r /dev/stdin
import json, os, sys
import homeassistant
base = os.path.join(os.path.dirname(homeassistant.__file__), "components")
reqs: set[str] = set()
for comp in ("bluetooth", "frontend", "assist_pipeline"):
    manifest = os.path.join(base, comp, "manifest.json")
    reqs.update(json.load(open(manifest)).get("requirements") or [])
sys.stdout.write("\n".join(sorted(reqs)) + "\n")
PY

log "Wiring the custom integration into ${HA_CONFIG}"
mkdir -p "$HA_CONFIG/custom_components"
ln -sfn "$REPO_ROOT/custom_components/subzero_ble" \
  "$HA_CONFIG/custom_components/subzero_ble"

if [ ! -f "$HA_CONFIG/configuration.yaml" ]; then
  cat > "$HA_CONFIG/configuration.yaml" <<'YAML'
# Minimal Home Assistant config for developing the subzero_ble integration.
#
# default_config is intentionally omitted: it loads many unrelated integrations.
# - frontend  : web UI, so the integration can be added/exercised from a browser
# - bluetooth : the stack this integration is built on; it is also required for
#               the config flow's discovery step (sets up fine with no adapter)
# - logger    : debug logging for the integration
frontend:

bluetooth:

logger:
  default: warning
  logs:
    custom_components.subzero_ble: debug
YAML
fi

log "Setting up hassfest (the repo's CI validation) at ${CORE_DIR}"
# hassfest lives in the home-assistant/core repo, not the pip package. Clone the
# tag that matches the installed core so validation matches what the developer
# runs against, and so pyrightconfig.json's extraPaths can point here.
if [ "$(cat "$CORE_DIR/.subzero-ha-version" 2>/dev/null || true)" != "$HA_VERSION" ]; then
  rm -rf "$CORE_DIR"
  git clone --depth 1 --branch "$HA_VERSION" --single-branch \
    https://github.com/home-assistant/core.git "$CORE_DIR"
  echo "$HA_VERSION" > "$CORE_DIR/.subzero-ha-version"
fi

# hassfest shells out to the ruff version core pins for pre-commit.
RUFF_PIN="$(grep -oE 'ruff==[0-9.]+' "$CORE_DIR/requirements_test_pre_commit.txt" | head -1)"
if [ -n "$RUFF_PIN" ]; then
  VIRTUAL_ENV="$VENV" uv pip install "$RUFF_PIN"
fi

log "Done"
cat <<EOF

Home Assistant dev environment is ready.

  Run Home Assistant : source "$VENV/bin/activate" && hass -c "$HA_CONFIG"
                       (web UI on http://localhost:8123)
  Validate (hassfest): scripts/hassfest.sh
EOF
