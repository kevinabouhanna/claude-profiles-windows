"""Picking up a fresh Claude Code sign-in that claude-swap is still ignoring.

The bug these guard: after signing in again with Claude Code, claude-swap kept
reporting the active account as ``relogin_required`` because its stored copy
still held the dead token. The app showed "Sign-in needed" and a manual
``cswap add`` checklist for an account that worked fine.
"""

from __future__ import annotations

import pytest

from claude_profiles.models import ActiveStatus
from claude_profiles.services.cswap_client import CswapError, CswapErrorKind
from claude_profiles.services.login_repair import (
    MIN_GAP_SECONDS,
    RETRY_BASE_SECONDS,
    RETRY_MAX_SECONDS,
    LoginRepair,
)
from claude_profiles.services.mock_cswap import (
    PERSONAL_EMAIL,
    WORK_EMAIL,
    MockCswapClient,
)
from claude_profiles.services.polling_service import PollingCoordinator
from claude_profiles.services.profile_service import ProfileService
from claude_profiles.services.settings_service import SettingsService


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def work_active(*, stale: bool = False, dead: bool = False) -> MockCswapClient:
    """Work is the account Claude Code is signed in as."""
    backend = MockCswapClient("two_accounts")
    backend.switch("work")
    if stale:
        backend.stale_logins.add(WORK_EMAIL)
    if dead:
        backend.dead_logins.add(WORK_EMAIL)
    backend.calls.clear()
    return backend


def work_state(accounts):
    work = accounts.by_alias("work")
    assert work is not None
    return work


# --- the repair itself -----------------------------------------------------


def test_a_stale_stored_login_is_restored_and_healed():
    backend = work_active(stale=True)
    repair = LoginRepair(backend, clock=Clock())
    before = backend.list_accounts()
    assert work_state(before).usage_status.needs_reauth

    outcome = repair.run(before)

    assert outcome is not None
    assert outcome.attempted and outcome.healed
    assert outcome.error is None
    assert outcome.accounts is not None
    assert not work_state(outcome.accounts).usage_status.needs_reauth
    # Confirm the login, re-store it, then re-read: nothing else.
    assert backend.calls == ["list", "status", "resync", "list"]


def test_a_healthy_active_account_is_left_alone():
    backend = work_active()
    repair = LoginRepair(backend, clock=Clock())
    assert repair.run(backend.list_accounts()) is None
    assert "resync" not in backend.calls


def test_an_inactive_account_is_never_touched():
    """Only the signed-in account's login can be picked up. An idle account
    needs the user to sign in as it, and a bare add would store the wrong one."""
    backend = work_active()
    backend.stale_logins.add(PERSONAL_EMAIL)
    repair = LoginRepair(backend, clock=Clock())
    accounts = backend.list_accounts()
    assert accounts.by_alias("personal").usage_status.needs_reauth

    assert repair.run(accounts) is None
    assert "status" not in backend.calls
    assert "resync" not in backend.calls


def test_nothing_is_stored_when_claude_code_moved_to_another_login(monkeypatch):
    """A sign-in landing between the list and the repair must not be stored."""
    backend = work_active(stale=True)
    accounts = backend.list_accounts()
    monkeypatch.setattr(
        backend,
        "status",
        lambda: ActiveStatus(email="someone-else@example.invalid", managed=False),
    )
    repair = LoginRepair(backend, clock=Clock())

    outcome = repair.run(accounts)

    assert outcome is not None
    assert not outcome.attempted and not outcome.healed
    assert "resync" not in backend.calls


def test_a_different_slot_with_the_same_email_is_not_stored(monkeypatch):
    """One address can belong to two organisations, in two slots."""
    backend = work_active(stale=True)
    accounts = backend.list_accounts()
    monkeypatch.setattr(
        backend,
        "status",
        lambda: ActiveStatus(email=WORK_EMAIL, managed=True, number=7),
    )
    outcome = LoginRepair(backend, clock=Clock()).run(accounts)
    assert outcome is not None and not outcome.attempted
    assert "resync" not in backend.calls


def test_a_failed_restore_is_reported_not_raised():
    backend = work_active(stale=True)
    backend.fail_next_add = CswapError(CswapErrorKind.REPORTED, "credential store locked")
    outcome = LoginRepair(backend, clock=Clock()).run(backend.list_accounts())
    assert outcome is not None
    assert outcome.attempted and not outcome.healed
    assert outcome.error is not None
    assert "locked" in outcome.error.user_message


def test_an_unexpected_exception_is_contained(monkeypatch):
    backend = work_active(stale=True)

    def explode() -> str:
        raise OSError("disk went away")

    monkeypatch.setattr(backend, "resync_login", explode)
    outcome = LoginRepair(backend, clock=Clock()).run(backend.list_accounts())
    assert outcome is not None and outcome.error is not None
    assert outcome.error.kind is CswapErrorKind.UNKNOWN


