"""Local preference and activity storage.

Everything lives under ``%LOCALAPPDATA%\\ClaudeProfiles``. The directory is
ACL-restricted to the current user on creation. Only UI preferences, non-secret
profile aliases, and safe activity strings are ever written; no credential,
token, or account payload is persisted.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models import ActivityEntry
from .redaction import safe_for_log

APP_DIR_NAME = "ClaudeProfiles"
SETTINGS_FILE = "settings.json"
ACTIVITY_FILE = "activity.jsonl"
MAX_ACTIVITY_ENTRIES = 500

_CREATE_NO_WINDOW = 0x08000000


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".claude-profiles"


@dataclass
class Settings:
    """User preferences. Contains no secrets and no email addresses."""

    refresh_interval_seconds: int = 120
    # On by default: a tray app the user installed to watch their quota is
    # not useful if it has to be started by hand every morning.
    launch_at_signin: bool = True
    notifications_enabled: bool = False
    warn_threshold_pct: int = 80
    critical_threshold_pct: int = 95
    hotkeys_enabled: bool = False
    # A short hash of the executable the Windows shortcuts were written for,
    # so startup can notice that a better launcher is now available. Hashed
    # rather than stored: the path contains the Windows username, and this file
    # is documented as holding no user-identifying data.
    autostart_target: str = ""
    # No longer used: profiles now come from whatever accounts claude-swap
    # has registered. Kept so settings files written by 0.1 pre-releases load
    # and round-trip unchanged.
    profile_aliases: dict[str, str] = field(default_factory=dict)

    def normalized(self) -> Settings:
        """Clamp values that the UI or a hand-edited file could put out of range."""
        interval = max(30, min(3600, int(self.refresh_interval_seconds)))
        warn = max(1, min(99, int(self.warn_threshold_pct)))
        critical = max(warn + 1, min(100, int(self.critical_threshold_pct)))
        return replace(
            self,
            refresh_interval_seconds=interval,
            warn_threshold_pct=warn,
            critical_threshold_pct=critical,
        )


class SettingsService:
    """Reads and writes settings plus the local activity history."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else default_data_dir()
        self._settings: Settings | None = None
        # Startup reconciliation and the poll loop can both log at once.
        self._activity_lock = threading.Lock()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def settings_path(self) -> Path:
        return self._data_dir / SETTINGS_FILE

    @property
    def activity_path(self) -> Path:
        return self._data_dir / ACTIVITY_FILE

    # -- directory ----------------------------------------------------------

    def ensure_data_dir(self) -> Path:
        created = not self._data_dir.exists()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if created:
            self._restrict_permissions()
        return self._data_dir

    def _restrict_permissions(self) -> None:
        """Limit the data directory to the current user.

        Best effort: a failure here is not worth blocking startup, and the
        directory already sits inside the per-user LOCALAPPDATA tree.
        """
        if sys.platform != "win32":
            with contextlib.suppress(OSError):
                self._data_dir.chmod(0o700)
            return
        user = os.environ.get("USERNAME")
        if not user:
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [
                    "icacls",
                    str(self._data_dir),
                    "/inheritance:r",
                    "/grant:r",
                    f"{user}:(OI)(CI)F",
                ],
                capture_output=True,
                timeout=10,
                creationflags=_CREATE_NO_WINDOW,
                check=False,
            )

    # -- settings -----------------------------------------------------------

    def load(self) -> Settings:
        if self._settings is not None:
            return self._settings
        settings = Settings()
        try:
            # utf-8-sig tolerates a byte-order mark. Notepad and PowerShell's
            # Set-Content both write one, and a BOM makes json.loads fail, which
            # would silently discard every preference the user had set.
            raw = self.settings_path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
            if isinstance(data, dict):
                known = {f for f in Settings().__dataclass_fields__}
                settings = Settings(**{k: v for k, v in data.items() if k in known})
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # A missing or corrupt file just means defaults.
            settings = Settings()
        self._settings = settings.normalized()
        return self._settings

    def save(self, settings: Settings) -> Settings:
        normalized = settings.normalized()
        self._settings = normalized
        self.ensure_data_dir()
        payload = json.dumps(asdict(normalized), indent=2)
        tmp = self.settings_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.settings_path)
        except OSError:
            tmp.unlink(missing_ok=True)
        return normalized

    def update(self, **changes: Any) -> Settings:
        return self.save(replace(self.load(), **changes))

    # -- activity history ---------------------------------------------------

    def append_activity(self, entry: ActivityEntry) -> None:
        """Append one safe line. The message is redacted and email-masked."""
        self.ensure_data_dir()
        record = {
            "timestamp": entry.timestamp.astimezone(UTC).isoformat(),
            "level": entry.level,
            "message": safe_for_log(entry.message),
        }
        try:
            with self.activity_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            return
        self._trim_activity()

    def _trim_activity(self) -> None:
        try:
            lines = self.activity_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= MAX_ACTIVITY_ENTRIES:
            return
        keep = lines[-MAX_ACTIVITY_ENTRIES:]
        tmp = self.activity_path.with_suffix(".jsonl.tmp")
        try:
            tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
            os.replace(tmp, self.activity_path)
        except OSError:
            tmp.unlink(missing_ok=True)

    def read_activity(self, limit: int = MAX_ACTIVITY_ENTRIES) -> list[ActivityEntry]:
        try:
            lines = self.activity_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[ActivityEntry] = []
        for line in lines[-limit:]:
            try:
                data = json.loads(line)
                entries.append(
                    ActivityEntry(
                        # Stored as UTC; convert back so reloaded entries read
                        # in the same local clock as freshly emitted ones.
                        timestamp=datetime.fromisoformat(data["timestamp"]).astimezone(),
                        message=str(data.get("message", "")),
                        level=str(data.get("level", "info")),
                    )
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return entries

    def clear_activity(self) -> None:
        with contextlib.suppress(OSError):
            self.activity_path.unlink(missing_ok=True)

    def extend_activity(self, entries: Iterable[ActivityEntry]) -> None:
        for entry in entries:
            self.append_activity(entry)
