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
#
# ``add`` and ``alias`` exist so accounts can be set up from the UI. Neither
# performs authentication: ``add`` registers whichever account Claude Code is
# *already* signed in as, and signing in remains an interactive terminal step
# the user performs themselves. Everything destructive - remove, purge, export,
# import, add-token, config - stays out.
ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {"list", "status", "switch", "run", "add", "alias"}
)

# Commands that mutate stored accounts. Run from an explicit click, with one
# exception: a bare ``add`` that re-stores the login Claude Code is already
# using, for the account claude-swap itself says is active (see
# ``services/login_repair.py``). That never changes which account is active,
# never names a slot or alias, and never signs anyone in.
MUTATING_COMMANDS: frozenset[str] = frozenset({"switch", "add", "alias"})

# Aliases are the only identifiers we accept from configuration.
# cswap additionally requires an alias not be purely numeric, since that would
# collide with slot numbers.
ALIAS_PATTERN = re.compile(r"\A(?!\d+\Z)[a-z0-9][a-z0-9_\-.]{0,31}\Z")

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

    def switch(
        self, alias: str | None, *, fallback_number: int | None = None
    ) -> SwitchOutcome: ...

    def add_current_account(self, alias: str) -> str: ...

    def resync_login(self) -> str: ...

    def set_alias(self, number: int, alias: str) -> str: ...

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


def missing_cswap_advice() -> str:
    """What to tell someone whose claude-swap cannot be found.

    An installed Claude Profiles ships its own copy, so if that is missing the
    install is damaged; only a source checkout needs claude-swap separately.
    """
    if getattr(sys, "frozen", False):
        return (
            "The claude-swap copy bundled with Claude Profiles is missing. "
            "Reinstall Claude Profiles to restore it."
        )
    return (
        "claude-swap is not installed or not on PATH. "
        "Install it with: uv tool install claude-swap"
    )


def bundled_cswap() -> Path | None:
    """The claude-swap build shipped inside the installed app, if any.

    The installer puts it at ``<app folder>\\cswap\\cswap.exe``, pinned to the
    version the app's JSON parsing was tested against. Only a frozen build has
    one; a source checkout uses whatever claude-swap is installed.
    """
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).resolve().parent / "cswap" / "cswap.exe"
    return candidate if candidate.is_file() else None