# --- pacing ----------------------------------------------------------------


def test_a_dead_login_backs_off_instead_of_retrying_every_poll():
    backend = work_active(dead=True)
    clock = Clock()
    repair = LoginRepair(backend, clock=clock)
    accounts = backend.list_accounts()

    first = repair.run(accounts)
    assert first is not None and first.attempted and not first.healed

    clock.now += RETRY_BASE_SECONDS - 1
    assert repair.run(accounts) is None
    clock.now += 1
    assert repair.run(accounts) is not None  # second attempt

    # The gap doubles after each failure...
    clock.now += RETRY_BASE_SECONDS * 2 - 1
    assert repair.run(accounts) is None
    clock.now += 1
    assert repair.run(accounts) is not None

    # ...and never exceeds the cap.
    assert repair.retry_delay(50) == RETRY_MAX_SECONDS
    assert backend.calls.count("resync") == 3


def test_opening_the_app_skips_the_backoff_but_not_the_minimum_gap():
    backend = work_active(dead=True)
    clock = Clock()
    repair = LoginRepair(backend, clock=clock)
    accounts = backend.list_accounts()
    repair.run(accounts)

    repair.request()
    clock.now += MIN_GAP_SECONDS - 1
    assert repair.run(accounts) is None
    clock.now += 1
    assert repair.run(accounts) is not None
    # The request is used up by that attempt.
    clock.now += MIN_GAP_SECONDS
    assert repair.run(accounts) is None


def test_signing_in_again_is_picked_up_once_the_backoff_lifts():
    backend = work_active(dead=True)
    clock = Clock()
    repair = LoginRepair(backend, clock=clock)
    assert not repair.run(backend.list_accounts()).healed

    # The user signs in again; claude-swap's copy is now the stale one.
    backend.dead_logins.clear()
    backend.stale_logins.add(WORK_EMAIL)
    clock.now += RETRY_BASE_SECONDS
    outcome = repair.run(backend.list_accounts())
    assert outcome is not None and outcome.healed


def test_a_recovered_account_is_repaired_at_once_next_time():
    backend = work_active(stale=True)
    clock = Clock()
    repair = LoginRepair(backend, clock=clock)
    assert repair.run(backend.list_accounts()).healed
    # Healthy for a while: bookkeeping is dropped.
    assert repair.run(backend.list_accounts()) is None

    clock.now += MIN_GAP_SECONDS
    backend.stale_logins.add(WORK_EMAIL)
    outcome = repair.run(backend.list_accounts())
    assert outcome is not None and outcome.healed


# --- where it runs ---------------------------------------------------------


def test_a_poll_returns_the_repaired_reading():
    backend = work_active(stale=True)
    coordinator = PollingCoordinator(
        backend, lambda: 120, repair=LoginRepair(backend, clock=Clock())
    )

    result = coordinator.run_once()

    assert result is not None and result.ok
    assert result.repair is not None and result.repair.healed
    assert not work_state(result.accounts).usage_status.needs_reauth
    assert coordinator.last_result is result.accounts


def test_a_poll_without_a_repair_is_unchanged():
    backend = work_active(stale=True)
    result = PollingCoordinator(backend, lambda: 120).run_once()
    assert result is not None and result.repair is None
    assert work_state(result.accounts).usage_status.needs_reauth
    assert "resync" not in backend.calls


@pytest.fixture
def service(tmp_path):
    def _make(backend: MockCswapClient) -> ProfileService:
        svc = ProfileService(backend, SettingsService(data_dir=tmp_path / "data"))
        svc.refresh_sync()
        return svc

    return _make


def test_fix_sign_in_restores_a_stale_login(service):
    backend = work_active(stale=True)
    svc = service(backend)
    assert svc.state("work").needs_reauth

    ok, message = svc.repair_login_sync("work")

    assert ok, message
    assert not svc.state("work").needs_reauth
    assert not svc.is_busy
    assert any(
        "Picked up the new Claude Code sign-in for Work" in e.message
        for e in svc.recent_activity()
    )


def test_fix_sign_in_asks_for_a_sign_in_when_the_login_itself_is_dead(service):
    backend = work_active(dead=True)
    svc = service(backend)

    ok, message = svc.repair_login_sync("work")

    assert not ok
    assert "Sign in again" in message
    assert svc.state("work").needs_reauth
    assert not svc.is_busy
    assert not any("Picked up" in e.message for e in svc.recent_activity())


def test_fix_sign_in_reports_a_failed_restore(service):
    backend = work_active(stale=True)
    svc = service(backend)
    backend.fail_next_add = CswapError(CswapErrorKind.REPORTED, "credential store locked")

    ok, message = svc.repair_login_sync("work")

    assert not ok
    assert "locked" in message
    assert not svc.is_busy
