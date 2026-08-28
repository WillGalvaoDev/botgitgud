from __future__ import annotations

import ast
import inspect

import pytest

from botgitgud.analysis import setup_finding as setup_finding_module
from botgitgud.analysis.benchmark import PercentileBand
from botgitgud.analysis.benchmark_aggregate import DescriptiveStats
from botgitgud.analysis.setup_finding import (
    OBSERVATION_TEMPLATES,
    BandPrevalence,
    BenchmarkSampleRef,
    CaveatCode,
    DistributionContext,
    EvidenceLevel,
    FindingCategory,
    FindingSubject,
    ForbiddenSetupVocabularyError,
    ObservationCode,
    PrevalenceSummary,
    Publicability,
    SetupFinding,
    SetupFindingError,
    category_unavailable_finding,
    compute_evidence_level,
    compute_publicability,
    missing_benchmark_finding,
    missing_player_setup_finding,
    render_observation,
    sort_setup_findings,
    validate_setup_language,
)

# -- fixtures compartilhadas --------------------------------------------------

_BENCHMARK_ID = "Warlock/Demonology/1234/5/34/v1"
_SAMPLE = BenchmarkSampleRef(benchmark_id=_BENCHMARK_ID, band_name="p95-99")
_OTHER_SAMPLE = BenchmarkSampleRef(benchmark_id=_BENCHMARK_ID, band_name="p75-95")


def _prevalence(
    *, count: int = 80, n_available: int = 100, prevalence: float = 0.8
) -> PrevalenceSummary:
    return PrevalenceSummary(count=count, n_available=n_available, prevalence=prevalence)


def _finding(
    *,
    subject: FindingSubject | None = None,
    observation: ObservationCode = ObservationCode.MATCHES_COMMON_PATTERN,
    evidence_level: EvidenceLevel = EvidenceLevel.STRONG,
    publicability: Publicability = Publicability.PUBLISHABLE,
    sample: BenchmarkSampleRef = _SAMPLE,
    prevalence: PrevalenceSummary | None = None,
    caveats: tuple[CaveatCode, ...] = (),
    actionable: bool = False,
) -> SetupFinding:
    return SetupFinding(
        subject=subject if subject is not None else FindingSubject.talent_build("1:1|2:2"),
        observation=observation,
        evidence_level=evidence_level,
        publicability=publicability,
        sample=sample,
        prevalence=prevalence if prevalence is not None else _prevalence(),
        caveats=caveats,
        actionable=actionable,
    )


# -- 1: construção válida ------------------------------------------------------


def test_setup_finding_valid_construction() -> None:
    finding = _finding()
    assert finding.category is FindingCategory.TALENT_BUILD
    assert finding.observation is ObservationCode.MATCHES_COMMON_PATTERN
    assert finding.publicability is Publicability.PUBLISHABLE


# -- 2-5: finding_id determinístico -------------------------------------------


def test_finding_id_deterministic_for_same_evidence() -> None:
    a = _finding()
    b = _finding()
    assert a.finding_id == b.finding_id


def test_category_changes_finding_id() -> None:
    talent = _finding(subject=FindingSubject.talent_build("1:1"))
    trinket = _finding(subject=FindingSubject.trinket(111))
    assert talent.finding_id != trinket.finding_id


def test_subject_changes_finding_id() -> None:
    a = _finding(subject=FindingSubject.trinket(111))
    b = _finding(subject=FindingSubject.trinket(222))
    assert a.finding_id != b.finding_id


def test_observation_changes_finding_id_when_semantically_relevant() -> None:
    a = _finding(observation=ObservationCode.MATCHES_COMMON_PATTERN)
    b = _finding(observation=ObservationCode.DIFFERS_FROM_COMMON_PATTERN)
    assert a.finding_id != b.finding_id


def test_evidence_quality_does_not_change_finding_id() -> None:
    """evidence_level/caveats/actionable descrevem a QUALIDADE da evidência,
    não O QUE está sendo alegado — não fazem parte da identidade.
    """
    weak = _finding(evidence_level=EvidenceLevel.WEAK, publicability=Publicability.CAUTION)
    strong = _finding(
        evidence_level=EvidenceLevel.STRONG,
        publicability=Publicability.PUBLISHABLE,
        caveats=(CaveatCode.LOW_SAMPLE,),
        actionable=True,
    )
    assert weak.finding_id == strong.finding_id


def test_band_changes_finding_id() -> None:
    a = _finding(sample=_SAMPLE)
    b = _finding(sample=_OTHER_SAMPLE)
    assert a.finding_id != b.finding_id


# -- 6-9: prevalence validation ------------------------------------------------


def test_prevalence_accepts_0_to_1_boundaries() -> None:
    PrevalenceSummary(count=0, n_available=0, prevalence=0.0)
    PrevalenceSummary(count=5, n_available=5, prevalence=1.0)


