from __future__ import annotations

from collections import defaultdict
from itertools import count

from botgitgud.analysis.benchmark_aggregate import player_identity
from botgitgud.analysis.cohort_match import DEGRADATION_ORDER, DEGRADATION_ORDER_V2, match_cohort
from botgitgud.domain.external_buffs import EXTERNAL_NON_OFFENSIVE_IDS
from botgitgud.domain.models import CohortCriteria, FightRef, PlayerBuild, PlayerLog

_DEFAULT_TALENTS: frozenset[tuple[int, int]] = frozenset({(1, 1), (2, 1), (3, 2)})
_OTHER_TALENTS: frozenset[tuple[int, int]] = frozenset({(9, 1), (10, 2), (11, 1)})
_LOG_IDS = count()


def _log(
    *,
    duration_s: float = 300.0,
    item_level: float | None = 283.0,
    tier_pieces: int | None = 4,
    has_augmentation: bool = False,
    external_buffs: frozenset[int] = frozenset(),
    talent_pairs: frozenset[tuple[int, int]] = _DEFAULT_TALENTS,
    name: str | None = None,
    report_code: str | None = None,
    fight_id: int = 1,
    kill: bool = True,
    percentile: float = 50.0,
) -> PlayerLog:
    log_id = next(_LOG_IDS)
    name = name or f"Ref{log_id}"
    fight = FightRef(
        report_code=report_code or f"REPORT{log_id:010d}",
        fight_id=fight_id,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=duration_s,
        kill=kill,
    )
    build = PlayerBuild(
        character_name=name,
        server="Azralon",
        class_name="Warlock",
        spec_name="Demonology",
        role="dps",
        item_level=item_level,
        talent_hash=None,
        tier_pieces=tier_pieces,
        external_buffs=external_buffs,
        has_augmentation=has_augmentation,
        talent_pairs=talent_pairs,
    )
    return PlayerLog(
        fight=fight, build=build, dps=100000.0, percentile=percentile, cast_timeline={}
    )


def _target() -> PlayerLog:
    return _log(
        duration_s=300.0, item_level=283.0, tier_pieces=4, has_augmentation=False, name="Target"
    )


def test_hygiene_excludes_self_non_kills_and_deduplicates_pull_then_player() -> None:
    target = _target()
    candidates = [
        _log(name="target"),
        _log(kill=False),
        _log(name="PullLow", report_code="SAMEPULL", percentile=20.0),
        _log(name="PullHigh", report_code="SAMEPULL", percentile=90.0),
        _log(name="Repeat", report_code="PLAYERPULL1", percentile=40.0),
        _log(name="repeat", report_code="PLAYERPULL2", percentile=80.0),
    ]

    matched, report = match_cohort(target, candidates, min_n=1)

    assert {log.build.character_name for log in matched} == {"PullHigh", "repeat"}
    assert report.excluded_self == 1
    assert report.excluded_non_kill == 1
    assert report.deduped_pull == 1
    assert report.deduped_player == 1
    assert all(player_identity(log) != player_identity(target) for log in matched)


def test_hygiene_is_deterministic_when_candidates_are_permuted() -> None:
    target = _target()
    candidates = [
        _log(name="Zulu", report_code="PULL-Z", percentile=30.0),
        _log(name="Discarded", report_code="PULL-Z", percentile=20.0),
        _log(name="Alpha", report_code="PULL-A", percentile=70.0),
        _log(name="Bravo", report_code="PULL-B", percentile=50.0),
        _log(name="C", report_code="PULL-C", percentile=60.0),
    ]

    forward = match_cohort(target, candidates, min_n=3)
    reverse = match_cohort(target, list(reversed(candidates)), min_n=3)

    assert forward == reverse
    assert [log.build.character_name for log in forward[0]] == ["Alpha", "Bravo", "C", "Zulu"]


