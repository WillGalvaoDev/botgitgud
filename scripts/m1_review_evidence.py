"""Offline M1 evidence. Reads corpus; writes only the requested JSON artifact.

Run with the repository's Python from an isolated validation copy. No Store,
API, training or historical-data mutation is performed by this script.
"""

# ruff: noqa: E402 -- explicitly bootstrap the offline synthetic fixtures.

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import platform
import random
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
from test_m0_methodology_contract import _candidates, _gap, _log, _references
from test_m1_algebra_properties import _case, _split_case, assert_split_ledger

from botgitgud.analysis.cohort_match import match_cohort
from botgitgud.analysis.findings import TopPriorities
from botgitgud.analysis.measurement import (
    MetricStatus,
    account_damage,
    compare_damage,
    damage_reference_id,
)
from botgitgud.analysis.metric_observations import UNITS, compare_metrics
from botgitgud.domain.spells import SpellCatalog
from botgitgud.ingest.parquet_codec import read_parquet_log
from botgitgud.report.coaching_answer import render_coaching_answer
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.dps_gap_text import render_dps_gap_section
from botgitgud.report.text import ReportHeader


def fingerprint(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "median": ordered[len(ordered) // 2] if values else None,
        "p95": ordered[int((len(ordered) - 1) * 0.95)] if values else None,
        "max": max(values) if values else None,
    }


def synthetic(catalog: SpellCatalog) -> list[dict]:
    cases = [
        ("D01", _log((100,), (1000.0,)), _references(_log((110,), (1000.0,), duration=330))),
        ("D02", _log((10,), (100.0,)), _references(_log((20,), (100.0,)))),
        ("D03", _log((50, 50), (1000.0, 1000.0)), _references(_log((100, 100), (1000.0, 1000.0)))),
        (
            "D04",
            _log((50, 500), (1000.0, 1000.0)),
            _references(_log((100, 100), (1000.0, 1000.0)), 13)
            + [
                replace(
                    _log((v, v), (1000.0, 1000.0), name=f"Lower{v}"),
                    fight=replace(_log((1,), (1.0,)).fight, fight_id=100 + v),
                )
                for v in (40, 45)
            ],
        ),
        (
            "D05",
            _log((1,), (100.0,)),
            [
                replace(_log((1,), (v,)), fight=replace(_log((1,), (1.0,)).fight, fight_id=i + 2))
                for i, v in enumerate([100.0, 4.0, 100.0] * 5)
            ],
        ),
        ("D06", replace(_log((100,), (1000.0,)), support_subtracted_damage=10000.0), []),
    ]
    results = []
    for name, player, refs in cases:
        report = _gap(player, refs, catalog)
        contract = ReportContract(
            resultado=ReportHeader("Player", "Boss", "Warlock", "Demonology", len(refs), 300, 330),
            setup=None,
            execucao=ExecutionSection((), None, report),
            top_actions=TopPriorities(),
            confianca=ConfidenceSummary(len(refs), len(refs), (), (), ()),
            material_priorities=_candidates(report),
        )
        results.append(
            {
                "case": name,
                "source": "synthetic",
                "comparison": asdict(report.comparison),
                "metrics": {k: asdict(v) for k, v in report.metric_comparisons.items()},
                "proxy": {"status": "UNKNOWN", "reason": "CAST_INSTANCE_LINK_UNAVAILABLE"},
                "cli": "\n".join(render_dps_gap_section(report)),
                "discord": render_coaching_answer(contract),
            }
        )
    return results


def build() -> dict:
    raw = ROOT / "data" / "raw"
    files = list(raw.rglob("*.parquet"))
    assert files, "Corpus required; no vacuous evidence"
    before = fingerprint(raw, [p for p in raw.rglob("*") if p.is_file()])
    logs = [read_parquet_log(p) for p in sorted(files)]
    census = Counter()
    for log in logs:
        a = account_damage(log)
        census[(a.damage_scope, a.status.value, ",".join(a.reasons) or "NONE")] += 1
    groups = defaultdict(list)
    for log in logs:
        groups[
            (
                log.fight.encounter_id,
                log.fight.difficulty,
                log.build.class_name,
                log.build.spec_name,
            )
        ].append(log)
    with tempfile.TemporaryDirectory() as directory:
        catalog = SpellCatalog(Path(directory) / "spells.json", blizzard=None)
        # Use the persisted operational identity catalog, not the old root seed.
        base = json.loads((ROOT / "data/spells.json").read_text(encoding="utf8"))
        (Path(directory) / "corpus-spells.json").write_text(json.dumps(base), encoding="utf8")
        corpus_catalog = SpellCatalog(Path(directory) / "corpus-spells.json", blizzard=None)
        catalog.learn(1, "Example", "wcl")
        catalog.learn(2, "Second", "wcl")
        examples = synthetic(catalog)
        comparisons = []
        metric_ns = defaultdict(list)
        residuals = []
        for group in groups.values():
            for player in group:
                matched, _ = match_cohort(player, group)
                comparison = compare_damage(player, tuple(matched))
                row = {
                    "player": damage_reference_id(player),
                    "matched_n": len(matched),
                    "quantitative_n": comparison.reference_n,
                    "player_status": comparison.player.status.value,
                    "public_comparison": comparison.player.status is MetricStatus.AVAILABLE
                    and comparison.reference_n >= 8,
                }
                if comparison.player.status is MetricStatus.AVAILABLE:
                    metrics = compare_metrics(player, matched, corpus_catalog)
                    row["metrics"] = {
                        key: {
                            "n": len(value.reference_ids),
                            "player_status": value.player.status.value,
                            "excluded_reasons": dict(Counter(value.excluded_references.values())),
                        }
                        for key, value in metrics.items()
                    }
                    for key, value in metrics.items():
                        metric_ns[key.split(":")[0]].append(len(value.reference_ids))
                if comparison.residual_dps is not None:
                    residuals.append(abs(comparison.residual_dps))
                comparisons.append(row)
        rng = random.Random(20260912)
        algebra = []
        split_oracle = []
        for _ in range(500):
            cases = [
                (
                    rng.randint(1, 900),
                    rng.randint(0, 10**8),
                    rng.randint(0, 10**8),
                    rng.randint(0, 100),
                )
                for _ in range(16)
            ]
            player, *refs = [_case(v, i + 1) for i, v in enumerate(cases)]
            c = compare_damage(player, tuple(refs))
            expected = (
                sum(a.total for a in player.damage_by_ability.values())
                - player.support_subtracted_damage
            ) / player.fight.duration_s - math.fsum(
                (sum(a.total for a in r.damage_by_ability.values()) - r.support_subtracted_damage)
                / r.fight.duration_s
                for r in refs
            ) / len(refs)
            assert c.total_delta_dps is not None and c.residual_dps is not None
            tolerance = max(
                1e-9,
                1e-12
                * math.fsum(
                    abs(v) for v in (expected, c.player.net_dps or 0, c.reference_mean_net_dps or 0)
                ),
            )
            assert abs(c.total_delta_dps - expected) <= tolerance
            assert abs(c.residual_dps) <= tolerance
            algebra.append(abs(c.residual_dps))
            player, *refs = [_split_case(v, i + 1) for i, v in enumerate(cases)]
            split_oracle.append(assert_split_ledger(player, refs, catalog))
    after = fingerprint(raw, [p for p in raw.rglob("*") if p.is_file()])
    assert before == after
    source = [
        p
        for folder in ("src", "tests", "scripts")
        for p in (ROOT / folder).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix != ".pyc"
        and not any(part.startswith("_record_scratch") for part in p.parts)
    ]
    source += [ROOT / "pyproject.toml", ROOT / "spells.json"]
    return {
        "created_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "source_fingerprint": fingerprint(ROOT, source),
        "source_file_count": len(source),
        "raw_before": before,
        "raw_after": after,
        "identity_catalog_sha256": hashlib.sha256(
            (ROOT / "data/spells.json").read_bytes()
        ).hexdigest(),
        "historical_hash_status": "UNRESOLVED; no historical byte-preservation claim",
        "files": len(files),
        "pulls": len({(log.fight.report_code, log.fight.fight_id) for log in logs}),
        "players": len({(log.build.character_name, log.build.server) for log in logs}),
        "census": [
            {"scope": k[0], "status": k[1], "reason": k[2], "n": v}
            for k, v in sorted(census.items())
        ],
        "public_comparisons": sum(r["public_comparison"] for r in comparisons),
        "metric_n_population": (
            "Every metric ID in the union of each accounting-eligible player and its matched pool; "
            "N excludes unavailable observations independently. Not independent player counts."
        ),
        "metric_n": {name: distribution(metric_ns[name]) for name in UNITS},
        "comparisons": comparisons,
        "real_residual_dps": distribution(residuals),
        "synthetic_residual_dps": distribution(algebra),
        "synthetic_seed": 20260912,
        "independent_split_oracle": {
            "cases": len(split_oracle),
            "split_pairs": sum(item["split_pairs"] for item in split_oracle),
            "omitted_rows": sum(item["omitted"] for item in split_oracle),
            "arithmetic": "fractions.Fraction",
            "tolerance": "max(1e-9, 1e-12 * sum(abs(compared_terms)))",
        },
        "examples": examples,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    if args.output.suffix == ".gz":
        with gzip.open(args.output, "wt", encoding="utf8") as stream:
            stream.write(payload)
    else:
        args.output.write_text(payload, encoding="utf8")
    print(
        json.dumps(
            {
                key: value
                for key, value in evidence.items()
                if key not in {"examples", "comparisons"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )
