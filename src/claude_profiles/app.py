"""Application entry point."""

from __future__ import annotations

import argparse
import html
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .resources.icons import app_icon
from .services.cswap_client import (
    CswapBackend,
    find_cswap,
    missing_cswap_advice,
)
from .services.environment import SwitchableBackend, resolve_backend
from .services.mock_cswap import SCENARIOS, MockCswapClient
from .services.settings_service import Settings, SettingsService
from .services.wsl import WINDOWS, distro_of
from .tray import TrayController
from .widgets import theme


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
        "--no-windows-integration",
        action="store_true",
        help=(
            "Do not create or update Start menu and Startup shortcuts. Used by "
            "the build verifier so checking a build cannot alter the user's "
            "Windows configuration."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Override the settings/activity directory (useful for testing).",
    )
    return parser.parse_args(argv)


def build_backend(
    args: argparse.Namespace, settings: Settings | None = None
) -> tuple[CswapBackend, bool]:
    """Return ``(backend, is_mock)``.

    A real backend is a :class:`SwitchableBackend` pointed at wherever the
    settings say Claude Code runs, so the Settings page can move it later.
    """
    if args.mock or args.scenario != "healthy":
        return MockCswapClient(args.scenario), True
    # Settings are normalized on load: "windows" or a valid "wsl:<distro>".
    environment = settings.claude_environment if settings is not None else WINDOWS
    return SwitchableBackend(resolve_backend(environment), environment), False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Claude Profiles")
    app.setOrganizationName("Claude Profiles")
    app.setWindowIcon(app_icon())
    # Closing a window returns to the tray; only Quit exits.
    app.setQuitOnLastWindowClosed(False)

    # Read the system theme and accent colour, then dress every standard
    # control as Fluent before any window is built.
    theme.refresh_tokens()
    theme.apply_app_font(app)
    app.setStyleSheet(theme.stylesheet())

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "Claude Profiles",
            "No system tray is available on this desktop, so the app cannot run.",
        )
        return 1

    settings_service = SettingsService(data_dir=args.data_dir)
    settings = settings_service.load()
    backend, is_mock = build_backend(args, settings)

    controller = TrayController(
        app,
        backend,
        settings_service,
        mock_mode=is_mock,
        skip_windows_integration=args.no_windows_integration,
        environment_resolver=None if is_mock else resolve_backend,
    )
    controller.start()

    # The bundled Windows copy only matters while Claude Code runs on Windows;
    # a WSL target reports its own setup problems on the Settings page.
    on_windows = distro_of(settings.claude_environment) is None
    if not is_mock and on_windows and find_cswap() is None:
        QMessageBox.warning(
            None,
            "claude-swap not found",
            f"{html.escape(missing_cswap_advice())}<br><br>"
            "You can also explore the interface with synthetic data by running "
            "<code>claude-profiles --mock</code>.",
        )

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
