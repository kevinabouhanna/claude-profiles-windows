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
from .redaction import redact_exception

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

    def try_begin(self) -> bool:
        """Claim the single poll slot, or return False if one is running.

        Public so the caller can claim *before* announcing a poll has started.
        Checking `is_polling` and then spawning a worker is not atomic: two
        callers could both pass the check, both announce a start, and only one
        do any work - leaving the status line stuck on "Refreshing...".
        """
        return self._try_begin()

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
        return self.run_claimed()

    def run_claimed(self) -> PollResult:
        """Run a poll whose slot the caller already claimed."""
        try:
            accounts = self._backend.list_accounts()
        except CswapError as exc:
            # A transient failure advances the backoff; a hard one (cswap
            # missing) does not, because retrying faster will not help.
            if exc.kind.is_transient or exc.kind is CswapErrorKind.REPORTED:
                self._consecutive_failures += 1
            self.last_error = exc
            return PollResult(error=exc)
        except Exception as exc:  # noqa: BLE001 - see below
            # Anything not already a CswapError - a decode error, an OSError
            # from the subprocess layer, a bug in parsing - must still come
            # back as a result. Letting it escape kills the worker thread
            # before it can report, so the caller never reschedules and
            # automatic polling stops for the rest of the session.
            wrapped = CswapError(CswapErrorKind.UNKNOWN, redact_exception(exc))
            self._consecutive_failures += 1
            self.last_error = wrapped
            return PollResult(error=wrapped)
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
        # A poll runs on a worker thread that holds a reference to this object
        # and emits into it when it finishes. Nothing used to wait for that
        # thread, so the object could be torn down first and the emit would
        # land on freed memory - an access violation, not an exception.
        self._shutdown = threading.Event()
        self._worker: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.poll_now()

    def stop(self, timeout: float = 30.0) -> None:
        """Stop polling and wait for any in-flight poll to finish.

        Waiting matters: the worker emits into this object, so returning while
        it is still running leaves a thread holding a reference to something
        the caller is about to destroy.
        """
        self._running = False
        self._shutdown.set()
        self._timer.stop()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)
        self._worker = None

    def reschedule(self) -> None:
        """Restart the countdown, e.g. after the interval setting changed."""
        if not self._running:
            return
        self._timer.start(int(self.coordinator.next_delay() * 1000))

    def poll_now(self) -> bool:
        """Request a poll. Returns False if one is already in flight."""
        if self._shutdown.is_set() or not self.coordinator.try_begin():
            return False
        self.pollStarted.emit()
        thread = threading.Thread(target=self._work, name="cswap-poll", daemon=True)
        self._worker = thread
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
        # run_once already converts unexpected errors into a result; this guard
        # covers a failure in run_once itself. The loop only continues because
        # _on_result runs, so the worker must always emit exactly once.
        try:
            result = self.coordinator.run_claimed()
        except Exception as exc:  # noqa: BLE001 - a dead loop is worse
            result = PollResult(
                error=CswapError(CswapErrorKind.UNKNOWN, redact_exception(exc))
            )
        if self._shutdown.is_set():
            # Being torn down: the receiver may already be gone.
            return
        self._resultReady.emit(result)

    @Slot(object)
    def _on_result(self, result: PollResult | None) -> None:
        # Runs on the GUI thread, so the timer below is safe to touch.
        if result is None:
            # Should not happen now the slot is claimed up front, but a start
            # was announced, so a finish must follow or the UI stays busy.
            self.pollFinished.emit()
            self.reschedule()
            return
        if result.accounts is not None:
            self.pollSucceeded.emit(result.accounts)
        elif result.error is not None:
            self.pollFailed.emit(result.error)
        self.pollFinished.emit()
        self.reschedule()
