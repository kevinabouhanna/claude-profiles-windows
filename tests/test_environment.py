"""Choosing where Claude Code runs, and what the rest of the app does about it."""

from __future__ import annotations

import dataclasses

import pytest

from claude_profiles.app import build_backend, parse_args
from claude_profiles.services.cswap_client import CswapClient
from claude_profiles.services.environment import SwitchableBackend, resolve_backend
from claude_profiles.services.mock_cswap import MockCswapClient
from claude_profiles.services.polling_service import (
    PollingCoordinator,
    PollingService,
    PollResult,
)
from claude_profiles.services.settings_service import Settings, SettingsService
from claude_profiles.services.wsl import WslCswapClient
from claude_profiles.tray import TrayController


class SleepyMock(MockCswapClient):
    """A mock environment that can be asleep, like a stopped WSL distro."""

    def __init__(self, scenario: str = "healthy", *, asleep: bool = False) -> None:
        super().__init__(scenario)
        self.asleep = asleep
        self.sign_in = ["wsl.exe", "-d", "Ubuntu", "--exec", "claude", "auth", "login"]

    def dormant_reason(self) -> str | None:
        return "Ubuntu is not running" if self.asleep else None

    def sign_in_command(self) -> list[str] | None:
        return self.sign_in

    def sign_in_unavailable(self) -> str:
        return "Claude Code is not installed in Ubuntu."

    def describe(self) -> str:
        return "Using claude-swap 0.29.0 in Ubuntu."


# --- the backend that can change target -----------------------------------------


def test_resolving_a_setting_picks_the_right_client():
    assert isinstance(resolve_backend("windows"), CswapClient)
    wsl_client = resolve_backend("wsl:Ubuntu")
    assert isinstance(wsl_client, WslCswapClient) and wsl_client.distro == "Ubuntu"
    assert isinstance(resolve_backend("wsl:not valid"), CswapClient)


def test_swapping_targets_counts_generations():
    first, second = MockCswapClient("healthy"), MockCswapClient("two_accounts")
    backend = SwitchableBackend(first)
    assert len(backend.list_accounts().accounts) == 3
    backend.set_target(second, "wsl:Ubuntu")
    assert backend.generation == 1
    assert backend.label == "Ubuntu (WSL)"
    assert len(backend.list_accounts().accounts) == 2


def test_hooks_have_safe_defaults_for_targets_without_them():
    backend = SwitchableBackend(MockCswapClient())
    assert backend.dormant_reason() is None
    assert backend.sign_in_command() is None
    assert backend.describe() == ""


def test_the_app_starts_where_the_settings_say(tmp_path):
    args = parse_args([])
    backend, is_mock = build_backend(args, Settings(claude_environment="wsl:Ubuntu"))
    assert not is_mock and isinstance(backend, SwitchableBackend)
    assert isinstance(backend.target, WslCswapClient)

    backend, _ = build_backend(args, Settings())
    assert isinstance(backend.target, CswapClient)


# --- polling never wakes a sleeping environment by itself ----------------------


def test_a_background_poll_leaves_a_sleeping_distro_alone():
    backend = SleepyMock(asleep=True)
    coordinator = PollingCoordinator(backend, lambda: 120)

    result = coordinator.run_once(wake=False)

    assert result is not None and result.dormant == "Ubuntu is not running"
    assert "list" not in backend.calls
    # Neither a success nor a failure: no backoff, and the next visible poll runs.
    assert coordinator.consecutive_failures == 0
    assert coordinator.last_success_at is None


def test_a_poll_with_a_window_open_wakes_it():
    backend = SleepyMock(asleep=True)
    result = PollingCoordinator(backend, lambda: 120).run_once(wake=True)
    assert result is not None and result.accounts is not None
    assert backend.calls == ["list"]


def test_a_running_environment_is_polled_in_the_background():
    backend = SleepyMock(asleep=False)
    result = PollingCoordinator(backend, lambda: 120).run_once(wake=False)
    assert result is not None and result.accounts is not None


def test_a_poll_from_before_a_switch_is_dropped(qtbot):
    backend = SwitchableBackend(MockCswapClient("healthy"))
    service = PollingService(backend, lambda: 120)
    stale = PollResult(accounts=backend.list_accounts(), generation=backend.generation)
    backend.set_target(MockCswapClient("two_accounts"), "wsl:Ubuntu")

    with qtbot.assertNotEmitted(service.pollSucceeded, wait=0):
        service._on_result(stale)
    service.stop()


# --- the tray follows the setting -------------------------------------------------


@pytest.fixture
def env_controller(qapp, tmp_path):
    made: list[TrayController] = []

    def _make(*, wsl_target: SleepyMock | None = None) -> TrayController:
        target = wsl_target or SleepyMock("two_accounts")
        backend = SwitchableBackend(MockCswapClient("healthy"))
        ctl = TrayController(
            qapp,
            backend,
            SettingsService(data_dir=tmp_path / "data"),
            skip_windows_integration=True,
            environment_resolver=lambda value: (
                target if value.startswith("wsl:") else MockCswapClient("healthy")
            ),
            distro_lister=lambda: ["Ubuntu"],
        )
        made.append(ctl)
        return ctl

    yield _make
    for ctl in made:
        ctl.polling.stop()
        ctl.popup.hide()
        if ctl._window is not None:
            ctl._window.hide()


