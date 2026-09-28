# Architecture

## The central decision

Claude Profiles owns **no credential logic**. Every credential read, write, switch, and quota fetch
is delegated to the `cswap` CLI. This app is a presentation and orchestration layer over that tool's
documented JSON contract.

The practical consequence: there is exactly one module that can spawn a process with captured output
(`services/cswap_client.py`) and one that can open a terminal (`services/process_launcher.py`).
Neither accepts a file path, so no code path can reach a credential file even by accident.

```
                        ┌──────────────────────────────┐
  tray.py ──────────────│      TrayController          │
  (QSystemTrayIcon)     │  the only place UI and       │
                        │  services are wired together │
                        └──────────────┬───────────────┘
                                       │
   CompactPopup  ◄──── signals ────────┤
   MainWindow    ◄──── signals ────────┤
                                       │
                        ┌──────────────▼───────────────┐
                        │       ProfileService         │  the only object the UI talks to
                        │  accounts → profiles (any N) │
                        │  switch orchestration        │
                        │  activity log                │
                        └──────────────┬───────────────┘
                                       │
        ┌────────────────┬─────────────┼──────────────┬─────────────────┐
        ▼                ▼             ▼              ▼                 ▼
 PollingService   CswapClient   SettingsService  ProcessLauncher  NotificationService
 (timer + guard)  (allowlist)   (prefs + log)    (wt / powershell)  (tray + thresholds)
                        │
                        ▼
                  ┌───────────┐
                  │   cswap   │  ← the only external dependency
                  └───────────┘
```

## Module responsibilities

| Module | Responsibility |
|---|---|
| `models.py` | Frozen dataclasses mirroring the JSON contract. Tolerant parsing; no Qt imports. |
| `services/cswap_client.py` | Allowlisted invocation, JSON extraction, error envelopes. **The only module that spawns `cswap` with captured output.** |
| `services/mock_cswap.py` | Drop-in backend producing synthetic payloads through the real parsers. |
| `services/polling_service.py` | `PollingCoordinator` (pure Python: overlap guard + backoff) and a thin Qt shell. |
| `services/profile_service.py` | Builds one profile per claude-swap account (any number), switch orchestration, busy flag, activity log. |
| `services/redaction.py` | The single chokepoint for diagnostics. |
| `services/settings_service.py` | ACL-locked JSON prefs and capped activity history. |
| `services/process_launcher.py` | Opens a visible terminal; reads nothing back. |
| `services/notification_service.py` | Tray balloons plus the threshold latch. |
| `services/autostart.py`, `hotkeys.py` | Windows integration. Hotkeys are opt-in; start-at-sign-in defaults on (see the note in `docs/development.md`). |
| `widgets/`, `main_window.py`, `tray.py` | Presentation only; no subprocess or file access. |

## The claude-swap contract, as verified

Checked against claude-swap 0.26.0 rather than assumed from documentation. `--json` is supported on
`list`, `status`, `switch`, and `auto`; all payloads carry `schemaVersion: 1`.

`cswap list --json` — the authoritative source for everything rendered:

```jsonc
{
  "schemaVersion": 1,
  "activeAccountNumber": 1,
  "accounts": [{
    "number": 1, "email": "...", "alias": "personal",
    "organizationName": "...", "organizationUuid": "...", "isOrganization": false,
    "active": true,
    "usageStatus": "ok",          // one of 8 values; unknown values degrade to UNKNOWN
    "usage": {
      "fiveHour":  { "pct": 42.0, "resetsAt": "...Z", "countdown": "2h 13m", "clock": "18:00" },
      "sevenDay":  { "pct": 63.0, "expectedPct": 57.0, "aheadOfPace": true,
                     "projectedExhaustionAt": "...Z", "willLastToReset": true },
      "spend":     { "used": 42.5, "limit": 100.0, "currency": "USD" },
      "scoped":    [ { "name": "Opus", "pct": 18.0 } ]      // per-model rows
    },
    "usageFetchedAt": "...Z", "usageAgeSeconds": 3.2,

    // present instead when a refresh failed but a reading is retained:
    "lastGoodUsage": {...}, "lastGoodFetchedAt": "...Z", "lastGoodAgeSeconds": 1380.0,
    "usageError": "...", "usageRetryAt": "...Z"
  }]
}
```

`cswap status --json` returns a **different shape** — `{"active": {"email", "managed"}}`, not an
account row. `managed: false` means the current Claude Code login is not registered with claude-swap
at all.

### Four contract details that shaped the design

1. **Errors go to stdout, not stderr.** With `--json`, cswap prints
   `{"error": {"type", "message"}}` to stdout and exits 1. The client therefore parses stdout
   *regardless of exit code*; treating a non-zero exit as "output is garbage" would discard the only
   useful diagnostic.

2. **`run` takes `NUM|EMAIL`, not an alias.** The installed CLI's help documents only numbers and
   emails for `run`. Rather than depend on undocumented alias support or hard-code an email address,
   `ProfileService.run_command_for` resolves the alias to a slot number from `list --json` first.
   `switch` *is* documented to accept aliases, so it sends the alias, with a fallback to the resolved
   number if that is rejected.

3. **Last-known values already exist upstream.** `lastGoodUsage` means the app does not need a second
   cache of its own — which would inevitably drift from claude-swap's. `Account.effective_usage`
   simply prefers `usage` and falls back to `lastGoodUsage`, and `is_stale` drives the UI marking.

4. **The usage schema is richer than a 5h/7d pair.** `spend`, `scoped[]`, and the pace fields are all
   rendered when present and silently omitted when absent, so a claude-swap that stops sending them
   degrades gracefully.

## Concurrency

