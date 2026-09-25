"""Tests for profile mapping, switch orchestration, and activity-log safety."""

from __future__ import annotations

import pytest

from claude_profiles.models import AccountList
from claude_profiles.services.cswap_client import CswapError, CswapErrorKind
from claude_profiles.services.mock_cswap import MockCswapClient
from claude_profiles.services.profile_service import ProfileService
from claude_profiles.services.redaction import contains_secret

from . import fixtures as fx

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def service(settings_service):
    backend = MockCswapClient("healthy")
    return ProfileService(backend, settings_service), backend


# --- mapping --------------------------------------------------------------


def test_aliases_map_to_profiles(service):
    svc, _ = service
    svc.refresh_sync()

    personal = svc.state("personal")
    work = svc.state("work")
    assert personal is not None and work is not None
    assert personal.is_active is True
    assert work.is_active is False
    assert personal.is_registered and work.is_registered


def test_display_label_uses_runtime_email_not_source(service):
    """Emails come from cswap output, never from configuration."""
    svc, _ = service
    svc.refresh_sync()
    label = svc.state("personal").display_label
    assert label.startswith("Personal — ")
    assert "@example.invalid" in label


def test_unregistered_alias_is_reported(settings_service):
    backend = MockCswapClient("no_accounts")
    svc = ProfileService(backend, settings_service)
    svc.refresh_sync()

    state = svc.state("work")
    assert state.is_registered is False
    assert state.error is not None
    assert "work" in state.error


def test_stale_account_is_marked_but_still_rendered(settings_service):
    backend = MockCswapClient("work_stale")
    svc = ProfileService(backend, settings_service)
    svc.refresh_sync()

    work = svc.state("work").account
    assert work is not None
    assert work.is_stale is True
    assert work.effective_usage is not None  # last-known values retained


def test_reauth_state_is_surfaced(settings_service):
    backend = MockCswapClient("work_reauth")
    svc = ProfileService(backend, settings_service)
    svc.refresh_sync()

    assert svc.state("work").needs_reauth is True
    assert svc.state("personal").needs_reauth is False


def test_unknown_schema_emits_one_warning(settings_service):
    backend = MockCswapClient("unknown_schema")
    svc = ProfileService(backend, settings_service)
    seen: list[int] = []
    svc.schemaWarning.connect(seen.append)

    svc.refresh_sync()
    svc.refresh_sync()

    assert seen == [99]  # warned once, not on every poll


# --- switching ------------------------------------------------------------


def test_switch_succeeds_and_updates_active(service):
    svc, backend = service
    svc.refresh_sync()

    ok, message = svc.switch_sync("work")

    assert ok is True
    assert "Work" in message
    assert svc.state("work").is_active is True
    assert svc.state("personal").is_active is False
    assert "switch:work" in backend.calls
    # The list refresh after a switch is what state is read from.
    assert backend.calls.count("list") >= 2


def test_switch_confirms_with_status(service):
    svc, backend = service
    svc.refresh_sync()
    svc.switch_sync("work")
    assert "status" in backend.calls


def test_switch_to_already_active_is_a_noop(service):
    svc, backend = service
    svc.refresh_sync()
    before = list(backend.calls)

    ok, message = svc.switch_sync("personal")

    assert ok is True
    assert "already" in message.lower()
    assert not any(c.startswith("switch:") for c in backend.calls[len(before) :])


def test_switch_failure_reports_safe_message(service):
    svc, backend = service
    svc.refresh_sync()
    backend.fail_next_switch = CswapError(
        CswapErrorKind.REPORTED, "No account matches that alias."
    )

    ok, message = svc.switch_sync("work")

    assert ok is False
    assert "No account matches" in message
    assert svc.state("personal").is_active is True  # unchanged


def test_switch_timeout_is_handled(service):
    svc, backend = service
    svc.refresh_sync()
    backend.fail_next_switch = CswapError(
        CswapErrorKind.TIMEOUT, "claude-swap did not respond within 45 seconds."
    )

    ok, message = svc.switch_sync("work")
    assert ok is False
    assert "did not respond" in message


