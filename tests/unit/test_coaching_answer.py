from __future__ import annotations

from itertools import product

from botgitgud.analysis.dps_gap import AbilityGap, DpsGapReport
from botgitgud.analysis.findings import ExecutionFinding, Finding, TopPriorities
from botgitgud.analysis.grading import Grade, QuantileStats
from botgitgud.analysis.materiality import (
    Conclusion,
    MaterialCandidate,
    PositiveObservation,
    PositiveObservationBasis,
    Sample,
)
from botgitgud.analysis.performance_features import Direction, ScalarFinding
from botgitgud.analysis.pipeline import CoreAbilityReport
from botgitgud.analysis.remediation import (
    Remediation,
    RemediationBasis,
    RemediationCondition,
    RemediationKind,
)
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.analysis.setup_finding import (
    BenchmarkSampleRef,
    EvidenceLevel,
    FindingCategory,
    FindingSubject,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.domain.ability_role import AbilityRole
from botgitgud.domain.spells import SpellInfo
from botgitgud.report.coaching_answer import (
    COACHING_ANSWER_FIELD_BUDGET,
    MAX_DISCORD_COACHING_ANSWER,
    REMEDIATION_PHRASES,
    REMEDIATION_VOCABULARY,
    render_coaching_answer,
    render_remediation,
)
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.text import ReportHeader


def _scalar(
    *,
    grade: Grade = "red",
    quantile: float = 0.05,
    user: float = 1.0,
    median: float = 0.0,
    direction: Direction = "higher_better",
    n: int = 28,
) -> ScalarFinding:
    stats = QuantileStats(n=n, p10=median, p25=median, p50=median, p75=median, p90=median)
    return ScalarFinding(
        grade=grade,
        quantile=quantile,
        user_value=user,
        stats=stats,
        ci90=None,
        direction=direction,
    )


def _ability(
    spell_id: int,
    name: str,
    *,
    cohort_n: int = 28,
    quantile: float = 0.05,
) -> AbilityGap:
    return AbilityGap(
        spell=SpellInfo(spell_id, name, "curated"),
        delta_dps_pct=-1,
        volume_dps_pct=-0.5,
        efficiency_dps_pct=-0.5,
        diagnosis="usos_perdidos_excedentes",
        confidence="alta",
        unit_kind="DAMAGE_EVENT",
        gross_dps_finding=_scalar(n=cohort_n, quantile=quantile),
    )


def _candidate(
    spell_id: int,
    *,
    basis: RemediationBasis = RemediationBasis.USE_COUNT,
    arbitrary: bool = False,
    grade: Grade = "red",
) -> MaterialCandidate:
    finding = Finding(
        kind="ABILITY_GAP",
        title="TEXTO LIVRE PROIBIDO",
        detail="DETALHE LIVRE PROIBIDO",
        confidence="alta",
    )
    return MaterialCandidate(
        finding=finding,
        remediation=Remediation(RemediationKind.DIRECT_ACTION, basis, spell_id),
        grade=grade,
        ordering_is_arbitrary=arbitrary,
    )


def _contract(**overrides: object) -> ReportContract:
    abilities = (_ability(1, "Primeira"), _ability(2, "Segunda"), _ability(3, "Terceira"))
    defaults: dict[str, object] = {
        "resultado": ReportHeader(
            char_name="Jogador",
            boss_name="Chefe",
            class_name="Warlock",
            spec="Demonology",
            reference_n=28,
            duration_min_s=100,
            duration_max_s=200,
        ),
        "setup": None,
        "execucao": ExecutionSection(
            comparisons=(),
            performance=None,
            dps_gap=DpsGapReport(100, 120, -1 / 6, 100, abilities, 0, 0),
        ),
        "top_actions": TopPriorities(),
        "confianca": ConfidenceSummary(28, 28, (), (), ()),
        "conclusion": Conclusion(_scalar(), None, Sample(28), 8),
    }
    defaults.update(overrides)
    return ReportContract(**defaults)  # type: ignore[arg-type]


def test_remediation_table_is_total_and_closed() -> None:
    keys = set(product(RemediationKind, RemediationBasis, (*RemediationCondition, None)))
    assert set(REMEDIATION_PHRASES) == keys
    assert set(REMEDIATION_PHRASES.values()) == set(REMEDIATION_VOCABULARY)
    for key, phrase in REMEDIATION_PHRASES.items():
        kind, basis, condition = key
        if (kind is RemediationKind.CONDITIONAL_ACTION) == (condition is not None):
            assert render_remediation(Remediation(kind, basis, condition=condition)) == phrase


def test_complete_action_vocabulary_avoids_forbidden_language() -> None:
    forbidden = (
        "use on cooldown",
        "sempre que disponível",
        "pack",
        "mecânica",
        "posicionamento",
        "healer",
        "errou a janela",
        "fora dos seus buffs",
        "movimentação",
        "alvo indisponível",
        "nível de recurso num cast",
        "timing exato",
    )
    for phrase in REMEDIATION_VOCABULARY:
        lowered = phrase.lower()
        assert all(term not in lowered for term in forbidden)


def test_full_rendered_answers_avoid_forbidden_language() -> None:
    """TEST_PLAN item 2: the RB-3 net over the WHOLE rendered answer, not
    just the isolated action vocabulary -- the what-happened sentence, the
    why-it-matters sentence, the positive phrase and the setup phrase can
    each independently leak a forbidden term even when the action table
    (proven closed above) never does. Sweeps the full (kind, basis,
    condition) product x every candidate grade x every ExecutionCategory,
    plus every positive basis and every setup (category, observation) pair.
    """
    forbidden = (
        "use on cooldown",
        "sempre que disponível",
        "pack",
        "mecânica",
        "posicionamento",
        "healer",
        "errou a janela",
        "fora dos seus buffs",
        "movimentação",
        "alvo indisponível",
        "nível de recurso num cast",
        "timing exato",
    )

    def _assert_clean(answer: str) -> None:
        lowered = answer.lower()
        for term in forbidden:
            assert term not in lowered, f"forbidden term {term!r} leaked into: {answer!r}"

    # -- (kind, basis, condition) product x every grade: happened/importance/action
    for kind in RemediationKind:
        conditions: tuple[RemediationCondition | None, ...] = (
            tuple(RemediationCondition) if kind is RemediationKind.CONDITIONAL_ACTION else (None,)
        )
        for basis in RemediationBasis:
            for condition in conditions:
                for grade in ("red", "yellow", "green", "insufficient"):
                    candidate = MaterialCandidate(
                        finding=_candidate(1).finding,
                        remediation=Remediation(kind, basis, 1, condition),
                        grade=grade,  # type: ignore[arg-type]
                    )
                    _assert_clean(
                        render_coaching_answer(_contract(material_priorities=(candidate,)))
                    )

    # -- every ExecutionCategory: DEATH/ACTIVE_TIME/WASTE happened/importance/action
    execution_bases = {
        "DEATH": RemediationBasis.OBSERVED_DEATH,
        "ACTIVE_TIME": RemediationBasis.ACTIVE_PARTICIPATION,
        "WASTE": RemediationBasis.AGGREGATE_RESOURCE_WASTE,
    }
    for category, basis in execution_bases.items():
        finding = ExecutionFinding(category, _scalar(user=2, median=0))  # type: ignore[arg-type]
        candidate = MaterialCandidate(
            finding=finding,
            remediation=Remediation(RemediationKind.DIRECT_ACTION, basis),
            grade="red",
        )
        _assert_clean(render_coaching_answer(_contract(material_priorities=(candidate,))))

    # -- every positive-observation basis
    for basis in PositiveObservationBasis:
        is_ability = basis is PositiveObservationBasis.ABILITY_ABOVE_COHORT
        observation = PositiveObservation(basis, 1 if is_ability else None)
        core_abilities = (
            (CoreAbilityReport("Primeira", AbilityRole.CORE_DAMAGE, (), damage_share=0.2),)
            if is_ability
            else ()
        )
        answer = render_coaching_answer(
            _contract(positive_observation=observation, core_abilities=core_abilities)
        )
        _assert_clean(answer)

    # -- every setup (category, observation) pair that RB-8 can publish
    setup_subjects = {
        FindingCategory.TALENT_BUILD: FindingSubject.talent_build("1:1"),
        FindingCategory.TRINKET: FindingSubject.trinket(1),
        FindingCategory.TRINKET_PAIR: FindingSubject.trinket_pair(1, 2),
        FindingCategory.SET_BONUS: FindingSubject.set_bonus("s1"),
        FindingCategory.SECONDARY_STATS: FindingSubject.secondary_stat("haste"),
    }
    for subject in setup_subjects.values():
        for observation_code in (
            ObservationCode.DIFFERS_FROM_COMMON_PATTERN,
            ObservationCode.LOW_PREVALENCE,
        ):
            setup_finding = SetupFinding(
                subject=subject,
                observation=observation_code,
                evidence_level=EvidenceLevel.STRONG,
                publicability=Publicability.PUBLISHABLE,
                sample=BenchmarkSampleRef("benchmark"),
                actionable=True,
            )
            setup = SetupAnalysis("benchmark", (setup_finding,), True, True)
            answer = render_coaching_answer(
                _contract(material_priorities=(_candidate(1),), setup=setup)
            )
            _assert_clean(answer)


def test_zero_priorities_is_complete_and_does_not_pad_with_setup() -> None:
    setup_finding = SetupFinding(
        subject=FindingSubject.talent_build("1:1"),
        observation=ObservationCode.DIFFERS_FROM_COMMON_PATTERN,
        evidence_level=EvidenceLevel.STRONG,
        publicability=Publicability.PUBLISHABLE,
        sample=BenchmarkSampleRef("benchmark"),
        actionable=True,
    )
    setup = SetupAnalysis("benchmark", (setup_finding,), True, True)
    answer = render_coaching_answer(_contract(setup=setup))
    assert answer == "Seu dano ficou na faixa mais baixa da coorte comparável."
    assert "Setup" not in answer
    assert "1." not in answer


def test_three_priorities_keep_input_order_and_hide_legacy_numbers_and_text() -> None:
    priorities = (_candidate(2), _candidate(1), _candidate(3))
    answer = render_coaching_answer(
        _contract(
            material_priorities=priorities,
            conclusion=Conclusion(_scalar(), None, Sample(28), 8123),
        )
    )
    assert answer.index("Segunda") < answer.index("Primeira") < answer.index("Terceira")
    assert all(f"{index}." in answer for index in range(1, 4))
    assert "9876" not in answer
    assert "8123" not in answer
    assert "TEXTO LIVRE" not in answer and "DETALHE LIVRE" not in answer


def test_arbitrary_order_is_explicitly_not_precedence() -> None:
    answer = render_coaching_answer(
        _contract(material_priorities=(_candidate(1, arbitrary=True), _candidate(2)))
    )
    assert "não têm precedência entre si" in answer
    assert "1." not in answer and "2." not in answer


def test_window_or_buffs_keeps_uncertainty() -> None:
    conditional = MaterialCandidate(
        finding=_candidate(1).finding,
        remediation=Remediation(
            RemediationKind.CONDITIONAL_ACTION,
            RemediationBasis.DAMAGE_PER_USE,
            1,
            RemediationCondition.WINDOW_OR_OWN_BUFFS_UNDISTINGUISHED,
        ),
        grade="red",
    )
    answer = render_coaching_answer(_contract(material_priorities=(conditional,)))
    assert "não separam qual deles" in answer
    assert "errou a janela" not in answer and "fora dos seus buffs" not in answer


def test_death_reports_count_with_agreement_without_inventing_a_cause() -> None:
    cases = (
        (0, "Foram medidas 0 mortes"),
        (1, "Foi medida 1 morte"),
        (2, "Foram medidas 2 mortes"),
    )
    for count, expected in cases:
        death = MaterialCandidate(
            ExecutionFinding("DEATH", _scalar(user=count, median=0, direction="lower_better")),
            Remediation(RemediationKind.DIRECT_ACTION, RemediationBasis.OBSERVED_DEATH),
            "red",
        )
        answer = render_coaching_answer(_contract(material_priorities=(death,)))
        assert expected in answer and "mediana 0" in answer
        assert "mecânica" not in answer and "posicionamento" not in answer


def test_positive_ability_requires_measured_offensive_damage_share() -> None:
    """RB-7's floor is offensive relevance; the contract already carries a
    measured one -- `damage_share`, populated only when DAMAGE_SHARE is
    observable for this pull -- so no import of `domain/ability_role.py`
    is needed to prove it (M5's mechanical boundary forbids it anyway).
    """
    observation = PositiveObservation(PositiveObservationBasis.ABILITY_ABOVE_COHORT, 1)

    no_measurement = CoreAbilityReport("Primeira", AbilityRole.CORE_DAMAGE, (), damage_share=None)
    hidden_unmeasured = render_coaching_answer(
        _contract(positive_observation=observation, core_abilities=(no_measurement,))
    )
    assert "Ponto positivo" not in hidden_unmeasured

    zero_contribution = CoreAbilityReport("Primeira", AbilityRole.CORE_DAMAGE, (), damage_share=0.0)
    hidden_zero = render_coaching_answer(
        _contract(positive_observation=observation, core_abilities=(zero_contribution,))
    )
    assert "Ponto positivo" not in hidden_zero

    measured = CoreAbilityReport("Primeira", AbilityRole.CORE_DAMAGE, (), damage_share=0.12)
    visible = render_coaching_answer(
        _contract(positive_observation=observation, core_abilities=(measured,))
    )
    assert "Ponto positivo: Primeira" in visible


def test_setup_only_appears_when_actionable_and_publicable() -> None:
    base = dict(
        subject=FindingSubject.talent_build("1:1"),
        observation=ObservationCode.DIFFERS_FROM_COMMON_PATTERN,
        evidence_level=EvidenceLevel.STRONG,
        sample=BenchmarkSampleRef("benchmark"),
        actionable=True,
    )
    hidden = SetupFinding(publicability=Publicability.HIDDEN, **base)  # type: ignore[arg-type]
    shown = SetupFinding(publicability=Publicability.PUBLISHABLE, **base)  # type: ignore[arg-type]
    priorities = (_candidate(1),)
    hidden_answer = render_coaching_answer(
        _contract(
            material_priorities=priorities,
            setup=SetupAnalysis("benchmark", (hidden,), True, True),
        )
    )
    shown_answer = render_coaching_answer(
        _contract(
            material_priorities=priorities,
            setup=SetupAnalysis("benchmark", (shown,), True, True),
        )
    )
    assert "Setup" not in hidden_answer
    assert "Setup: vale revisar" in shown_answer


def test_answer_is_deterministic_bounded_and_has_no_url() -> None:
    hostile = "@everyone https://evil.invalid **[" + "x" * 10_000
    abilities = (_ability(1, hostile), _ability(2, hostile), _ability(3, hostile))
    gap = DpsGapReport(1, 2, -0.5, 1, abilities, 0, 0)
    contract = _contract(
        execucao=ExecutionSection((), None, gap),
        material_priorities=(_candidate(1), _candidate(2), _candidate(3)),
    )
    first = render_coaching_answer(contract)
    assert first == render_coaching_answer(contract)
    assert len(first) <= MAX_DISCORD_COACHING_ANSWER
    assert "http://" not in first and "https://" not in first
    assert "@everyone" not in first


def test_worst_case_all_fields_long_fits_declared_field_budget() -> None:
    """Three longest names plus conclusion, positive and setup all fit at once."""
    longest_name = "H" * 72
    abilities = tuple(
        _ability(spell_id, longest_name, cohort_n=9999, quantile=0.25) for spell_id in (1, 2, 3)
    )
    setup_finding = SetupFinding(
        subject=FindingSubject.secondary_stat("haste"),
        observation=ObservationCode.DIFFERS_FROM_COMMON_PATTERN,
        evidence_level=EvidenceLevel.STRONG,
        publicability=Publicability.PUBLISHABLE,
        sample=BenchmarkSampleRef("benchmark"),
        actionable=True,
    )
    priorities = tuple(
        _candidate(
            spell_id,
            basis=RemediationBasis.DAMAGE_PER_USE,
            arbitrary=spell_id == 1,
            grade="yellow",
        )
        for spell_id in (1, 2, 3)
    )
    contract = _contract(
        conclusion=Conclusion(_scalar(grade="yellow", quantile=0.25), 24.9, Sample(9999), 9999),
        execucao=ExecutionSection((), None, DpsGapReport(1, 2, -0.5, 1, abilities, 0, 0)),
        material_priorities=priorities,
        positive_observation=PositiveObservation(PositiveObservationBasis.ABILITY_ABOVE_COHORT, 1),
        core_abilities=(
            CoreAbilityReport(longest_name, AbilityRole.CORE_DAMAGE, (), damage_share=1.0),
        ),
        setup=SetupAnalysis("benchmark", (setup_finding,), True, True),
    )

    answer = render_coaching_answer(contract)

    assert COACHING_ANSWER_FIELD_BUDGET <= MAX_DISCORD_COACHING_ANSWER
    assert len(answer) <= COACHING_ANSWER_FIELD_BUDGET
    assert answer.count(longest_name) == 4
    assert "Ponto positivo" in answer
    assert "Setup:" in answer


def test_nexcurse_and_drahzhul_regressions_preserve_m31_decisions() -> None:
    nexcurse_abilities = (
        _ability(1, "Soul Barrage"),
        _ability(2, "Burning Cleave"),
        _ability(3, "Felseeker"),
    )
    nexcurse = render_coaching_answer(
        _contract(
            conclusion=Conclusion(_scalar(grade="green", quantile=0.9), 99.6, Sample(28), 3),
            execucao=ExecutionSection(
                (), None, DpsGapReport(176_379, 150_000, 0.17, 180, nexcurse_abilities, 0, 0)
            ),
            material_priorities=(_candidate(1), _candidate(2), _candidate(3)),
        )
    )
    assert nexcurse.index("Soul Barrage") < nexcurse.index("Burning Cleave")
    assert nexcurse.index("Burning Cleave") < nexcurse.index("Felseeker")

    drahzhul_abilities = (
        _ability(1, "The Last Light"),
        _ability(2, "Soul Barrage"),
        _ability(3, "Burning Cleave"),
        _ability(4, "Felseeker"),
    )
    death = MaterialCandidate(
        ExecutionFinding("DEATH", _scalar(user=1, median=0, direction="lower_better")),
        Remediation(RemediationKind.DIRECT_ACTION, RemediationBasis.OBSERVED_DEATH),
        "red",
    )
    drahzhul = render_coaching_answer(
        _contract(
            execucao=ExecutionSection(
                (), None, DpsGapReport(100_000, 150_000, -1 / 3, 180, drahzhul_abilities, 0, 0)
            ),
            material_priorities=(death, _candidate(1), _candidate(2)),
        )
    )
    assert drahzhul.index("morte") < drahzhul.index("The Last Light")
    assert "Burning Cleave" not in drahzhul
    assert "Felseeker" not in drahzhul
