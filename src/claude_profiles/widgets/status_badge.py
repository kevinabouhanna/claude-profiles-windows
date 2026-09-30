"""Fluent status pills and the small avatar that identifies a profile."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..models import Account, UsageStatus
from ..resources import fluent_icons
from . import theme

# tone -> (icon name, token attribute)
_TONES = {
    "ok": ("success", "success"),
    "warn": ("warning", "caution"),
    "error": ("error", "critical"),
    "muted": ("info", "text_tertiary"),
    "accent": ("success", "accent"),
}


class StatusBadge(QWidget):
    """An icon + label pill whose colour and glyph encode severity."""

    def __init__(self, text: str = "", tone: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 9, 3)
        layout.setSpacing(5)

        self._icon = QLabel()
        self._icon.setFixedSize(13, 13)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon)

        self._label = QLabel(text)
        layout.addWidget(self._label)

        self._tone = tone
        self.apply(text, tone)

    def apply(self, text: str, tone: str) -> None:
        self._tone = tone
        self._label.setText(text)
        if not text:
            self.hide()
            return
        # A badge is built before its card adopts it, and showing a widget with
        # no parent opens it as a window of its own for an instant. Until it
        # has a parent, not hiding it is enough: it appears along with the card.
        if self.parentWidget() is not None:
            self.show()

        t = theme.tokens()
        icon_name, token_attr = _TONES.get(tone, _TONES["muted"])
        color = getattr(t, token_attr)
        tint = self._tint_for(tone, token_attr)

        self._icon.setPixmap(fluent_icons.pixmap(icon_name, color, 13))
        self._label.setStyleSheet(
            f"color: {color}; font-size: {theme.CAPTION}px; font-weight: 600;"
        )
        # The pill itself is drawn in paintEvent; a stylesheet background would
        # not apply to a plain QWidget subclass.
        self._tint = tint
        self.update()

    def setTone(self, tone: str) -> None:  # noqa: N802 - kept for existing calls
        self.apply(self._label.text(), tone)

    def _tint_for(self, tone: str, token_attr: str) -> str:
        t = theme.tokens()
        if tone == "accent":
            return t.accent_tint(0.16)
        if token_attr in {"success", "caution", "critical"}:
            return t.status_tint(token_attr, 0.16)
        return t.control

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # A plain QWidget ignores background-color from QSS, so draw it here.
        tint = getattr(self, "_tint", theme.tokens().control)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            self.rect().adjusted(0, 0, -1, -1),
            theme.RADIUS_CONTROL,
            theme.RADIUS_CONTROL,
        )
        painter.fillPath(path, QColor(tint))
        painter.end()


class ProfileAvatar(QWidget):
    """A round identity chip carrying the profile's Fluent glyph."""

    def __init__(self, color: str, icon_name: str, size: int = 32,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._icon_name = icon_name
        self.setFixedSize(size, size)

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        t = theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        base = QColor(t.on_surface(self._color))
        tint = QColor(base)
        tint.setAlphaF(0.18)
        painter.setBrush(tint)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())

        fluent_icons.draw_glyph(
            painter,
            QRect(0, 0, self.width(), self.height()),
            self._icon_name,
            base.name(),
            size=int(self.width() * 0.46),
        )
        painter.end()


def health_badge_text(account: Account | None) -> tuple[str, str]:
    """Map an account to ``(text, tone)`` without ever exposing a secret."""
    if account is None:
        return "Not set up", "muted"
    status = account.usage_status
    if status.needs_reauth:
        return "Sign-in needed", "error"
    if status is UsageStatus.UNAVAILABLE:
        return "Usage unavailable", "warn"
    if status in {UsageStatus.KEYCHAIN_UNAVAILABLE, UsageStatus.FOREIGN_CREDENTIAL}:
        return status.label, "warn"
    if status is UsageStatus.API_KEY:
        return status.label, "muted"
    if status is UsageStatus.UNKNOWN:
        return status.label, "muted"
    if account.is_stale:
        return "Last known", "warn"
    return "Healthy", "ok"


def format_age(seconds: float | None) -> str:
    """Human-readable data age, e.g. ``12s ago`` / ``23m ago``."""
    if seconds is None:
        return "unknown"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 86400:
        return f"{seconds / 3600:.0f}h ago"
    return f"{seconds / 86400:.0f}d ago"
