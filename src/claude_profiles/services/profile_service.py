"""Profile state, switch orchestration, and the activity log.

This is the only object the UI talks to. It maps configured aliases onto the
accounts ``cswap list --json`` reports, owns the busy flag that disables switch
controls, and records safe activity lines.

The busy flag is always cleared in a ``finally`` block, so the UI returns to an
enabled state after success, failure, and timeout alike.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterable
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from ..models import (
    DEFAULT_PROFILES,
    AccountList,
    ActiveStatus,
    ActivityEntry,
    Profile,
    ProfileState,
)
from .cswap_client import CswapBackend, CswapError
from .settings_service import SettingsService


class ProfileService(QObject):
    """Owns per-profile state and every action the user can take on it."""

    statesChanged = Signal()
    busyChanged = Signal(bool)
    activityAdded = Signal(object)  # ActivityEntry
    switchSucceeded = Signal(str)  # profile key
    switchFailed = Signal(str, str)  # profile key, user-safe message
    setupSucceeded = Signal(str, str)  # profile key, user-safe message
    setupFailed = Signal(str, str)  # profile key, user-safe message
    schemaWarning = Signal(int)  # unrecognised schemaVersion

    def __init__(
        self,
        backend: CswapBackend,
        settings_service: SettingsService,
        profiles: Iterable[Profile] = DEFAULT_PROFILES,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._settings = settings_service
        self._profiles = tuple(profiles)
        self._states: dict[str, ProfileState] = {
            p.key: ProfileState(profile=p) for p in self._profiles
        }
        self._busy = False
        self._busy_lock = threading.Lock()
        self._last_active_key: str | None = None
        self._warned_schema = False
        # Retained so the setup page can show accounts that carry no alias yet.
        self.last_accounts: AccountList | None = None
        self.last_status: ActiveStatus | None = None

    # -- state --------------------------------------------------------------

    @property
    def profiles(self) -> tuple[Profile, ...]:
        return self._profiles

    @property
    def states(self) -> tuple[ProfileState, ...]:
        return tuple(self._states[p.key] for p in self._profiles)

    def state(self, key: str) -> ProfileState | None:
        return self._states.get(key)

    @property
    def active_state(self) -> ProfileState | None:
        for state in self.states:
            if state.is_active:
                return state
        return None

    @property
    def is_busy(self) -> bool:
        return self._busy

    def _set_busy(self, value: bool) -> None:
        with self._busy_lock:
            if self._busy == value:
                return
            self._busy = value
        self.busyChanged.emit(value)

    # -- activity log -------------------------------------------------------

    def log(self, message: str, level: str = "info") -> ActivityEntry:
        """Record a safe, user-facing line. Redaction happens on write."""
        entry = ActivityEntry(timestamp=datetime.now(), message=message, level=level)
        self._settings.append_activity(entry)
        self.activityAdded.emit(entry)
        return entry

    def recent_activity(self, limit: int = 200) -> list[ActivityEntry]:
        return self._settings.read_activity(limit)

    def clear_activity(self) -> None:
        self._settings.clear_activity()
        self.log("Local activity history cleared")

    # -- applying poll results ---------------------------------------------

    def apply_accounts(self, accounts: AccountList) -> None:
        """Fold a ``list --json`` result into per-profile state."""
        self.last_accounts = accounts
        if not accounts.is_known_schema and not self._warned_schema:
            self._warned_schema = True
            self.schemaWarning.emit(accounts.schema_version)

        previously_active = self._last_active_key
        for profile in self._profiles:
            account = accounts.by_alias(profile.alias)
            state = self._states[profile.key]
            state.account = account
            state.error = (
                None
                if account is not None
                else f"No claude-swap account is aliased {profile.alias!r} yet."
            )

        active = self.active_state
        active_key = active.profile.key if active else None
        if active_key != previously_active:
            if previously_active is not None and active_key is not None:
                self.log(
                    f"Switched from {self._name(previously_active)} "
                    f"to {self._name(active_key)} at {datetime.now():%H:%M}"
                )
            elif active_key is not None:
                self.log(f"{self._name(active_key)} is the active account")
            self._last_active_key = active_key

        for state in self.states:
            if state.needs_reauth:
                self.log(f"{state.profile.name} requires re-authentication", level="warning")

        self.statesChanged.emit()

    def apply_error(self, error: CswapError) -> None:
        """Record a failed refresh without discarding what is on screen.

        Existing state is intentionally left in place; the UI marks it stale
        rather than blanking the numbers the user was reading.
        """
        self.log(f"Usage refresh failed - {error.user_message}", level="error")
        self.statesChanged.emit()

    def _name(self, key: str) -> str:
        state = self._states.get(key)
        return state.profile.name if state else key

    # -- actions ------------------------------------------------------------

    def refresh_sync(self) -> bool:
        """Poll once on the calling thread. Returns True on success."""
        try:
            accounts = self._backend.list_accounts()
        except CswapError as exc:
            self.apply_error(exc)
            return False
        self.apply_accounts(accounts)
        self.log("Usage refreshed")
        return True

    def switch_sync(self, key: str) -> tuple[bool, str]:
        """Switch the active account, then re-read authoritative state.

        Returns ``(ok, message)``. The busy flag is cleared no matter how this
        exits, so switch controls always become usable again.
        """
        state = self._states.get(key)
        if state is None:
            return False, "Unknown profile."
        if self._busy:
            return False, "Another action is still running."

        self._set_busy(True)
        try:
            if state.account is None:
                message = (
                    f"{state.profile.name} is not registered with claude-swap yet. "
                    f"Add it with: cswap add --alias {state.profile.alias}"
                )
                self.log(message, level="error")
                self.switchFailed.emit(key, message)
                return False, message

            if state.is_active:
                message = f"{state.profile.name} is already the active account."
                self.switchSucceeded.emit(key)
                return True, message

            previous = self.active_state
            previous_name = previous.profile.name if previous else "another account"

            try:
                self._backend.switch(
                    state.profile.alias, fallback_number=state.account.number
                )
            except CswapError as exc:
                message = exc.user_message
                self.log(
                    f"Switch to {state.profile.name} failed - {message}", level="error"
                )
                self.switchFailed.emit(key, message)
                return False, message

            # cswap list is authoritative; the switch payload is only a hint.
            try:
                accounts = self._backend.list_accounts()
            except CswapError as exc:
                self.apply_error(exc)
                message = (
                    f"Switched to {state.profile.name}, but refreshing usage failed."
                )
                self.switchSucceeded.emit(key)
                return True, message

            self.apply_accounts(accounts)
            # Confirmation only; the list result already stands.
            with contextlib.suppress(CswapError):
                self._backend.status()

            message = f"Switched from {previous_name} to {state.profile.name}"
            self.switchSucceeded.emit(key)
            return True, message
        finally:
            self._set_busy(False)

    # -- account setup ------------------------------------------------------

    def read_current_login(self) -> ActiveStatus | None:
        """Ask cswap who Claude Code is signed in as right now.

        This is what makes ``add`` safe to offer from a button: the user can
        see which account is about to be registered before they click.
        """
        try:
            status = self._backend.status()
        except CswapError as exc:
            self.log(f"Could not read the current login - {exc.user_message}", level="error")
            return None
        self.last_status = status
        return status

    def register_current_as(self, key: str) -> tuple[bool, str]:
        """Register the signed-in Claude Code account under a profile's alias.

        Runs ``cswap add --alias <alias>``. No authentication happens here -
        signing in is something the user does in Claude Code beforehand.
        """
        state = self._states.get(key)
        if state is None:
            return False, "Unknown profile."
        if self._busy:
            return False, "Another action is still running."

        self._set_busy(True)
        try:
            try:
                self._backend.add_current_account(state.profile.alias)
            except CswapError as exc:
                message = exc.user_message
                self.log(
                    f"Could not register {state.profile.name} - {message}", level="error"
                )
                self.setupFailed.emit(key, message)
                return False, message

            self.refresh_sync()
            registered = self.state(key)
            if registered is not None and registered.account is not None:
                message = (
                    f"Registered {registered.account.email} as {state.profile.name}."
                )
            else:
                message = f"Registered the signed-in account as {state.profile.name}."
            self.log(message)
            self.setupSucceeded.emit(key, message)
            return True, message
        finally:
            self._set_busy(False)

    def assign_alias(self, number: int, key: str) -> tuple[bool, str]:
        """Give an already-registered account this profile's alias."""
        state = self._states.get(key)
        if state is None:
            return False, "Unknown profile."
        if self._busy:
            return False, "Another action is still running."

        self._set_busy(True)
        try:
            try:
                self._backend.set_alias(number, state.profile.alias)
            except CswapError as exc:
                message = exc.user_message
                self.log(f"Could not set the alias - {message}", level="error")
                self.setupFailed.emit(key, message)
                return False, message

            self.refresh_sync()
            message = f"Account {number} is now {state.profile.name}."
            self.log(message)
            self.setupSucceeded.emit(key, message)
            return True, message
        finally:
            self._set_busy(False)

    def run_in_background(self, func, *args) -> None:
        """Run a blocking service call off the UI thread."""
        if self._busy:
            return
        threading.Thread(
            target=func, args=args, name="cswap-action", daemon=True
        ).start()

    def switch(self, key: str) -> None:
        """Fire-and-forget switch on a worker thread."""
        if self._busy:
            return
        threading.Thread(
            target=self.switch_sync, args=(key,), name="cswap-switch", daemon=True
        ).start()

    def run_command_for(self, key: str) -> list[str] | None:
        """argv that launches Claude Code as this profile, or None.

        The alias is resolved to a slot number here because the installed CLI
        documents ``run`` as accepting ``NUM|EMAIL`` only.
        """
        state = self._states.get(key)
        if state is None or state.account is None or state.account.number is None:
            return None
        return self._backend.build_run_command(state.account.number)