def test_n_available_non_negative() -> None:
    with pytest.raises(SetupFindingError):
        PrevalenceSummary(count=0, n_available=-1, prevalence=0.0)


def test_count_cannot_exceed_n_available() -> None:
    with pytest.raises(SetupFindingError):
        PrevalenceSummary(count=6, n_available=5, prevalence=1.0)


def test_invalid_prevalence_rejected() -> None:
    with pytest.raises(SetupFindingError):
        PrevalenceSummary(count=1, n_available=1, prevalence=1.5)
    with pytest.raises(SetupFindingError):
        PrevalenceSummary(count=1, n_available=1, prevalence=-0.1)


# -- 10-12: band breakdown -----------------------------------------------------


def test_band_breakdown_round_trip() -> None:
    summary = PrevalenceSummary(
        count=80,
        n_available=100,
        prevalence=0.8,
        bands=(
            BandPrevalence(PercentileBand("p50-75", 50.0, 75.0), n_available=50, prevalence=0.4),
            BandPrevalence(PercentileBand("p95-99", 95.0, 99.0), n_available=20, prevalence=0.9),
        ),
    )
    restored = PrevalenceSummary.from_dict(summary.to_dict())
    assert restored == summary
    # ordem canônica por band.low, independente da ordem de construção
    assert [b.band.name for b in restored.bands] == ["p50-75", "p95-99"]


def test_custom_band_works_without_hardcoded_names() -> None:
    custom = BandPrevalence(PercentileBand("elite-100", 99.5, 100.0), n_available=3, prevalence=1.0)
    summary = PrevalenceSummary(count=3, n_available=3, prevalence=1.0, bands=(custom,))
    assert summary.bands[0].band.name == "elite-100"


def test_no_dependency_on_hardcoded_bands() -> None:
    """`PercentileBand` (EB.1) já não hardcoda nomes — este teste garante
    que nada em setup_finding.py reintroduz uma lista fixa de nomes.
    """
    source = inspect.getsource(setup_finding_module)
    assert "p95-99" not in source
    assert "p75-95" not in source
    assert "p50-75" not in source


# -- 13-16: evidence / publicability / caveats ---------------------------------


def test_evidence_level_enum_thresholds() -> None:
    assert compute_evidence_level(n_available=0, min_sample_size=8) is EvidenceLevel.INSUFFICIENT
    assert compute_evidence_level(n_available=4, min_sample_size=8) is EvidenceLevel.WEAK
    assert compute_evidence_level(n_available=10, min_sample_size=8) is EvidenceLevel.MODERATE
    assert compute_evidence_level(n_available=30, min_sample_size=8) is EvidenceLevel.STRONG


def test_publicability_derivation_is_testable() -> None:
    assert compute_publicability(EvidenceLevel.INSUFFICIENT) is Publicability.HIDDEN
    assert compute_publicability(EvidenceLevel.WEAK) is Publicability.CAUTION
    assert compute_publicability(EvidenceLevel.MODERATE) is Publicability.PUBLISHABLE
    assert compute_publicability(EvidenceLevel.STRONG) is Publicability.PUBLISHABLE


def test_insufficient_sample_supported() -> None:
    finding = _finding(
        evidence_level=compute_evidence_level(n_available=0, min_sample_size=8),
        publicability=compute_publicability(EvidenceLevel.INSUFFICIENT),
        caveats=(CaveatCode.LOW_SAMPLE,),
    )
    assert finding.evidence_level is EvidenceLevel.INSUFFICIENT
    assert finding.publicability is Publicability.HIDDEN
    assert CaveatCode.LOW_SAMPLE in finding.caveats


def test_partial_coverage_caveat_supported() -> None:
    finding = _finding(caveats=(CaveatCode.PARTIAL_SETUP_COVERAGE,))
    assert CaveatCode.PARTIAL_SETUP_COVERAGE in finding.caveats


# -- 17-19: missing data --------------------------------------------------------


def test_missing_player_setup_finding() -> None:
    finding = missing_player_setup_finding(subject=FindingSubject.trinket(111), sample=_SAMPLE)
    assert finding.observation is ObservationCode.MISSING_DATA
    assert finding.publicability is Publicability.HIDDEN
    assert CaveatCode.PLAYER_SETUP_MISSING in finding.caveats
    assert finding.actionable is False


def test_missing_benchmark_finding() -> None:
    finding = missing_benchmark_finding(subject=FindingSubject.trinket(111), sample=_SAMPLE)
    assert finding.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.BENCHMARK_UNAVAILABLE in finding.caveats


def test_category_unavailable_finding() -> None:
    finding = category_unavailable_finding(subject=FindingSubject.set_bonus("1234"), sample=_SAMPLE)
    assert finding.observation is ObservationCode.MISSING_DATA
    assert CaveatCode.CATEGORY_UNAVAILABLE in finding.caveats


