"""Tests for the polling coordinator: overlap prevention, backoff, staleness."""

from __future__ import annotations

import threading
import time

import pytest

from claude_profiles.models import AccountList
from claude_profiles.services.cswap_client import CswapError, CswapErrorKind
from claude_profiles.services.polling_service import (
    MAX_BACKOFF_SECONDS,
    PollingCoordinator,
)

from . import fixtures as fx


class SlowBackend:
    """Counts calls and holds each one open long enough to race."""

    def __init__(self, delay: float = 0.05, error: CswapError | None = None) -> None:
        self.delay = delay
        self.error = error
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def list_accounts(self) -> AccountList:
        with self._lock:
            self.calls += 1
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            return AccountList.parse(fx.VALID_LIST)
        finally:
            with self._lock:
                self.concurrent -= 1


def test_single_poll_succeeds():
    backend = SlowBackend(delay=0)
    coordinator = PollingCoordinator(backend, lambda: 120)
    result = coordinator.run_once()
    assert result is not None and result.ok
    assert result.accounts is not None
    assert len(result.accounts.accounts) == 2
    assert coordinator.last_success_at is not None


def test_concurrent_polls_cannot_overlap():
    """50 threads calling run_once must produce exactly one backend call."""
    backend = SlowBackend(delay=0.1)
    coordinator = PollingCoordinator(backend, lambda: 120)
    results: list[object] = []
    barrier = threading.Barrier(50)

    def worker() -> None:
        barrier.wait()
        results.append(coordinator.run_once())

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert backend.calls == 1
    assert backend.max_concurrent == 1
    # Exactly one caller got a result; the other 49 were told a poll was running.
    assert sum(1 for r in results if r is not None) == 1
    assert coordinator.skipped_polls == 49


def test_guard_releases_after_completion():
    backend = SlowBackend(delay=0)
    coordinator = PollingCoordinator(backend, lambda: 120)
    assert coordinator.run_once() is not None
    assert coordinator.is_polling is False
    assert coordinator.run_once() is not None
    assert backend.calls == 2


def test_guard_releases_after_failure():
    """A raised error must not leave the coordinator permanently blocked."""
    error = CswapError(CswapErrorKind.TIMEOUT, "took too long")
    backend = SlowBackend(delay=0, error=error)
    coordinator = PollingCoordinator(backend, lambda: 120)

    result = coordinator.run_once()
    assert result is not None and not result.ok
    assert coordinator.is_polling is False

    backend.error = None
    assert coordinator.run_once() is not None
    assert backend.calls == 2


# --- backoff --------------------------------------------------------------


def test_default_delay_is_the_configured_interval():
    coordinator = PollingCoordinator(SlowBackend(delay=0), lambda: 120)
    assert coordinator.next_delay() == 120


def test_backoff_doubles_then_caps():
    error = CswapError(CswapErrorKind.TIMEOUT, "nope")
    backend = SlowBackend(delay=0, error=error)
    coordinator = PollingCoordinator(backend, lambda: 120)

    delays = []
    for _ in range(5):
        coordinator.run_once()
        delays.append(coordinator.next_delay())

    assert delays[0] == 240
    assert delays[1] == 480
    assert delays[2] == MAX_BACKOFF_SECONDS
    assert all(d <= MAX_BACKOFF_SECONDS for d in delays)


def test_backoff_resets_after_one_success():
    error = CswapError(CswapErrorKind.TIMEOUT, "nope")
    backend = SlowBackend(delay=0, error=error)
    coordinator = PollingCoordinator(backend, lambda: 120)

    coordinator.run_once()
    coordinator.run_once()
    assert coordinator.consecutive_failures == 2

    backend.error = None
    coordinator.run_once()
    assert coordinator.consecutive_failures == 0
    assert coordinator.next_delay() == 120


def test_missing_executable_does_not_inflate_backoff():
    """Retrying faster will not conjure an installation, so do not escalate."""
    error = CswapError(CswapErrorKind.NOT_INSTALLED, "not installed")
    coordinator = PollingCoordinator(SlowBackend(delay=0, error=error), lambda: 120)
    coordinator.run_once()
    assert coordinator.consecutive_failures == 0
    assert coordinator.next_delay() == 120


