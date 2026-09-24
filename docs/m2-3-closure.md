# M2.3 and macro M2 - MILESTONE_CLOSED

Date: 2026-09-24. Closed by independent Astra review under the approved workflow
([final review](submilestones/M2.3/final-independent-review.md)). M2.3 is the explicit
integration/closure unit of macro M2 ([workflow](milestone-workflow.md), closure rule;
[roadmap](methodology-roadmap-m2-m6.md)); its closure closes **macro M2**.

Authorities: [M0 product contract](m0-methodology-contract.md), [SPEC M1](m1-specification.md),
[SPEC M2.1](m2-1-specification.md) ([closure](m2-1-closure.md), `edba6e6`),
[SPEC M2.2](m2-2-specification.md) ([closure](m2-2-closure.md), `b6241df`), workflow and
roadmap. Accepted SPEC: [v003](submilestones/M2.3/spec-v003.md), persisted verbatim as
[m2-3-specification.md](m2-3-specification.md). Markdown SHA-256
`b39f576234efc2dd7747200c15953fbda4b055e84e7f4926b57a79ecc8a6f372`; contract hash
(`spec-v003.json`) `0bc1b444c5831319495de8ba318d19426c6da0e2fdaa37aca8e1e3b2000aeee9`.
Superseded versions v001/v002 are preserved beside it.

Base: M2.2 closure commit `b6241df9b53f98226f975f8b227474b700ce0e08`.

## Reviewed artifacts

Every file listed in [independent-final-artifacts.json](submilestones/M2.3/independent-final-artifacts.json)
was re-hashed at closure: all 26 working-copy SHA-256 values match the final review
exactly, before any closure edit. The closure edits only `docs/README.md` (index status,
itself hashed by the review before this edit), the roadmap status line and this record.
Key artifacts:

- `tests/unit/test_m2_3_comparability_integration.py`: `9ac0b9135421b08062a8c971624c8de0d4044c3b5573aa08e81bc953ef15e0c5`
- `docs/m2-3-review-evidence.md`: `b8269fa7e092e6f4335b28a9d2347db8b9e8906cc864e088604664b9692ebda2`
- `src/botgitgud/analysis/comparability_provenance.py`: `882a84d9f236452780ca2181be7b0881017e23f9be878fa13245b89615a44f9b`
- `src/botgitgud/analysis/cohort_match.py`: `ea470488486b42c42134537f53e6dda29253767f41da8f88cdff4a2e4d237224`
- `src/botgitgud/analysis/pipeline.py`: `d6d0a1a0aaae0423ee7deb5910e97b4720aba26fd5407b0bc96cbba63dabb610`
- `reference_eligibility.py` and `metric_population.py`: unchanged from the M2.1/M2.2 closures.

Hashes are of the reviewed working-copy bytes. With `core.autocrlf=true` some working
copies are CRLF; their committed Git blobs are the LF-normalized form (for example the
M2.3 test file: LF SHA-256 `47fe31b78562d834d0945853380476390ec0416e04110770e1c366b7a33e929a`;
`cohort_match.py`: `4292d2a3d4b1cfb582dd1a74c4eed87cf7fbf9af951893a677ceb010dda7cf44`;
`pipeline.py`: `3a9a43598e372bdb08dd2fc6960b649c5eb7e9fa5508d8f1c06431c0e38a5f78`), and a
checkout re-materializes the reviewed bytes. LF-only files are byte-identical in the blob.

## Review history

1. [Independent review](submilestones/M2.3/independent-review.md) (SPEC v001): **REQUIRES_CHANGES**.
   R1 normative contradiction (permutation invariance vs. `match_cohort` preservation with
   divergent tied duplicates) → Opus, SPEC v002 (D-M23-07 quarantine). R2 incomplete permanent
   proofs → Sonnet.
2. [Re-review](submilestones/M2.3/independent-rereview.md) (SPEC v002): R1 resolved; **R3**
   (tie-key quarantine vs. §7.1 id invariant) → Opus, SPEC v003 (observation unit, id-closed
   exclusion); R2 partially resolved.
3. [v003 review](submilestones/M2.3/independent-v003-review.md): R3 resolved; R2.1 (Discord
   content proof) and R2.3 (before/after table) still open → Sonnet.
4. [Final review](submilestones/M2.3/final-independent-review.md): **MILESTONE_CLOSED** for M2.3
   and macro M2. R2/AC6 resolved; R3 remains closed; AC1-AC6 met.

Reproduction scripts, render outputs, artifact manifests and independent logs are preserved
beside the reviews in `docs/submilestones/M2.3/`. Earlier probe scripts document the defects
of their round and are intentionally unchanged.

## Validation (final review)

- M2.3, matching, pipeline, M1 persistence/regressions, M2.2 and golden: **221 passed,
  4 skipped; 1 snapshot passed**.
- Full offline suite with the five public CI placeholders: **2722 passed, 47 skipped,
  1 deselected, 1 failed**. Sole failure: `test_no_raw_print_calls_anywhere_in_src`, offender
  `src/botgitgud/orchestrator/__main__.py`, preexisting debt recorded since M2.1; not converted
  to PASS.
- Ruff check passed; Ruff format: 333 files formatted; Pyright: 0 errors, 0 warnings.

Evidence package: [m2-3-review-evidence.md](m2-3-review-evidence.md).

## Limits and scope

Declared limitations stand (SPEC v003 §11): editorial ledger N versus per-metric N
(inventoried for M5.2), reduced content coverage of the real golden fixture due to the
partition mismatch, unverified hotfix compatibility, relaxation is not adjustment, per-metric
aspirational populations only in provenance, and the observation quarantine may exclude a
same-pull homonym on another server. No historical data was rewritten.

**M2.3 MILESTONE_CLOSED. Macro M2 MILESTONE_CLOSED.** M3 is not started by this closure; the
orchestrator remains suspended.
