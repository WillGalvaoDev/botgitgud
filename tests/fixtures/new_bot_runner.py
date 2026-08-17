"""Loads the root bot.py (the evolving, NOT-frozen T0.3+ pipeline) as an
isolated module — the T0.7 counterpart to legacy_runner.py.

Same CWD-isolation rationale as legacy_runner.py (docs/desvios.md D-4):
bot.py's SpellCatalog is constructed with a relative Path("spells.json"),
so importing/running it from the repo root would read AND WRITE the
git-tracked spells.json on every test run.
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
BOT_PATH = REPO_ROOT / "bot.py"
REPO_SPELLS_JSON = REPO_ROOT / "spells.json"
MODULE_NAME = "new_bot_under_test"


@contextmanager
def isolated_new_bot(isolated_cwd: Path) -> Iterator[ModuleType]:
    isolated_cwd.mkdir(parents=True, exist_ok=True)
    isolated_spells = isolated_cwd / "spells.json"
    if not isolated_spells.exists() and REPO_SPELLS_JSON.exists():
        shutil.copy(REPO_SPELLS_JSON, isolated_spells)

    previous_cwd = Path.cwd()
    os.chdir(isolated_cwd)
    try:
        spec = importlib.util.spec_from_file_location(MODULE_NAME, BOT_PATH)
        if spec is None or spec.loader is None:
            msg = f"não foi possível carregar {BOT_PATH}"
            raise RuntimeError(msg)
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        os.chdir(previous_cwd)
        sys.modules.pop(MODULE_NAME, None)