# -- 20-21: distribution / secondary stats --------------------------------------


def test_secondary_stat_distribution_accepts_raw_rating() -> None:
    dist = DistributionContext(
        player_value=1234.5,
        benchmark=DescriptiveStats(n=50, median=1200.0, p25=1000.0, p75=1400.0),
    )
    finding = SetupFinding(
        subject=FindingSubject.secondary_stat("Haste"),
        observation=ObservationCode.MATCHES_COMMON_PATTERN,
        evidence_level=EvidenceLevel.STRONG,
        publicability=Publicability.PUBLISHABLE,
        sample=_SAMPLE,
        distribution=dist,
        caveats=(CaveatCode.RAW_RATING_ONLY,),
    )
    assert finding.distribution is not None
    assert finding.distribution.player_value == 1234.5


def test_distribution_never_converts_to_percentage() -> None:
    """Nenhuma função deste módulo converte rating em porcentagem — o
    contrato só carrega os números crus e um caveat explícito."""
    source = inspect.getsource(setup_finding_module)
    assert "/ 100" not in source
    assert "* 100" not in source


# -- 22-24: subject identity ----------------------------------------------------


def test_talent_subject_needs_no_resolved_name() -> None:
    subject = FindingSubject.talent_build("12:1|34:2")
    assert subject.talent_fingerprint == "12:1|34:2"
    assert subject.key == "talent:12:1|34:2"


def test_trinket_subject_uses_item_id() -> None:
    subject = FindingSubject.trinket(190958)
    assert subject.item_id == 190958
    assert subject.key == "trinket:190958"


def test_trinket_pair_is_canonical() -> None:
    a = FindingSubject.trinket_pair(200, 100)
    b = FindingSubject.trinket_pair(100, 200)
    assert a == b
    assert a.item_id == 100 and a.item_id_other == 200
    assert a.key == "trinket_pair:100+200"


def test_trinket_pair_rejects_identical_ids() -> None:
    with pytest.raises(SetupFindingError):
        FindingSubject.trinket_pair(100, 100)


def test_subject_requires_exact_fields_for_category() -> None:
    with pytest.raises(SetupFindingError):
        FindingSubject(category=FindingCategory.TRINKET)  # falta item_id
    with pytest.raises(SetupFindingError):
        FindingSubject(category=FindingCategory.TALENT_BUILD, item_id=1)  # campo errado


# -- 25-27: serialization / ordering --------------------------------------------


def test_setup_finding_serialization_round_trip() -> None:
    finding = _finding(caveats=(CaveatCode.OBSERVATIONAL_ONLY, CaveatCode.LOW_SAMPLE))
    restored = SetupFinding.from_dict(finding.to_dict())
    assert restored == finding
    assert restored.finding_id == finding.finding_id


def test_dict_key_order_does_not_change_finding_id() -> None:
    payload_a = {
        "subject": {"finding_category": "trinket", "item_id": 111},
        "observation": "low_prevalence",
        "evidence_level": "strong",
        "publicability": "publishable",
        "sample": {"benchmark_id": _SAMPLE.benchmark_id, "band_name": _SAMPLE.band_name},
    }
    payload_b = {
        "sample": {"band_name": _SAMPLE.band_name, "benchmark_id": _SAMPLE.benchmark_id},
        "publicability": "publishable",
        "evidence_level": "strong",
        "observation": "low_prevalence",
        "subject": {"item_id": 111, "finding_category": "trinket"},
    }
    a = SetupFinding.from_dict(payload_a)
    b = SetupFinding.from_dict(payload_b)
    assert a.finding_id == b.finding_id


def test_input_order_does_not_alter_sorted_output() -> None:
    f1 = _finding(subject=FindingSubject.trinket(1), evidence_level=EvidenceLevel.STRONG)
    f2 = _finding(subject=FindingSubject.trinket(2), evidence_level=EvidenceLevel.WEAK)
    f3 = _finding(subject=FindingSubject.talent_build("1:1"))

    order_a = sort_setup_findings([f1, f2, f3])
    order_b = sort_setup_findings([f3, f2, f1])
    order_c = sort_setup_findings([f2, f3, f1])
    assert order_a == order_b == order_c


# -- 28: actionable != causal ----------------------------------------------------


def test_actionable_is_not_a_causal_claim() -> None:
    """Um trinket raro (actionable=True) usa "worth reviewing"-shaped
    vocabulário, nunca "replace this for more DPS" — nenhum campo de ganho
    estimado existe em SetupFinding para começo de conversa.
    """
    finding = _finding(
        subject=FindingSubject.trinket(999),
        observation=ObservationCode.LOW_PREVALENCE,
        actionable=True,
    )
    assert finding.actionable is True
    assert not hasattr(finding, "estimated_gain_pct")
    text = render_observation(ObservationCode.LOW_PREVALENCE)
    validate_setup_language(text)  # não levanta — texto é observacional


