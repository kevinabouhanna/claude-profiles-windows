# Claude Profiles

A private, local-only Windows tray app for running two Claude Code accounts side by side.

It shows both accounts' quota at once, switches between them in one click, and can open an isolated
Claude Code session per account — without ever touching your credentials itself.

All credential handling and quota data comes from [claude-swap](https://github.com/realiti4/claude-swap),
which this app drives as a subprocess. Claude Profiles contains **no** OAuth logic, no token parsing,
no credential backup, and no writes to `.credentials.json`.

---

## What it does

- **One tray icon.** Left-click opens a compact dashboard showing both profiles; right-click opens a
  native context menu.
- **Both accounts at a glance** — 5-hour and 7-day usage with reset countdowns, optional per-model
  rows, data age, and login health.
- **One-click switching**, with the switch controls disabled while the action runs and a Windows
  notification confirming the result.
- **Per-profile launches** that open an isolated Claude Code session in a new terminal without
  changing the globally active profile.
- **A full dashboard** with side-by-side cards, an activity log, settings, and a privacy page.

## What it can and cannot switch

| | |
|---|---|
| ✅ Switches | The account Claude Code uses for **new** sessions, via claude-swap. |
| ✅ Isolated sessions | `cswap run` opens a terminal bound to one account, leaving the global profile alone. |
| ❌ Does **not** switch | Your browser session on claude.ai. Signing in there is separate and unaffected. |
| ❌ Does **not** touch | Running Claude Code processes, VS Code, terminals, or open chats — nothing is ever killed for you. |
| ⚠️ Needs a nudge | An already-open VS Code Claude panel may still show the previous account. Close and reopen that panel. |

---

## Prerequisites

- Windows 10 or 11
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) or pipx, to install claude-swap
- Claude Code, signed in to at least one account

## 1. Install claude-swap

```powershell
uv tool install claude-swap
# or: pipx install claude-swap
cswap --version
```

If `cswap` is not found afterwards, make sure `%USERPROFILE%\.local\bin` is on your `PATH`.

## 2. Register your accounts

**You can do this inside the app.** Start it (step 3), then open the **Accounts** tab — from the
tray menu, or by clicking *Set up Personal…* on an unregistered profile card.

The page walks the two steps in order:

1. **Sign in.** Claude Profiles never handles sign-in itself. The button opens a terminal running
   Claude Code; sign in there (type `/login` to switch account), close it, then press **Re-check**.
2. **Register.** The page shows exactly which address Claude Code is currently signed in as, then
   stores it under Personal or Work on a click. It asks you to confirm the address first, because
   claude-swap captures whichever account is signed in *right now*.

Repeat for the second account. If an account is already registered under the wrong alias, the
bottom of the page re-points it at a profile instead of adding it twice.

<details>
<summary>The equivalent terminal commands, if you prefer</summary>

```powershell
claude                      # sign in as the first account, then exit
cswap add --alias personal

claude                      # sign in as the second account, then exit
cswap add --alias work

cswap list                  # both accounts should appear with their aliases
cswap alias 1 personal      # to re-point an existing account
```
</details>

The aliases `personal` and `work` are the only account identifiers Claude Profiles stores. Email
addresses are read from `cswap list --json` at runtime and never written into source or config.

## 3. Run the app

```powershell
git clone <this repo>
cd claude-profiles-windows

uv venv
uv pip install -e .

.venv\Scripts\python.exe -m claude_profiles
# or, once installed:  .venv\Scripts\claude-profiles.exe
```

The tray icon appears in the notification area. Left-click it for the compact view.

### Demo mode

Explore the whole interface with synthetic data — no credentials, no real accounts touched:

```powershell
.venv\Scripts\python.exe -m claude_profiles --mock
.venv\Scripts\python.exe -m claude_profiles --scenario work_reauth
```

Scenarios: `healthy`, `work_reauth`, `work_stale`, `work_unavailable`, `high_usage`, `no_accounts`,
`unknown_schema`.

---

## Settings

| Setting | Default | Notes |
|---|---|---|
| Refresh interval | 120 s | Polls never overlap and back off automatically after errors. |
| Launch at Windows sign-in | **On** | Creates a shortcut in your Startup folder. **No registry keys are ever written.** Untick it and the shortcut is deleted. |
| Threshold notifications | Off | Fires on an upward crossing only, and re-arms after a reset. |
| Global shortcuts | Off | `Ctrl+Alt+1` Personal, `Ctrl+Alt+2` Work. Registered only when enabled. |

