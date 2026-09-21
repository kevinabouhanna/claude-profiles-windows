"""Contract tests for the cswap subprocess layer."""

from __future__ import annotations

import subprocess

import pytest

from claude_profiles.models import UsageStatus
from claude_profiles.services.cswap_client import (
    ALLOWED_COMMANDS,
    CswapClient,
    CswapError,
    CswapErrorKind,
)

from . import fixtures as fx
from .conftest import FakeCompleted

# --- parsing valid output -------------------------------------------------


def test_parses_valid_list(make_client):
    client, runner = make_client(fx.dumps(fx.VALID_LIST))
    accounts = client.list_accounts()

    assert runner.last_args[1:] == ["list", "--json"]
    assert accounts.active_account_number == 1
    assert len(accounts.accounts) == 2

    personal = accounts.by_alias("personal")
    assert personal is not None
    assert personal.active is True
    assert personal.usage is not None
    assert personal.usage.five_hour is not None
    assert personal.usage.five_hour.pct == 42.0
    assert personal.usage.five_hour.countdown == "2h 13m"
    # Per-model rows arrive as the scoped array.
    assert [w.name for w in personal.usage.scoped] == ["Opus"]
    assert personal.is_stale is False


def test_parses_seven_day_pace_fields(make_client):
    client, _ = make_client(fx.dumps(fx.VALID_LIST))
    personal = client.list_accounts().by_alias("personal")
    assert personal is not None and personal.usage is not None
    seven = personal.usage.seven_day
    assert seven is not None
    assert seven.ahead_of_pace is True
    assert seven.expected_pct == 57.0
    assert seven.projected_exhaustion_at is not None


def test_parses_status_shape(make_client):
    """status returns {active:{email,managed}}, not a list account row."""
    client, runner = make_client(fx.dumps(fx.VALID_STATUS))
    status = client.status()
    assert runner.last_args[1:] == ["status", "--json"]
    assert status.email == fx.WORK_EMAIL
    assert status.managed is True


def test_status_reports_unmanaged_login(make_client):
    client, _ = make_client(fx.dumps(fx.UNMANAGED_STATUS))
    assert client.status().managed is False


def test_empty_account_list_is_not_an_error(make_client):
    client, _ = make_client(fx.dumps(fx.EMPTY_LIST))
    accounts = client.list_accounts()
    assert accounts.accounts == ()
    assert accounts.by_alias("personal") is None


# --- degraded states ------------------------------------------------------


def test_stale_account_keeps_last_known_values(make_client):
    client, _ = make_client(fx.dumps(fx.STALE_LIST))
    work = client.list_accounts().by_alias("work")
    assert work is not None
    assert work.usage is None
    assert work.is_stale is True
    # The retained reading is what the UI shows, explicitly marked stale.
    assert work.effective_usage is not None
    assert work.effective_usage.five_hour is not None
    assert work.effective_usage.five_hour.pct == 74.0
    assert work.effective_age_seconds == 1380.0


def test_unavailable_account_exposes_error_and_retry(make_client):
    client, _ = make_client(fx.dumps(fx.UNAVAILABLE_LIST))
    work = client.list_accounts().by_alias("work")
    assert work is not None
    assert work.usage_status is UsageStatus.UNAVAILABLE
    assert work.usage_error == "rate limited by upstream"
    assert work.usage_retry_at is not None
    assert work.is_stale is True


def test_expired_account_flags_reauth(make_client):
    client, _ = make_client(fx.dumps(fx.EXPIRED_LIST))
    work = client.list_accounts().by_alias("work")
    assert work is not None
    assert work.usage_status is UsageStatus.RELOGIN_REQUIRED
    assert work.usage_status.needs_reauth is True
    assert work.usage_status.label == "Re-authentication required"
    assert work.effective_usage is None


def test_unknown_status_value_degrades_to_unknown(make_client):
    payload = {
        "schemaVersion": 1,
        "accounts": [{"email": "a@b.invalid", "usageStatus": "something_new"}],
    }
    client, _ = make_client(fx.dumps(payload))
    account = client.list_accounts().accounts[0]
    assert account.usage_status is UsageStatus.UNKNOWN
    assert account.usage_status.needs_reauth is False


def test_unknown_schema_version_is_flagged_not_fatal(make_client):
    client, _ = make_client(fx.dumps(fx.UNKNOWN_SCHEMA_LIST))
    accounts = client.list_accounts()
    assert accounts.schema_version == 99
    assert accounts.is_known_schema is False
    assert len(accounts.accounts) == 2  # still parsed


# --- malformed and error output -------------------------------------------


def test_error_envelope_on_stdout_with_exit_1(make_client):
    """cswap prints its error object to stdout and exits 1."""
    client, _ = make_client(
        FakeCompleted(stdout=fx.dumps(fx.ERROR_ENVELOPE), returncode=1)
    )
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    error = excinfo.value
    assert error.kind is CswapErrorKind.REPORTED
    assert error.error_type == "AccountNotFound"
    assert "No account matches" in error.user_message


def test_truncated_json_raises_invalid_json(make_client):
    client, _ = make_client(fx.MALFORMED_TRUNCATED)
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert excinfo.value.kind is CswapErrorKind.INVALID_JSON


def test_non_json_output_raises_invalid_json(make_client):
    client, _ = make_client(fx.MALFORMED_NOT_JSON)
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert excinfo.value.kind is CswapErrorKind.INVALID_JSON


