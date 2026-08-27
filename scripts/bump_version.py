#!/usr/bin/env python3
"""Bump the integration version in manifest.json.

const.VERSION is read from that file at import time, so this is the only
place the version is stored.

Usage:
  python3 scripts/bump_version.py patch
  python3 scripts/bump_version.py minor
  python3 scripts/bump_version.py major
  python3 scripts/bump_version.py 0.21.0
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "subzero_ble"
    / "manifest.json"
)


def _parse(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(f"Not a major.minor.patch version: {version}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _bump(current: str, spec: str) -> str:
    if spec in {"major", "minor", "patch"}:
        major, minor, patch = _parse(current)
        if spec == "major":
            return f"{major + 1}.0.0"
        if spec == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"
    _parse(spec)
    return spec


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: bump_version.py <major|minor|patch|X.Y.Z>"
        )
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    new_version = _bump(data["version"], sys.argv[1])
    data["version"] = new_version
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(new_version)


if __name__ == "__main__":
    main()