def test_switch_to_unregistered_profile_explains_setup(settings_service):
    backend = MockCswapClient("no_accounts")
    svc = ProfileService(backend, settings_service)
    svc.refresh_sync()

    ok, message = svc.switch_sync("work")

    assert ok is False
    assert "cswap add --alias work" in message


# --- busy lifecycle -------------------------------------------------------


def test_busy_clears_after_success(service):
    svc, _ = service
    svc.refresh_sync()
    seen: list[bool] = []
    svc.busyChanged.connect(seen.append)

    svc.switch_sync("work")

    assert svc.is_busy is False
    assert seen == [True, False]


def test_busy_clears_after_failure(service):
    svc, backend = service
    svc.refresh_sync()
    backend.fail_next_switch = CswapError(CswapErrorKind.REPORTED, "nope")
    seen: list[bool] = []
    svc.busyChanged.connect(seen.append)

    svc.switch_sync("work")

    assert svc.is_busy is False
    assert seen == [True, False]


def test_busy_clears_after_unexpected_exception(service):
    """Even an unforeseen error must re-enable the controls."""

    class Exploding(MockCswapClient):
        def switch(self, alias, *, fallback_number=None):
            raise RuntimeError("boom")

    svc, _ = service
    svc.refresh_sync()
    svc._backend = Exploding("healthy")

    with pytest.raises(RuntimeError):
        svc.switch_sync("work")

    assert svc.is_busy is False


def test_second_switch_is_rejected_while_busy(service):
    svc, _ = service
    svc.refresh_sync()
    svc._set_busy(True)
    try:
        ok, message = svc.switch_sync("work")
        assert ok is False
        assert "still running" in message
    finally:
        svc._set_busy(False)


# --- activity log ---------------------------------------------------------


def test_switch_writes_a_readable_activity_line(service):
    svc, _ = service
    svc.refresh_sync()
    svc.switch_sync("work")

    messages = [e.message for e in svc.recent_activity()]
    assert any("Switched from Personal to Work at" in m for m in messages)


def test_activity_log_never_contains_secrets(service, settings_service):
    """Property check over everything the log can be asked to record."""
    svc, backend = service
    svc.refresh_sync()

    poisoned = (
        'access_token="sk-ant-api03-ABCDEF1234567890" '
        "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"
    )
    svc.log(f"Refresh failed - {poisoned}", level="error")
    backend.fail_next_switch = CswapError(CswapErrorKind.REPORTED, poisoned)
    svc.switch_sync("work")
    backend.fail_next_list = CswapError(CswapErrorKind.TIMEOUT, poisoned)
    svc.refresh_sync()

    for entry in svc.recent_activity():
        assert not contains_secret(entry.message), entry.message
        assert "sk-ant" not in entry.message
        assert "eyJ" not in entry.message


def test_activity_log_masks_emails_on_disk(service):
    svc, _ = service
    svc.log("Switched to someone@example.invalid")
    messages = [e.message for e in svc.recent_activity()]
    assert any("s***@example.invalid" in m for m in messages)
    assert not any("someone@example.invalid" in m for m in messages)


def test_clear_activity_empties_history(service):
    svc, _ = service
    svc.refresh_sync()
    assert svc.recent_activity()

    svc.clear_activity()
    messages = [e.message for e in svc.recent_activity()]
    assert messages == ["Local activity history cleared"]


def test_failed_refresh_keeps_previous_state_on_screen(service):
    svc, backend = service
    svc.refresh_sync()
    before = svc.state("personal").account
    assert before is not None

    backend.fail_next_list = CswapError(CswapErrorKind.TIMEOUT, "timed out")
    assert svc.refresh_sync() is False

    # Numbers the user was reading are not blanked by a failed refresh.
    assert svc.state("personal").account is before


def test_run_command_resolves_alias_to_slot_number(service):
    svc, _ = service
    svc.refresh_sync()
    assert svc.run_command_for("work") == ["<mock-cswap>", "run", "2"]
    assert svc.run_command_for("personal") == ["<mock-cswap>", "run", "1"]


def test_run_command_is_none_when_unregistered(settings_service):
    svc = ProfileService(MockCswapClient("no_accounts"), settings_service)
    svc.refresh_sync()
    assert svc.run_command_for("work") is None


