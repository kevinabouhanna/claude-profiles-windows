"""Version and changelog helper for cutting a release.

The version lives in exactly one place, ``src/claude_profiles/__init__.py``.
pyproject.toml, the exe's version resource, the installer, and the About text
all read it from there, so a release is: bump, tag, push.

Usage:
    python tools/release.py version              print the current version
    python tools/release.py bump patch|minor|major|X.Y.Z
                                                 set the version and move the
                                                 [Unreleased] changelog notes
                                                 under it
    python tools/release.py check-tag v1.2.3     fail unless the tag matches
    python tools/release.py notes [X.Y.Z]        print that version's changelog
                                                 section (release notes)

Versions are strict ``MAJOR.MINOR.PATCH`` - Windows version resources and the
installer both need plain numbers.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "claude_profiles" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
REPO_URL = "https://github.com/kevinabouhanna/claude-profiles-windows"

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
_UNRELEASED = "## [Unreleased]"


class ReleaseError(Exception):
    pass


def read_version(path: Path = VERSION_FILE) -> str:
    match = _VERSION_LINE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ReleaseError(f"no __version__ line in {path}")
    return match.group(1)


def parse(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if not match:
        raise ReleaseError(f"{version!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def next_version(current: str, part: str) -> str:
    if SEMVER.match(part):
        if parse(part) <= parse(current):
            raise ReleaseError(f"{part} is not newer than {current}")
        return part
    major, minor, patch = parse(current)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"expected patch, minor, major or X.Y.Z, got {part!r}")


def check_tag(tag: str, version: str) -> None:
    if tag != f"v{version}":
        raise ReleaseError(
            f"tag {tag!r} does not match the code's version {version!r}; "
            f"expected 'v{version}'"
        )


def release_notes(changelog: str, version: str) -> str:
    """The body of ``## [version]``, without its heading or link references."""
    heading = re.compile(rf"^## \[{re.escape(version)}\].*$", re.MULTILINE)
    match = heading.search(changelog)
    if not match:
        raise ReleaseError(f"CHANGELOG.md has no '## [{version}]' section")
    rest = changelog[match.end():]
    end = re.search(r"^(## |\[[^\]]+\]: )", rest, re.MULTILINE)
    body = (rest[: end.start()] if end else rest).strip()
    if not body:
        raise ReleaseError(f"the CHANGELOG.md section for {version} is empty")
    return body


def cut_changelog(changelog: str, previous: str, version: str, today: date) -> str:
    """Move the [Unreleased] notes under a new ``## [version] - date`` heading."""
    if _UNRELEASED not in changelog:
        raise ReleaseError(f"CHANGELOG.md has no '{_UNRELEASED}' section")
    head, _, tail = changelog.partition(_UNRELEASED)
    end = re.search(r"^(## |\[[^\]]+\]: )", tail, re.MULTILINE)
    pending = (tail[: end.start()] if end else tail).strip()
    after = tail[end.start():] if end else ""
    if not pending:
        raise ReleaseError(
            "nothing under [Unreleased] - describe the changes before releasing"
        )

    section = f"{_UNRELEASED}\n\n## [{version}] - {today.isoformat()}\n\n{pending}\n\n"
    text = head + section + after.lstrip("\n")

    unreleased_link = re.compile(r"^\[Unreleased\]: .*$", re.MULTILINE)
    links = (
        f"[Unreleased]: {REPO_URL}/compare/v{version}...HEAD\n"
        f"[{version}]: {REPO_URL}/compare/v{previous}...v{version}"
    )
    if unreleased_link.search(text):
        text = unreleased_link.sub(links, text, count=1)
    else:
        text = text.rstrip("\n") + "\n\n" + links + "\n"
    return text


def write_version(version: str, path: Path = VERSION_FILE) -> None:
    source = path.read_text(encoding="utf-8")
    path.write_text(
        _VERSION_LINE.sub(f'__version__ = "{version}"', source, count=1),
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"version", "bump", "check-tag", "notes"}:
        print(__doc__)
        return 2
    command, args = argv[1], argv[2:]
    try:
        current = read_version()
        if command == "version":
            print(current)
        elif command == "check-tag":
            if len(args) != 1:
                raise ReleaseError("usage: check-tag vX.Y.Z")
            check_tag(args[0], current)
            print(f"ok  {args[0]} matches {current}")
        elif command == "notes":
            version = args[0] if args else current
            print(release_notes(CHANGELOG.read_text(encoding="utf-8"), version))
        else:
            if len(args) != 1:
                raise ReleaseError("usage: bump patch|minor|major|X.Y.Z")
            version = next_version(current, args[0])
            changelog = cut_changelog(
                CHANGELOG.read_text(encoding="utf-8"), current, version, date.today()
            )
            write_version(version)
            CHANGELOG.write_text(changelog, encoding="utf-8")
            print(f"{current} -> {version}\n")
            print("Review CHANGELOG.md, then:")
            print(f'  git commit -am "Release v{version}"')
            print(f'  git tag -a v{version} -m "Claude Profiles {version}"')
            print("  git push origin main --follow-tags")
    except ReleaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
