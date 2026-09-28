# Privacy and data handling

Claude Profiles is a local-only desktop application. This document is the complete inventory of what
it stores, what it never stores, and what it cannot do. The same summary appears in the app's
**Privacy** tab.

## Where data lives

```
%LOCALAPPDATA%\ClaudeProfiles\
├── settings.json     UI preferences and profile aliases
└── activity.jsonl    capped activity history (500 entries)
```

The directory is created with `icacls <dir> /inheritance:r /grant:r <you>:(OI)(CI)F`, which removes
inherited permissions and grants access to your user account only.

A few small PNGs are also written to the standard Windows cache location
(`%LOCALAPPDATA%\Claude Profiles\cache\icons`). These are checkbox ticks and spin-button arrows
rendered from the system icon font, because Qt stylesheets can only reference indicator images by
URL. They are generated UI assets containing no account data, and deleting them is harmless.

Both **Open data folder** and **Clear local activity history** are available in the Privacy tab.

## What is stored

**`settings.json`** — UI preferences only:

| Field | Example | Notes |
|---|---|---|
| `refresh_interval_seconds` | `120` | |
| `launch_at_signin` | `true` | |
| `notifications_enabled` | `false` | |
| `warn_threshold_pct` / `critical_threshold_pct` | `80` / `95` | |
| `hotkeys_enabled` | `false` | |
| `autostart_target` | `0ab8bec2806c4a1d` | A short hash of the launcher the shortcuts point at, so a stale shortcut can be detected. Hashed, not stored as a path, because a path contains your Windows username. |
| `profile_aliases` | `{}` | Unused and always empty; kept so older settings files still load. The list of accounts comes from claude-swap each time. |

**`activity.jsonl`** — one JSON object per line, each a timestamp, a level, and a safe message:

```json
{"timestamp": "2026-09-22T14:32:07+00:00", "level": "info", "message": "Switched from Personal to Work at 14:32"}
{"timestamp": "2026-09-22T14:32:09+00:00", "level": "info", "message": "Usage refreshed"}
{"timestamp": "2026-09-22T14:35:11+00:00", "level": "warning", "message": "Work requires re-authentication"}
```

Before any line is written it passes through `safe_for_log()`, which redacts secret-shaped text and
masks email addresses (`someone@example.com` → `s***@example.com`). The file is capped at 500
entries and trimmed on write.

## What is never stored

- **No access tokens, refresh tokens, API keys, setup tokens, session cookies, or passwords.** The
  app never reads or writes them in any form.
- **No contents of** `%USERPROFILE%\.claude\.credentials.json`, `%USERPROFILE%\.claude-swap-backup\`,
  or any Claude Code session file. These paths are never opened by this application.
- **No raw subprocess output.** `cswap` stdout and stderr never reach a log, a dialog, or the
  activity history. Only parsed `error.type` and `error.message` fields do, after redaction.
- **No email addresses in source or configuration.** Only aliases are stored. The addresses shown in
  the UI come from `cswap list --json` at runtime and are masked before being written to disk.
- **No telemetry, analytics, crash reporting, cloud sync, or remote configuration.**

## Network activity

**None.** The application imports no networking library and opens no sockets. Its only external
interaction is executing `cswap` and reading the JSON it prints to stdout.

Any network traffic involved in fetching quota data is made by claude-swap, not by this app.

### The bundled claude-swap

The installer includes claude-swap, in the `cswap` folder beside the app, so it is the copy the app
runs. It is claude-swap's own code, pinned to a tested version and frozen into `cswap.exe`, with
one change: its once-a-day check of PyPI for a newer version is switched off, because the bundled
copy is updated by installing a newer Claude Profiles. Its credentials and account data live where
claude-swap always keeps them, in `%USERPROFILE%\.claude-swap-backup\`, and are shared with any
claude-swap you installed yourself. Uninstalling Claude Profiles leaves them in place.

## Redaction

`services/redaction.py` is the single chokepoint for every string that reaches a log, dialog, or
activity entry.

| Pattern | Example | Result |
|---|---|---|
| Anthropic keys | `sk-ant-api03-…` | `[redacted]` |
| JWTs | `eyJhbGciOi….….…` | `[redacted]` |
| Bearer tokens | `Bearer abc123…` | `Bearer [redacted]` |
| Authorization headers | `Authorization: …` | `Authorization: [redacted]` |
| Secret assignments | `access_token="…"` | key kept, value `[redacted]` |
| Credential paths | `…\.credentials.json` | `[credentials-path]` |
| Opaque 40+ char blobs | exception text only | `[redacted]` |

Organization UUIDs and ISO timestamps are deliberately preserved — the 40-character floor on the
blob rule sits above the 36 characters of a UUID, and there is a test asserting exactly that.

`contains_secret(text)` is defined as "would redaction change this string", so the safety check can
never fall behind the pattern list. A property test asserts that no activity entry ever matches,
including entries generated from deliberately poisoned error messages.

## What the app deliberately cannot do

- **It cannot switch your browser session.** Signing in to claude.ai in a browser is entirely
  separate and unaffected. The UI never claims otherwise.
- **It cannot terminate anything.** Claude Code, VS Code, terminals, and open chats are left alone.
- **It cannot authenticate for you.** The Accounts tab can open a terminal running Claude Code and
  can register an account that is *already* signed in, but the sign-in itself happens in Claude
  Code, in that terminal, driven by you. No credential passes through this app.
- **It cannot run arbitrary commands.** Only `cswap list`, `status`, `switch`, `run`, `add`, and
  `alias` are reachable, through typed methods with validated arguments and no shell. `remove`,
  `purge`, `export`, `import`, `add-token`, and `config` are refused.
- **It cannot modify credentials.** Every credential operation belongs to claude-swap.

## Windows integration, all opt-in

| Feature | Default | What it writes |
|---|---|---|
| Launch at sign-in | **On** | A `.lnk` in your Startup folder. **No registry keys.** Untick the setting, or delete the shortcut in Explorer, to undo. |
| Start menu entry | On (once) | A `.lnk` in your Start menu, created on first run only. If you delete it, it is not recreated. |
| Global shortcuts | Off | Nothing persistent; `RegisterHotKey` is in-process and released on exit. |
| Threshold notifications | Off | Nothing; state is in memory. |

Start-at-sign-in and the Start menu entry are the two things written without an explicit click,
so that the app behaves like any other installed program. Both are plain shortcuts in your own
profile, visible in Explorer, and removable by hand. Everything else in this list stays untouched
until you turn it on.

An `app.ico` is also generated in the data folder, purely so those shortcuts have an icon.
