# Changelog

All notable changes to Claude Profiles are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). How releases are cut is described in
[`docs/releasing.md`](docs/releasing.md).

## [Unreleased]

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

[Unreleased]: https://github.com/kevinabouhanna/claude-profiles-windows/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kevinabouhanna/claude-profiles-windows/releases/tag/v0.1.0
