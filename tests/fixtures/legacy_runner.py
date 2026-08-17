"""Loads legacy/bot.py as an isolated module.

legacy/bot.py is frozen (never edited — it's the behavior reference for the
golden test) and uses a relative path (`SPELLS_FILE = "spells.json"`) for its
spell-name cache. Importing and running it from the repo root would read AND
WRITE the git-tracked spells.json on every test run (see docs/desvios.md D-4).

`isolated_legacy_bot` redirects the process CWD to a private scratch
directory for the whole lifetime of the `with` block — covering both module
import (which reads spells.json at load time) and every later call the
caller makes (module functions re-open the file by relative path on every
write), since CWD is process-global and threads inherit it.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_BOT_PATH = REPO_ROOT / "legacy" / "bot.py"
REPO_SPELLS_JSON = REPO_ROOT / "spells.json"
MODULE_NAME = "legacy_bot_under_test"


@contextmanager
def isolated_legacy_bot(isolated_cwd: Path) -> Iterator[ModuleType]:
    """Import legacy/bot.py fresh with CWD pinned to `isolated_cwd`.

    Seeds `isolated_cwd/spells.json` from the repo's copy (if not already
    present) so the module's cache behaves like production without ever
    writing back to the tracked file.
    """
    isolated_cwd.mkdir(parents=True, exist_ok=True)
    isolated_spells = isolated_cwd / "spells.json"
    if not isolated_spells.exists() and REPO_SPELLS_JSON.exists():
        shutil.copy(REPO_SPELLS_JSON, isolated_spells)

    previous_cwd = Path.cwd()
    os.chdir(isolated_cwd)
    try:
        spec = importlib.util.spec_from_file_location(MODULE_NAME, LEGACY_BOT_PATH)
        if spec is None or spec.loader is None:
            msg = f"não foi possível carregar {LEGACY_BOT_PATH}"
            raise RuntimeError(msg)
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        os.chdir(previous_cwd)
        sys.modules.pop(MODULE_NAME, None)
