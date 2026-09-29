"""A single quota row: label, Fluent progress track, percentage, countdown.

Laid out as four aligned columns rather than text painted over a bar. Nothing is
ever drawn inside the track - a progress indicator that contains words reads as
a broken control, and it cannot stay legible once the fill passes under the
text. When there is no reading to show the row reports that in the value column
and leaves the track empty.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..models import UsageWindow, pace_pct
from . import theme

WARN_PCT = 80.0
CRITICAL_PCT = 95.0

TRACK_HEIGHT = 4
ROW_HEIGHT = 26

# The pace marker stands proud of the track so it reads as a line to compare
# against, not as part of the fill.
MARKER_WIDTH = 2.0
MARKER_OVERHANG = 4

LABEL_WIDTH = 38
VALUE_WIDTH = 42
COUNTDOWN_WIDTH = 104


def fill_color(pct: float, accent: str) -> str:
    """Identity colour normally; Fluent caution then critical as quota runs out.

    Keeping the profile colour below the warning threshold preserves the
    blue/orange distinction while still escalating clearly.
    """
    t = theme.tokens()
    if pct >= CRITICAL_PCT:
        return t.critical
    if pct >= WARN_PCT:
        return t.caution
    return t.on_surface(accent)


class UsageBar(QWidget):
    """``5h  ▬▬▬▬▬░░░░  42%   2h 13m``"""

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
        self.setFixedHeight(ROW_HEIGHT if compact else ROW_HEIGHT + 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- state --------------------------------------------------------------

    def set_window(
        self,
        window: UsageWindow | None,
        *,
        stale: bool = False,
        length_seconds: float | None = None,
    ) -> None:
        """Show ``window``. ``length_seconds`` lets the pace marker be placed
        for windows claude-swap reports no expected percentage for."""
        self._pct = window.pct if window else None
        self._countdown = window.countdown if window else None
        self._expected_pct = pace_pct(window, length_seconds)
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
            return f"{self._label}: no reading available"
        parts = [f"{self._label} — {window.pct:.0f}% used"]
        if window.countdown:
            parts.append(f"Resets in {window.countdown}")
        if window.clock:
            parts.append(f"Reset time {window.clock}")
        if self._expected_pct is not None:
            ahead = (
                window.ahead_of_pace
                if window.ahead_of_pace is not None
                else window.pct > self._expected_pct
            )
            pace = "Ahead of" if ahead else "Within"
            parts.append(
                f"{pace} pace: spending evenly would put you at {self._expected_pct:.0f}% now"
            )
        if window.will_last_to_reset is False:
            parts.append("Projected to run out before reset")
        if self._stale:
            parts.append("Last known reading; the latest refresh failed")
        return "\n".join(parts)

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        t = theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        countdown_width = COUNTDOWN_WIDTH if self._countdown else 0

        track_x = LABEL_WIDTH + 10
        track_right = width - VALUE_WIDTH - countdown_width - 10
        track_w = max(24, track_right - track_x)
        track_y = (height - TRACK_HEIGHT) / 2

        # Label column
        painter.setFont(theme.font(theme.CAPTION))
        painter.setPen(QColor(t.text_tertiary))
        painter.drawText(
            QRectF(0, 0, LABEL_WIDTH, height),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._label,
        )

        # Track
        track = QPainterPath()
        track.addRoundedRect(
            QRectF(track_x, track_y, track_w, TRACK_HEIGHT),
            TRACK_HEIGHT / 2,
            TRACK_HEIGHT / 2,
        )
        painter.fillPath(track, QColor(t.track))

        if self._pct is None:
            painter.setFont(theme.font(theme.CAPTION))
            painter.setPen(QColor(t.text_disabled))
            painter.drawText(
                QRectF(track_right, 0, VALUE_WIDTH, height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "—",
            )
            painter.end()
            return

        pct = max(0.0, min(100.0, self._pct))
        fill_w = track_w * pct / 100.0
        if fill_w > 0.5:
            fill = QPainterPath()
            fill.addRoundedRect(
                QRectF(track_x, track_y, max(fill_w, TRACK_HEIGHT), TRACK_HEIGHT),
                TRACK_HEIGHT / 2,
                TRACK_HEIGHT / 2,
            )
            color = QColor(fill_color(pct, self._accent))
            if self._stale:
                color.setAlphaF(0.45)
            painter.fillPath(fill, color)

        # Pace marker: where usage would sit if spent evenly. Fill past it
        # means spending faster than the quota refills.
        if self._expected_pct is not None:
            marker_x = track_x + track_w * max(0.0, min(100.0, self._expected_pct)) / 100.0
            marker = QPainterPath()
            marker.addRoundedRect(
                QRectF(
                    marker_x - MARKER_WIDTH / 2,
                    track_y - MARKER_OVERHANG,
                    MARKER_WIDTH,
                    TRACK_HEIGHT + 2 * MARKER_OVERHANG,
                ),
                MARKER_WIDTH / 2,
                MARKER_WIDTH / 2,
            )
            color = QColor(t.text)
            if self._stale:
                color.setAlphaF(0.45)
            painter.fillPath(marker, color)

        # Value column
        painter.setFont(theme.font(theme.CAPTION, 600))
        painter.setPen(QColor(t.text_secondary if self._stale else t.text))
        painter.drawText(
            QRectF(track_right, 0, VALUE_WIDTH, height),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{pct:.0f}%",
        )

        # Countdown column
        if self._countdown:
            painter.setFont(theme.font(theme.CAPTION))
            painter.setPen(QColor(t.text_tertiary))
            # "resets" prefixed so the duration cannot be misread as time
            # already spent.
            painter.drawText(
                QRectF(width - countdown_width, 0, countdown_width, height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"resets {self._countdown}",
            )
        painter.end()
