"""The only module in the app that spawns ``cswap``.

Design rules enforced here:

* **Allowlist.** There is no free-form command entry point. Callers get four
  typed methods; every argv element is validated before it reaches the OS.
* **No shell.** argv is always a list and ``shell=True`` is never used.
* **Errors on stdout.** With ``--json``, cswap prints an error envelope to
  *stdout* and exits 1, so this client parses stdout regardless of exit code.
* **No raw output escapes.** stdout/stderr never reach a log or the UI; only
  parsed, redacted ``error.type`` / ``error.message`` fields do.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..models import AccountList, ActiveStatus, SwitchOutcome
from .redaction import redact

# Subcommands this app is ever permitted to invoke.
ALLOWED_COMMANDS: frozenset[str] = frozenset({"list", "status", "switch", "run"})

# Aliases are the only identifiers we accept from configuration.
ALIAS_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9_\-]{0,31}\Z")

# Belt-and-braces check on every argv element.
_SAFE_ARG = re.compile(r"\A[A-Za-z0-9_\-.@]{1,64}\Z")

DEFAULT_TIMEOUT = 20.0
SWITCH_TIMEOUT = 45.0

# Keeps a console window from flashing when the app spawns cswap.
_CREATE_NO_WINDOW = 0x08000000


class CswapErrorKind(Enum):
    NOT_INSTALLED = "not_installed"
    TIMEOUT = "timeout"
    INVALID_JSON = "invalid_json"
    REPORTED = "reported"
    DISALLOWED = "disallowed"
    UNKNOWN = "unknown"

    @property
    def is_transient(self) -> bool:
        """Should the poller back off and retry rather than give up?"""
        return self in {CswapErrorKind.TIMEOUT, CswapErrorKind.UNKNOWN}


class CswapError(Exception):
    """A user-safe failure. ``message`` has already passed through redaction."""

    def __init__(
        self,
        kind: CswapErrorKind,
        message: str,
        *,
        error_type: str | None = None,
    ) -> None:
        self.kind = kind
        self.message = redact(message, aggressive=True)
        self.error_type = redact(error_type) if error_type else None
        super().__init__(self.message)

    @property
    def user_message(self) -> str:
        return self.message


@runtime_checkable
class CswapBackend(Protocol):
    """Contract shared by the real client and the mock, so the UI cannot tell
    them apart."""

    def list_accounts(self) -> AccountList: ...

    def status(self) -> ActiveStatus: ...

    def switch(self, alias: str, *, fallback_number: int | None = None) -> SwitchOutcome: ...

    def build_run_command(self, number: int) -> list[str]: ...

    @property
    def is_available(self) -> bool: ...


def _extract_json(text: str) -> Any:
    """Pull the JSON document out of ``text``.

    Tolerates leading/trailing noise (a warning line, a progress spinner) by
    falling back to the outermost brace pair.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty response")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(stripped[start : end + 1])


