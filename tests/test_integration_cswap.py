"""Contract tests against the **real** claude-swap installation.

Everything else in this suite runs against the mock or canned payloads, so the
one thing never verified is the assumption the whole app rests on: that the
live tool still emits what the models expect. claude-swap is a separate
project on its own release cycle, and a renamed field would surface as an
empty dashboard rather than an error.

**These tests are read-only by construction, not by convention.** They run
through :class:`ReadOnlyCswapClient`, which refuses at the argv level to invoke
anything except ``list`` and ``status``. ``switch``, ``add``, ``alias`` and
``run`` cannot be reached from here even by mistake.

Opt in with::

    pytest --run-integration
    pytest --run-integration -m integration      # just these

Skipped by default, and skipped individually when claude-swap is absent or has
no accounts registered.
"""

from __future__ import annotations

import subprocess
from datetime import datetime

import pytest

from claude_profiles.models import UsageStatus, profiles_for
from claude_profiles.services.cswap_client import (
    CswapClient,
    CswapError,
    find_cswap,
)
from claude_profiles.services.redaction import contains_secret, mask_email

pytestmark = pytest.mark.integration

READ_ONLY = frozenset({"list", "status"})


class RefusedWrite(AssertionError):
    """Raised if a test ever attempts a state-changing command."""


class ReadOnlyCswapClient(CswapClient):
    """A client that physically cannot mutate claude-swap's state."""

    def __init__(self) -> None:
        super().__init__(runner=self._guarded_run)

    @staticmethod
    def _guarded_run(argv, **kwargs):
        # argv[0] is the executable; argv[1] is the subcommand.
        subcommand = argv[1] if len(argv) > 1 else ""
        if subcommand not in READ_ONLY:
            raise RefusedWrite(
                f"integration tests may not run {subcommand!r}; "
                f"allowed: {sorted(READ_ONLY)}"
            )
        return subprocess.run(argv, **kwargs)


@pytest.fixture(scope="module")
def client() -> ReadOnlyCswapClient:
    if find_cswap() is None:
        pytest.skip("claude-swap is not installed")
    return ReadOnlyCswapClient()


@pytest.fixture(scope="module")
def accounts(client):
    try:
        listing = client.list_accounts()
    except CswapError as exc:
        pytest.skip(f"claude-swap could not list accounts: {exc.user_message}")
    if not listing.accounts:
        pytest.skip("no accounts registered with claude-swap")
    return listing


# --- the guard itself -----------------------------------------------------


def test_the_read_only_guard_actually_refuses_writes(client):
    """If this fails, every other test in this file is unsafe.

    Two independent layers stop a write, and this checks both. Commands the
    app allows in production but that change state are stopped by this file's
    runner guard; commands the app never permits at all are stopped earlier,
    by CswapClient's own allowlist.
    """
    from claude_profiles.services.cswap_client import CswapErrorKind

    for command in ("switch", "add", "alias", "run"):
        with pytest.raises(RefusedWrite):
            client._invoke([command, "--json"], 5)

    for command in ("remove", "purge", "export", "import", "add-token"):
        with pytest.raises(CswapError) as excinfo:
            client._invoke([command, "--json"], 5)
        assert excinfo.value.kind is CswapErrorKind.DISALLOWED


def test_only_read_commands_reach_the_subprocess(client, accounts):
    """Whatever these tests did, only list and status were ever executed."""
    seen: list[str] = []
    original = client._runner

    def recording(argv, **kwargs):
        seen.append(argv[1] if len(argv) > 1 else "")
        return original(argv, **kwargs)

    client._runner = recording
    try:
        client.list_accounts()
        client.status()
    finally:
        client._runner = original

    assert set(seen) <= READ_ONLY


# --- the list contract ----------------------------------------------------


def test_list_returns_a_schema_the_app_understands(accounts):
    assert accounts.is_known_schema, (
        f"claude-swap now reports schemaVersion {accounts.schema_version}; "
        "the parsers were written for version 1"
    )


def test_every_account_carries_what_the_ui_renders(accounts):
    for account in accounts.accounts:
        assert account.email, "an account with no address cannot be labelled"
        assert account.number is not None, "the launcher resolves aliases to slots"
        assert isinstance(account.active, bool)