def find_cswap() -> str | None:
    """Locate the cswap executable without trusting an arbitrary PATH entry.

    The bundled copy wins over one on PATH: it is the version this build was
    tested against, and both read the same account store, so preferring it
    changes nothing about which accounts are seen.
    """
    bundled = bundled_cswap()
    if bundled is not None:
        return str(bundled)
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
        argv_prefix: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        unreadable_message: str | None = None,
    ) -> None:
        # ``argv_prefix`` runs cswap through a fixed launcher - the WSL target
        # uses ``wsl.exe -d <distro> --exec ... cswap``. It is built by the
        # caller from validated parts; only the cswap arguments after it come
        # from this class's allowlist.
        self._prefix = list(argv_prefix) if argv_prefix else None
        # ``discover`` distinguishes "not specified, go look for it" from
        # "deliberately absent", which is what the not-installed path needs.
        if self._prefix is not None:
            self._executable: str | None = self._prefix[-1]
        else:
            self._executable = executable or (find_cswap() if discover else None)
        self._timeout = timeout
        self._switch_timeout = switch_timeout
        # Injectable for tests; defaults to subprocess.run.
        self._runner = runner or subprocess.run
        self._extra_env = dict(extra_env or {})
        # When set, a response with no JSON and a failing exit code means the
        # launcher itself failed (a distro that will not start), which is not
        # something retrying faster fixes - so it is reported as not installed.
        self._unreadable_message = unreadable_message

    @property
    def executable(self) -> str | None:
        return self._executable

    @property
    def is_available(self) -> bool:
        return self._executable is not None

    def _argv(self, args: list[str]) -> list[str]:
        if self._prefix is not None:
            return [*self._prefix, *args]
        assert self._executable is not None
        return [self._executable, *args]

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self._extra_env)
        return env

    # -- environment hooks (the WSL target has its own versions) -------------

    def dormant_reason(self) -> str | None:
        """Windows needs nothing woken before a poll."""
        return None

    def sign_in_command(self) -> list[str] | None:
        """``claude auth login`` for the Windows Claude Code, if installed."""
        from .process_launcher import find_claude

        claude = find_claude()
        return [claude, "auth", "login"] if claude else None

    def sign_in_unavailable(self) -> str:
        return (
            "Could not find the Claude Code command. Open a terminal and run: "
            "claude auth login"
        )

    def describe(self) -> str:
        return "Using claude-swap on Windows."

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
                missing_cswap_advice(),
            )

        argv = self._argv(args)
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "encoding": "utf-8",
            "errors": "replace",
            "env": self._env(),
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
            if self._unreadable_message and getattr(completed, "returncode", 0) not in (0, None):
                raise CswapError(CswapErrorKind.NOT_INSTALLED, self._unreadable_message) from exc
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

    def switch(
        self, alias: str | None, *, fallback_number: int | None = None
    ) -> SwitchOutcome:
        """Switch the active account.

        Upstream documents ``switch NUM|EMAIL|ALIAS``, so the alias is sent as
        given. If that is rejected, and the caller resolved a slot number from
        ``list``, retry by number rather than failing the user's click. An
        account registered without an alias is switched by number directly.
        """
        if alias is None:
            if fallback_number is None:
                raise CswapError(
                    CswapErrorKind.DISALLOWED, "No alias or slot number to switch to."
                )
            payload = self._invoke(
                ["switch", str(int(fallback_number)), "--json"], self._switch_timeout
            )
            return SwitchOutcome.parse(payload)
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

    # -- setup commands (no --json support upstream) ------------------------

    def _invoke_text(self, args: list[str], timeout: float) -> str:
        """Run a subcommand that does not support ``--json``.

        ``--json`` is accepted only by ``list``, ``status``, and ``switch``, so
        ``add`` and ``alias`` report through their exit code and plain text.
        Output is redacted before it is returned, and callers re-read
        authoritative state from ``list --json`` rather than parsing it.
        """
        self._validate(args)
        if self._executable is None:
            raise CswapError(
                CswapErrorKind.NOT_INSTALLED,
                missing_cswap_advice(),
            )

        argv = self._argv(args)
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": timeout,
            "encoding": "utf-8",
            "errors": "replace",
            "env": self._env(),
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

        combined = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
        message = redact(combined, aggressive=True).strip()
        if getattr(completed, "returncode", 0) not in (0, None):
            raise CswapError(
                CswapErrorKind.REPORTED,
                message or "claude-swap could not complete that request.",
            )
        return message

    def add_current_account(self, alias: str) -> str:
        """``cswap add --alias <alias>``.

        Registers whichever account Claude Code is **currently signed in as**.
        This performs no authentication of its own - the user signs in through
        Claude Code first, in a terminal, and this only records the result.
        """
        if not ALIAS_PATTERN.match(alias):
            raise CswapError(CswapErrorKind.DISALLOWED, "Refused an invalid profile alias.")
        return self._invoke_text(["add", "--alias", alias], self._switch_timeout)

    def resync_login(self) -> str:
        """Bare ``cswap add``: re-store the login Claude Code is using now.

        For an account claude-swap already knows, ``add`` with no arguments
        copies the current Claude Code login into that account's slot, keeps
        its alias, and clears any "re-login needed" verdict bound to the old
        token. It makes no network call. With no ``--slot`` and no ``--alias``
        it cannot overwrite a different account or collide with a name; the
        caller still confirms the login belongs to a registered account first,
        because for an unknown one ``add`` would register a new slot instead.
        """
        return self._invoke_text(["add"], self._switch_timeout)

    def set_alias(self, number: int, alias: str) -> str:
        """``cswap alias <number> <alias>`` for an already-registered account."""
        if not isinstance(number, int) or number < 0:
            raise CswapError(CswapErrorKind.DISALLOWED, "Refused an invalid account number.")
        if not ALIAS_PATTERN.match(alias):
            raise CswapError(CswapErrorKind.DISALLOWED, "Refused an invalid profile alias.")
        return self._invoke_text(["alias", str(number), alias], self._timeout)

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
        return self._argv(args)
