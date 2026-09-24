"""Shared pytest fixtures: guards proving the suite never mutates the real
local corpus (`data/raw`) or operational logs (`data/logs`).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from dir_snapshot import diff_snapshots, snapshot_directory
from real_corpus import CORPUS_ROOT, require_real_corpus


@pytest.fixture(autouse=True, scope="session")
def _protect_real_data_raw() -> Iterator[None]:
    """M0: o snapshot de sessão cerca inclusive o setup que carrega o corpus.

    A dependência explícita de `real_corpus` garante que esta medição aconteça
    antes da primeira leitura. O teardown ocorre depois de todos os testes, de
    modo que a carga e qualquer consumidor posterior ficam dentro da prova.
    """
    before = snapshot_directory(CORPUS_ROOT)
    yield
    violations = diff_snapshots(before, snapshot_directory(CORPUS_ROOT))
    assert not violations, f"a suíte mutou {CORPUS_ROOT} (corpus real): {violations}"


@pytest.fixture(scope="session")
def real_corpus(_protect_real_data_raw: None) -> list[Any]:
    """O corpus local é opcional no CI, mas obrigatório para provas de fidelidade."""
    return require_real_corpus()


@pytest.fixture(autouse=True, scope="session")
def _protect_real_data_logs() -> Any:
    """QA.0: `data/logs` real pode conter evidência operacional legítima de
    uma execução real do bot (soaks, supervisor) — não é mais seguro supor
    que está ausente antes da suíte, e apagá-lo só para satisfazer um guard
    destruiria essa evidência.

    O que a suíte de fato garante, provado aqui: nenhum teste cria, apaga
    ou modifica o que já estava em `data/logs` quando a sessão começou —
    comparado por conteúdo (tamanho+sha256 via dir_snapshot.py), nunca por
    mtime. Escopo de SESSÃO (não de um teste isolado) e teardown (não
    setup): o snapshot final só é tirado depois que TODO teste já rodou,
    então nenhum teste posterior escapa da checagem por ordem de coleção.
    """
    from botgitgud.logging_setup import log_dir_for

    real_logs = log_dir_for(Path(__file__).resolve().parent.parent / "data")
    before = snapshot_directory(real_logs)
    yield
    violations = diff_snapshots(before, snapshot_directory(real_logs))
    assert not violations, (
        f"a suíte mutou {real_logs} (dado operacional real, não um fixture de teste): {violations}"
    )