def _eligible_real_pools(real_corpus: list[PlayerLog]) -> list[list[PlayerLog]]:
    pools: dict[tuple[int, int, int | None, str, str], list[PlayerLog]] = defaultdict(list)
    for log in real_corpus:
        key = (
            log.fight.encounter_id,
            log.fight.difficulty,
            log.fight.partition,
            log.build.class_name,
            log.build.spec_name,
        )
        pools[key].append(log)
    return [pool for pool in pools.values() if len(pool) >= 8]


def test_inv_cohort_self_on_real_corpus(real_corpus: list[PlayerLog]) -> None:
    for pool in _eligible_real_pools(real_corpus):
        for target in pool:
            matched, _report = match_cohort(target, pool, min_n=8)
            assert all(player_identity(member) != player_identity(target) for member in matched)


def test_inv_cohort_independence_on_real_corpus(real_corpus: list[PlayerLog]) -> None:
    for pool in _eligible_real_pools(real_corpus):
        for target in pool:
            matched, _report = match_cohort(target, pool, min_n=8)
            pulls = [(member.fight.report_code, member.fight.fight_id) for member in matched]
            players = [player_identity(member) for member in matched]
            assert len(pulls) == len(set(pulls))
            assert len(players) == len(set(players))


def test_hygiene_precedes_covariate_degradation() -> None:
    target = _target()
    candidates = [
        _log(name="Strict", report_code="STRICT"),
        _log(name="PullWinner", report_code="DUPLICATE", percentile=90.0),
        _log(name="PullLoser", report_code="DUPLICATE", percentile=20.0),
        _log(name="OffIlvl", report_code="OFFILVL", item_level=400.0),
    ]

    matched, report = match_cohort(target, candidates, min_n=3)

    assert len(matched) == 3
    assert report.deduped_pull == 1
    assert "item_level" in report.relaxed


def test_hygiene_does_not_change_cohort_id() -> None:
    criteria = CohortCriteria(
        encounter_id=3179,
        difficulty=5,
        partition=3,
        class_name="Warlock",
        spec_name="Demonology",
        metric="dps",
        duration_min_s=279.0,
        duration_max_s=321.0,
    )
    cohort_id_before = criteria.cohort_id()

    match_cohort(_target(), [_log(name="Reference")], min_n=1)

    assert criteria.cohort_id() == cohort_id_before


# -- degradation order & documented acceptance criteria ------------------------