def test_interval_changes_are_picked_up_live():
    interval = {"value": 120.0}
    coordinator = PollingCoordinator(SlowBackend(delay=0), lambda: interval["value"])
    assert coordinator.next_delay() == 120
    interval["value"] = 300.0
    assert coordinator.next_delay() == 300


def test_interval_has_a_sane_floor():
    coordinator = PollingCoordinator(SlowBackend(delay=0), lambda: 0)
    assert coordinator.next_delay() >= 5


# --- last-known values ----------------------------------------------------


def test_failed_poll_preserves_previous_result():
    backend = SlowBackend(delay=0)
    coordinator = PollingCoordinator(backend, lambda: 120)
    coordinator.run_once()
    previous = coordinator.last_result
    assert previous is not None

    backend.error = CswapError(CswapErrorKind.TIMEOUT, "nope")
    result = coordinator.run_once()

    assert result is not None and not result.ok
    # The last good payload is still available for the UI to render as stale.
    assert coordinator.last_result is previous
    assert coordinator.last_error is not None


def test_seconds_since_success_tracks_a_clock():
    now = {"t": 1000.0}
    coordinator = PollingCoordinator(
        SlowBackend(delay=0), lambda: 120, clock=lambda: now["t"]
    )
    assert coordinator.seconds_since_success() is None
    coordinator.run_once()
    now["t"] = 1075.0
    assert coordinator.seconds_since_success() == pytest.approx(75.0)


# --- the Qt loop ----------------------------------------------------------
#
# Everything above exercises PollingCoordinator, which is plain Python. That
# left the Qt half untested, and automatic polling was silently dead: the
# worker thread rescheduled with QTimer.singleShot, which off the GUI thread
# creates its timer in a thread with no event loop, so it never fired. One poll
# at startup and nothing afterwards. These tests drive the real service.


class CountingBackend:
    def __init__(self) -> None:
        self.calls = 0

    def list_accounts(self) -> AccountList:
        self.calls += 1
        return AccountList.parse(fx.VALID_LIST)


@pytest.fixture
def service(qtbot, request):
    """Build a PollingService that is always stopped before the test ends.

    Without this the worker thread outlives the QObject it emits into, which
    is an access violation rather than a test failure - it takes the whole
    pytest process down, intermittently.
    """
    from claude_profiles.services.polling_service import PollingService

    created = []

    def _make(interval: float = 5.0):
        backend = CountingBackend()
        svc = PollingService(backend, lambda: interval)
        created.append(svc)
        return svc, backend

    yield _make

    for svc in created:
        svc.stop()


def test_polling_keeps_going_after_the_first_poll(service, qtbot):
    """The regression that made users press Refresh by hand."""
    svc, backend = service(interval=5.0)  # 5s is the floor in next_delay
    svc.start()

    qtbot.waitUntil(lambda: backend.calls >= 3, timeout=20000)
    assert backend.calls >= 3
    svc.stop()


def test_results_reach_the_gui_thread(service, qtbot):
    svc, _ = service()
    received: list[object] = []
    svc.pollSucceeded.connect(received.append)

    svc.start()
    qtbot.waitUntil(lambda: bool(received), timeout=10000)

    assert isinstance(received[0], AccountList)
    svc.stop()


def test_a_timer_is_armed_after_each_poll(service, qtbot):
    svc, backend = service()
    svc.start()
    qtbot.waitUntil(lambda: backend.calls >= 1, timeout=10000)
    qtbot.waitUntil(lambda: svc._timer.isActive(), timeout=5000)
    assert svc._timer.isActive()
    svc.stop()


def test_stop_halts_the_loop(service, qtbot):
    svc, backend = service()
    svc.start()
    qtbot.waitUntil(lambda: backend.calls >= 1, timeout=10000)
    svc.stop()
    assert svc._timer.isActive() is False
    assert svc.is_running is False


# --- refresh on demand ----------------------------------------------------


