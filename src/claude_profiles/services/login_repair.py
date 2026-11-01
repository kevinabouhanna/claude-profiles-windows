"""Pick up a fresh Claude Code sign-in that claude-swap has not stored yet.

claude-swap keeps its own copy of every account's login, including the one
Claude Code is using right now. When a token dies it marks the account
"re-login needed", and that mark holds while *any* stored copy still carries
the dead token. Signing in again with Claude Code replaces the live login but
not claude-swap's copy, so the account keeps reporting ``relogin_required`` -
and claude-swap stops fetching its usage - even though Claude Code works fine.
claude-swap's own remedy is to run ``cswap add`` after signing in, which copies
the current login into the account's slot and lifts the mark.

This module runs that step for the user, so signing in is the only thing they
ever have to do. It is deliberately narrow:

* It only acts on the account claude-swap reports as **active**, which is by
  definition the one Claude Code is signed in as, and only while that account
  reports a sign-in problem.
* It re-checks ``cswap status`` first and goes ahead only if the login still
  belongs to that same registered account, so a sign-in landing in between can
  never be registered as a new account.
* It runs a bare ``cswap add``: no slot, no alias, no network call. It never
  signs anyone in and never sees a credential.
* A login that really is dead stays dead after the copy, so attempts back off
  (two minutes, doubling, at most every half hour) instead of repeating on
  every poll. Opening the app, or pressing Fix sign-in, tries again at once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..models import Account, AccountList
from .cswap_client import CswapBackend, CswapError, CswapErrorKind
from .redaction import redact_exception

RETRY_BASE_SECONDS = 120.0
RETRY_MAX_SECONDS = 1800.0
# Even a requested attempt waits this long after the previous one, so opening
# and closing the flyout repeatedly cannot turn into a stream of cswap calls.
MIN_GAP_SECONDS = 20.0


@dataclass(frozen=True)
class RepairOutcome:
    """What one repair attempt did."""

    email: str
    number: int | None
    # True once the account no longer reports a sign-in problem.
    healed: bool = False
    # False when the attempt stopped before running ``add``, because Claude
    # Code turned out to be signed in as some other account.
    attempted: bool = False
    # The ``list`` taken after the repair; the poll uses it in place of its own.
    accounts: AccountList | None = None
    error: CswapError | None = None


def _identity(account: Account) -> str:
    return f"{account.number}:{account.email}"


class LoginRepair:
    """Decides when to re-store the active login, and does it.

    Shared by the poller and the Fix sign-in button, which run on different
    worker threads, so every attempt holds one lock from start to finish.
    """

    def __init__(
        self,
        backend: CswapBackend,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._clock = clock
        self._lock = threading.Lock()
        self._last_attempt: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._requested = False

    @staticmethod
    def candidate(accounts: AccountList | None) -> Account | None:
        """The account a repair could help, if any."""
        if accounts is None:
            return None
        active = accounts.active_account
        if active is None or not active.usage_status.needs_reauth:
            return None
        return active

    def request(self) -> None:
        """Let the next automatic check skip the backoff.

        Called when the user opens a window: they may have just signed in
        again, and the numbers they are about to read should reflect it.
        """
        self._requested = True

    def retry_delay(self, failures: int) -> float:
        if failures <= 0:
            return 0.0
        return min(RETRY_BASE_SECONDS * 2 ** (failures - 1), RETRY_MAX_SECONDS)

    def run(self, accounts: AccountList | None, *, force: bool = False) -> RepairOutcome | None:
        """Repair the active account if it needs it and an attempt is due.

        Returns ``None`` when nothing was tried. Never raises: a failure comes
        back in the outcome, so a poll is never lost to a repair.
        """
        with self._lock:
            account = self.candidate(accounts)
            self._forget_all_but(account)
            if account is None:
                self._requested = False
                return None
            if not self._due(account, force=force):
                return None
            self._requested = False
            key = _identity(account)
            self._last_attempt[key] = self._clock()
            try:
                outcome = self._attempt(account)
            except CswapError as exc:
                outcome = RepairOutcome(
                    email=account.email, number=account.number, attempted=True, error=exc
                )
            except Exception as exc:  # noqa: BLE001 - a repair must not kill the poll
                outcome = RepairOutcome(
                    email=account.email,
                    number=account.number,
                    attempted=True,
                    error=CswapError(CswapErrorKind.UNKNOWN, redact_exception(exc)),
                )
            if outcome.healed:
                self._last_attempt.pop(key, None)
                self._failures.pop(key, None)
            else:
                self._failures[key] = self._failures.get(key, 0) + 1
            return outcome

    # -- internals ----------------------------------------------------------

    def _forget_all_but(self, account: Account | None) -> None:
        """Drop bookkeeping for accounts that recovered or stopped being active,
        so a later problem with them is repaired straight away."""
        keep = _identity(account) if account is not None else None
        for book in (self._last_attempt, self._failures):
            for key in [k for k in book if k != keep]:
                del book[key]

    def _due(self, account: Account, *, force: bool) -> bool:
        key = _identity(account)
        last = self._last_attempt.get(key)
        if last is None:
            return True
        elapsed = self._clock() - last
        if elapsed < MIN_GAP_SECONDS:
            return False
        if force or self._requested:
            return True
        return elapsed >= self.retry_delay(self._failures.get(key, 0))

    def _attempt(self, account: Account) -> RepairOutcome:
        status = self._backend.status()
        same_login = (
            status.managed
            and status.email == account.email
            and (status.number is None or account.number is None or status.number == account.number)
        )
        if not same_login:
            # Claude Code moved to another login since the list was read; the
            # next poll sees the new state and decides afresh.
            return RepairOutcome(email=account.email, number=account.number)

        self._backend.resync_login()
        refreshed = self._backend.list_accounts()
        after = (
            refreshed.by_number(account.number) if account.number is not None else None
        ) or next((a for a in refreshed.accounts if a.email == account.email), None)
        healed = after is not None and not after.usage_status.needs_reauth
        return RepairOutcome(
            email=account.email,
            number=account.number,
            healed=healed,
            attempted=True,
            accounts=refreshed,
        )