def test_strict_match_when_everyone_qualifies() -> None:
    candidates = [_log() for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v1")

    assert len(filtered) == 10
    assert report.n_members == 10
    assert report.relaxed == ()
    assert "tier_pieces" in report.matched
    assert "item_level" in report.matched
    assert "talent_cluster" in report.matched
    assert "has_augmentation" in report.matched
    assert report.adjustment_covariates == ()
    assert "duration±7%" in report.matched


def test_relaxation_happens_in_the_exact_documented_order_and_stops_at_min_n() -> None:
    """Synthetic cohort: only 3 members pass every strict filter; 7 more
    only pass once item_level is relaxed (tier_pieces/external_buffs
    don't discriminate here, so those relax first with no effect, then
    item_level relaxation is what actually admits the extra 7).
    """
    strict = [_log(item_level=283.0) for _ in range(3)]
    off_ilvl = [_log(item_level=400.0) for _ in range(7)]  # only ilvl disqualifies these
    filtered, report = match_cohort(_target(), strict + off_ilvl, min_n=8)

    assert len(filtered) == 10
    assert report.n_members == 10
    assert list(DEGRADATION_ORDER).index("tier_pieces") < list(DEGRADATION_ORDER).index(
        "item_level"
    )
    assert "item_level" in report.relaxed
    assert "item_level" not in report.matched


def test_v1_relaxation_never_goes_past_the_covariate_that_reaches_min_n() -> None:
    """The cascade is sequential and never "looks ahead": tier_pieces and
    external_buffs relax first (their fixed position in the order) even
    though they weren't blocking anything here, but has_augmentation
    (which comes *after* item_level in the order) is never touched once
    relaxing item_level alone already reaches min_n.
    """
    candidates = [_log(item_level=400.0) for _ in range(9)]
    _filtered, report = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v1")

    assert "item_level" in report.relaxed
    assert "has_augmentation" in report.matched  # past item_level in the order — never reached


def test_v2_relaxation_never_goes_past_the_covariate_that_reaches_min_n() -> None:
    candidates = [_log(item_level=400.0) for _ in range(9)]
    _filtered, report = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")

    assert "item_level" in report.relaxed
    assert "duration±7%" in report.matched  # past item_level in v2 — never widened


def test_duration_widens_through_bands_before_giving_up() -> None:
    # target=300s: 7% band is max(21, 15)=21s, 12% band is max(36, 15)=36s.
    # 330s is 30s off — outside 7%, inside 12%.
    near = [_log(duration_s=330.0) for _ in range(8)]
    _filtered, report = match_cohort(_target(), near, min_n=8)

    assert "duration±12%" in report.matched
    assert report.n_members == 8


def test_augmentation_covariate_keeps_only_matching_logs_while_n_is_sufficient() -> None:
    """T2.1 acceptance: a player without Augmentation gets a cohort with
    only non-Augmentation logs, as long as n >= min_n without relaxing.
    """
    without_aug = [_log(has_augmentation=False) for _ in range(10)]
    with_aug = [_log(has_augmentation=True) for _ in range(10)]
    filtered, report = match_cohort(
        _target(), without_aug + with_aug, min_n=8, matching_policy_version="v1"
    )

    assert len(filtered) == 10
    assert all(not c.build.has_augmentation for c in filtered)
    assert "has_augmentation" in report.matched


def test_has_augmentation_relaxed_when_needed_to_reach_min_n() -> None:
    without_aug = [_log(has_augmentation=False) for _ in range(3)]
    with_aug = [_log(has_augmentation=True) for _ in range(7)]
    filtered, report = match_cohort(
        _target(), without_aug + with_aug, min_n=8, matching_policy_version="v1"
    )

    assert len(filtered) == 10
    assert "has_augmentation" in report.relaxed


def test_external_buffs_compares_only_offensive_ids() -> None:
    matching = [_log(external_buffs=frozenset({10060})) for _ in range(8)]
    mismatched = [_log(external_buffs=frozenset({29166})) for _ in range(8)]
    filtered, report = match_cohort(
        _log(external_buffs=frozenset({10060})), matching + mismatched, min_n=8
    )

    assert len(filtered) == 8
    assert "external_buffs" in report.matched


def test_non_offensive_external_buffs_never_change_inclusion() -> None:
    assert len(EXTERNAL_NON_OFFENSIVE_IDS) == 9
    for defensive_id in EXTERNAL_NON_OFFENSIVE_IDS:
        candidates = [_log(external_buffs=frozenset()) for _ in range(4)] + [
            _log(external_buffs=frozenset({defensive_id})) for _ in range(4)
        ]
        filtered, report = match_cohort(_target(), candidates, min_n=8)
        assert len(filtered) == 8
        assert report.relaxed == ()


def test_different_offensive_external_buff_still_excludes() -> None:
    matching = [_log(external_buffs=frozenset()) for _ in range(8)]
    with_power_infusion = [_log(external_buffs=frozenset({10060})) for _ in range(8)]
    filtered, report = match_cohort(_target(), matching + with_power_infusion, min_n=8)
    assert len(filtered) == 8
    assert "external_buffs" in report.matched


def test_talent_cluster_keeps_only_jaccard_similar_builds_while_n_is_sufficient() -> None:
    """T2.2 (resolves D-24): a candidate whose build is Jaccard-dissimilar
    to the target's is excluded while the matching build alone already
    clears min_n.
    """
    same_build = [_log(talent_pairs=_DEFAULT_TALENTS) for _ in range(10)]
    other_build = [_log(talent_pairs=_OTHER_TALENTS) for _ in range(10)]
    filtered, report = match_cohort(
        _target(), same_build + other_build, min_n=8, matching_policy_version="v1"
    )

    assert len(filtered) == 10
    assert all(c.build.talent_pairs == _DEFAULT_TALENTS for c in filtered)
    assert "talent_cluster" in report.matched


def test_talent_cluster_relaxed_when_needed_to_reach_min_n() -> None:
    same_build = [_log(talent_pairs=_DEFAULT_TALENTS) for _ in range(3)]
    other_build = [_log(talent_pairs=_OTHER_TALENTS) for _ in range(7)]
    filtered, report = match_cohort(
        _target(), same_build + other_build, min_n=8, matching_policy_version="v1"
    )

    assert len(filtered) == 10
    assert "talent_cluster" in report.relaxed


def test_talent_cluster_never_matches_when_target_has_no_talent_data() -> None:
    """An empty target.build.talent_pairs (no combatantInfo) can never
    strictly match — jaccard_similarity treats "no data" as unknown, not
    as "identical to everyone" — so the covariate is always relaxed.
    """
    target = _log(talent_pairs=frozenset(), name="Target")
    candidates = [_log(talent_pairs=_DEFAULT_TALENTS) for _ in range(10)]
    _filtered, report = match_cohort(target, candidates, min_n=8, matching_policy_version="v1")

    assert "talent_cluster" in report.relaxed


def test_missing_item_level_never_matches_that_covariate() -> None:
    candidates = [_log(item_level=None) for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8)

    assert len(filtered) == 10  # item_level got relaxed to admit them
    assert "item_level" in report.relaxed


def test_returns_fewer_than_min_n_when_every_relaxation_is_exhausted() -> None:
    candidates = [_log(duration_s=1000.0) for _ in range(3)]  # far outside even ±20%
    filtered, report = match_cohort(_target(), candidates, min_n=8)

    assert len(filtered) == 0
    assert report.n_members == 0
    assert "duration±20%" in report.matched  # widened all the way, still not enough


# -- EC.3: matching_policy_version="v2" removes talent_cluster ------------------------


def test_default_matching_policy_version_is_v2() -> None:
    candidates = [_log(has_augmentation=True) for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8)
    assert len(filtered) == 10
    assert "talent_cluster" not in report.matched
    assert "has_augmentation" not in report.matched
    assert report.adjustment_covariates == ("has_augmentation",)


def test_v2_never_includes_talent_cluster_as_matched_or_relaxed() -> None:
    candidates = [_log(talent_pairs=_OTHER_TALENTS) for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")
    assert len(filtered) == 10  # nunca filtrado por talent — builds diferentes, sem problema
    assert "talent_cluster" not in report.matched
    assert "talent_cluster" not in report.relaxed


def test_v2_degradation_order_excludes_talent_cluster() -> None:
    assert "talent_cluster" not in DEGRADATION_ORDER_V2
    assert "has_augmentation" not in DEGRADATION_ORDER_V2
    assert set(DEGRADATION_ORDER_V2) == set(DEGRADATION_ORDER) - {
        "talent_cluster",
        "has_augmentation",
    }


def test_mandatory_regression_10_bad_40_good_builds_v1_collapses_v2_does_not() -> None:
    """Cenário de regressão OBRIGATÓRIO (EC.3): 50 refs, 10 com build BAD,
    40 com build GOOD; jogador alvo usa BAD. Sob v1, o matching por
    talent_cluster colapsa a coorte para os 10 BAD (mesmo cluster do
    alvo). Sob v2, talent_cluster NUNCA participa do matching — a coorte
    inteira de 50 permanece, independente do build do jogador.
    """
    good_talents = frozenset({(1, 1), (2, 2), (3, 3)})
    bad_talents = frozenset({(9, 1), (8, 2)})

    target = _log(talent_pairs=bad_talents, name="Target")
    refs = [_log(talent_pairs=bad_talents, name=f"Bad{i}") for i in range(10)] + [
        _log(talent_pairs=good_talents, name=f"Good{i}") for i in range(40)
    ]

    matched_v1, report_v1 = match_cohort(target, refs, min_n=8, matching_policy_version="v1")
    assert len(matched_v1) == 10  # v1: colapsa para o mesmo cluster do alvo (comportamento antigo)
    assert "talent_cluster" in report_v1.matched

    matched_v2, report_v2 = match_cohort(target, refs, min_n=8, matching_policy_version="v2")
    assert len(matched_v2) == 50  # v2: NUNCA colapsa para os 10 BAD por causa de talents
    assert "talent_cluster" not in report_v2.matched
    assert "talent_cluster" not in report_v2.relaxed


def test_v2_still_relaxes_other_covariates_normally() -> None:
    """EC.3 só remove talent_cluster — as outras covariáveis (e sua ordem
    de degradação) continuam funcionando normalmente sob v2."""
    candidates = [_log(item_level=None) for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")
    assert len(filtered) == 10
    assert "item_level" in report.relaxed


def test_v2_declares_augmentation_as_adjustment_covariate() -> None:
    _filtered, report = match_cohort(_target(), [_log()], min_n=1)
    assert report.adjustment_covariates == ("has_augmentation",)


def test_v1_keeps_augmentation_as_filter_not_adjustment() -> None:
    candidates = [_log(has_augmentation=False) for _ in range(8)] + [
        _log(has_augmentation=True) for _ in range(8)
    ]
    filtered, report = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v1")
    assert len(filtered) == 8
    assert "has_augmentation" in report.matched
    assert report.adjustment_covariates == ()


def test_nexcurse_v2_acceptance_on_real_corpus(real_corpus: list[PlayerLog]) -> None:
    pool = [
        log
        for log in real_corpus
        if log.fight.encounter_id == 3183 and log.fight.difficulty == 5 and log.fight.partition == 3
    ]
    target = next(log for log in pool if log.build.character_name == "Nexcurse")
    matched, report = match_cohort(target, pool, min_n=20)
    assert len(matched) >= 20
    assert report.relaxed == ()


def test_v2_reproduces_the_same_result_deterministically() -> None:
    candidates = [_log(talent_pairs=_OTHER_TALENTS, name=f"Ref{i}") for i in range(10)]
    a = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")
    b = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")
    assert [c.build.character_name for c in a[0]] == [c.build.character_name for c in b[0]]
    assert a[1] == b[1]


def test_default_cascade_reports_all_three_levels() -> None:
    hard, hard_report = match_cohort(_target(), [_log() for _ in range(10)])
    target, target_report = match_cohort(_target(), [_log() for _ in range(20)])
    stretch, stretch_report = match_cohort(_target(), [_log() for _ in range(35)])

    assert (len(hard), hard_report.cohort_level) == (10, "HARD")
    assert (len(target), target_report.cohort_level) == (20, "TARGET")
    assert (len(stretch), stretch_report.cohort_level) == (35, "STRETCH")


def test_target_to_stretch_never_relaxes_offensive_context() -> None:
    matching = [_log(external_buffs=frozenset()) for _ in range(20)]
    mismatched = [_log(external_buffs=frozenset({10060})) for _ in range(15)]

    filtered, report = match_cohort(_target(), matching + mismatched)

    assert len(filtered) == 20
    assert report.cohort_level == "TARGET"
    assert "external_buffs" in report.matched
    assert "external_buffs" not in report.relaxed


def test_hard_to_target_never_relaxes_offensive_context() -> None:
    matching = [_log(external_buffs=frozenset()) for _ in range(8)]
    mismatched = [_log(external_buffs=frozenset({10060})) for _ in range(12)]

    filtered, report = match_cohort(_target(), matching + mismatched)

    assert len(filtered) == 8
    assert report.cohort_level == "HARD"
    assert "external_buffs" in report.matched
    assert "external_buffs" not in report.relaxed