Switch confirmations always appear, regardless of the notification setting, because they are the
direct result of something you clicked.

### Behaving like an installed app

On first run the app adds itself to the **Start menu** and to **Startup**, both as ordinary
shortcuts you can see and delete in Explorer. They point at `pythonw.exe`, the windowed Python
interpreter, so launching Claude Profiles never opens a console window.

Notification-area apps are hidden by default on Windows 11. To pin the icon so it is always
visible: **Settings > Personalisation > Taskbar > Other system tray icons**, then switch on
*Claude Profiles*.

## When an account needs re-authentication

If claude-swap reports `token_expired`, `relogin_required`, or `no_credentials`, the card shows
**Re-authentication required** and offers instructions. The fix is manual by design:

1. Open the **Accounts** tab and press **Open a terminal to sign in to Claude Code**.
2. Sign in as that account, then close the terminal and press **Re-check**.
3. Press **Register as Personal** / **Register as Work** to refresh the stored slot.

The equivalent by hand is `claude` to sign in, then `cswap add --alias <alias>`.

This app will never attempt to automate authentication in the background.

---

## Privacy and security

- **Local only.** The app opens no network connections. Its sole external interaction is running the
  local `cswap` executable and parsing the JSON it prints.
- **No credential access.** `%USERPROFILE%\.claude\.credentials.json`, `.claude-swap-backup\`, and
  Claude Code session files are never read, written, copied, exported, or backed up.
- **Allowlisted commands.** Only `cswap list`, `status`, `switch`, `run`, `add`, and `alias` can be
  invoked. There is no free-form command path, argv is always a list, and a shell is never used.
  `remove`, `purge`, `export`, `import`, `add-token`, and `config` are all refused.
- **Setup is not authentication.** `add` only records the account Claude Code is *already* signed
  in as. Signing in stays an interactive step you perform in a terminal; the app never sees or
  handles a credential.
- **No raw output is logged.** stdout and stderr never reach a log or the UI — only parsed, redacted
  error fields do.
- **Redaction everywhere.** Anything written to disk passes through a scrubber for API keys, JWTs,
  bearer tokens, and `token=`/`secret=` assignments, and has email addresses masked.
- **No telemetry**, analytics, crash reporting, cloud sync, or remote configuration.
- **Restricted storage.** `%LOCALAPPDATA%\ClaudeProfiles\` is ACL-locked to your user account on
  creation, and holds only UI preferences, aliases, and a capped activity history.

The in-app **Privacy** tab states all of this, with **Open data folder** and **Clear local activity
history** actions.

See [`docs/privacy.md`](docs/privacy.md) for the full data inventory.

---

## Building a standalone EXE

A PyInstaller spec is included. Nothing is built automatically.

```powershell
uv pip install -r requirements-dev.txt
.venv\Scripts\pyinstaller.exe claude_profiles.spec
# result: dist\ClaudeProfiles.exe
```

The EXE bundles the UI only — it still requires `cswap` to be installed separately, because
credential handling deliberately stays outside this application.

## Development

```powershell
uv pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
```

See [`docs/development.md`](docs/development.md) and [`docs/architecture.md`](docs/architecture.md).

---

## Credits and licence

Claude Profiles is MIT licensed (see [`LICENSE`](LICENSE)).

It depends on and gratefully credits:

- **[realiti4/claude-swap](https://github.com/realiti4/claude-swap)** (MIT) — the credential and
  quota backend. Invoked as a subprocess; **no code copied**.
- **[jens-duttke/usage-monitor-for-claude](https://github.com/jens-duttke/usage-monitor-for-claude)**
  (MIT) — studied as a reference for Windows tray behaviour, adaptive polling, and privacy practice.
  **No code copied.**
- **[hamed-elfayome/Claude-Usage-Tracker](https://github.com/hamed-elfayome/Claude-Usage-Tracker)**
  (MIT) — studied as a UX reference for multi-profile presentation. It is a macOS Swift app; **no
  code ported**.

No source from these projects is included here, so no third-party notices are bundled. Their
licences were verified before implementation.
