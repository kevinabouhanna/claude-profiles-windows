# Releasing

Releases are cut by pushing a version tag. GitHub Actions does the rest, so nothing that ships is
built on a personal machine.

## Versioning

Claude Profiles follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

| Bump | When |
|---|---|
| `patch` | Bug fixes only. Nothing a user has to relearn. |
| `minor` | New features or behaviour, backwards compatible. Settings files keep working. |
| `major` | Something a user must act on: a setting removed, a new minimum claude-swap version, a changed install location. |

The version lives in **one place**: `__version__` in `src/claude_profiles/__init__.py`. Everything
else reads it from there:

- `pyproject.toml` (via `[tool.hatch.version]`)
- the exe's version resource, shown in Explorer under *Properties > Details*
- the installer's version and its Apps & features entry
- the About text in the app's Settings page

Tests fail if `pyproject.toml` hard-codes a version, or if the current version has no changelog
entry.

## Changelog

Every user-visible change gets a line under `## [Unreleased]` in [`CHANGELOG.md`](../CHANGELOG.md),
in the same pull request as the change, grouped as *Added*, *Changed*, *Fixed*, *Removed* or
*Security* ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)). That section becomes the
GitHub Release notes, so write it for users, not for reviewers.

## Cutting a release

```powershell
# 1. Move [Unreleased] under a new version and bump __version__.
.venv\Scripts\python.exe tools\release.py bump minor      # or patch, major, or 1.4.0

# 2. Read the diff. Edit CHANGELOG.md if the notes need polish.
git diff

# 3. Commit, tag, push.
git commit -am "Release v0.2.0"
git tag -a v0.2.0 -m "Claude Profiles 0.2.0"
git push origin main --follow-tags
```

Pushing the tag starts [`.github/workflows/release.yml`](../.github/workflows/release.yml), which:

1. Refuses to continue unless the tag matches `__version__` and the changelog has a section for it.
2. Runs lint and the full test suite.
3. Builds with `tools/build_release.ps1`: icon, PyInstaller, `verify_build.py`, portable zip,
   Inno Setup installer, and `SHA256SUMS.txt`.
4. Attaches a [build provenance attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations)
   to the installer and zip, so anyone can prove they came from this repository's workflow.
5. Publishes a GitHub Release titled *Claude Profiles X.Y.Z* with the changelog section as its notes.

If something fails after the tag is pushed, fix it on `main` and cut the next patch version. Do
not move or re-push a published tag.

## Dry runs

**In CI:** *Actions > Release > Run workflow* on any branch runs the whole build and uploads the
results as a workflow artifact, without publishing anything.

**Locally**, with [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed
(`winget install JRSoftware.InnoSetup`):

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_release.ps1
```

The output lands in `release\`, which is git-ignored. Local builds are for testing only: publish
through the workflow, so the binaries are reproducible and attested.

## The installer

[`installer/ClaudeProfiles.iss`](../installer/ClaudeProfiles.iss) produces a per-user installer:

- **No administrator prompt.** It installs to `%LOCALAPPDATA%\Programs\Claude Profiles`, the folder
  the app already treats as its installed home.
- **Upgrades in place.** The fixed `AppId` lets a new version find and replace the old one. It closes
  a running instance first and clears the old `_internal` folder so no stale libraries remain.
- **One Start menu entry.** It owns the same `Claude Profiles.lnk` the app maintains, so the entry
  is never duplicated.
- **Clean uninstall.** It stops the app and removes the program folder, both shortcuts, and the icon
  cache. Settings in `%LOCALAPPDATA%\ClaudeProfiles` are kept, as uninstallers conventionally do.

Never change the `AppId`: a new one makes Windows treat the next version as a different program.

## Code signing

The binaries are not code-signed, so SmartScreen warns on first run until the download builds
reputation. The release notes explain the *More info > Run anyway* path and how to verify the
attestation. Signing can be added later as a step before the installer is compiled (for example
Azure Trusted Signing or SignPath's free tier for open source) without changing anything else here.
