from __future__ import annotations

from botgitgud.analysis.cohort_match import DEGRADATION_ORDER, DEGRADATION_ORDER_V2, match_cohort
from botgitgud.domain.models import FightRef, PlayerBuild, PlayerLog

_DEFAULT_TALENTS: frozenset[tuple[int, int]] = frozenset({(1, 1), (2, 1), (3, 2)})
_OTHER_TALENTS: frozenset[tuple[int, int]] = frozenset({(9, 1), (10, 2), (11, 1)})


def _log(
    *,
    duration_s: float = 300.0,
    item_level: float | None = 283.0,
    tier_pieces: int | None = 4,
    has_augmentation: bool = False,
    external_buffs: frozenset[int] = frozenset(),
    talent_pairs: frozenset[tuple[int, int]] = _DEFAULT_TALENTS,
    name: str = "Ref",
) -> PlayerLog:
    fight = FightRef(
        report_code="ABCDEFGHIJKLMNOP",
        fight_id=1,
        encounter_id=3179,
        boss_name="Fallen-King Salhadaar",
        difficulty=5,
        duration_s=duration_s,
        kill=True,
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
    return PlayerLog(fight=fight, build=build, dps=100000.0, percentile=50.0, cast_timeline={})


def _target() -> PlayerLog:
    return _log(
        duration_s=300.0, item_level=283.0, tier_pieces=4, has_augmentation=False, name="Target"
    )


# -- degradation order & documented acceptance criteria ------------------------


def test_strict_match_when_everyone_qualifies() -> None:
    candidates = [_log() for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8)

    assert len(filtered) == 10
    assert report.n_members == 10
    assert report.relaxed == ()
    assert "tier_pieces" in report.matched
    assert "item_level" in report.matched
    assert "talent_cluster" in report.matched
    assert "has_augmentation" in report.matched
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


def test_relaxation_never_goes_past_the_covariate_that_reaches_min_n() -> None:
    """The cascade is sequential and never "looks ahead": tier_pieces and
    external_buffs relax first (their fixed position in the order) even
    though they weren't blocking anything here, but has_augmentation
    (which comes *after* item_level in the order) is never touched once
    relaxing item_level alone already reaches min_n.
    """
    candidates = [_log(item_level=400.0) for _ in range(9)]
    _filtered, report = match_cohort(_target(), candidates, min_n=8)

    assert "item_level" in report.relaxed
    assert "has_augmentation" in report.matched  # past item_level in the order — never reached


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
    filtered, report = match_cohort(_target(), without_aug + with_aug, min_n=8)

    assert len(filtered) == 10
    assert all(not c.build.has_augmentation for c in filtered)
    assert "has_augmentation" in report.matched


def test_has_augmentation_relaxed_when_needed_to_reach_min_n() -> None:
    without_aug = [_log(has_augmentation=False) for _ in range(3)]
    with_aug = [_log(has_augmentation=True) for _ in range(7)]
    filtered, report = match_cohort(_target(), without_aug + with_aug, min_n=8)

    assert len(filtered) == 10
    assert "has_augmentation" in report.relaxed


def test_external_buffs_requires_exact_set_equality() -> None:
    matching = [_log(external_buffs=frozenset({10060})) for _ in range(8)]
    mismatched = [_log(external_buffs=frozenset({29166})) for _ in range(8)]
    filtered, report = match_cohort(
        _log(external_buffs=frozenset({10060})), matching + mismatched, min_n=8
    )

    assert len(filtered) == 8
    assert "external_buffs" in report.matched


def test_talent_cluster_keeps_only_jaccard_similar_builds_while_n_is_sufficient() -> None:
    """T2.2 (resolves D-24): a candidate whose build is Jaccard-dissimilar
    to the target's is excluded while the matching build alone already
    clears min_n.
    """
    same_build = [_log(talent_pairs=_DEFAULT_TALENTS) for _ in range(10)]
    other_build = [_log(talent_pairs=_OTHER_TALENTS) for _ in range(10)]
    filtered, report = match_cohort(_target(), same_build + other_build, min_n=8)

    assert len(filtered) == 10
    assert all(c.build.talent_pairs == _DEFAULT_TALENTS for c in filtered)
    assert "talent_cluster" in report.matched


def test_talent_cluster_relaxed_when_needed_to_reach_min_n() -> None:
    same_build = [_log(talent_pairs=_DEFAULT_TALENTS) for _ in range(3)]
    other_build = [_log(talent_pairs=_OTHER_TALENTS) for _ in range(7)]
    filtered, report = match_cohort(_target(), same_build + other_build, min_n=8)

    assert len(filtered) == 10
    assert "talent_cluster" in report.relaxed


def test_talent_cluster_never_matches_when_target_has_no_talent_data() -> None:
    """An empty target.build.talent_pairs (no combatantInfo) can never
    strictly match — jaccard_similarity treats "no data" as unknown, not
    as "identical to everyone" — so the covariate is always relaxed.
    """
    target = _log(talent_pairs=frozenset(), name="Target")
    candidates = [_log(talent_pairs=_DEFAULT_TALENTS) for _ in range(10)]
    _filtered, report = match_cohort(target, candidates, min_n=8)

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


def test_default_matching_policy_version_is_v1_unchanged() -> None:
    """Sem passar `matching_policy_version`, o comportamento é EXATAMENTE
    o de antes do EC.3 existir — mesmo teste de `test_strict_match_when_
    everyone_qualifies`, confirmando que o parâmetro novo não alterou o
    default."""
    candidates = [_log() for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8)
    assert len(filtered) == 10
    assert "talent_cluster" in report.matched


def test_v2_never_includes_talent_cluster_as_matched_or_relaxed() -> None:
    candidates = [_log(talent_pairs=_OTHER_TALENTS) for _ in range(10)]
    filtered, report = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")
    assert len(filtered) == 10  # nunca filtrado por talent — builds diferentes, sem problema
    assert "talent_cluster" not in report.matched
    assert "talent_cluster" not in report.relaxed


def test_v2_degradation_order_excludes_talent_cluster() -> None:
    assert "talent_cluster" not in DEGRADATION_ORDER_V2
    assert set(DEGRADATION_ORDER_V2) == set(DEGRADATION_ORDER) - {"talent_cluster"}


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


def test_v2_reproduces_the_same_result_deterministically() -> None:
    candidates = [_log(talent_pairs=_OTHER_TALENTS, name=f"Ref{i}") for i in range(10)]
    a = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")
    b = match_cohort(_target(), candidates, min_n=8, matching_policy_version="v2")
    assert [c.build.character_name for c in a[0]] == [c.build.character_name for c in b[0]]
    assert a[1] == b[1]
