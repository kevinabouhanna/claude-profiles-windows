"""Render the app icon to build/claude_profiles.ico for packaging.

Kept out of the .spec on purpose: QPixmap needs a live QGuiApplication, and
creating one without it aborts the process, which surfaces as PyInstaller
dying at the PYZ stage with no diagnostic.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claude_profiles.resources.icons import save_app_icon  # noqa: E402


def main() -> int:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - required for QPixmap
    target = Path(__file__).resolve().parents[1] / "build" / "claude_profiles.ico"
    save_app_icon(target)
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
