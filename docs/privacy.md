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

Both **Open data folder** and **Clear local activity history** are available in the Privacy tab.

## What is stored

**`settings.json`** — UI preferences only:

| Field | Example | Notes |
|---|---|---|
| `refresh_interval_seconds` | `120` | |
| `launch_at_signin` | `false` | |
| `notifications_enabled` | `false` | |
| `warn_threshold_pct` / `critical_threshold_pct` | `80` / `95` | |
| `hotkeys_enabled` | `false` | |
| `profile_aliases` | `{"personal": "personal", "work": "work"}` | Non-secret claude-swap aliases. |

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
interaction is executing the local `cswap` binary and reading the JSON it prints to stdout.

Any network traffic involved in fetching quota data is made by claude-swap, not by this app.

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
- **It cannot authenticate for you.** Re-login is always manual: you run `claude`, sign in, then run
  `cswap add --alias <alias>`.
- **It cannot run arbitrary commands.** Only `cswap list`, `status`, `switch`, and `run` are
  reachable, through typed methods with validated arguments and no shell.
- **It cannot modify credentials.** Every credential operation belongs to claude-swap.

## Windows integration, all opt-in

| Feature | Default | What it writes |
|---|---|---|
| Launch at sign-in | Off | A `.lnk` in your Startup folder. **No registry keys.** Delete the shortcut to undo. |
| Global shortcuts | Off | Nothing persistent; `RegisterHotKey` is in-process and released on exit. |
| Threshold notifications | Off | Nothing; state is in memory. |

Nothing in this list is written unless you turn the setting on.
