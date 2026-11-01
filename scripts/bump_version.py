#!/usr/bin/env python3
"""Bump the integration version in manifest.json.

const.VERSION is read from that file at import time, so this is the only
place the version is stored.

Stable versions are ``X.Y.Z``. Pre-releases are ``X.Y.ZbN`` (``0.59.1b1``).
The ``bN`` suffix is what makes the release workflow publish a GitHub
pre-release, which is the flag HACS hides until Pre-release is turned on.
A beta tag leaves the matching stable tag free: ``0.59.1b2`` can still be
released later as ``0.59.1``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "subzero_ble"
    / "manifest.json"
)

_STABLE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_BETA = re.compile(r"^(\d+)\.(\d+)\.(\d+)b(\d+)$")
_BUMPS = {"major", "minor", "patch", "current"}


def is_prerelease(version: str) -> bool:
    """Return True when version is ``X.Y.ZbN``."""
    return _BETA.fullmatch(version) is not None


def _split(version: str) -> tuple[int, int, int, int | None]:
    """Return major, minor, patch, and the beta number when present."""
    if match := _BETA.fullmatch(version):
        major, minor, patch, beta = match.groups()
        return int(major), int(minor), int(patch), int(beta)
    if match := _STABLE.fullmatch(version):
        major, minor, patch = match.groups()
        return int(major), int(minor), int(patch), None
    raise ValueError(f"Not an X.Y.Z or X.Y.ZbN version: {version}")


def _format(major: int, minor: int, patch: int, beta: int | None) -> str:
    base = f"{major}.{minor}.{patch}"
    if beta is None:
        return base
    return f"{base}b{beta}"


def _next_stable(
    major: int, minor: int, patch: int, spec: str
) -> tuple[int, int, int]:
    if spec == "major":
        return major + 1, 0, 0
    if spec == "minor":
        return major, minor + 1, 0
    if spec == "patch":
        return major, minor, patch + 1
    raise ValueError(f"Not a version bump: {spec}")


def _bump(current: str, spec: str, *, pre: bool = False) -> str:
    """Return the version ``spec`` selects, starting from ``current``.

    ``patch``, ``minor``, and ``major`` move to the next stable number. With
    ``pre``, that number is published as ``b1``. ``current`` keeps a stable
    version, drops a ``bN`` suffix, or with ``pre`` increments that suffix.
    An explicit version is used as given. ``pre`` with a stable explicit
    version is rejected so a beta cannot consume the stable tag.
    """
    if spec in _BUMPS:
        major, minor, patch, beta = _split(current)
        if spec == "current":
            if pre:
                if beta is None:
                    raise ValueError(
                        "A pre-release needs a new version. "
                        "Choose patch, minor, or major."
                    )
                return _format(major, minor, patch, beta + 1)
            return _format(major, minor, patch, None)
        next_major, next_minor, next_patch = _next_stable(major, minor, patch, spec)
        return _format(next_major, next_minor, next_patch, 1 if pre else None)

    _split(spec)
    if pre and not is_prerelease(spec):
        raise ValueError(
            f"A pre-release version needs a bN suffix, for example {spec}b1."
        )
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bump the integration version in manifest.json."
    )
    parser.add_argument(
        "spec",
        nargs="?",
        help="major, minor, patch, current, or an explicit X.Y.Z / X.Y.ZbN version",
    )
    parser.add_argument(
        "--pre",
        action="store_true",
        help="select a pre-release version (X.Y.ZbN)",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_only",
        help="print the next version without writing manifest.json",
    )
    parser.add_argument(
        "--check-prerelease",
        metavar="VERSION",
        help="exit 0 when VERSION is X.Y.ZbN, otherwise exit 1",
    )
    args = parser.parse_args()

    if args.check_prerelease is not None:
        raise SystemExit(0 if is_prerelease(args.check_prerelease) else 1)
    if args.spec is None:
        parser.error("spec is required unless --check-prerelease is set")

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    try:
        new_version = _bump(data["version"], args.spec, pre=args.pre)
    except ValueError as err:
        parser.error(str(err))
    if not args.print_only:
        data["version"] = new_version
        MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(new_version)


if __name__ == "__main__":
    main()
