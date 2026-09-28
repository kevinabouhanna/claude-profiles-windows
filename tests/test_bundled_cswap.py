"""claude-swap ships inside the installer; these guard how it is found and built."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from claude_profiles.services import cswap_client
from claude_profiles.services.cswap_client import (
    bundled_cswap,
    find_cswap,
    missing_cswap_advice,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def frozen_app(tmp_path, monkeypatch):
    """Pretend to be the installed app at tmp_path/ClaudeProfiles.exe."""
    exe = tmp_path / "ClaudeProfiles.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return tmp_path


def _add_bundled(app_dir: Path) -> Path:
    cswap = app_dir / "cswap" / "cswap.exe"
    cswap.parent.mkdir()
    cswap.write_bytes(b"")
    return cswap


# --- finding it -------------------------------------------------------------


def test_a_source_checkout_has_no_bundled_copy(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert bundled_cswap() is None


def test_the_installed_app_finds_its_own_copy(frozen_app):
    cswap = _add_bundled(frozen_app)
    assert bundled_cswap() == cswap.resolve()


def test_the_bundled_copy_wins_over_one_on_path(frozen_app, monkeypatch):
    """It is the version this build was tested against."""
    cswap = _add_bundled(frozen_app)
    monkeypatch.setattr(cswap_client.shutil, "which", lambda name: r"C:\elsewhere\cswap.exe")
    assert find_cswap() == str(cswap.resolve())


def test_without_a_bundled_copy_path_is_still_used(frozen_app, monkeypatch):
    monkeypatch.setattr(cswap_client.shutil, "which", lambda name: r"C:\elsewhere\cswap.exe")
    assert find_cswap() == r"C:\elsewhere\cswap.exe"


def test_an_installed_app_missing_its_copy_says_reinstall(frozen_app):
    advice = missing_cswap_advice()
    assert "Reinstall Claude Profiles" in advice
    assert "uv tool install" not in advice, "the installer is meant to be all they need"


def test_a_source_checkout_is_told_to_install_claude_swap(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert "uv tool install claude-swap" in missing_cswap_advice()


# --- the build ----------------------------------------------------------------


def _pinned() -> str:
    text = (ROOT / "tools" / "cswap" / "requirements.in").read_text(encoding="utf-8")
    return re.search(r"^claude-swap==(\S+)$", text, re.M).group(1)


def test_the_lock_file_matches_the_pin():
    lock = (ROOT / "tools" / "cswap" / "requirements.txt").read_text(encoding="utf-8")
    assert f"claude-swap=={_pinned()} " in lock
    # Every requirement carries a hash, so --require-hashes can be enforced.
    assert lock.count("==") <= lock.count("--hash=sha256:")


def test_the_pinned_version_is_the_one_the_contract_was_verified_against():
    """Bumping the pin means re-running the contract tests and saying so."""
    architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert f"claude-swap {_pinned()}" in architecture


def test_the_release_installs_the_bundle_with_hashes_enforced():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "--require-hashes -r tools/cswap/requirements.txt" in workflow


def test_the_build_verifier_only_asks_cswap_for_its_version():
    """Verifying a build must never read the developer's real accounts."""
    source = (ROOT / "tools" / "verify_build.py").read_text(encoding="utf-8")
    start = source.index("def check_bundled_cswap")
    body = source[start:source.index("def check_starts_and_works")]
    assert '"--version"' in body
    assert "list" not in body and "status" not in body


def test_notices_cover_every_bundled_package():
    spec = importlib.util.spec_from_file_location(
        "third_party_notices", ROOT / "tools" / "third_party_notices.py"
    )
    notices = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(notices)
    names = notices.cswap_packages()
    assert "claude-swap" in names
    assert "keyring" in names and "textual" in names
    assert "PySide6" in notices.APP_PACKAGES


# --- the entry point's patches -------------------------------------------------


def test_the_bundled_entry_point_disables_self_updating(capsys):
    pytest.importorskip("claude_swap")
    spec = importlib.util.spec_from_file_location(
        "cswap_entry", ROOT / "tools" / "cswap" / "cswap_entry.py"
    )
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    from claude_swap import update_check

    try:
        assert update_check.check_for_update("0.0.1") is None, "no PyPI notice"
        with pytest.raises(SystemExit):
            update_check.run_self_upgrade()
        assert "bundled with Claude Profiles" in capsys.readouterr().err
    finally:
        importlib.reload(update_check)
