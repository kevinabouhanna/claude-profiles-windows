"""Entry point for ``python -m claude_profiles`` and for the frozen build.

The import is absolute on purpose. A relative ``from .app import main`` works
under ``-m``, but PyInstaller runs this file *as* ``__main__`` with no package
context, where a relative import raises
``ImportError: attempted relative import with no known parent package`` before
anything else happens.
"""

from claude_profiles.app import main

raise SystemExit(main())
