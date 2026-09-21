"""Small pill labels for health, active state, and staleness."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from ..models import Account, UsageStatus

_TONES = {
    "ok": ("#16a34a", "rgba(22, 163, 74, 0.13)"),
    "warn": ("#b45309", "rgba(245, 158, 11, 0.16)"),
    "error": ("#dc2626", "rgba(239, 68, 68, 0.14)"),
    "muted": ("#64748b", "rgba(100, 116, 139, 0.14)"),
}


class StatusBadge(QLabel):
    """A rounded pill whose colour encodes severity."""

    def __init__(self, text: str = "", tone: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTone(tone)

    def setTone(self, tone: str) -> None:  # noqa: N802 - Qt naming
        color, background = _TONES.get(tone, _TONES["muted"])
        self.setStyleSheet(
            f"color: {color}; background-color: {background};"
            "border-radius: 8px; padding: 2px 9px; font-size: 11px; font-weight: 600;"
        )

    def apply(self, text: str, tone: str) -> None:
        self.setText(text)
        self.setTone(tone)
        self.setVisible(bool(text))


def health_badge_text(account: Account | None) -> tuple[str, str]:
    """Map an account to ``(text, tone)`` without ever exposing a secret."""
    if account is None:
        return "Not set up", "muted"
    status = account.usage_status
    if status.needs_reauth:
        return "Re-authentication required", "error"
    if status is UsageStatus.UNAVAILABLE:
        return "Usage unavailable", "warn"
    if status in {UsageStatus.KEYCHAIN_UNAVAILABLE, UsageStatus.FOREIGN_CREDENTIAL}:
        return status.label, "warn"
    if status is UsageStatus.API_KEY:
        return status.label, "muted"
    if status is UsageStatus.UNKNOWN:
        return status.label, "muted"
    if account.is_stale:
        return "Showing last known", "warn"
    return "Healthy", "ok"


def format_age(seconds: float | None) -> str:
    """Human-readable data age, e.g. ``12s ago`` / ``23m ago``."""
    if seconds is None:
        return "unknown age"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"