def test_usage_status_is_a_value_the_app_recognises(accounts):
    """An unknown status degrades to UNKNOWN, which renders as "Unknown state"."""
    unknown = [a for a in accounts.accounts if a.usage_status is UsageStatus.UNKNOWN]
    assert not unknown, (
        f"{len(unknown)} account(s) report a usageStatus not in the known set; "
        "claude-swap may have added one"
    )


def test_exactly_one_account_is_active(accounts):
    active = [a for a in accounts.accounts if a.active]
    assert len(active) == 1, f"expected one active account, found {len(active)}"


def test_the_active_flag_agrees_with_the_active_number(accounts):
    active = accounts.active_account
    assert active is not None
    if accounts.active_account_number is not None:
        assert active.number == accounts.active_account_number


# --- usage payloads -------------------------------------------------------


def test_usage_windows_parse_and_are_in_range(accounts):
    seen = 0
    for account in accounts.accounts:
        usage = account.effective_usage
        if usage is None:
            continue
        for label, window in (("5h", usage.five_hour), ("7d", usage.seven_day)):
            if window is None:
                continue
            seen += 1
            assert 0.0 <= window.pct <= 100.0, (
                f"{label} reported {window.pct}% for account {account.number}"
            )
            if window.resets_at is not None:
                assert isinstance(window.resets_at, datetime)
    if seen == 0:
        pytest.skip("no usage readings available right now")


def test_at_least_one_account_has_a_live_reading(accounts):
    """If every account is stale the dashboard shows nothing current."""
    live = [a for a in accounts.accounts if a.usage is not None]
    if not live:
        pytest.skip("all readings are currently stale; not a contract failure")
    assert live


def test_scoped_windows_are_named_when_present(accounts):
    for account in accounts.accounts:
        usage = account.effective_usage
        if usage is None:
            continue
        for window in usage.scoped:
            assert window.name, "a per-model row with no name cannot be labelled"


def test_freshness_metadata_accompanies_a_live_reading(accounts):
    for account in accounts.accounts:
        if account.usage is None:
            continue
        assert account.usage_fetched_at is not None or account.usage_age_seconds is not None, (
            "the UI reports how old a reading is; neither field was present"
        )


# --- the status contract --------------------------------------------------


def test_status_reports_the_current_login(client):
    status = client.status()
    assert status.email, "status must name the signed-in account"
    assert isinstance(status.managed, bool)


def test_status_and_list_agree_on_the_active_account(client, accounts):
    status = client.status()
    if not status.managed:
        pytest.skip("the current login is not managed by claude-swap")
    active = accounts.active_account
    assert active is not None
    assert status.email == active.email, (
        f"status says {mask_email(status.email)} but list says "
        f"{mask_email(active.email)}"
    )


# --- this machine's setup -------------------------------------------------


def test_every_registered_account_becomes_a_profile(accounts):
    """Checks this installation, not just the contract."""
    if not accounts.accounts:
        pytest.skip("no accounts registered with claude-swap yet")
    profiles = profiles_for(accounts)
    assert len(profiles) == len(accounts.accounts)
    assert len({p.key for p in profiles}) == len(profiles), "profile keys must be unique"
    for profile in profiles:
        assert profile.number is not None


def test_a_launch_command_can_be_built_for_each_profile(client, accounts):
    """build_run_command only assembles argv; it never spawns anything."""
    for account in accounts.accounts:
        if account.number is None:
            continue
        argv = client.build_run_command(account.number)
        assert argv[1:] == ["run", str(account.number)]


# --- privacy --------------------------------------------------------------


def test_nothing_the_app_would_log_contains_a_secret(accounts):
    """Fields that reach the activity log and the UI must stay clean."""
    for account in accounts.accounts:
        for value in (account.usage_error, account.organization_name, account.alias):
            if value:
                assert not contains_secret(value)


def test_a_real_address_is_masked_before_it_reaches_disk(accounts):
    """The activity log stores masked addresses; check that against real ones."""
    from claude_profiles.services.redaction import safe_for_log

    for account in accounts.accounts:
        logged = safe_for_log(f"Switched to {account.email}")
        assert account.email not in logged, "the full address reached the log line"
        assert "***@" in logged
        # The domain is kept deliberately, so the line stays useful.
        assert account.email.split("@", 1)[1] in logged


def test_a_real_payload_survives_redaction_unharmed(accounts):
    """Redaction must not mangle legitimate values such as organization UUIDs."""
    from claude_profiles.services.redaction import redact

    for account in accounts.accounts:
        if account.organization_name:
            assert redact(account.organization_name) == account.organization_name
