"""Usage polling with a hard single-flight guarantee and error backoff.

The scheduling logic lives in :class:`PollingCoordinator`, which is plain
Python and holds the overlap guard and backoff ladder. :class:`PollingService`
is a thin Qt shell that drives it from a timer and a worker thread. Keeping the
two apart means the concurrency rules can be tested directly, without an event
loop, and the Qt layer stays trivial enough to read at a glance.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..models import AccountList
from .cswap_client import CswapBackend, CswapError, CswapErrorKind

MAX_BACKOFF_SECONDS = 900.0

# While a window is on screen the numbers are being read, so they are refreshed
# more often. Hidden, the app falls back to the user's configured interval.
FOREGROUND_INTERVAL_SECONDS = 30.0

# How stale a reading may be before opening a window triggers a refresh.
ON_DEMAND_MAX_AGE_SECONDS = 10.0


def effective_interval(configured: float, ui_visible: bool) -> float:
    """The polling interval to use right now.

    Never slower than the user asked for, and never faster than they asked for
    either when nothing is being displayed.
    """
    if ui_visible:
        return min(float(configured), FOREGROUND_INTERVAL_SECONDS)
    return float(configured)


@dataclass(frozen=True)
class PollResult:
    """Outcome of one completed poll."""

    accounts: AccountList | None = None
    error: CswapError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class PollingCoordinator:
    """Owns the overlap guard and the backoff ladder.

    A poll is claimed atomically: if one is already running, :meth:`run_once`
    returns ``None`` immediately rather than queueing a second subprocess. This
    is what guarantees the app never has two ``cswap`` calls in flight.
    """

    def __init__(
        self,
        backend: CswapBackend,
        interval_provider: Callable[[], float],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._interval_provider = interval_provider
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight = False
        self._consecutive_failures = 0
        self.last_result: AccountList | None = None
        self.last_error: CswapError | None = None
        self.last_success_at: float | None = None
        self.skipped_polls = 0

    @property
    def is_polling(self) -> bool:
        with self._lock:
            return self._inflight

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def _try_begin(self) -> bool:
        with self._lock:
            if self._inflight:
                self.skipped_polls += 1
                return False
            self._inflight = True
            return True

    def _end(self) -> None:
        with self._lock:
            self._inflight = False

    def run_once(self) -> PollResult | None:
        """Run one poll, or return ``None`` if one is already in flight."""
        if not self._try_begin():
            return None
        try:
            accounts = self._backend.list_accounts()
        except CswapError as exc:
            # A transient failure advances the backoff; a hard one (cswap
            # missing) does not, because retrying faster will not help.
            if exc.kind.is_transient or exc.kind is CswapErrorKind.REPORTED:
                self._consecutive_failures += 1
            self.last_error = exc
            return PollResult(error=exc)
        else:
            self._consecutive_failures = 0
            self.last_error = None
            self.last_result = accounts
            self.last_success_at = self._clock()
            return PollResult(accounts=accounts)
        finally:
            self._end()

    def next_delay(self) -> float:
        """Seconds until the next poll: the configured interval, doubled per
        consecutive failure, capped."""
        interval = max(5.0, float(self._interval_provider()))
        if self._consecutive_failures == 0:
            return interval
        delay = interval * (2 ** self._consecutive_failures)
        return min(delay, MAX_BACKOFF_SECONDS)

    def seconds_since_success(self) -> float | None:
        if self.last_success_at is None:
            return None
        return self._clock() - self.last_success_at


class PollingService(QObject):
    """Drives :class:`PollingCoordinator` from a Qt timer and a worker thread."""

    pollStarted = Signal()
    pollSucceeded = Signal(object)  # AccountList
    pollFailed = Signal(object)  # CswapError
    pollFinished = Signal()

    # Carries the worker thread's result back to the GUI thread. Qt queues a
    # signal across threads; QTimer.singleShot does not - called off the GUI
    # thread it creates its timer *in that thread*, which has no event loop, so
    # the callback never runs. Rescheduling through singleShot is why automatic
    # polling used to stop dead after the very first poll.
    _resultReady = Signal(object)  # PollResult | None

    def __init__(
        self,
        backend: CswapBackend,
        interval_provider: Callable[[], float],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.coordinator = PollingCoordinator(backend, interval_provider)
        self._timer = QTimer(self)
        # Single-shot, rescheduled after each completion. A repeating timer
        # could stack ticks behind a slow poll; this cannot.
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.poll_now)
        self._resultReady.connect(self._on_result)
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.poll_now()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    def reschedule(self) -> None:
        """Restart the countdown, e.g. after the interval setting changed."""
        if not self._running:
            return
        self._timer.start(int(self.coordinator.next_delay() * 1000))

    def poll_now(self) -> bool:
        """Request a poll. Returns False if one is already in flight."""
        if self.coordinator.is_polling:
            return False
        self.pollStarted.emit()
        thread = threading.Thread(target=self._work, name="cswap-poll", daemon=True)
        thread.start()
        return True

    def poll_if_stale(self, max_age: float = ON_DEMAND_MAX_AGE_SECONDS) -> bool:
        """Poll unless the last reading is newer than ``max_age``.

        Used when a window opens: the numbers should be current the moment the
        user looks at them, but re-running cswap because they reopened the
        flyout two seconds later is pure noise.
        """
        age = self.coordinator.seconds_since_success()
        if age is not None and age < max_age:
            return False
        return self.poll_now()

    def _work(self) -> None:
        self._resultReady.emit(self.coordinator.run_once())

    @Slot(object)
    def _on_result(self, result: PollResult | None) -> None:
        # Runs on the GUI thread, so the timer below is safe to touch.
        if result is None:
            # Lost the race against another poll; the winner reports.
            return
        if result.accounts is not None:
            self.pollSucceeded.emit(result.accounts)
        elif result.error is not None:
            self.pollFailed.emit(result.error)
        self.pollFinished.emit()
        self.reschedule()
