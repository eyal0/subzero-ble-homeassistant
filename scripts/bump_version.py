#!/usr/bin/env python3
"""Bump the integration version in manifest.json.

const.VERSION is read from that file at import time, so this is the only
place the version is stored.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "subzero_ble"
    / "manifest.json"
)


def _parse(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Not a major.minor.patch version: {version}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _spec_arg(value: str) -> str:
    if value in {"major", "minor", "patch"}:
        return value
    try:
        _parse(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(str(err)) from err
    return value


def _bump(current: str, spec: str) -> str:
    if spec in {"major", "minor", "patch"}:
        major, minor, patch = _parse(current)
        if spec == "major":
            return f"{major + 1}.0.0"
        if spec == "minor":
            return f"{major}.{minor + 1}.0"
        return f"{major}.{minor}.{patch + 1}"
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bump the integration version in manifest.json."
    )
    parser.add_argument(
        "spec",
        type=_spec_arg,
        help="major, minor, patch, or an explicit X.Y.Z version",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="print the next version without writing manifest.json",
    )
    args = parser.parse_args()

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    new_version = _bump(data["version"], args.spec)
    if not args.print_only:
        data["version"] = new_version
        MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(new_version)


if __name__ == "__main__":
    main()