# -- 29-30: vocabulário permitido / proibido -------------------------------------


def test_official_templates_all_pass_the_language_guard() -> None:
    for code, template in OBSERVATION_TEMPLATES.items():
        validate_setup_language(template)
        assert isinstance(code, ObservationCode)


@pytest.mark.parametrize(
    "text",
    [
        "this is the best talent build",
        "an optimal trinket choice",
        "clearly better than the alternative",
        "worse than the common pattern",
        "a bad build overall",
        "wrong talent for this encounter",
        "you should change this",
        "should use a different trinket",
        "increases damage substantially",
        "this setup causes low DPS",
        "upgrade your gear",
        "+5% dps from this trinket",
        "gain 3% dps by switching",
    ],
)
def test_every_forbidden_causal_term_fails_in_the_setup_module(text: str) -> None:
    with pytest.raises(ForbiddenSetupVocabularyError):
        validate_setup_language(text)


def test_render_observation_revalidates_formatted_text() -> None:
    rendered = render_observation(ObservationCode.HIGH_PREVALENCE, prevalence_pct=80.0)
    assert "80%" in rendered
    validate_setup_language(rendered)


# -- 31: nenhum SetupScore -------------------------------------------------------


def test_no_setup_score_exists_anywhere() -> None:
    """Nenhum dos nomes rejeitados existe como SÍMBOLO real (classe/função/
    atributo do módulo) ou definição no código — só em prosa de docstring,
    explicando POR QUE foram rejeitados (mesma distinção que achado 3.12
    já faz entre prosa e código em test_cadence.py).
    """
    for banned in ("SetupScore", "setup_score", "overall_setup_grade", "setup_rating"):
        assert not hasattr(setup_finding_module, banned)

    source = inspect.getsource(setup_finding_module)
    for definition in (
        "class SetupScore",
        "def setup_score",
        "setup_score:",
        "setup_score =",
        "def overall_setup_grade",
        "overall_setup_grade:",
        "def setup_rating",
        "setup_rating:",
    ):
        assert definition not in source


# -- honestidade obrigatória: 80% vs 10% nunca vira "melhor"/"pior" -------------


def test_honesty_prevalence_never_becomes_a_causal_comparison() -> None:
    finding_a = _finding(
        subject=FindingSubject.talent_build("common-build"),
        observation=ObservationCode.HIGH_PREVALENCE,
        evidence_level=EvidenceLevel.STRONG,
        publicability=Publicability.PUBLISHABLE,
        prevalence=_prevalence(count=80, n_available=100, prevalence=0.80),
        actionable=False,
    )
    finding_b = _finding(
        subject=FindingSubject.trinket(555),
        observation=ObservationCode.LOW_PREVALENCE,
        evidence_level=EvidenceLevel.STRONG,
        publicability=Publicability.PUBLISHABLE,
        prevalence=_prevalence(count=10, n_available=100, prevalence=0.10),
        actionable=True,
    )

    assert finding_a.prevalence is not None and finding_a.prevalence.prevalence == 0.80
    assert finding_b.prevalence is not None and finding_b.prevalence.prevalence == 0.10

    text_a = render_observation(ObservationCode.HIGH_PREVALENCE, prevalence_pct=80.0)
    text_b = render_observation(ObservationCode.LOW_PREVALENCE)
    for text in (text_a, text_b):
        validate_setup_language(text)  # nunca levanta — vocabulário observacional

    # nenhuma das duas alegações é comparada uma com a outra, e nenhum
    # campo de ganho/score existe para permitir "A é melhor que B".
    assert not hasattr(finding_a, "score")
    assert not hasattr(finding_b, "score")
    assert not hasattr(finding_a, "estimated_gain_pct")


# -- 32-34: zero Store / zero WCL / zero Discord ---------------------------------


def test_module_has_zero_store_wcl_discord_dependency() -> None:
    """Verifica os IMPORTS reais via AST — não uma busca de substring, que
    bateria na prosa do docstring do módulo explicando o que ele NÃO
    importa (`import discord, JobQueue, Store` aparecem lá só como texto).
    """
    tree = ast.parse(inspect.getsource(setup_finding_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_prefixes = (
        "discord",
        "duckdb",
        "httpx",
        "aiohttp",
        "botgitgud.ingest.store",
        "botgitgud.wcl",
        "botgitgud.bot",
    )
    for name in imported:
        assert not any(name == p or name.startswith(p + ".") for p in forbidden_prefixes)

    source = inspect.getsource(setup_finding_module)
    assert "open(" not in source
    assert "Path(" not in source
