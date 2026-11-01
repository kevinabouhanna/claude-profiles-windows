"""Claude Code inside a WSL2 distribution.

Claude Code in WSL - including the VS Code extension in Remote-WSL - keeps its
login in the distro's ``~/.claude``, which the Windows ``cswap.exe`` never sees.
So for a WSL target the app runs claude-swap *inside* the distro and parses the
same ``--json`` contract. claude-swap supports WSL natively (it detects
``WSL_DISTRO_NAME``) and keeps its store in ``~/.local/share/claude-swap``.

Rules this module keeps:

* ``wsl.exe`` is a second fixed executable, resolved from ``%SystemRoot%``,
  never from PATH. Every call is ``wsl.exe -d <distro> --exec <program> ...``:
  an argv list run without a shell, so nothing is ever parsed by bash.
* The distro name must appear in ``wsl.exe -l -q`` and match a conservative
  character class. The home directory and tool paths read back from the
  distro are validated the same way before they go into an argv.
* The cswap command allowlist, argument validation and redaction are the
  Windows ones: this only changes the prefix in front of the cswap arguments.
* A background poll never starts a stopped distro: WSL shuts its VM down when
  idle, and polling every couple of minutes would keep booting it. Only a
  visible window or an explicit action wakes it.
* Nothing here reads a credential.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import AccountList, ActiveStatus, SwitchOutcome
from .cswap_client import CswapClient, CswapError, CswapErrorKind

WINDOWS = "windows"
WSL_PREFIX = "wsl:"

# The JSON contract the app parses was settled in this release; the Windows
# build bundles it. Older ones lack fields the app relies on.
MIN_CSWAP_VERSION = (0, 26, 0)

INSTALL_CSWAP = "uv tool install claude-swap"
UPGRADE_CSWAP = "uv tool upgrade claude-swap"
INSTALL_CLAUDE = "curl -fsSL https://claude.ai/install.sh | bash"

LIST_TIMEOUT = 15.0
DETECT_TIMEOUT = 45.0  # the first call after the VM stopped includes its boot

_CREATE_NO_WINDOW = 0x08000000

_DISTRO = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_POSIX_PATH = re.compile(r"\A/[A-Za-z0-9._/+-]{1,255}\Z")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# Distros that exist to serve another product, not to be worked in.
_HIDDEN_DISTROS = frozenset({"docker-desktop", "docker-desktop-data", "rancher-desktop",
                             "rancher-desktop-data"})


def wsl_executable() -> str:
    """``%SystemRoot%\\System32\\wsl.exe``, never a PATH lookup."""
    root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    return str(Path(root) / "System32" / "wsl.exe")


def environment_value(distro: str | None) -> str:
    """The settings value for a target: ``windows`` or ``wsl:<distro>``."""
    return f"{WSL_PREFIX}{distro}" if distro else WINDOWS


def distro_of(value: str) -> str | None:
    """The distro a settings value names, or None for Windows (or junk)."""
    if isinstance(value, str) and value.startswith(WSL_PREFIX):
        name = value[len(WSL_PREFIX):]
        if _DISTRO.match(name):
            return name
    return None


def environment_label(value: str) -> str:
    distro = distro_of(value)
    return f"{distro} (WSL)" if distro else "Windows"


def _decode(raw: bytes | str | None) -> str:
    """wsl.exe's own messages are UTF-16LE unless ``WSL_UTF8=1`` took effect."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.replace("\x00", "")
    if b"\x00" in raw:
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8", errors="replace").lstrip("\ufeff")


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["WSL_UTF8"] = "1"
    return env


Runner = Callable[..., Any]


def _run(runner: Runner, argv: list[str], timeout: float) -> tuple[int, str, str]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "timeout": timeout,
        "env": _env(),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    completed = runner(argv, **kwargs)
    return (
        getattr(completed, "returncode", 0) or 0,
        _decode(getattr(completed, "stdout", b"")),
        _decode(getattr(completed, "stderr", b"")),
    )


