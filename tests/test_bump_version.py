"""Version bump rules for stable releases and pre-releases."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bump_version.py"
MANIFEST = ROOT / "custom_components" / "subzero_ble" / "manifest.json"

_spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
assert _spec is not None and _spec.loader is not None
bump_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_version)


def test_stable_bumps() -> None:
    """patch, minor, and major move a stable version to the next stable number."""
    assert bump_version._bump("0.59.0", "patch") == "0.59.1"
    assert bump_version._bump("0.59.0", "minor") == "0.60.0"
    assert bump_version._bump("0.59.0", "major") == "1.0.0"
    assert bump_version._bump("0.59.0", "current") == "0.59.0"


def test_prerelease_bumps_from_stable() -> None:
    """A pre-release of the next number starts at b1 and does not use that tag."""
    assert bump_version._bump("0.59.0", "patch", pre=True) == "0.59.1b1"
    assert bump_version._bump("0.59.0", "minor", pre=True) == "0.60.0b1"
    assert bump_version._bump("0.59.0", "major", pre=True) == "1.0.0b1"


def test_prerelease_current_increments_beta() -> None:
    """current --pre bumps bN. current without --pre publishes that stable tag."""
    assert bump_version._bump("0.59.1b1", "current", pre=True) == "0.59.1b2"
    assert bump_version._bump("0.59.1b2", "current", pre=True) == "0.59.1b3"
    assert bump_version._bump("0.59.1b2", "current") == "0.59.1"


def test_bump_from_a_beta_uses_the_numeric_base() -> None:
    """patch from 0.59.1b3 is 0.59.2, or 0.59.2b1 when it is another pre-release."""
    assert bump_version._bump("0.59.1b3", "patch") == "0.59.2"
    assert bump_version._bump("0.59.1b3", "patch", pre=True) == "0.59.2b1"
    assert bump_version._bump("0.59.1b3", "minor", pre=True) == "0.60.0b1"


def test_explicit_versions() -> None:
    """An explicit beta is kept. pre cannot be paired with a stable number."""
    assert bump_version._bump("0.59.0", "0.59.1b1") == "0.59.1b1"
    assert bump_version._bump("0.59.0", "0.59.1b1", pre=True) == "0.59.1b1"
    assert bump_version._bump("0.59.0", "0.22.0") == "0.22.0"
    with pytest.raises(ValueError, match="bN suffix"):
        bump_version._bump("0.59.0", "0.59.1", pre=True)


def test_current_prerelease_on_a_stable_version_is_rejected() -> None:
    """current --pre needs an existing bN. The next number is patch, minor, or major."""
    with pytest.raises(ValueError, match="patch, minor, or major"):
        bump_version._bump("0.59.0", "current", pre=True)


def test_is_prerelease() -> None:
    """Only X.Y.ZbN is a pre-release. The stable number is not."""
    assert bump_version.is_prerelease("0.59.1b1") is True
    assert bump_version.is_prerelease("0.59.1b2") is True
    assert bump_version.is_prerelease("0.59.1") is False
    assert bump_version.is_prerelease("0.59.1b") is False
    assert bump_version.is_prerelease("0.59.1-beta.1") is False


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _manifest_version() -> str:
    """Return the version currently stored in manifest.json."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def test_release_script_dry_run_selects_a_beta_tag() -> None:
    """patch --pre plans vX.Y.ZbN and leaves the manifest untouched."""
    before = MANIFEST.read_text(encoding="utf-8")
    expected = bump_version._bump(_manifest_version(), "patch", pre=True)
    result = subprocess.run(
        ["./scripts/release.sh", "patch", "--pre", "--dry-run"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"v{expected}" in result.stdout
    assert f"git tag v{expected}" in result.stdout
    assert MANIFEST.read_text(encoding="utf-8") == before


def test_print_and_check_do_not_write_the_manifest() -> None:
    """The flags the workflow uses print or exit without editing manifest.json."""
    before = MANIFEST.read_text(encoding="utf-8")
    expected = bump_version._bump(_manifest_version(), "patch", pre=True)
    printed = _run("patch", "--pre", "--print")
    checked = _run("--check-prerelease", expected)
    stable = _run("--check-prerelease", "0.59.1")
    rejected = _run("current", "--pre", "--print")

    assert printed.returncode == 0
    assert printed.stdout.strip() == expected
    assert checked.returncode == 0
    assert stable.returncode == 1
    assert rejected.returncode != 0
    assert MANIFEST.read_text(encoding="utf-8") == before
