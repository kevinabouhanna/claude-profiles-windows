"""Write THIRD-PARTY-NOTICES.txt for everything the release bundles.

The installer ships more than this project's own code: the Python runtime, Qt
through PySide6, and claude-swap with its dependencies. Their licences require
their notices to travel with them, so this collects each one from the
installed package metadata of the build environment - the same packages
PyInstaller froze - rather than from a hand-kept list that could drift.

Usage:  python tools/third_party_notices.py [output-file]
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "dist" / "Claude Profiles" / "THIRD-PARTY-NOTICES.txt"
CSWAP_LOCK = ROOT / "tools" / "cswap" / "requirements.txt"

# The GUI's runtime dependencies. PySide6 arrives as several distributions.
APP_PACKAGES = ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6")

_LICENSE_NAME = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)[^/]*$", re.IGNORECASE)

HEADER = """\
Claude Profiles - third-party notices
=====================================

Claude Profiles is MIT licensed (see LICENSE in the source repository). This
installation also includes the software below, each under its own licence.
The full text of each licence found in the package follows its entry.

The cswap folder is a build of claude-swap by realiti4, bundled unmodified
apart from turning off its self-update check, since it is updated together
with Claude Profiles.
"""


def cswap_packages(lock: Path = CSWAP_LOCK) -> list[str]:
    """Distribution names pinned in the claude-swap lock file."""
    names = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if match:
            names.append(match.group(1))
    return names


def _license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    texts = []
    for file in dist.files or ():
        if _LICENSE_NAME.search(str(file).replace("\\", "/")):
            try:
                texts.append((file.name, Path(dist.locate_file(file)).read_text("utf-8")))
            except (OSError, UnicodeDecodeError):
                continue
    return texts


def _licence_label(meta) -> str:
    return (
        meta.get("License-Expression")
        or (meta.get("License") if meta.get("License") and len(meta.get("License")) < 80 else None)
        or next(
            (c.split("::")[-1].strip() for c in meta.get_all("Classifier") or ()
             if c.startswith("License ::")),
            "see licence text",
        )
    )


def entry(name: str) -> str:
    dist = metadata.distribution(name)
    meta = dist.metadata
    url = meta.get("Home-page") or next(
        (u.split(",", 1)[1].strip() for u in meta.get_all("Project-URL") or ()), ""
    )
    lines = [
        "-" * 78,
        f"{meta['Name']} {dist.version}",
        f"Licence: {_licence_label(meta)}",
    ]
    if url:
        lines.append(f"Source: {url}")
    texts = _license_texts(dist)
    if not texts:
        raise SystemExit(f"no licence text found for {name}; add it before shipping")
    for filename, text in texts:
        lines += ["", f"[{filename}]", text.strip()]
    return "\n".join(lines) + "\n"


def python_entry() -> str:
    licence = Path(sys.base_prefix) / "LICENSE.txt"
    text = licence.read_text(encoding="utf-8") if licence.is_file() else ""
    return "\n".join(
        [
            "-" * 78,
            f"Python {sys.version.split()[0]}",
            "Licence: PSF-2.0",
            "Source: https://www.python.org/",
            "",
            text.strip() or "See https://docs.python.org/3/license.html",
        ]
    ) + "\n"


def pyinstaller_entry() -> str:
    return "\n".join(
        [
            "-" * 78,
            "PyInstaller bootloader",
            "Licence: GPL-2.0-or-later WITH Bootloader-exception",
            "Source: https://pyinstaller.org/",
            "",
            "The executables are started by PyInstaller's bootloader, which is "
            "licensed under the GPL with an exception that allows it to be used "
            "in programs under any licence.",
        ]
    ) + "\n"


def build() -> str:
    names = list(dict.fromkeys([*APP_PACKAGES, *cswap_packages()]))
    installed = [n for n in names if _is_installed(n)]
    missing = sorted(set(names) - set(installed) - {"PySide6-Addons"})
    if missing:
        raise SystemExit(f"not installed in this environment: {', '.join(missing)}")
    parts = [HEADER, python_entry(), pyinstaller_entry()]
    parts += [entry(name) for name in installed]
    return "\n".join(parts)


def _is_installed(name: str) -> bool:
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
