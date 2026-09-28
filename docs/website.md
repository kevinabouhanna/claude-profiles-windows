# The website

[kevinabouhanna.github.io/claude-profiles-windows](https://kevinabouhanna.github.io/claude-profiles-windows/)
is the project's front door for people searching for the problem it solves. It lives in `site/`
and is published by [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) whenever
`site/` changes on `main`.

It is static HTML and CSS with one small inline script, with no build step and no dependencies.
Preview it locally with:

```powershell
python -m http.server 8000 --directory site
```

## Writing

- **No em dashes or en dashes in the copy.** Use a comma, a colon, or two sentences.
- **Never imply a fixed number of accounts.** The app supports as many as claude-swap holds, so
  say "every account", "as many as you have", or "several". Not "both" or "your two accounts".
- Say what a thing does, in the words someone would search for. No adjectives that carry no
  information.

## Design

- Brutalist and light only: warm paper, near-black ink, 3px borders, hard offset shadows, square
  corners, monospace headings. The one accent is Claude Code's clay orange. `#problem` runs red
  and `#how` runs green, so the problem and the fix read apart at a glance.
- Every colour is a token on `:root` in `styles.css`. A raw hex anywhere else fails a test.
- Everything must work at 320px wide with no horizontal scrolling.

## Keep in step

`tests/test_site.py` checks most of these, but not all:

- The `FAQPage` JSON-LD in `<head>` must match the visible FAQ exactly. Edit both together.
- `llms.txt` is the plain-text brief for assistants. Update it when a fact about the app changes.
- `sitemap.xml` carries a `lastmod`. Bump it on a real content change.
- `assets/og.png` is the link preview, rendered from `assets/og-card.html` at 1200x630. The
  command to redo it is at the top of that file.
- `assets/shots/` holds copies of `docs/images/`. Re-copy them when the screenshots change.
- The download buttons link to `releases/latest/download/ClaudeProfiles-Setup.exe`, the stable name
  that `tools/build_release.ps1` produces alongside the versioned installer. Do not rename it.
