# M2.1 - MILESTONE_CLOSED

Date: 2026-09-22. Closed by independent Astra review under the approved workflow.

Authorities: [M0 product contract](m0-methodology-contract.md), [workflow](milestone-workflow.md), [roadmap](methodology-roadmap-m2-m6.md), [accepted SPEC v1](submilestones/M2.1/spec-v001.md). SPEC contract SHA-256: `721dbbaaa725962ef874ebe6b2e686edaa63ee2439c4853619e4115cd1e8b77a`.

Implementation provenance: committed Sonnet checkpoint attempt-00012, commit `affd8653390cd718f28c30ebf0c73060efe8511c`. Attempt-00013 contains no additional material delta. Sonnet verified the code directly and executed Ruff formatting. Final code/test Git blobs match the checkpoint. The accepted SPEC was not regenerated.

## Independent review and validation

[Astra review](m2-1-validation/astra-review.md): **MILESTONE_CLOSED**, no blocking counterexamples against AC1-AC6. Includes independent replay census, reason counts, a compound-case probe and fixture preservation.

- Specific M2.1: **39 passed, 1 skipped**.
- Full offline suite: **2610 passed, 46 skipped, 1 deselected, 1 failed**.
- Sole failure: `test_no_raw_print_calls_anywhere_in_src`, exclusively `src/botgitgud/orchestrator/__main__.py`. This is recorded preexisting infrastructure debt outside M2.1; the failure was not converted to PASS.
- Ruff check passed; Ruff format --check: 329 files formatted.
- Pyright: 0 errors, 0 warnings.

[Full results](m2-1-validation/validation/full.json), JUnit and final static reports preserve commands, failures, skips and warnings. Public synthetic CI prerequisites were used without dotenv or real credentials. Experimental orchestrator gates were not closure authority.

## Limits and scope

The optional local Parquet corpus was absent and explicitly skipped; recorded real-fixture replay executed. Hotfix within a partition remains unobservable under SPEC. Basic eligibility is necessary, not sufficient; pipeline integration belongs to M2.3.

**M2.2 and M2.3 NOT_STARTED. Macro M2 is not closed.** M1 semantics and historical data remain unchanged. The orchestrator remains **ORCHESTRATOR_SUSPENDED**; its database retains the original TECH_BLOCK and is not rewritten to reflect manual closure. All 20,584 historical run files match the preservation manifest.
