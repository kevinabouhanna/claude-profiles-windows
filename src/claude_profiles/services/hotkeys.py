"""Optional system-wide hotkeys (Ctrl+Alt+1 / Ctrl+Alt+2).

Qt has no cross-platform global hotkey, so this uses the Win32 ``RegisterHotKey``
API through ctypes plus a native event filter.

Disabled by default. Nothing is registered until the user opts in, and a
combination already owned by another application fails with a clear message
rather than silently doing nothing.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

VK_1 = 0x31
VK_2 = 0x32

DEFAULT_BINDINGS = (
    ("personal", MOD_CONTROL | MOD_ALT, VK_1, "Ctrl+Alt+1"),
    ("work", MOD_CONTROL | MOD_ALT, VK_2, "Ctrl+Alt+2"),
)


class HotkeyFilter(QAbstractNativeEventFilter):
    """Routes WM_HOTKEY messages to the registered callback."""

    def __init__(self, callback: Callable[[int], None]) -> None:
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt naming
        if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        except (ValueError, TypeError):
            return False, 0
        if msg.message == WM_HOTKEY:
            self._callback(int(msg.wParam))
        return False, 0


class HotkeyManager(QObject):
    """Registers and releases the global shortcuts."""

    def __init__(self, on_triggered: Callable[[str], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_triggered = on_triggered
        self._registered: dict[int, str] = {}
        self._filter: HotkeyFilter | None = None
        self._supported = sys.platform == "win32"

    @property
    def is_supported(self) -> bool:
        return self._supported

    @property
    def is_active(self) -> bool:
        return bool(self._registered)

    def _handle(self, hotkey_id: int) -> None:
        key = self._registered.get(hotkey_id)
        if key:
            self._on_triggered(key)

    def enable(self, app) -> tuple[bool, str]:
        """Register every binding. Returns ``(ok, message)``."""
        if not self._supported:
            return False, "Global shortcuts are only supported on Windows."
        if self._registered:
            return True, "Global shortcuts are already active."

        user32 = ctypes.windll.user32
        self._filter = HotkeyFilter(self._handle)
        app.installNativeEventFilter(self._filter)

        failed: list[str] = []
        for index, (key, modifiers, vk, label) in enumerate(DEFAULT_BINDINGS, start=1):
            if user32.RegisterHotKey(None, index, modifiers | MOD_NOREPEAT, vk):
                self._registered[index] = key
            else:
                failed.append(label)

        if failed and not self._registered:
            self.disable(app)
            return False, (
                f"Could not register {', '.join(failed)} - another application "
                "is already using it."
            )
        if failed:
            return True, f"Registered, except {', '.join(failed)} (already in use)."
        return True, "Global shortcuts enabled (Ctrl+Alt+1, Ctrl+Alt+2)."

    def disable(self, app) -> None:
        if self._supported:
            user32 = ctypes.windll.user32
            for hotkey_id in list(self._registered):
                user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()
        if self._filter is not None:
            app.removeNativeEventFilter(self._filter)
            self._filter = None
