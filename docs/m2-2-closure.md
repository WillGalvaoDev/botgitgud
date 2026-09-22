# M2.2 - MILESTONE_CLOSED

Date: 2026-09-22. Closed by independent Astra review under the approved workflow.

Authorities: [M0 product contract](m0-methodology-contract.md), [SPEC M1](m1-specification.md), [SPEC M2.1](m2-1-specification.md) (closed), [workflow](milestone-workflow.md), [roadmap](methodology-roadmap-m2-m6.md), [accepted SPEC v1](submilestones/M2.2/spec-v001.md), persisted verbatim as [m2-2-specification.md](m2-2-specification.md). SPEC SHA-256: `ea35eb66efe593076a1fe8efed9b337731f0a970097877ca72fe33ab19d84968`.

Reviewed artifacts (SHA-256 confirmed by the final review and again at closure):

- `src/botgitgud/analysis/metric_population.py`: `8aff6e01430c2e6fa7b0406ca42a0869f941f4b1186574bb40e80b3287aa5e2a`
- `tests/unit/test_m2_2_metric_population.py`: `daff74b6353ac37eb62d70aa64b883591b0667c18db0f30ac61a82dbee483e41`

Hashes above are of the reviewed working-copy bytes. With `core.autocrlf=true`, the test file's working copy is uniformly CRLF (1270 CRLF, no lone CR); its committed Git blob is the LF-normalized form, SHA-256 `f78a47a63947a5412f007deeff6d55a7843d33a0905fa3066f001528bb860e72`, and a checkout re-materializes the reviewed bytes. The implementation and SPEC blobs are byte-identical to the reviewed hashes.

Base: M2.1 closure commit `edba6e62d144811724efe3a2f9bdf0d6023c64ee`.

## Review history

1. [Independent review](submilestones/M2.2/independent-review.md): **REQUIRES_CHANGES** (R1 identity collision, R2 exclusion ordering, R3 incomplete evidence).
2. [Re-review](submilestones/M2.2/independent-rereview.md): R1/R2 resolved with permanent regressions; R3 partially resolved (synthetic sensitivity table not preserved).
3. [Final review](submilestones/M2.2/final-independent-review.md): **MILESTONE_CLOSED**. R3 resolved; all ten preserved sensitivity rows matched an independent execution; no blocking counterexample against AC1-AC6.

Reproduction scripts and independent logs are preserved beside the reviews in `docs/submilestones/M2.2/`. The first probe script documents the pre-correction defect and is intentionally unchanged.

## Validation

- M2.1 + M2.2 selection: **119 passed, 2 skipped** (optional corpus absent; real fixture replay executed).
- Full offline suite with the five public CI placeholders: **2690 passed, 47 skipped, 1 deselected, 1 failed**. Sole failure: `test_no_raw_print_calls_anywhere_in_src`, offender `src/botgitgud/orchestrator/__main__.py`, preexisting debt recorded since M2.1; not converted to PASS.
- Ruff check passed; Ruff format: 331 files formatted; Pyright: 0 errors, 0 warnings.

Evidence package: [m2-2-review-evidence.md](m2-2-review-evidence.md).

## Limits and scope

`metric_population.py` is not consumed by any production path. Wiring, propagation, persisted provenance, the declared reconciliation with the `match_cohort` growth stage above the floor, and handling of rejected reference-id collisions belong to M2.3. Hotfix compatibility remains unverified; relaxation is not adjustment; setup is not matched. No historical data was rewritten.

**M2.2 MILESTONE_CLOSED. M2.3 not started by this closure. Macro M2 is not closed.** The orchestrator remains suspended.
