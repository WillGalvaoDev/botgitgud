"""Acesso somente-leitura ao corpus real usado nas provas offline."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from botgitgud.domain.models import PlayerLog


MIN_CORPUS_LOGS = 900
CORPUS_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw"


def discover_corpus_paths(root: Path | None = None) -> list[Path]:
    """A ordenação explícita torna a amostragem futura reproduzível entre execuções."""
    corpus_root = CORPUS_ROOT if root is None else root
    return sorted(corpus_root.rglob("*.parquet")) if corpus_root.is_dir() else []


def _load_paths(paths: list[Path]) -> list[PlayerLog]:
    # O import tardio mantém a coleta funcional sem o extra opcional `data`.
    from botgitgud.ingest.parquet_codec import read_parquet_log

    return [read_parquet_log(path) for path in paths]


@cache
def load_real_corpus() -> list[PlayerLog]:
    """Uma única carga por sessão evita reler todo o warehouse para cada consumidor."""
    return _load_paths(discover_corpus_paths())


def require_real_corpus(root: Path | None = None) -> list[PlayerLog]:
    """O CI sem dados deve declarar ausência de prova, nunca produzir um passe vazio."""
    paths = discover_corpus_paths(root)
    if not paths:
        pytest.skip(f"corpus real ausente em {CORPUS_ROOT if root is None else root}")
    if len(paths) < MIN_CORPUS_LOGS:
        pytest.skip(
            f"corpus real abaixo do piso: encontrados {len(paths)} logs; exigidos {MIN_CORPUS_LOGS}"
        )
    return load_real_corpus() if root is None else _load_paths(paths)


def select_partition(
    logs: list[PlayerLog],
    encounter_id: int,
    difficulty: int,
    partition: int,
) -> list[PlayerLog]:
    """Filtrar os objetos carregados evita acoplar o contrato ao layout do warehouse."""
    return [
        log
        for log in logs
        if log.fight.encounter_id == encounter_id
        and log.fight.difficulty == difficulty
        and log.fight.partition == partition
    ]