def test_apply_accounts_accepts_raw_fixture_payload(service):
    svc, _ = service
    svc.apply_accounts(AccountList.parse(fx.VALID_LIST))
    assert svc.state("personal").is_active is True
    assert svc.state("work").account.email == fx.WORK_EMAIL


def test_activity_timestamps_round_trip_in_local_time(service):
    """A reloaded entry must show the same clock time as a fresh one."""
    svc, _ = service
    emitted: list = []
    svc.activityAdded.connect(emitted.append)

    svc.log("Switched from Personal to Work")

    reloaded = svc.recent_activity()[-1]
    assert emitted[-1].timestamp.strftime("%H:%M") == reloaded.timestamp.strftime("%H:%M")


# --- account setup --------------------------------------------------------


@pytest.fixture
def empty_service(settings_service):
    backend = MockCswapClient("no_accounts")
    svc = ProfileService(backend, settings_service)
    svc.refresh_sync()
    return svc, backend


def test_setup_starts_with_nothing_registered(empty_service):
    svc, _ = empty_service
    assert svc.state("personal").is_registered is False
    assert svc.state("work").is_registered is False


def test_read_current_login_reports_unmanaged(empty_service):
    svc, _ = empty_service
    status = svc.read_current_login()
    assert status is not None
    assert status.managed is False
    assert status.email is not None
    assert svc.last_status is status


def test_register_current_account(empty_service):
    svc, backend = empty_service

    ok, message = svc.register_current_as("personal")

    assert ok is True
    assert "add:personal" in backend.calls
    assert svc.state("personal").is_registered is True
    assert "@example.invalid" in message


def test_registering_both_profiles_in_sequence(empty_service):
    svc, backend = empty_service

    assert svc.register_current_as("personal")[0] is True
    assert svc.register_current_as("work")[0] is True

    assert svc.state("personal").is_registered is True
    assert svc.state("work").is_registered is True
    # A freshly set-up install has exactly one active profile.
    assert sum(1 for s in svc.states if s.is_active) == 1


def test_register_duplicate_alias_fails_safely(empty_service):
    svc, _ = empty_service
    svc.register_current_as("personal")

    ok, message = svc.register_current_as("personal")

    assert ok is False
    assert "already registered" in message.lower()
    assert svc.state("personal").is_registered is True  # unchanged


def test_register_failure_reports_and_clears_busy(empty_service):
    svc, backend = empty_service
    backend.fail_next_add = CswapError(
        CswapErrorKind.REPORTED, "No Claude Code credentials were found."
    )
    seen: list[bool] = []
    svc.busyChanged.connect(seen.append)

    ok, message = svc.register_current_as("work")

    assert ok is False
    assert "No Claude Code credentials" in message
    assert svc.is_busy is False
    assert seen == [True, False]


def test_register_emits_setup_signals(empty_service):
    svc, _ = empty_service
    succeeded: list[tuple[str, str]] = []
    svc.setupSucceeded.connect(lambda k, m: succeeded.append((k, m)))

    svc.register_current_as("work")

    assert succeeded and succeeded[0][0] == "work"


def test_assign_alias_to_existing_account(service):
    svc, backend = service
    svc.refresh_sync()

    ok, message = svc.assign_alias(2, "work")

    assert ok is True
    assert "alias:2:work" in backend.calls
    assert "Work" in message


def test_setup_is_rejected_while_busy(empty_service):
    svc, _ = empty_service
    svc._set_busy(True)
    try:
        ok, message = svc.register_current_as("personal")
        assert ok is False
        assert "still running" in message
    finally:
        svc._set_busy(False)


def test_setup_activity_lines_are_safe(empty_service):
    svc, backend = empty_service
    backend.fail_next_add = CswapError(
        CswapErrorKind.REPORTED,
        'failed: access_token="sk-ant-api03-ABCDEF1234567890"',
    )
    svc.register_current_as("work")
    svc.register_current_as("personal")

    for entry in svc.recent_activity():
        assert not contains_secret(entry.message), entry.message
        assert "sk-ant" not in entry.message
