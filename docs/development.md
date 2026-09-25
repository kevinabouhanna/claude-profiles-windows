# Development

## Setup

```powershell
uv venv
uv pip install -r requirements-dev.txt
```

Python 3.12+ is required. PySide6 6.11 ships a `cp310-abi3` wheel, so it runs on 3.12, 3.13, and
3.14 without a build step.

## Running

```powershell
.venv\Scripts\python.exe -m claude_profiles            # real backend
.venv\Scripts\python.exe -m claude_profiles --mock     # synthetic data
.venv\Scripts\python.exe -m claude_profiles --scenario work_unavailable
.venv\Scripts\python.exe -m claude_profiles --data-dir .\local\testdata
```

`--data-dir` keeps experiments out of `%LOCALAPPDATA%`.

## Mock scenarios

The mock backend builds the same JSON documents the real CLI emits and feeds them through the same
`models.parse` code paths, so demo mode exercises production parsing rather than a parallel
implementation.

| Scenario | What it demonstrates |
|---|---|
| `healthy` | Both accounts fine; usage drifts slowly so the UI visibly updates. |
| `work_reauth` | `relogin_required` — red badge, no data, re-login instructions. |
| `work_stale` | `usage: null` with `lastGoodUsage` — retained values, marked stale. |
| `work_unavailable` | `usageError` + `usageRetryAt` — amber badge with a retry time. |
| `high_usage` | Threshold colours plus a `spend` block. |
| `no_accounts` | Nothing registered; setup guidance instead of numbers. |
| `unknown_schema` | `schemaVersion: 99` — warning banner, data still parsed. |

Failure injection for tests:

```python
backend.fail_next_list = CswapError(CswapErrorKind.TIMEOUT, "timed out")
backend.fail_next_switch = CswapError(CswapErrorKind.REPORTED, "no such account")
backend.switch_delay = 0.5      # to exercise the busy state
backend.calls                   # recorded invocations
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy src
```

| File | Covers |
|---|---|
| `test_cswap_client.py` | Every payload shape; error-envelope-on-stdout-with-exit-1; timeouts; missing executable; the command allowlist and alias validation. |
| `test_redaction.py` | Each secret pattern, idempotence, and no-false-positive checks on UUIDs and timestamps. |
| `test_profile_service.py` | Alias mapping, switch success/failure/timeout, busy lifecycle, activity-log safety. |
| `test_polling_service.py` | 50-thread overlap test, backoff ladder, last-known-value retention. |
| `test_process_launcher.py` | PowerShell quoting, Windows Terminal argv, console-flag handling. |
| `test_theme.py` | Every Fluent token parses as a QColor, tone ordering, stylesheet integrity. |

### Testing conventions

- **No Qt event loop where it can be avoided.** Concurrency rules live in `PollingCoordinator` and
  parsing lives in `models`/`CswapClient`, both plain Python. Only `test_profile_service.py` needs
  the `qapp` fixture, because `ProfileService` is a `QObject`.
- **Inject, don't patch.** `CswapClient(runner=...)` takes a callable in place of `subprocess.run`;
  `PollingCoordinator(clock=...)` takes a clock. Tests avoid monkeypatching internals.
- **Reserved addresses only.** Fixtures use `@example.invalid` (RFC 2606). No real address appears
  anywhere in the repository.

## Adding a field from claude-swap

1. Add it to the matching dataclass in `models.py` as an **optional** field with a tolerant parse.
2. Add a fixture in `tests/fixtures.py` both with and without it.
3. Render it in `widgets/profile_card.py` behind a presence check.
4. Add it to the mock so demo mode shows it.

Never make a new field required: claude-swap documents many as conditional, and a missing one must
degrade rather than crash.

## Rules to preserve

- `services/cswap_client.py` is the **only** module that may spawn `cswap` with captured output.
- Never add a method that accepts a free-form command string.
- Keep the allowlist minimal. `add` and `alias` were added deliberately for in-app setup;
  anything that removes, exports, or rewrites stored accounts stays out.
- Never log raw stdout or stderr. Route every diagnostic through `redaction.py`.
- Never read or write `.credentials.json`, `.claude-swap-backup\`, or session files.
- Keep every new setting **off** by default if it writes anything outside the app's data folder.
- Colour tokens must be solid hex, never CSS `rgba()`. QSS accepts both; `QColor` accepts only
  hex and silently yields black for the rest. Use `theme.tokens()` rather than literals.
- Icons come from `resources/fluent_icons.py`, never from emoji or text glyphs.

## Packaging

```powershell
.venv\Scripts\pyinstaller.exe claude_profiles.spec
```

Produces `dist\ClaudeProfiles.exe` — a windowed, single-file build. It bundles the UI only; `cswap`
remains a separate installation by design.
