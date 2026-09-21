"""EB.4 test helper: a scripted, in-memory WCL backend for
`analysis/benchmark_builder.py`'s tests. Produces the exact response shapes
`ingest/fight_rankings.py` (`fetch_report_rankings`) and
`ingest/benchmark_fetch.py` (`fetch_fight_player_details`) expect — both
verified against a real recorded cassette before this helper was written.

Every response is FIGHT-scoped: one `add_fight(...)` registers every
player at that (report_code, fight_id) at once, and `query_fn` always
answers with the WHOLE fight's roster in one call — this is what makes the
fight-wide-sharing tests meaningful (as opposed to trivially true because
the fake only ever knows about one player).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from botgitgud.errors import ApiError


@dataclass(frozen=True, slots=True)
class FakePlayer:
    name: str
    rank_percent: float | None
    item_level: float = 289.0
    server: str = "Azralon"
    class_name: str = "Warlock"
    spec_name: str = "Demonology"
    talents: tuple[tuple[int, int], ...] = ((1, 1),)
    trinket_id: int | None = 5000
    # Campos ADITIVOS (defaults preservam byte-a-byte o que EB.4/EB.5 já
    # observavam): um segundo trinket habilita TRINKET_PAIR, `set_ids`
    # habilita SET_BONUS, `stats` permite variar SECONDARY_STATS por
    # jogador. Nada disto muda a resposta de um `FakePlayer` construído
    # como antes.
    trinket_id_2: int | None = None
    set_ids: tuple[int, ...] = ()
    stats: tuple[tuple[str, float], ...] = ()


@dataclass
class FakeWclBackend:
    fights: dict[tuple[str, int], tuple[FakePlayer, ...]] = field(default_factory=dict)
    fight_partition: dict[tuple[str, int], int] = field(default_factory=dict)
    fight_duration_s: dict[tuple[str, int], float] = field(default_factory=dict)
    # (op_name, report_code, fight_id) -> força ApiError nesta chamada específica
    failing: set[tuple[str, str, int]] = field(default_factory=set)
    query_log: list[tuple[str, str, int]] = field(default_factory=list)
    # Quando setado, cada chamada bem-sucedida DECREMENTA points_remaining
    # deste client — simula o gasto real que faz um orçamento estático de
    # teste efetivamente esgotar, em vez de "affordar" o mesmo lote para
    # sempre porque nada nunca é gasto (um client puramente estático nunca
    # reflete o reeexame de orçamento a cada lote que o motor real faz).
    client: FakeBudgetClient | None = None
    points_per_query: float = 2.0

    def add_fight(
        self,
        report_code: str,
        fight_id: int,
        players: tuple[FakePlayer, ...],
        *,
        partition: int = 3,
        duration_s: float = 300.0,
    ) -> None:
        self.fights[(report_code, fight_id)] = players
        self.fight_partition[(report_code, fight_id)] = partition
        self.fight_duration_s[(report_code, fight_id)] = duration_s

    def query_fn(self, query: str, variables: dict[str, Any], *, op_name: str) -> dict[str, object]:
        code: str = variables["code"]
        fight_id: int = variables["fightIDs"][0]
        key = (op_name, code, fight_id)
        self.query_log.append(key)
        if key in self.failing:
            raise ApiError(f"scripted failure for {key}")
        if self.client is not None:
            self.client.points_remaining -= self.points_per_query

        players = self.fights.get((code, fight_id), ())
        if op_name == "fetch_report_rankings":
            return self._rankings_response(code, fight_id, players)
        if op_name == "fetch_player_setup_only":
            return self._setup_response(players)
        raise AssertionError(f"FakeWclBackend não sabe responder op_name={op_name!r}")

    def _rankings_response(
        self, report_code: str, fight_id: int, players: tuple[FakePlayer, ...]
    ) -> dict[str, object]:
        characters = [
            {
                "name": p.name,
                "server": {"name": p.server, "region": "US"},
                "class": p.class_name,
                "spec": p.spec_name,
                "amount": 50_000.0,
                "rankPercent": p.rank_percent,
                "bracketData": p.item_level,
                "totalParses": 10,
            }
            for p in players
        ]
        duration_ms = self.fight_duration_s.get((report_code, fight_id), 300.0) * 1000.0
        return {
            "data": {
                "reportData": {
                    "report": {
                        "rankings": {
                            "data": [
                                {
                                    "fightID": fight_id,
                                    "partition": self.fight_partition.get(
                                        (report_code, fight_id), 3
                                    ),
                                    "encounter": {"id": 3179},
                                    "difficulty": 5,
                                    "size": 20,
                                    "kill": True,
                                    "duration": duration_ms,
                                    "roles": {"dps": {"characters": characters}},
                                }
                            ]
                        }
                    }
                }
            }
        }

    def _setup_response(self, players: tuple[FakePlayer, ...]) -> dict[str, object]:
        details = []
        for i, p in enumerate(players):
            gear = (
                [{"slot": 12, "id": p.trinket_id, "itemLevel": p.item_level, "setID": None}]
                if p.trinket_id is not None
                else []
            )
            if p.trinket_id_2 is not None:
                gear.append(
                    {"slot": 13, "id": p.trinket_id_2, "itemLevel": p.item_level, "setID": None}
                )
            gear.extend(
                {"slot": 100 + n, "id": 9000 + n, "itemLevel": p.item_level, "setID": set_id}
                for n, set_id in enumerate(p.set_ids)
            )
            stats: dict[str, object] = {"Haste": {"min": 1000.0 + i}}
            stats.update({name: {"min": value} for name, value in p.stats})
            details.append(
                {
                    "id": i + 1,
                    "name": p.name,
                    "type": p.class_name,
                    "specs": [{"spec": p.spec_name}],
                    "maxItemLevel": p.item_level,
                    "combatantInfo": {
                        "talentTree": [{"nodeID": n, "rank": r, "id": None} for n, r in p.talents],
                        "gear": gear,
                        "stats": stats,
                    },
                }
            )
        return {
            "data": {
                "reportData": {
                    "report": {
                        "table": {
                            "data": {"playerDetails": {"dps": details, "healers": [], "tanks": []}}
                        }
                    }
                }
            }
        }


@dataclass
class FakeBudgetClient:
    points_remaining: float
    points_limit: float = 1_000_000.0
    refresh_calls: int = 0

    def refresh_budget(self) -> None:
        self.refresh_calls += 1
