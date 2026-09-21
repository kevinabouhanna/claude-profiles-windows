"""Application entry point."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .resources.icons import tray_icon
from .services.cswap_client import CswapBackend, CswapClient, find_cswap
from .services.mock_cswap import SCENARIOS, MockCswapClient
from .services.settings_service import SettingsService
from .tray import TrayController


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="claude-profiles",
        description=(
            "Private, local-only tray app for switching between and monitoring "
            "Claude Code accounts via claude-swap."
        ),
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run against synthetic data. No real account is read or changed.",
    )
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default="healthy",
        help="Which mock situation to demonstrate (implies --mock).",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Override the settings/activity directory (useful for testing).",
    )
    return parser.parse_args(argv)


def build_backend(args: argparse.Namespace) -> tuple[CswapBackend, bool]:
    """Return ``(backend, is_mock)``."""
    if args.mock or args.scenario != "healthy":
        return MockCswapClient(args.scenario), True
    return CswapClient(), False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Claude Profiles")
    app.setOrganizationName("Claude Profiles")
    app.setWindowIcon(tray_icon())
    # Closing a window returns to the tray; only Quit exits.
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "Claude Profiles",
            "No system tray is available on this desktop, so the app cannot run.",
        )
        return 1

    backend, is_mock = build_backend(args)
    settings_service = SettingsService(data_dir=args.data_dir)

    controller = TrayController(app, backend, settings_service, mock_mode=is_mock)
    controller.start()

    if not is_mock and find_cswap() is None:
        QMessageBox.warning(
            None,
            "claude-swap not found",
            "Claude Profiles could not find the <b>cswap</b> command.<br><br>"
            "Install it with:<br><code>uv tool install claude-swap</code><br><br>"
            "You can also explore the interface with synthetic data by running "
            "<code>claude-profiles --mock</code>.",
        )

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