def _names(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        name = line.strip().lstrip("*").strip()
        if name and _DISTRO.match(name) and name.lower() not in _HIDDEN_DISTROS:
            names.append(name)
    return names


def list_distros(runner: Runner = subprocess.run) -> list[str]:
    """Installed distributions a user could run Claude Code in.

    Listing does not start the WSL VM. Any failure - WSL not installed, the
    optional component off - is just "no distros".
    """
    try:
        code, out, _ = _run(runner, [wsl_executable(), "-l", "-q"], LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    return _names(out) if code == 0 else []


def running_distros(runner: Runner = subprocess.run) -> set[str] | None:
    """Distributions running right now, or None if WSL could not be asked."""
    try:
        code, out, _ = _run(
            runner, [wsl_executable(), "-l", "--running", "-q"], LIST_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if code != 0:
        # wsl.exe exits non-zero when *nothing* is running.
        return set()
    return set(_names(out))


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def search_path(home: str) -> str:
    """PATH for programs run with ``--exec``.

    ``--exec`` skips the login shell, so ``~/.local/bin`` - where both
    ``uv tool install`` and Claude Code's native installer put their commands -
    is not on PATH unless set here. ``cswap run`` needs it to find ``claude``.
    """
    return ":".join(
        [
            f"{home}/.local/bin",
            f"{home}/.npm-global/bin",
            f"{home}/bin",
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
        ]
    )


@dataclass(frozen=True)
class WslEnvironment:
    """What detection found inside one distro."""

    distro: str
    home: str
    cswap: str
    version: tuple[int, int, int]
    claude: str | None = None

    def exec_prefix(self) -> list[str]:
        """``wsl.exe -d <distro> --cd ~ --exec /usr/bin/env PATH=...``.

        ``env`` sets PATH and then runs the next argument directly; it stops
        reading options at the first word that is not an assignment, so the
        program and its arguments are never taken as options of its own.
        """
        return [
            wsl_executable(),
            "-d",
            self.distro,
            "--cd",
            "~",
            "--exec",
            "/usr/bin/env",
            f"PATH={search_path(self.home)}",
        ]

    def cswap_prefix(self) -> list[str]:
        return [*self.exec_prefix(), self.cswap]

    def sign_in_command(self) -> list[str] | None:
        if self.claude is None:
            return None
        return [*self.exec_prefix(), self.claude, "auth", "login"]

    @property
    def version_text(self) -> str:
        return ".".join(str(part) for part in self.version)


class WslProblem(Exception):
    """Detection failed in a way the user has to fix. ``message`` says how."""


def detect(distro: str, runner: Runner = subprocess.run) -> WslEnvironment:
    """Find claude-swap (and Claude Code) inside ``distro``.

    Raises :class:`WslProblem` with a user-facing explanation when the distro
    is missing, cannot start, or lacks a usable claude-swap. Starts the distro
    if it is stopped.
    """
    if not _DISTRO.match(distro or ""):
        raise WslProblem("That WSL distribution name is not valid.")
    if distro not in list_distros(runner):
        raise WslProblem(
            f"There is no WSL distribution called {distro}. Pick another one in "
            "Settings, or switch back to Windows."
        )
    base = [wsl_executable(), "-d", distro, "--cd", "~", "--exec"]
    try:
        code, out, _ = _run(runner, [*base, "printenv", "HOME"], DETECT_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise WslProblem(f"{distro} did not start in time.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise WslProblem(f"{distro} could not be started.") from exc
    home = out.strip()
    if code != 0 or not _POSIX_PATH.match(home):
        raise WslProblem(f"{distro} could not be started.")

    env_prefix = [*base, "/usr/bin/env", f"PATH={search_path(home)}"]
    try:
        _, out, _ = _run(runner, [*env_prefix, "which", "cswap", "claude"], DETECT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WslProblem(f"Could not look for claude-swap in {distro}.") from exc
    found: dict[str, str] = {}
    for line in out.splitlines():
        path = line.strip()
        if _POSIX_PATH.match(path):
            found.setdefault(path.rsplit("/", 1)[-1], path)

    cswap = found.get("cswap")
    if cswap is None:
        raise WslProblem(
            f"claude-swap is not installed in {distro}. Install it there with: "
            f"{INSTALL_CSWAP}"
        )
    try:
        _, out, err = _run(runner, [*env_prefix, cswap, "--version"], DETECT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WslProblem(f"claude-swap in {distro} did not respond.") from exc
    version = parse_version(out) or parse_version(err)
    if version is None:
        raise WslProblem(f"claude-swap in {distro} did not report a version.")
    if version < MIN_CSWAP_VERSION:
        wanted = ".".join(str(p) for p in MIN_CSWAP_VERSION)
        have = ".".join(str(p) for p in version)
        raise WslProblem(
            f"claude-swap {have} in {distro} is too old; {wanted} or newer is "
            f"needed. Update it there with: {UPGRADE_CSWAP}"
        )
    return WslEnvironment(
        distro=distro, home=home, cswap=cswap, version=version, claude=found.get("claude")
    )


class WslCswapClient:
    """A :class:`CswapBackend` that runs claude-swap inside a WSL distro.

    Detection is lazy and cached: it may boot the VM, so it happens on the
    first call that is allowed to (a poll while a window is open, or an
    action), not at app start. A failed detection is retried on the next call,
    so installing claude-swap in the distro is picked up without a restart.
    """

    def __init__(self, distro: str, *, runner: Runner = subprocess.run) -> None:
        self.distro = distro
        self._runner = runner
        self._lock = threading.Lock()
        self._env: WslEnvironment | None = None
        self._client: CswapClient | None = None

    # -- detection ------------------------------------------------------------

    @property
    def environment(self) -> WslEnvironment | None:
        return self._env

    def ensure_detected(self) -> WslEnvironment:
        """Detect once; raise a not-installed CswapError the user can act on."""
        with self._lock:
            if self._env is not None:
                return self._env
            try:
                env = detect(self.distro, self._runner)
            except WslProblem as exc:
                raise CswapError(CswapErrorKind.NOT_INSTALLED, str(exc)) from exc
            self._env = env
            self._client = CswapClient(
                argv_prefix=env.cswap_prefix(),
                runner=self._runner,
                extra_env={"WSL_UTF8": "1"},
                unreadable_message=f"claude-swap in {self.distro} could not be run.",
            )
            return env

    def _cswap(self) -> CswapClient:
        self.ensure_detected()
        assert self._client is not None
        return self._client

    def dormant_reason(self) -> str | None:
        """Why a background poll should not run now, or None if it may.

        A stopped distro stays stopped: polls skip it until a window opens.
        """
        running = running_distros(self._runner)
        if running is None or self.distro in running:
            return None
        return f"{self.distro} is not running"

    def sign_in_command(self) -> list[str] | None:
        env = self.ensure_detected()
        return env.sign_in_command()

    def sign_in_unavailable(self) -> str:
        return (
            f"Claude Code is not installed in {self.distro}. Install it there "
            f"with: {INSTALL_CLAUDE}"
        )

    def describe(self) -> str:
        env = self.ensure_detected()
        text = f"Using claude-swap {env.version_text} in {self.distro}."
        if env.claude is None:
            text += (
                f" Claude Code was not found there, so launching and signing in "
                f"need it installed: {INSTALL_CLAUDE}"
            )
        return text

    # -- CswapBackend -----------------------------------------------------------

    @property
    def is_available(self) -> bool:
        # Unknown until detection runs; a failure surfaces as NOT_INSTALLED.
        return True

    @property
    def executable(self) -> str | None:
        return self._env.cswap if self._env is not None else None

    def list_accounts(self) -> AccountList:
        return self._cswap().list_accounts()

    def status(self) -> ActiveStatus:
        return self._cswap().status()

    def switch(self, alias: str | None, *, fallback_number: int | None = None) -> SwitchOutcome:
        return self._cswap().switch(alias, fallback_number=fallback_number)

    def add_current_account(self, alias: str) -> str:
        return self._cswap().add_current_account(alias)

    def resync_login(self) -> str:
        return self._cswap().resync_login()

    def set_alias(self, number: int, alias: str) -> str:
        return self._cswap().set_alias(number, alias)

    def build_run_command(self, number: int) -> list[str]:
        return self._cswap().build_run_command(number)