def test_poll_if_stale_polls_when_there_is_no_reading(service, qtbot):
    svc, backend = service()
    assert svc.poll_if_stale() is True
    qtbot.waitUntil(lambda: backend.calls == 1, timeout=10000)


def test_poll_if_stale_skips_a_fresh_reading(service, qtbot):
    """Reopening the flyout seconds later should not re-run cswap."""
    svc, backend = service()
    svc.poll_now()
    qtbot.waitUntil(lambda: backend.calls == 1, timeout=10000)

    assert svc.poll_if_stale(max_age=60) is False
    assert backend.calls == 1


def test_poll_if_stale_polls_once_the_reading_ages(service, qtbot):
    svc, backend = service()
    svc.poll_now()
    qtbot.waitUntil(lambda: backend.calls == 1, timeout=10000)

    assert svc.poll_if_stale(max_age=0) is True
    qtbot.waitUntil(lambda: backend.calls == 2, timeout=10000)


# --- adaptive cadence -----------------------------------------------------


def test_visible_ui_polls_faster():
    from claude_profiles.services.polling_service import (
        FOREGROUND_INTERVAL_SECONDS,
        effective_interval,
    )

    assert effective_interval(120, True) == FOREGROUND_INTERVAL_SECONDS
    assert effective_interval(120, False) == 120


def test_a_short_configured_interval_is_never_slowed_down():
    from claude_profiles.services.polling_service import effective_interval

    assert effective_interval(15, True) == 15
    assert effective_interval(15, False) == 15


def test_hidden_ui_uses_the_configured_interval():
    from claude_profiles.services.polling_service import effective_interval

    for configured in (30, 60, 120, 600, 3600):
        assert effective_interval(configured, False) == configured


# --- surviving unexpected errors ------------------------------------------
#
# run_once used to catch only CswapError. Anything else escaped the worker
# thread before it could report, so the GUI thread never rescheduled and
# automatic polling stopped for the rest of the session - the same symptom as
# a dead timer, reached by a different route.


class ExplodingBackend:
    """Raises something that is *not* a CswapError on a chosen call."""

    def __init__(self, fail_on: int = 2, error: Exception | None = None) -> None:
        self.calls = 0
        self.fail_on = fail_on
        self.error = error or RuntimeError("unexpected boom")

    def list_accounts(self) -> AccountList:
        self.calls += 1
        if self.calls == self.fail_on:
            raise self.error
        return AccountList.parse(fx.VALID_LIST)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("boom"),
        ValueError("bad value"),
        OSError("device not ready"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        KeyError("missing"),
    ],
)
def test_unexpected_errors_become_results(error):
    backend = ExplodingBackend(fail_on=1, error=error)
    coordinator = PollingCoordinator(backend, lambda: 120)

    result = coordinator.run_once()

    assert result is not None, "must report rather than raise"
    assert result.ok is False
    assert result.error is not None
    assert coordinator.is_polling is False


def test_unexpected_error_does_not_leak_its_text():
    secret = RuntimeError('token="sk-ant-api03-ABCDEF1234567890"')
    coordinator = PollingCoordinator(ExplodingBackend(1, secret), lambda: 120)

    result = coordinator.run_once()

    assert result is not None and result.error is not None
    assert "sk-ant" not in result.error.user_message


def test_unexpected_error_advances_backoff_then_recovers():
    backend = ExplodingBackend(fail_on=1)
    coordinator = PollingCoordinator(backend, lambda: 120)

    coordinator.run_once()
    assert coordinator.consecutive_failures == 1

    assert coordinator.run_once().ok is True
    assert coordinator.consecutive_failures == 0


def test_polling_loop_survives_an_unexpected_error(qtbot):
    """The regression: one odd exception used to stop polling forever."""
    from claude_profiles.services.polling_service import PollingService

    backend = ExplodingBackend(fail_on=2)
    svc = PollingService(backend, lambda: 5.0)
    try:
        svc.start()
        qtbot.waitUntil(lambda: backend.calls >= 3, timeout=30000)
        assert backend.calls >= 3
        assert svc._timer.isActive()
    finally:
        svc.stop()
