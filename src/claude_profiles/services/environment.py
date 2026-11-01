"""Where Claude Code runs: Windows, or a WSL distribution.

The rest of the app holds one :class:`SwitchableBackend` for its whole life.
Changing the setting swaps what is behind it, so the profile service, the
poller and the sign-in repair all follow without being rebuilt. ``generation``
counts the swaps, so a poll that started against the old target can be
recognised and dropped instead of putting the old accounts back on screen.
"""

from __future__ import annotations

import threading

from ..models import AccountList, ActiveStatus, SwitchOutcome
from .cswap_client import CswapBackend, CswapClient
from .wsl import WINDOWS, WslCswapClient, distro_of, environment_label


def resolve_backend(value: str) -> CswapBackend:
    """The backend for a settings value. Anything unrecognised is Windows."""
    distro = distro_of(value)
    if distro is not None:
        return WslCswapClient(distro)
    return CswapClient()


class SwitchableBackend:
    """A :class:`CswapBackend` that forwards to whichever target is current."""

    def __init__(self, target: CswapBackend, value: str = WINDOWS) -> None:
        self._lock = threading.Lock()
        self._target = target
        self._value = value
        self._generation = 0

    @property
    def target(self) -> CswapBackend:
        return self._target

    @property
    def value(self) -> str:
        return self._value

    @property
    def label(self) -> str:
        return environment_label(self._value)

    @property
    def generation(self) -> int:
        return self._generation

    def set_target(self, target: CswapBackend, value: str) -> None:
        with self._lock:
            self._target = target
            self._value = value
            self._generation += 1

    # -- environment hooks ------------------------------------------------------

    def dormant_reason(self) -> str | None:
        hook = getattr(self._target, "dormant_reason", None)
        return hook() if hook else None

    def sign_in_command(self) -> list[str] | None:
        hook = getattr(self._target, "sign_in_command", None)
        return hook() if hook else None

    def sign_in_unavailable(self) -> str:
        hook = getattr(self._target, "sign_in_unavailable", None)
        return hook() if hook else "Claude Code could not be found."

    def describe(self) -> str:
        hook = getattr(self._target, "describe", None)
        return hook() if hook else ""

    # -- CswapBackend -----------------------------------------------------------

    @property
    def is_available(self) -> bool:
        return self._target.is_available

    @property
    def executable(self) -> str | None:
        return getattr(self._target, "executable", None)

    def list_accounts(self) -> AccountList:
        return self._target.list_accounts()

    def status(self) -> ActiveStatus:
        return self._target.status()

    def switch(self, alias: str | None, *, fallback_number: int | None = None) -> SwitchOutcome:
        return self._target.switch(alias, fallback_number=fallback_number)

    def add_current_account(self, alias: str) -> str:
        return self._target.add_current_account(alias)

    def resync_login(self) -> str:
        return self._target.resync_login()

    def set_alias(self, number: int, alias: str) -> str:
        return self._target.set_alias(number, alias)

    def build_run_command(self, number: int) -> list[str]:
        return self._target.build_run_command(number)
