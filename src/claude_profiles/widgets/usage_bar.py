"""A labelled quota bar: name, percentage, fill, and reset countdown."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..models import UsageWindow

WARN_PCT = 80.0
CRITICAL_PCT = 95.0

WARN_COLOR = "#f59e0b"
CRITICAL_COLOR = "#ef4444"


def fill_color(pct: float, accent: str) -> str:
    """Profile accent normally; amber then red as the quota runs down.

    Keeping the accent below the warning threshold preserves the visual
    identity of each profile while still signalling risk clearly.
    """
    if pct >= CRITICAL_PCT:
        return CRITICAL_COLOR
    if pct >= WARN_PCT:
        return WARN_COLOR
    return accent


class UsageBar(QWidget):
    """One quota window rendered as ``label  [=====----]  pct   resets in X``."""

    def __init__(
        self,
        label: str,
        accent: str,
        *,
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._accent = accent
        self._compact = compact
        self._pct: float | None = None
        self._countdown: str | None = None
        self._expected_pct: float | None = None
        self._stale = False
        self.setMinimumHeight(34 if not compact else 30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_window(self, window: UsageWindow | None, *, stale: bool = False) -> None:
        self._pct = window.pct if window else None
        self._countdown = window.countdown if window else None
        self._expected_pct = window.expected_pct if window else None
        self._stale = stale
        if window and window.name:
            self._label = window.name
        self.setToolTip(self._build_tooltip(window))
        self.update()

    def set_accent(self, accent: str) -> None:
        self._accent = accent
        self.update()

    def _build_tooltip(self, window: UsageWindow | None) -> str:
        if window is None:
            return f"{self._label}: no data"
        parts = [f"{self._label}: {window.pct:.0f}% used"]
        if window.countdown:
            parts.append(f"resets in {window.countdown}")
        if window.clock:
            parts.append(f"at {window.clock}")
        if window.expected_pct is not None:
            pace = "ahead of" if window.ahead_of_pace else "on or behind"
            parts.append(f"{pace} pace (expected {window.expected_pct:.0f}%)")
        if window.will_last_to_reset is False:
            parts.append("projected to run out before reset")
        return "\n".join(parts)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        label_width = 42
        pct_width = 44
        countdown_width = 108 if not self._compact else 96
        track_left = label_width + 6
        track_right = width - pct_width - countdown_width
        track_width = max(20, track_right - track_left)
        bar_height = 8
        bar_top = (self.height() - bar_height) / 2

        text_color = self.palette().text().color()
        muted = QColor(text_color)
        muted.setAlphaF(0.62)

        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 0.5))
        painter.setFont(font)

        painter.setPen(muted)
        painter.drawText(
            QRectF(0, 0, label_width, self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._label,
        )

        # Track
        track = QPainterPath()
        track.addRoundedRect(
            QRectF(track_left, bar_top, track_width, bar_height), 4, 4
        )
        track_color = QColor(text_color)
        track_color.setAlphaF(0.12)
        painter.fillPath(track, track_color)

        if self._pct is None:
            painter.setPen(muted)
            painter.drawText(
                QRectF(track_left, 0, track_width + pct_width, self.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "  no data",
            )
            painter.end()
            return

        pct = max(0.0, min(100.0, self._pct))
        fill_width = track_width * pct / 100.0
        if fill_width > 0:
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(track_left, bar_top, fill_width, bar_height), 4, 4)
            color = QColor(fill_color(pct, self._accent))
            if self._stale:
                color.setAlphaF(0.55)
            painter.fillPath(fill, color)

        # Pace marker: where usage "should" be by now.
        if self._expected_pct is not None:
            marker_x = track_left + track_width * max(0.0, min(100.0, self._expected_pct)) / 100.0
            pen_color = QColor(text_color)
            pen_color.setAlphaF(0.45)
            painter.setPen(pen_color)
            painter.drawLine(
                int(marker_x), int(bar_top - 2), int(marker_x), int(bar_top + bar_height + 2)
            )

        pct_font = QFont(font)
        pct_font.setBold(True)
        painter.setFont(pct_font)
        painter.setPen(text_color)
        painter.drawText(
            QRectF(track_right, 0, pct_width, self.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{pct:.0f}%",
        )

        if self._countdown:
            painter.setFont(font)
            painter.setPen(muted)
            painter.drawText(
                QRectF(width - countdown_width, 0, countdown_width, self.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"resets in {self._countdown}",
            )
        painter.end()
