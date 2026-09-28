"""Entry point for the claude-swap build bundled with Claude Profiles.

It runs claude-swap's own CLI unchanged, except for self-updating. A bundled
copy is updated by installing a newer Claude Profiles, which ships the
claude-swap version it was tested against, so:

* the passive "a newer version is on PyPI" notice is switched off, since its
  advice (``pip install --upgrade``) does not apply to a frozen build, and it
  is one fewer network request;
* ``cswap --upgrade`` explains where updates come from instead of trying to
  run pip against an interpreter that does not exist.

Nothing about accounts, credentials or switching is altered.
"""

from __future__ import annotations

import sys

from claude_swap import update_check

UPDATE_NOTE = (
    "This copy of claude-swap is bundled with Claude Profiles and is updated "
    "with it. Install the latest Claude Profiles from "
    "https://github.com/kevinabouhanna/claude-profiles-windows/releases/latest"
)


def _no_update_notice(current_version: str) -> None:
    return None


def _explain_upgrade(*_args, **_kwargs) -> None:
    print(UPDATE_NOTE, file=sys.stderr)
    raise SystemExit(1)


update_check.check_for_update = _no_update_notice
update_check.run_self_upgrade = _explain_upgrade

if __name__ == "__main__":
    from claude_swap.cli import main

    main()
