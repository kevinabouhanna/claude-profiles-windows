"""Tray notifications, including a threshold state machine.

Notifications fire on an upward crossing only, and re-arm once usage falls back
below the threshold (which happens at every quota reset). Without that latch a
120-second poll would re-notify roughly thirty times an hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWidgets import QSystemTrayIcon

from ..models import ProfileState


@dataclass
class _Latch:
    """Highest threshold already announced for one profile/window pair."""

    level: int = 0


@dataclass
class ThresholdTracker:
    """Decides when a quota crossing deserves a notification."""

    warn_pct: int = 80
    critical_pct: int = 95
    _latches: dict[tuple[str, str], _Latch] = field(default_factory=dict)

    def _level_for(self, pct: float) -> int:
        if pct >= self.critical_pct:
            return 2
        if pct >= self.warn_pct:
            return 1
        return 0

    def check(self, profile_key: str, window_key: str, pct: float | None) -> int | None:
        """Return the newly crossed level, or None if nothing changed."""
        if pct is None:
            return None
        latch = self._latches.setdefault((profile_key, window_key), _Latch())
        level = self._level_for(pct)
        if level > latch.level:
            latch.level = level
            return level
        if level < latch.level:
            latch.level = level  # re-arm after a reset
        return None

    def reset(self) -> None:
        self._latches.clear()


class NotificationService:
    """Thin wrapper over the tray icon's balloon notifications."""

    def __init__(self, tray: QSystemTrayIcon, enabled: bool = False) -> None:
        self._tray = tray
        self.enabled = enabled
        self.tracker = ThresholdTracker()

    def configure(self, *, enabled: bool, warn_pct: int, critical_pct: int) -> None:
        self.enabled = enabled
        self.tracker.warn_pct = warn_pct
        self.tracker.critical_pct = critical_pct

    def notify(self, title: str, message: str, *, force: bool = False) -> None:
        """Show a tray notification.

        ``force`` is used for direct results of a user action (a switch), which
        should appear even when passive threshold alerts are switched off.
        """
        if not (self.enabled or force):
            return
        if not self._tray.isSystemTrayAvailable():
            return
        icon = QSystemTrayIcon.MessageIcon.Information
        self._tray.showMessage(title, message, icon, 5000)

    def notify_switch(self, profile_name: str, email: str) -> None:
        self.notify(
            "Claude Profiles",
            f"{profile_name} is now the active Claude Code account\n{email}",
            force=True,
        )

    def check_thresholds(self, states: tuple[ProfileState, ...]) -> None:
        """Announce any newly crossed quota threshold."""
        if not self.enabled:
            return
        for state in states:
            account = state.account
            usage = account.effective_usage if account else None
            if usage is None or account is None or account.is_stale:
                continue
            for window_key, window in (("5h", usage.five_hour), ("7d", usage.seven_day)):
                if window is None:
                    continue
                level = self.tracker.check(state.profile.key, window_key, window.pct)
                if level:
                    severity = "critical" if level == 2 else "high"
                    self.notify(
                        f"{state.profile.name}: {window_key} usage {severity}",
                        f"{window.pct:.0f}% of the {window_key} limit used"
                        + (f" · resets in {window.countdown}" if window.countdown else ""),
                    )
