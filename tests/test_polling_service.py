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
