"""Rebuild existing Setup benchmarks under policy v2 using local progress only."""

from __future__ import annotations

import argparse
from pathlib import Path

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_build_progress import BenchmarkBuildProgressStore
from botgitgud.analysis.benchmark_builder import (
    LocalBenchmarkRebuildError,
    rebuild_benchmark_from_local_progress,
)
from botgitgud.analysis.benchmark_store import BenchmarkStore
from botgitgud.ingest.store import Store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    policy = BenchmarkPolicy.default()
    with Store(args.data_dir) as store:
        benchmark_store = BenchmarkStore(store)
        progress_store = BenchmarkBuildProgressStore(store)
        source_ids = sorted(
            str(row["benchmark_id"])
            for row in benchmark_store.list_benchmarks()
            if str(row["benchmark_policy_version"]) == "v1"
        )
        rebuilt: list[str] = []
        refused: list[tuple[str, str]] = []
        for source_id in source_ids:
            source = EncounterBenchmarkTarget.parse(source_id)
            target = EncounterBenchmarkTarget(
                spec=source.spec,
                encounter_id=source.encounter_id,
                difficulty=source.difficulty,
                partition=source.partition,
                benchmark_policy_version=policy.policy_version,
            )
            try:
                result = rebuild_benchmark_from_local_progress(
                    progress_store=progress_store,
                    benchmark_store=benchmark_store,
                    source_target=source,
                    target=target,
                    policy=policy,
                )
            except LocalBenchmarkRebuildError as exc:
                reason = str(exc)
                refused.append((source_id, reason))
                print(f"{source_id} -> REFUSED")
                print(f"  reason={reason}")
                continue

            rebuilt.append(source_id)
            print(f"{source_id} -> {result.benchmark_id}")
            print(f"  observations_reused={result.observations_reused}")
            print(f"  before={dict(sorted(result.before_by_band.items()))}")
            print(f"  after={dict(sorted(result.after_by_band.items()))}")
            if (
                source.spec.class_name == "Warrior"
                and source.spec.spec_name == "Arms"
                and source.encounter_id == 3421
                and source.difficulty == 4
            ):
                print(
                    "  note=known contaminated population is reproduced unchanged; no action taken"
                )

        print(f"summary: rebuilt={len(rebuilt)} refused={len(refused)}")
        if refused:
            print("refused benchmarks:")
            for source_id, reason in refused:
                print(f"  {source_id}: {reason}")
    return 0 if rebuilt else 1


if __name__ == "__main__":
    raise SystemExit(main())
