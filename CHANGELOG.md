# Changelog

All notable changes to Claude Profiles are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). How releases are cut is described in
[`docs/releasing.md`](docs/releasing.md).

## [Unreleased]

### Fixed

- Opening the dashboard or Settings for the first time no longer flashes a burst of blank windows
  on screen. Each settings description and account badge was briefly shown as a window of its own
  while the page was being built.
- Clicking the tray icon while the flyout is open now closes it. Before, the click closed the
  flyout and then immediately opened it again.

### Removed

- The Refresh buttons in the tray flyout, the tray menu and the Overview page. Usage already
  refreshes whenever you open the flyout or the dashboard, and on a timer in the background.

## [0.3.1] - 2026-09-29

### Fixed

- Demo mode (`--mock`) no longer shows an "Opus" per-model row under every account. Real accounts
  only have those rows when claude-swap reports a model-specific limit, and the website's
  screenshots, which are taken in demo mode, showed rows the app would not show you.
- Demo mode's 7-day pace marker is placed by time elapsed, as claude-swap does, instead of always
  just below usage.

## [0.3.0] - 2026-09-29

### Added

- A pace marker on the 5-hour bar as well as the 7-day one: a white line showing where your usage
  would be if you spent the quota evenly. Fill past the line means you are spending faster than
  it refills; short of it, you have room. It is worked out from the reset time when claude-swap
  does not report it, and the tooltip says whether you are ahead of pace or within it.
- `--scenario two_accounts` demo, with just Personal and Work.

### Changed

- The pace marker now shows in the tray flyout too, not only on the dashboard, and is drawn as a
  clear white line that stands above the bar.

## [0.2.0] - 2026-09-29

### Added

- claude-swap is now bundled in the installer, so Claude Profiles is the only thing to install
  besides Claude Code. It is pinned to the version the app is tested against and updated with it.
- Optional installer checkbox to put the bundled `cswap` on your PATH for terminal use. Off by
  default, and removed again on uninstall.
- `THIRD-PARTY-NOTICES.txt` in every release, with the licences of everything the installer ships:
  claude-swap and its dependencies, Qt through PySide6, and the Python runtime.

### Changed

- If claude-swap cannot be found, an installed copy now says to reinstall Claude Profiles rather
  than to install claude-swap separately.

## [0.1.0] - 2026-09-28

The first public release.

### Added

- Tray app showing every Claude Code account registered with claude-swap, however many there
  are: 5-hour and 7-day usage with reset countdowns, per-model rows, a pace indicator, data age,
  and login health. Each account gets its own card, colour and menu entry.
- One-click switching of the account Claude Code uses for new sessions, via
  [claude-swap](https://github.com/realiti4/claude-swap), with a Windows notification confirming
  the result.
- Isolated sessions: open a Claude Code terminal bound to one account without changing the global
  active account.
- Compact tray flyout, plus a full dashboard with Overview, Accounts, Activity, Privacy and Settings
  pages in a WinUI-style layout that follows the Windows accent colour.
- *Add an account* wizard: sign in, name the account anything (`personal`, `work`,
  `client-acme`), and save. It shows the exact address before registering it. Accounts can be
  renamed from the Accounts page.
- Opt-in threshold notifications, and global shortcuts `Ctrl+Alt+1` to `Ctrl+Alt+9` for the first
  nine accounts.
- Adaptive refresh: refresh on open, 30-second polling while a window is visible, and backoff after
  errors.
- Start menu and Startup shortcuts, with no registry writes.
- Demo mode (`--mock`, `--scenario`) running the whole interface on synthetic data.
- Local-only privacy model: no network code, allowlisted `cswap` commands, redaction of anything
  written to disk, and an ACL-locked data folder.
- Windows installer and portable zip, built and published by GitHub Actions with SHA-256 checksums
  and build provenance attestations.

[Unreleased]: https://github.com/kevinabouhanna/claude-profiles-windows/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/kevinabouhanna/claude-profiles-windows/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/kevinabouhanna/claude-profiles-windows/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kevinabouhanna/claude-profiles-windows/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kevinabouhanna/claude-profiles-windows/releases/tag/v0.1.0
