"""The release helper: one version source, tags that match it, changelog cuts."""

from __future__ import annotations

import importlib.util
import re
from datetime import date
from pathlib import Path

import pytest

import claude_profiles

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def release():
    spec = importlib.util.spec_from_file_location("release", ROOT / "tools" / "release.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CHANGELOG = """# Changelog

Intro.

## [Unreleased]

### Fixed

- A thing.

## [0.1.0] - 2026-09-28

### Added

- Everything.

[Unreleased]: https://github.com/kevinabouhanna/claude-profiles-windows/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kevinabouhanna/claude-profiles-windows/releases/tag/v0.1.0
"""


# --- the version has exactly one source ------------------------------------


def test_version_is_read_from_the_package(release):
    assert release.read_version() == claude_profiles.__version__


def test_package_version_is_strict_semver(release):
    release.parse(claude_profiles.__version__)


def test_pyproject_does_not_hardcode_a_version():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^version\s*=", pyproject, re.MULTILINE) is None
    assert 'dynamic = ["version"]' in pyproject


def test_current_version_has_release_notes(release):
    """A tag must never be cut for a version the changelog does not describe."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release.release_notes(changelog, claude_profiles.__version__)


# --- bumping ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("part", "expected"),
    [("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0"), ("1.5.0", "1.5.0")],
)
def test_next_version(release, part, expected):
    assert release.next_version("1.2.3", part) == expected


@pytest.mark.parametrize("bad", ["1.2.3", "1.2.2", "1.2", "v1.3.0", "1.3.0rc1", "huge"])
def test_next_version_rejects_non_increasing_or_malformed(release, bad):
    with pytest.raises(release.ReleaseError):
        release.next_version("1.2.3", bad)


def test_write_version_round_trips(release, tmp_path):
    target = tmp_path / "__init__.py"
    target.write_text('"""Doc."""\n\n__version__ = "0.1.0"\n', encoding="utf-8")
    release.write_version("0.2.0", target)
    assert release.read_version(target) == "0.2.0"
    assert target.read_text(encoding="utf-8").startswith('"""Doc."""')


# --- tags -------------------------------------------------------------------


def test_matching_tag_passes(release):
    release.check_tag("v1.2.3", "1.2.3")


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2.4", "v1.2.3-rc1", "release-1.2.3"])
def test_mismatched_tag_fails(release, tag):
    with pytest.raises(release.ReleaseError):
        release.check_tag(tag, "1.2.3")


# --- changelog --------------------------------------------------------------


def test_release_notes_are_just_that_section(release):
    notes = release.release_notes(CHANGELOG, "0.1.0")
    assert notes == "### Added\n\n- Everything."


def test_release_notes_for_a_missing_version_fail(release):
    with pytest.raises(release.ReleaseError):
        release.release_notes(CHANGELOG, "9.9.9")


def test_cut_moves_unreleased_under_the_new_version(release):
    text = release.cut_changelog(CHANGELOG, "0.1.0", "0.1.1", date(2026, 10, 1))

    assert "## [Unreleased]\n\n## [0.1.1] - 2026-10-01\n\n### Fixed\n\n- A thing." in text
    assert release.release_notes(text, "0.1.1") == "### Fixed\n\n- A thing."
    assert release.release_notes(text, "0.1.0") == "### Added\n\n- Everything."
    assert "compare/v0.1.1...HEAD" in text
    assert "[0.1.1]: " + release.REPO_URL + "/compare/v0.1.0...v0.1.1" in text
    assert text.count("[Unreleased]:") == 1


def test_cut_refuses_an_empty_unreleased_section(release):
    empty = CHANGELOG.replace("### Fixed\n\n- A thing.\n\n", "")
    with pytest.raises(release.ReleaseError):
        release.cut_changelog(empty, "0.1.0", "0.1.1", date(2026, 10, 1))