def test_invalid_json_error_does_not_leak_raw_output(make_client):
    """A parse failure must not echo whatever cswap printed."""
    secret = '{"access_token": "sk-ant-api03-SUPERSECRETVALUE"'
    client, _ = make_client(secret)
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert "sk-ant" not in excinfo.value.user_message
    assert "SUPERSECRET" not in excinfo.value.user_message


def test_leading_noise_before_json_is_tolerated(make_client):
    client, _ = make_client(fx.NOISE_THEN_JSON)
    assert len(client.list_accounts().accounts) == 2


def test_timeout_raises_timeout_kind(make_client, timeout_error):
    client, _ = make_client(timeout_error)
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert excinfo.value.kind is CswapErrorKind.TIMEOUT
    assert excinfo.value.kind.is_transient is True


def test_missing_executable_reports_not_installed():
    client = CswapClient(executable=None, discover=False)
    assert client.is_available is False
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert excinfo.value.kind is CswapErrorKind.NOT_INSTALLED
    assert "uv tool install claude-swap" in excinfo.value.user_message


def test_executable_vanishing_at_call_time(make_client):
    client, _ = make_client(FileNotFoundError("cswap.exe"))
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert excinfo.value.kind is CswapErrorKind.NOT_INSTALLED


def test_nonzero_exit_without_error_envelope(make_client):
    client, _ = make_client(FakeCompleted(stdout="{}", returncode=1))
    with pytest.raises(CswapError) as excinfo:
        client.list_accounts()
    assert excinfo.value.kind is CswapErrorKind.UNKNOWN


# --- allowlist ------------------------------------------------------------


def test_allowlist_contains_only_documented_commands():
    assert set(ALLOWED_COMMANDS) == {"list", "status", "switch", "run"}


@pytest.mark.parametrize(
    "command",
    ["add", "remove", "purge", "export", "import", "config", "auto", "add-token"],
)
def test_disallowed_commands_are_refused(make_client, command):
    client, runner = make_client("{}")
    with pytest.raises(CswapError) as excinfo:
        client._invoke([command, "--json"], 5)
    assert excinfo.value.kind is CswapErrorKind.DISALLOWED
    assert runner.calls == []  # nothing was ever spawned


@pytest.mark.parametrize(
    "alias",
    ["", "Personal", "has space", "semi;colon", "--flag", "a" * 40, "../x", "rm -rf"],
)
def test_invalid_aliases_are_refused(make_client, alias):
    client, runner = make_client("{}")
    with pytest.raises(CswapError) as excinfo:
        client.switch(alias)
    assert excinfo.value.kind is CswapErrorKind.DISALLOWED
    assert runner.calls == []


def test_injection_in_argument_is_refused(make_client):
    client, runner = make_client("{}")
    with pytest.raises(CswapError):
        client._invoke(["list", "&& shutdown /s"], 5)
    assert runner.calls == []


def test_never_uses_a_shell(make_client):
    client, runner = make_client(fx.dumps(fx.VALID_LIST))
    client.list_accounts()
    assert runner.kwargs[-1].get("shell") in (None, False)
    assert isinstance(runner.last_args, list)


def test_run_command_rejects_invalid_numbers():
    client = CswapClient(executable="cswap.exe")
    for bad in (-1, "1", None, 1.5):
        with pytest.raises(CswapError):
            client.build_run_command(bad)  # type: ignore[arg-type]


def test_run_command_uses_resolved_slot_number():
    client = CswapClient(executable="cswap.exe")
    assert client.build_run_command(2) == ["cswap.exe", "run", "2"]


# --- switching ------------------------------------------------------------


def test_switch_sends_alias(make_client):
    client, runner = make_client(fx.dumps({"schemaVersion": 1}))
    client.switch("work")
    assert runner.last_args[1:] == ["switch", "work", "--json"]


def test_switch_falls_back_to_number_when_alias_rejected(make_client):
    client, runner = make_client(
        FakeCompleted(stdout=fx.dumps(fx.ERROR_ENVELOPE), returncode=1),
        fx.dumps({"schemaVersion": 1, "active": {"email": fx.WORK_EMAIL, "number": 2}}),
    )
    outcome = client.switch("work", fallback_number=2)
    assert outcome.ok is True
    assert [c[1:] for c in runner.calls] == [
        ["switch", "work", "--json"],
        ["switch", "2", "--json"],
    ]


def test_switch_failure_without_fallback_propagates(make_client):
    client, _ = make_client(
        FakeCompleted(stdout=fx.dumps(fx.ERROR_ENVELOPE), returncode=1)
    )
    with pytest.raises(CswapError) as excinfo:
        client.switch("work")
    assert excinfo.value.kind is CswapErrorKind.REPORTED


def test_switch_timeout_uses_longer_budget(make_client):
    client, runner = make_client(fx.dumps({"schemaVersion": 1}))
    client.switch("work")
    assert runner.kwargs[-1]["timeout"] == 45.0


def test_list_uses_default_timeout(make_client):
    client, runner = make_client(fx.dumps(fx.VALID_LIST))
    client.list_accounts()
    assert runner.kwargs[-1]["timeout"] == 20.0


def test_switch_timeout_is_reported_as_timeout(make_client):
    client, _ = make_client(subprocess.TimeoutExpired(cmd="cswap", timeout=45))
    with pytest.raises(CswapError) as excinfo:
        client.switch("work")
    assert excinfo.value.kind is CswapErrorKind.TIMEOUT