`CswapClient` is pure synchronous code — it takes an injectable `runner` and does no threading, so
the whole parsing and allowlist surface is unit-testable without an event loop.

`PollingCoordinator` holds the concurrency rules in plain Python:

- **Single flight.** A poll is claimed atomically under a lock. A second caller gets `None` back
  immediately rather than queueing another subprocess. The test drives 50 threads at a barrier and
  asserts exactly one backend call.
- **Backoff.** Consecutive transient failures double the delay (120 → 240 → 480 → capped at 900 s),
  reset by one success. A missing executable does *not* escalate backoff, because retrying faster
  cannot conjure an installation.
- **Single-shot rescheduling.** The Qt timer is restarted after each completion rather than repeating,
  so a slow poll cannot stack ticks behind itself. The restart happens on the GUI thread, reached
  from the worker via a Qt signal — `QTimer.singleShot` cannot be used, because off the GUI thread
  it creates its timer in a thread with no event loop and the callback never fires. That mistake
  left automatic polling dead after the first poll while every unit test passed, since the tests
  covered only `PollingCoordinator`, which is plain Python. `tests/test_polling_service.py` now
  drives the real `PollingService` and asserts repeated polls.
- **Adaptive cadence.** `effective_interval` drops the gap to 30 s while a window is visible and
  restores the configured interval when everything is hidden; opening a window also calls
  `poll_if_stale`, which refreshes unless a reading arrived in the last 10 s.

Switches run on a worker thread; results reach the UI through queued Qt signals. The busy flag is
set and cleared in a `try`/`finally`, so the switch controls re-enable after success, failure,
timeout, and unexpected exceptions alike — each of which has a test.

## Security model

**Trust boundary.** Two kinds of I/O exist: spawning `cswap` with allowlisted argv, and reading and
writing the app's own preference file. No networking library is imported anywhere.

**Allowlist.** `ALLOWED_COMMANDS = {list, status, switch, run, add, alias}`. There is no method that
accepts a command string.

`add` and `alias` exist so accounts can be set up from the Accounts tab. Neither authenticates:
`add` records whichever account Claude Code is *already* signed in as, and signing in stays an
interactive terminal step the user performs. Everything destructive — `remove`, `purge`, `export`,
`import`, `add-token`, `config` — remains refused, with a test asserting it.

Upstream accepts `--json` only on `list`, `status`, and `switch` (cli.py:1301), so `add` and `alias`
report through exit codes and plain text. `_invoke_text` redacts their combined output before it is
returned, and callers re-read authoritative state from `list --json` rather than parsing it. Aliases must match `^[a-z0-9][a-z0-9_-]{0,31}$` *and* correspond to a configured
profile; every argv element is additionally checked against a conservative character class. Tests
assert that `add`, `remove`, `purge`, `export`, `import`, `config`, and `auto` are all refused and
that nothing is spawned when validation fails.

**Redaction.** `services/redaction.py` is the single chokepoint. `contains_secret` is defined as
"redaction would change this string", so it cannot drift out of sync with the patterns it guards.
The aggressive mode used for exception text also blanks opaque 40+ character blobs, with a 40-char
floor chosen so organization UUIDs (36 chars) and ISO timestamps survive intact.

**Storage.** `%LOCALAPPDATA%\ClaudeProfiles\` is created with
`icacls /inheritance:r /grant:r <user>:(OI)(CI)F`. It holds `settings.json` (UI preferences and
aliases) and `activity.jsonl` (capped at 500 entries, emails masked, secrets redacted).

## Presentation

`widgets/theme.py` holds Fluent Design tokens named after the WinUI resources they mirror
(`TextFillColorSecondary`, `CardStrokeColorDefault`, and so on), and `resources/fluent_icons.py`
draws every icon from **Segoe Fluent Icons** - the font Windows 11 uses for its own UI, falling back
to Segoe MDL2 Assets on Windows 10. Type comes from Segoe UI Variable. The accent colour is read
from the Qt palette, so the app follows whatever the user has set in Windows.

One trap is worth recording. Qt stylesheets accept CSS `rgba()` strings, but `QColor` does **not** -
it silently returns opaque black rather than failing. Tokens held as `rgba()` therefore looked
correct on stylesheet-driven widgets and rendered black everywhere QPainter was used, which is how
the progress tracks and status pills ended up as black boxes. Every token is now pre-composited to
solid hex, which behaves identically in both, and `tests/test_theme.py` asserts that each one parses
as a valid `QColor` and that none is an `rgba()` string.

### Layout and icons

The main window is a WinUI-style `NavigationPane` beside a stack of `Page`s. Pages
are built from `SettingsCard`s - icon, title, description, control on the right -
grouped under section headers, with a `ToggleSwitch` and a `- value +` `Stepper`
drawn to the WinUI templates because Qt provides neither.

The tray icon follows the notification-area convention of Windows' own icons: a
monochrome Segoe Fluent glyph drawn at the exact pixel size requested, white on a
dark taskbar and near-black on a light one (read from `SystemUsesLightTheme`,
which is separate from the apps theme), with the active profile as a badge in a
knocked-out ring. It is rendered into `QImage` rather than `QPixmap`, which keeps
glyph antialiasing greyscale; the previous icon had ClearType colour fringes baked
into a 16px bitmap. The app icon is a separate coloured tile, written as a real
multi-resolution `.ico` because Qt's own writer stores a single image.

## Deliberate non-goals

- No direct Anthropic API calls in version one. All quota data comes from claude-swap.
- No process management. Claude Code, VS Code, terminals, and chats are never terminated.
- No claim about browser sessions. Switching does not affect claude.ai in a browser, and the UI says so.
- No automated authentication. Re-login is always a manual, user-driven step.
