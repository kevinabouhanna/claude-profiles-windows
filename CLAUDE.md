# Working on Claude Profiles

## Every request ends installed and pushed

The maintainer uses the app on this machine and does not want to ask for either step. This is
standing permission: finish every request with the change running on this machine and pushed to
GitHub, without asking first.

1. **Check it.** Tests and lint must pass. Never install or push a failing tree.

   ```powershell
   .venv\Scripts\python.exe -m pytest -q
   .venv\Scripts\python.exe -m ruff check src tests
   ```

2. **Update the installed app** whenever the change touches anything that ships: `src/`,
   `installer/`, the `.spec` files, or `tools/cswap/`. Docs-only and site-only changes skip this.

   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\build_release.ps1
   $version = (.venv\Scripts\python.exe tools\release.py version).Trim()
   Start-Process "release\ClaudeProfiles-Setup-$version.exe" -Wait `
       -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"
   Start-Process "$env:LOCALAPPDATA\Programs\Claude Profiles\ClaudeProfiles.exe"
   ```

   The installer closes the running app and, when silent, does not start it again, so start it.
   Confirm the installed `ClaudeProfiles.exe` has the same hash as
   `dist\Claude Profiles\ClaudeProfiles.exe`. A local install keeps the current version number;
   bumping it is part of a release.

3. **Commit and push to `main`.** Commit as the noreply identity already in this repo's git config.
   Git signs in through the GitHub CLI, and the machine's default `gh` account is not the one that
   owns this repo, so switch for the push and switch back afterwards:

   ```powershell
   gh auth switch -u kevinabouhanna
   git push origin main
   gh auth switch   # back to the account that was active before
   ```

If any step fails, stop and report it rather than working around it.

Pushing to `main` does not publish a new download. Cut a release (see `docs/releasing.md`) only
when asked for one.
