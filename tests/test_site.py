"""The marketing site in site/: things that silently rot when copy changes."""

from __future__ import annotations

import html
import json
import re
import shutil
import struct
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
INDEX = SITE / "index.html"


def _text(fragment: str) -> str:
    """Tag-free, entity-decoded, whitespace-collapsed text."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_faq_structured_data_matches_the_visible_faq(page):
    """Search engines are shown the JSON-LD; readers are shown the page.

    They must say the same thing, or the FAQ rich result misrepresents it.
    """
    block = re.search(
        r'<script type="application/ld\+json" id="faq-jsonld">(.*?)</script>', page, re.S
    )
    assert block, "FAQ JSON-LD is missing"
    data = json.loads(block.group(1))
    structured = [
        (q["name"], q["acceptedAnswer"]["text"]) for q in data["mainEntity"]
    ]

    visible = [
        (_text(q), _text(a))
        for q, a in re.findall(
            r"<details>\s*<summary>(.*?)</summary>\s*<p>(.*?)</p>\s*</details>", page, re.S
        )
    ]
    assert visible, "no visible FAQ entries found"
    assert structured == visible


class _Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.refs.append(value)


def test_every_local_link_and_image_exists(page):
    parser = _Refs()
    parser.feed(page)
    local = [
        r for r in parser.refs
        if not r.startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert local, "expected local assets"
    missing = [r for r in local if not (SITE / r.split("#")[0]).is_file()]
    assert missing == []

    # Present on disk is not enough: .gitignore once dropped favicon.ico
    # (build icons are ignored), so the published site would have lacked it.
    if shutil.which("git"):
        ignored = [
            r for r in local
            if subprocess.run(
                ["git", "check-ignore", "-q", "--no-index", str(SITE / r)], cwd=ROOT, check=False
            ).returncode == 0
        ]
        assert ignored == [], "these would never be published"


@pytest.mark.parametrize("name", ["index.html", "llms.txt"])
def test_copy_uses_no_em_or_en_dashes(name):
    """House style: a comma, a colon, or two sentences instead."""
    text = (SITE / name).read_text(encoding="utf-8")
    assert "—" not in text and "–" not in text


def test_the_link_preview_is_1200_by_630():
    data = (SITE / "assets" / "og.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (1200, 630)


def test_every_colour_is_a_token():
    """A raw hex outside :root drifts from the palette the first time it changes."""
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    root_end = css.index("}", css.index(":root {"))
    after_tokens = re.sub(r"/\*.*?\*/", "", css[root_end:], flags=re.S)
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", after_tokens) == []


def test_download_buttons_point_at_a_file_the_release_produces(page):
    """The site links releases/latest/download/<name>; the build must make <name>."""
    names = set(re.findall(r"releases/latest/download/([\w.-]+)", page))
    assert names == {"ClaudeProfiles-Setup.exe"}
    build = (ROOT / "tools" / "build_release.ps1").read_text(encoding="utf-8")
    assert '"ClaudeProfiles-Setup.exe"' in build


def test_the_site_does_not_claim_a_fixed_number_of_accounts(page):
    """Any number of accounts is supported; the copy must not say otherwise."""
    lowered = _text(page).lower()
    for phrase in ("both accounts", "two accounts side by side", "your two accounts"):
        assert phrase not in lowered, phrase


def test_sitemap_and_robots_point_at_the_canonical_url(page):
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', page).group(1)
    assert canonical in (SITE / "sitemap.xml").read_text(encoding="utf-8")
    assert canonical + "sitemap.xml" in (SITE / "robots.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["index.html", "llms.txt"])
def test_the_site_never_asks_for_a_separate_claude_swap_install(name):
    """claude-swap ships inside the installer; one download is the promise."""
    text = (SITE / name).read_text(encoding="utf-8")
    assert "uv tool install" not in text
    assert "pipx install" not in text
    assert "included" in text.lower() or "bundle" in text.lower()
