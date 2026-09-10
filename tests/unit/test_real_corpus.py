"""Provas do contrato do corpus real, sem interpretar seus dados."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import real_corpus as corpus_module
from real_corpus import (
    CORPUS_ROOT,
    MIN_CORPUS_LOGS,
    discover_corpus_paths,
    require_real_corpus,
    select_partition,
)


def test_complete_corpus_loads_above_floor(real_corpus: list[Any]) -> None:
    assert len(real_corpus) >= MIN_CORPUS_LOGS


def test_corpus_discovery_is_deterministic() -> None:
    first = discover_corpus_paths()
    if not first:
        pytest.skip(f"corpus real ausente em {CORPUS_ROOT}")

    second = discover_corpus_paths()

    assert first == second
    assert first == sorted(first)


def test_live_cohort_identity_smoke(real_corpus: list[Any]) -> None:
    cohort = select_partition(real_corpus, 3183, 5, 3)

    assert len(cohort) == 37
    nexcurse = next(log for log in cohort if log.build.character_name == "Nexcurse")
    assert nexcurse.fight.report_code == "HkXRKf87WPpjT96w"
    assert nexcurse.fight.fight_id == 12
    assert nexcurse.build.class_name == "Warlock"
    assert nexcurse.build.spec_name == "Demonology"
    assert all(
        log.fight.encounter_id == 3183 and log.fight.difficulty == 5 and log.fight.partition == 3
        for log in cohort
    )


@pytest.mark.parametrize("root_exists", [False, True])
def test_missing_or_small_corpus_skips_cleanly(tmp_path: Path, root_exists: bool) -> None:
    root = tmp_path / "raw"
    if root_exists:
        root.mkdir()
        (root / "one.parquet").touch()

    expected = "abaixo do piso" if root_exists else "corpus real ausente"
    with pytest.raises(pytest.skip.Exception, match=expected):
        require_real_corpus(root)


def test_corpus_is_not_mutated(real_corpus: list[Any]) -> None:
    # A fixture autouse de sessão cerca o setup de `real_corpus` e verifica no teardown.
    assert real_corpus


def test_corpus_load_does_not_filter_files(real_corpus: list[Any]) -> None:
    assert len(real_corpus) == len(discover_corpus_paths())


def test_corpus_module_has_no_network_imports() -> None:
    tree = ast.parse(Path(corpus_module.__file__).read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert imported_roots.isdisjoint({"requests", "httpx", "urllib", "socket"})