def find_cswap() -> str | None:
    """Locate the cswap executable without trusting an arbitrary PATH entry."""
    found = shutil.which("cswap")
    if found:
        return found
    # uv/pipx default tool directory, which may not be on a GUI process's PATH.
    candidates = [
        Path.home() / ".local" / "bin" / "cswap.exe",
        Path.home() / ".local" / "bin" / "cswap",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class CswapClient:
    """Allowlisted, JSON-parsing wrapper around the cswap CLI."""

    def __init__(
        self,
        executable: str | None = None,
        *,
        discover: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        switch_timeout: float = SWITCH_TIMEOUT,
        runner: Any = None,
    ) -> None:
        # ``discover`` distinguishes "not specified, go look for it" from
        # "deliberately absent", which is what the not-installed path needs.
        self._executable = executable or (find_cswap() if discover else None)
        self._timeout = timeout
        self._switch_timeout = switch_timeout
        # Injectable for tests; defaults to subprocess.run.
        self._runner = runner or subprocess.run

    @property
    def executable(self) -> str | None:
        return self._executable

    @property
    def is_available(self) -> bool:
        return self._executable is not None

    # -- invocation ---------------------------------------------------------

    def _validate(self, args: list[str]) -> None:
        if not args:
            raise CswapError(CswapErrorKind.DISALLOWED, "No command given.")
        if args[0] not in ALLOWED_COMMANDS:
            raise CswapError(
                CswapErrorKind.DISALLOWED,
                f"Command {args[0]!r} is not permitted by this application.",
            )
        for arg in args:
            if arg == "--json":
                continue
            if not _SAFE_ARG.match(arg):
                raise CswapError(
                    CswapErrorKind.DISALLOWED,
                    "Refused an argument containing unexpected characters.",
                )

    def _invoke(self, args: list[str], timeout: float) -> Any:
        self._validate(args)
        if self._executable is None:
            raise CswapError(
                CswapErrorKind.NOT_INSTALLED,
                "claude-swap is not installed or not on PATH. "
                "Install it with: uv tool install claude-swap",
            )

        argv = [self._executable, *args]
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "encoding": "utf-8",
            "errors": "replace",
            "env": os.environ.copy(),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = _CREATE_NO_WINDOW

        try:
            completed = self._runner(argv, **kwargs)
        except FileNotFoundError as exc:
            raise CswapError(
                CswapErrorKind.NOT_INSTALLED,
                "claude-swap could not be launched. Check that it is installed.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CswapError(
                CswapErrorKind.TIMEOUT,
                f"claude-swap did not respond within {timeout:.0f} seconds.",
            ) from exc

        stdout = completed.stdout or ""
        try:
            payload = _extract_json(stdout)
        except (ValueError, json.JSONDecodeError) as exc:
            # Never surface stdout itself; it could quote credential material.
            raise CswapError(
                CswapErrorKind.INVALID_JSON,
                "claude-swap returned a response this app could not read.",
            ) from exc

        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
            raise CswapError(
                CswapErrorKind.REPORTED,
                str(error.get("message") or "claude-swap reported an error."),
                error_type=str(error.get("type") or ""),
            )

        if getattr(completed, "returncode", 0) not in (0, None):
            raise CswapError(
                CswapErrorKind.UNKNOWN,
                "claude-swap exited with an error.",
            )

        return payload

    # -- public API ---------------------------------------------------------

    def list_accounts(self) -> AccountList:
        payload = self._invoke(["list", "--json"], self._timeout)
        try:
            return AccountList.parse(payload)
        except ValueError as exc:
            raise CswapError(
                CswapErrorKind.INVALID_JSON,
                "The account list from claude-swap was not in the expected form.",
            ) from exc

    def status(self) -> ActiveStatus:
        payload = self._invoke(["status", "--json"], self._timeout)
        try:
            return ActiveStatus.parse(payload)
        except ValueError as exc:
            raise CswapError(
                CswapErrorKind.INVALID_JSON,
                "The status from claude-swap was not in the expected form.",
            ) from exc

    def switch(self, alias: str, *, fallback_number: int | None = None) -> SwitchOutcome:
        """Switch the active account.

        Upstream documents ``switch NUM|EMAIL|ALIAS``, so the alias is sent as
        given. If that is rejected, and the caller resolved a slot number from
        ``list``, retry by number rather than failing the user's click.
        """
        if not ALIAS_PATTERN.match(alias):
            raise CswapError(CswapErrorKind.DISALLOWED, "Refused an invalid profile alias.")
        try:
            payload = self._invoke(["switch", alias, "--json"], self._switch_timeout)
        except CswapError as exc:
            if exc.kind is CswapErrorKind.REPORTED and fallback_number is not None:
                payload = self._invoke(
                    ["switch", str(fallback_number), "--json"], self._switch_timeout
                )
            else:
                raise
        return SwitchOutcome.parse(payload)

    def build_run_command(self, number: int) -> list[str]:
        """argv for ``cswap run <number>``.

        Returned rather than executed: launching is the process launcher's job,
        because it must open a visible terminal window instead of a pipe. The
        installed CLI documents ``run`` as taking ``NUM|EMAIL`` only, so the
        caller resolves the alias to a slot number first.
        """
        if not isinstance(number, int) or number < 0:
            raise CswapError(CswapErrorKind.DISALLOWED, "Refused an invalid account number.")
        if self._executable is None:
            raise CswapError(
                CswapErrorKind.NOT_INSTALLED,
                "claude-swap is not installed or not on PATH.",
            )
        args = ["run", str(number)]
        self._validate(args)
        return [self._executable, *args]
