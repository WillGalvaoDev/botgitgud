"""EB.4 — checkpoint for an in-progress Encounter Benchmark build.

`analysis/cohort_increment.py`'s "cache-as-checkpoint" design doesn't
transfer here: a benchmark build needs to remember something the `logs`
table can't represent — a candidate that was TRIED and permanently failed
(never worth refetching), and the transient rank_percent/band a candidate
lands in, neither of which fit a full `PlayerLog` row. So this is a
DEDICATED table, `benchmark_build_progress`, scoped per `benchmark_id` (the
same identity EB.1 already made canonical) — never a second copy of the
`logs`/Parquet warehouse, and never written there: a candidate resolved
here is a THIN, in-memory-only value object (just what EB.2's aggregator
reads off a `PlayerLog`), never a `Store.write_log()` call. See
`benchmark_builder.py`'s module docstring for why.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from botgitgud.domain.models import GearPiece, RankingCandidate, SetupProfile, TalentNode
from botgitgud.ingest.store import Store

CREATE_BENCHMARK_BUILD_PROGRESS_TABLE = """
CREATE TABLE IF NOT EXISTS benchmark_build_progress (
    benchmark_id VARCHAR, report_code VARCHAR, fight_id INTEGER, player_name VARCHAR,
    status VARCHAR, band VARCHAR,
    rank_percent DOUBLE, duration_s DOUBLE, item_level DOUBLE,
    class_name VARCHAR, spec_name VARCHAR, server VARCHAR, partition INTEGER,
    setup_json VARCHAR,
    attempts INTEGER, last_error VARCHAR, updated_at TIMESTAMP,
    PRIMARY KEY (benchmark_id, report_code, fight_id, player_name)
)
"""
# Nenhuma migração ainda — mesmo hook point vazio de EB.3's
# MIGRATE_ENCOUNTER_BENCHMARKS_TABLE, para nunca precisar de um segundo
# mecanismo depois.
MIGRATE_BENCHMARK_BUILD_PROGRESS_TABLE: tuple[str, ...] = ()

# Uma falha transitória (WCL fora do ar, rate limit momentâneo) não pode
# descartar um candidato para sempre — mas também não pode retentar sem
# limite, ou um candidato genuinamente inacessível gira para sempre.
MAX_CANDIDATE_ATTEMPTS = 3

CandidateStatus = Literal["pending", "fetched", "failed"]


def now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _encode_setup(setup: SetupProfile | None) -> str | None:
    """Mesmo formato compacto de `ingest/parquet_codec.py`'s `_encode_setup`
    (arrays, não objetos) — reimplementado aqui, não importado: este é um
    contexto de armazenamento diferente (linha de checkpoint, não coluna
    Parquet), com um ciclo de vida diferente (linhas de progresso são
    substituídas/apagadas ao finalizar; Parquet é permanente), então acoplar
    os dois módulos não compraria nada além de risco para o contrato dos 977
    Parquet antigos.
    """
    if setup is None:
        return None
    return json.dumps(
        {
            "talents": [[t.node_id, t.rank, t.spell_id] for t in setup.talents],
            "gear": [[g.slot, g.item_id, g.item_level, g.set_id] for g in setup.gear],
            "stats": dict(setup.stats),
        },
        separators=(",", ":"),
    )


def _decode_setup(raw: str | None) -> SetupProfile | None:
    if not raw:
        return None
    payload = json.loads(raw)
    return SetupProfile(
        talents=tuple(
            TalentNode(node_id=t[0], rank=t[1], spell_id=t[2]) for t in payload.get("talents", [])
        ),
        gear=tuple(
            GearPiece(slot=g[0], item_id=g[1], item_level=g[2], set_id=g[3])
            for g in payload.get("gear", [])
        ),
        stats=dict(payload.get("stats", {})),
    )


@dataclass(frozen=True, slots=True)
class ProgressRow:
    benchmark_id: str
    report_code: str
    fight_id: int
    player_name: str
    status: CandidateStatus
    band: str | None
    rank_percent: float | None
    duration_s: float | None
    item_level: float | None
    class_name: str | None
    spec_name: str | None
    server: str | None
    partition: int | None
    setup: SetupProfile | None
    attempts: int
    last_error: str | None

    @property
    def candidate_key(self) -> tuple[str, int, str]:
        return (self.report_code, self.fight_id, self.player_name)

    @property
    def fight_key(self) -> tuple[str, int]:
        return (self.report_code, self.fight_id)


_COLUMNS = (
    "benchmark_id",
    "report_code",
    "fight_id",
    "player_name",
    "status",
    "band",
    "rank_percent",
    "duration_s",
    "item_level",
    "class_name",
    "spec_name",
    "server",
    "partition",
    "setup_json",
    "attempts",
    "last_error",
)


def _row_from_columns(values: tuple[Any, ...]) -> ProgressRow:
    payload = dict(zip(_COLUMNS, values, strict=True))
    return ProgressRow(
        benchmark_id=payload["benchmark_id"],
        report_code=payload["report_code"],
        fight_id=payload["fight_id"],
        player_name=payload["player_name"],
        status=payload["status"],
        band=payload["band"],
        rank_percent=payload["rank_percent"],
        duration_s=payload["duration_s"],
        item_level=payload["item_level"],
        class_name=payload["class_name"],
        spec_name=payload["spec_name"],
        server=payload["server"],
        partition=payload["partition"],
        setup=_decode_setup(payload["setup_json"]),
        attempts=payload["attempts"],
        last_error=payload["last_error"],
    )


class BenchmarkBuildProgressStore:
    """Same extension pattern `BenchmarkStore`/`JobQueue` already use: a
    class wrapping `Store`, writing to its own table via `Store`'s generic
    SQL methods — every write still serializes through `Store`'s one lock.
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._store.execute(CREATE_BENCHMARK_BUILD_PROGRESS_TABLE)
        for statement in MIGRATE_BENCHMARK_BUILD_PROGRESS_TABLE:
            self._store.execute(statement)

    def register_candidates(
        self, benchmark_id: str, candidates: Sequence[RankingCandidate]
    ) -> None:
        """Insere `pending` para candidatos NOVOS — idempotente por PK: um
        candidato já registrado (de uma janela anterior, `pending`,
        `fetched` ou `failed`) nunca é tocado. É isto que torna o resume
        possível: registrar a MESMA lista de candidatos de novo não perde
        nem reinicia progresso algum.
        """
        now = now_utc_naive()
        for c in candidates:
            self._store.execute(
                """
                INSERT INTO benchmark_build_progress (
                    benchmark_id, report_code, fight_id, player_name, status, band,
                    rank_percent, duration_s, item_level, class_name, spec_name, server,
                    partition, setup_json, attempts, last_error, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?, NULL, NULL, NULL, NULL, NULL,
                          NULL, 0, NULL, ?)
                ON CONFLICT (benchmark_id, report_code, fight_id, player_name) DO NOTHING
                """,
                [benchmark_id, c.report_code, c.fight_id, c.player_name, c.duration_s, now],
            )

    def read_progress(self, benchmark_id: str) -> list[ProgressRow]:
        rows = self._store.execute_returning(
            f"SELECT {', '.join(_COLUMNS)} FROM benchmark_build_progress "
            "WHERE benchmark_id = ? ORDER BY report_code, fight_id, player_name",
            [benchmark_id],
        )
        return [_row_from_columns(r) for r in rows]

    def mark_fetched(
        self,
        benchmark_id: str,
        report_code: str,
        fight_id: int,
        player_name: str,
        *,
        band: str,
        rank_percent: float | None,
        duration_s: float | None,
        item_level: float | None,
        class_name: str | None,
        spec_name: str | None,
        server: str | None,
        partition: int | None,
        setup: SetupProfile | None,
    ) -> None:
        self._store.execute(
            """
            UPDATE benchmark_build_progress SET
                status = 'fetched', band = ?, rank_percent = ?, duration_s = ?, item_level = ?,
                class_name = ?, spec_name = ?, server = ?, partition = ?, setup_json = ?,
                last_error = NULL, updated_at = ?
            WHERE benchmark_id = ? AND report_code = ? AND fight_id = ? AND player_name = ?
            """,
            [
                band,
                rank_percent,
                duration_s,
                item_level,
                class_name,
                spec_name,
                server,
                partition,
                _encode_setup(setup),
                now_utc_naive(),
                benchmark_id,
                report_code,
                fight_id,
                player_name,
            ],
        )

    def mark_failed(
        self, benchmark_id: str, report_code: str, fight_id: int, player_name: str, *, error: str
    ) -> None:
        """Incrementa `attempts`; vira `failed` PERMANENTE só ao atingir
        `MAX_CANDIDATE_ATTEMPTS` — antes disso continua `pending`
        (retentável na próxima janela). Nunca decide isso em Python lendo-
        modificando-escrevendo: o `CASE` inteiro é uma única instrução
        atômica.
        """
        self._store.execute(
            f"""
            UPDATE benchmark_build_progress SET
                attempts = attempts + 1,
                last_error = ?,
                status = CASE WHEN attempts + 1 >= {MAX_CANDIDATE_ATTEMPTS} THEN 'failed'
                              ELSE 'pending' END,
                updated_at = ?
            WHERE benchmark_id = ? AND report_code = ? AND fight_id = ? AND player_name = ?
            """,
            [error, now_utc_naive(), benchmark_id, report_code, fight_id, player_name],
        )

    def clear(self, benchmark_id: str) -> None:
        """Usado só por quem quer descartar e recomeçar um build do zero —
        nunca chamado pelo motor de avanço normal (resume nunca apaga).
        """
        self._store.execute(
            "DELETE FROM benchmark_build_progress WHERE benchmark_id = ?", [benchmark_id]
        )