def _choose(ctl: TrayController, value: str) -> None:
    ctl._on_settings_changed(dataclasses.replace(ctl._settings, claude_environment=value))


def test_choosing_wsl_replaces_the_accounts_on_screen(env_controller, qtbot):
    ctl = env_controller()
    ctl.profiles.refresh_sync()
    assert len(ctl.profiles.profiles) == 3

    _choose(ctl, "wsl:Ubuntu")

    # The Windows accounts are gone at once, then the WSL ones arrive.
    qtbot.waitUntil(lambda: len(ctl.profiles.profiles) == 2, timeout=5000)
    assert ctl._settings_service.load().claude_environment == "wsl:Ubuntu"
    assert any(
        "Now managing Claude Code in Ubuntu (WSL)" in e.message
        for e in ctl.profiles.recent_activity()
    )


def test_switching_back_to_windows_restores_its_accounts(env_controller, qtbot):
    ctl = env_controller()
    _choose(ctl, "wsl:Ubuntu")
    qtbot.waitUntil(lambda: len(ctl.profiles.profiles) == 2, timeout=5000)

    _choose(ctl, "windows")

    qtbot.waitUntil(lambda: len(ctl.profiles.profiles) == 3, timeout=5000)


def test_the_settings_page_offers_installed_distros(env_controller, qtbot):
    ctl = env_controller()
    ctl._list_distros()
    window = ctl._ensure_window()
    combo = window._environment_combo

    qtbot.waitUntil(lambda: combo.count() == 2, timeout=5000)
    assert [combo.itemText(i) for i in range(combo.count())] == ["Windows", "Ubuntu (WSL)"]

    emitted: list[Settings] = []
    window.settingsChanged.connect(emitted.append)
    combo.setCurrentIndex(1)
    assert emitted and emitted[-1].claude_environment == "wsl:Ubuntu"


def test_the_settings_card_describes_the_wsl_target(env_controller, qtbot):
    ctl = env_controller()
    window = ctl._ensure_window()
    _choose(ctl, "wsl:Ubuntu")
    qtbot.waitUntil(
        lambda: "claude-swap 0.29.0 in Ubuntu" in window._environment_card.status(),
        timeout=5000,
    )


def test_sign_in_runs_claude_inside_the_distro(env_controller, monkeypatch):
    ctl = env_controller()
    _choose(ctl, "wsl:Ubuntu")
    launched: list[list[str]] = []
    monkeypatch.setattr(
        ctl.launcher, "launch", lambda argv, *a, **k: launched.append(argv) or _ok()
    )
    ctl._ensure_window()

    ctl._open_sign_in()

    assert launched and launched[0][:3] == ["wsl.exe", "-d", "Ubuntu"]


def test_sign_in_explains_a_missing_claude(env_controller):
    target = SleepyMock("two_accounts")
    target.sign_in = None
    ctl = env_controller(wsl_target=target)
    _choose(ctl, "wsl:Ubuntu")
    window = ctl._ensure_window()

    ctl._open_sign_in()

    assert "not installed in Ubuntu" in window.setup_page._result.text()


def test_a_hidden_app_reports_a_sleeping_distro_without_waking_it(env_controller, qtbot):
    target = SleepyMock("two_accounts", asleep=True)
    ctl = env_controller(wsl_target=target)
    _choose(ctl, "wsl:Ubuntu")
    qtbot.waitUntil(lambda: not ctl.polling.coordinator.is_polling, timeout=5000)
    target.calls.clear()

    assert not ctl._ui_visible()
    assert ctl.polling.poll_now()
    qtbot.waitUntil(lambda: "Ubuntu is not running" in ctl.popup._status_label.text(), timeout=5000)
    assert "list" not in target.calls


def _ok():
    from claude_profiles.services.process_launcher import LaunchResult

    return LaunchResult(True, "ok")


def test_a_persistent_failure_is_logged_once(tmp_path):
    """Picking WSL before installing claude-swap there must not log every poll."""
    from claude_profiles.services.cswap_client import CswapError, CswapErrorKind
    from claude_profiles.services.profile_service import ProfileService

    svc = ProfileService(MockCswapClient(), SettingsService(data_dir=tmp_path / "d"))
    error = CswapError(CswapErrorKind.NOT_INSTALLED, "claude-swap is not installed in Ubuntu.")
    for _ in range(3):
        svc.apply_error(error)
    svc.refresh_sync()
    svc.apply_error(error)

    failures = [e for e in svc.recent_activity() if "Usage refresh failed" in e.message]
    assert len(failures) == 2  # once, then again after a success in between
