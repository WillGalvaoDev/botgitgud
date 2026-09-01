from __future__ import annotations

import ast
import inspect

from hypothesis import given, settings
from hypothesis import strategies as st

from botgitgud.analysis.findings import Finding
from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.analysis.setup_finding import (
    BenchmarkSampleRef,
    EvidenceLevel,
    FindingSubject,
    ObservationCode,
    Publicability,
    SetupFinding,
)
from botgitgud.report import discord_summary as discord_summary_module
from botgitgud.report.contract import ConfidenceSummary, ExecutionSection, ReportContract
from botgitgud.report.discord_summary import (
    MAX_DISCORD_REPORT_SUMMARY,
    clamp_text,
    render_discord_summary,
)
from botgitgud.report.text import ReportHeader, _fmt_dps, _fmt_percentile

_SAMPLE = BenchmarkSampleRef(benchmark_id="Warlock/Demonology/1/1/1/v1", band_name=None)
_REALISTIC_URL = "https://botgitgud.duckdns.org/r/" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P" + "q6r"


# -- factories ----------------------------------------------------------------------


def _header(**overrides: object) -> ReportHeader:
    defaults: dict[str, object] = {
        "char_name": "Zarad",
        "boss_name": "Fallen-King Salhadaar",
        "class_name": "Warlock",
        "spec": "Demonology",
        "reference_n": 20,
        "duration_min_s": 300.0,
        "duration_max_s": 360.0,
        "player_dps": 108297.0,
        "player_percentile": 57.0,
        "cohort_warnings": (),
        "matched_covariates": (),
        "relaxed_covariates": (),
    }
    defaults.update(overrides)
    return ReportHeader(**defaults)  # type: ignore[arg-type]


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "kind": "ABILITY_GAP",
        "title": "Use Fel Devastation on cooldown",
        "detail": "detail",
        "estimated_gain_pct": 3.2,
        "confidence": "alta",
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def _confidence(**overrides: object) -> ConfidenceSummary:
    defaults: dict[str, object] = {
        "reference_pool_members": 40,
        "matched_cohort_members": 20,
        "cohort_warnings": (),
        "matched_covariates": (),
        "relaxed_covariates": (),
    }
    defaults.update(overrides)
    return ConfidenceSummary(**defaults)  # type: ignore[arg-type]


def _execution(n: int = 3, **overrides: object) -> ExecutionSection:
    defaults: dict[str, object] = {
        "comparisons": tuple(object() for _ in range(n)),
        "performance": None,
        "dps_gap": None,
    }
    defaults.update(overrides)
    return ExecutionSection(**defaults)  # type: ignore[arg-type]


def _setup_finding(**overrides: object) -> SetupFinding:
    defaults: dict[str, object] = {
        "subject": FindingSubject.talent_build("1:1"),
        "observation": ObservationCode.MATCHES_COMMON_PATTERN,
        "evidence_level": EvidenceLevel.STRONG,
        "publicability": Publicability.PUBLISHABLE,
        "sample": _SAMPLE,
    }
    defaults.update(overrides)
    return SetupFinding(**defaults)  # type: ignore[arg-type]


def _setup_analysis(findings: tuple[SetupFinding, ...] = (), **overrides: object) -> SetupAnalysis:
    defaults: dict[str, object] = {
        "benchmark_id": "Warlock/Demonology/1/1/1/v1",
        "findings": findings,
        "player_setup_available": True,
        "benchmark_available": True,
    }
    defaults.update(overrides)
    return SetupAnalysis(**defaults)  # type: ignore[arg-type]


def _contract(**overrides: object) -> ReportContract:
    defaults: dict[str, object] = {
        "resultado": _header(),
        "setup": None,
        "execucao": _execution(),
        "top_actions": (_finding(),),
        "confianca": _confidence(),
        "manifest": None,
    }
    defaults.update(overrides)
    return ReportContract(**defaults)  # type: ignore[arg-type]


# -- 1. typical case ------------------------------------------------------------------


def test_typical_case_fits_and_looks_sane() -> None:
    contract = _contract(
        top_actions=(
            _finding(title="Use Fel Devastation on cooldown", estimated_gain_pct=3.2),
            _finding(title="Reduce downtime after movement", estimated_gain_pct=1.8),
        ),
        setup=_setup_analysis(findings=(_setup_finding(),)),
    )
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert "Zarad" in result
    assert "Fallen-King Salhadaar" in result
    assert _REALISTIC_URL in result


# -- 2-5. exact Top 3 counts: 0/1/2/3 --------------------------------------------------


def test_zero_top_actions() -> None:
    contract = _contract(top_actions=())
    result = render_discord_summary(contract)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert "Nenhum achado" in result


def test_one_top_action() -> None:
    contract = _contract(top_actions=(_finding(title="A"),))
    result = render_discord_summary(contract)
    assert "1. **A**" in result
    assert "2." not in result.split("Top 3")[1].split("\n\n")[0]


def test_two_top_actions() -> None:
    contract = _contract(top_actions=(_finding(title="A"), _finding(title="B")))
    result = render_discord_summary(contract)
    assert "1. **A**" in result
    assert "2. **B**" in result


def test_three_top_actions() -> None:
    contract = _contract(
        top_actions=(_finding(title="A"), _finding(title="B"), _finding(title="C"))
    )
    result = render_discord_summary(contract)
    assert "1. **A**" in result
    assert "2. **B**" in result
    assert "3. **C**" in result


def test_more_than_three_top_actions_only_shows_first_three() -> None:
    contract = _contract(
        top_actions=(
            _finding(title="A"),
            _finding(title="B"),
            _finding(title="C"),
            _finding(title="D"),
        )
    )
    result = render_discord_summary(contract)
    assert "1. **A**" in result
    assert "2. **B**" in result
    assert "3. **C**" in result
    assert "**D**" not in result


# -- 6-7. numeric formatting matches report/text.py's convention ----------------------


def test_dps_formatting_matches_existing_convention() -> None:
    header = _header(player_dps=108297.4)
    contract = _contract(resultado=header)
    result = render_discord_summary(contract)
    assert _fmt_dps(108297.4) in result


def test_percentile_formatting_matches_existing_convention() -> None:
    header = _header(player_percentile=57.4)
    contract = _contract(resultado=header)
    result = render_discord_summary(contract)
    assert _fmt_percentile(57.4) in result


def test_estimated_gain_pct_formatted_with_sign_and_pp_suffix() -> None:
    contract = _contract(top_actions=(_finding(title="A", estimated_gain_pct=3.2),))
    result = render_discord_summary(contract)
    assert "+3.2pp" in result


def test_negative_estimated_gain_pct_not_double_signed() -> None:
    contract = _contract(top_actions=(_finding(title="A", estimated_gain_pct=-3.2),))
    result = render_discord_summary(contract)
    assert "-3.2pp" in result
    assert "+-3.2pp" not in result


def test_missing_dps_and_percentile_render_as_nd() -> None:
    header = _header(player_dps=None, player_percentile=None)
    contract = _contract(resultado=header)
    result = render_discord_summary(contract)
    assert "n/d" in result


# -- 8-9. cohort/confidence -------------------------------------------------------------


def test_cohort_size_shown() -> None:
    contract = _contract(confianca=_confidence(matched_cohort_members=27))
    result = render_discord_summary(contract)
    assert "27 logs" in result


def test_cohort_falls_back_to_reference_pool_when_matched_absent() -> None:
    contract = _contract(
        confianca=_confidence(matched_cohort_members=None, reference_pool_members=15)
    )
    result = render_discord_summary(contract)
    assert "15 logs" in result


def test_cohort_size_absent_renders_honest_placeholder() -> None:
    contract = _contract(
        confianca=_confidence(matched_cohort_members=None, reference_pool_members=None)
    )
    result = render_discord_summary(contract)
    assert "amostra indisponível" in result


def test_small_sample_warning_reflected_in_confidence_phrase() -> None:
    contract = _contract(confianca=_confidence(cohort_warnings=("amostra pequena",)))
    result = render_discord_summary(contract)
    assert "confiança baixa" in result


def test_no_warnings_renders_normal_confidence() -> None:
    contract = _contract(confianca=_confidence(cohort_warnings=()))
    result = render_discord_summary(contract)
    assert "confiança normal" in result


def test_confidence_never_lists_long_covariate_lists() -> None:
    contract = _contract(
        confianca=_confidence(
            matched_covariates=("item_level", "talent_cluster", "duration"),
            relaxed_covariates=("weapon_enchant",),
        )
    )
    result = render_discord_summary(contract)
    assert "item_level" not in result
    assert "talent_cluster" not in result
    assert "weapon_enchant" not in result


# -- 10. Setup summary: honest, no gain, no causality, no global score ----------------


def test_setup_summary_no_data() -> None:
    contract = _contract(setup=None)
    result = render_discord_summary(contract)
    assert "Setup: sem dados" in result


def test_setup_summary_counts_publishable_observations() -> None:
    setup = _setup_analysis(findings=(_setup_finding(), _setup_finding()))
    contract = _contract(setup=setup)
    result = render_discord_summary(contract)
    assert "2 observações" in result


def test_setup_summary_hides_hidden_findings() -> None:
    setup = _setup_analysis(
        findings=(
            _setup_finding(publicability=Publicability.PUBLISHABLE),
            _setup_finding(publicability=Publicability.HIDDEN),
        )
    )
    contract = _contract(setup=setup)
    result = render_discord_summary(contract)
    assert "1 observação" in result


def test_setup_summary_all_hidden_says_insufficient() -> None:
    setup = _setup_analysis(findings=(_setup_finding(publicability=Publicability.HIDDEN),))
    contract = _contract(setup=setup)
    result = render_discord_summary(contract)
    assert "sem dados suficientes" in result


def test_setup_summary_notes_divergence_count() -> None:
    setup = _setup_analysis(
        findings=(
            _setup_finding(observation=ObservationCode.DIFFERS_FROM_COMMON_PATTERN),
            _setup_finding(observation=ObservationCode.MATCHES_COMMON_PATTERN),
        )
    )
    contract = _contract(setup=setup)
    result = render_discord_summary(contract)
    assert "divergem do padrão comum" in result


def test_setup_summary_never_contains_estimated_gain_or_score() -> None:
    setup = _setup_analysis(findings=(_setup_finding(),) * 5)
    contract = _contract(setup=setup)
    result = render_discord_summary(contract)
    assert "%" not in result.split("Setup:")[1].split("\n")[0]
    assert "score" not in result.lower()


def test_setup_summary_source_never_reads_estimated_gain_pct() -> None:
    """Structural guarantee for item 35: `_setup_summary`'s own CODE never
    accesses `.estimated_gain_pct` (the docstring may still name the field
    to explain why it is absent — that is documentation, not a read).
    """
    source = inspect.getsource(discord_summary_module._setup_summary)
    assert ".estimated_gain_pct" not in source

    tree = ast.parse(source)
    accessed_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "estimated_gain_pct" not in accessed_attrs
    assert "score" not in accessed_attrs


# -- 11. Execution summary: count only, never repeats Top 3 ---------------------------


def test_execution_summary_shows_count() -> None:
    contract = _contract(execucao=_execution(n=12))
    result = render_discord_summary(contract)
    assert "Execução: 12 achados" in result


def test_execution_summary_singular_noun() -> None:
    contract = _contract(execucao=_execution(n=1))
    result = render_discord_summary(contract)
    assert "Execução: 1 achado" in result
    assert "1 achados" not in result


def test_execution_summary_zero() -> None:
    contract = _contract(execucao=_execution(n=0))
    result = render_discord_summary(contract)
    assert "Execução: 0 achados" in result


# -- 12-13. link block: present/never truncated, absent/honest fallback ---------------


def test_url_present_is_never_truncated() -> None:
    contract = _contract()
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert f"[Ver relatório completo]({_REALISTIC_URL})" in result


def test_url_absent_uses_honest_fallback() -> None:
    contract = _contract()
    result = render_discord_summary(contract, report_url=None)
    assert "temporariamente indisponível" in result
    assert "http" not in result


def test_url_too_long_falls_back_instead_of_truncating() -> None:
    huge_url = "https://example.com/" + "x" * 1000
    contract = _contract()
    result = render_discord_summary(contract, report_url=huge_url)
    assert huge_url not in result
    assert "temporariamente indisponível" in result
    # never emits a truncated/broken markdown link
    assert "](https://example.com/" not in result


def test_url_with_unsafe_characters_falls_back() -> None:
    unsafe_url = "https://example.com/r/token with space)"
    contract = _contract()
    result = render_discord_summary(contract, report_url=unsafe_url)
    assert unsafe_url not in result
    assert "temporariamente indisponível" in result


def test_url_not_http_scheme_falls_back() -> None:
    contract = _contract()
    result = render_discord_summary(contract, report_url="javascript:alert(1)")
    assert "javascript:" not in result
    assert "temporariamente indisponível" in result


# -- 14-19. giant fields --------------------------------------------------------------


def test_giant_player_name_handled() -> None:
    header = _header(char_name="Z" * 10_000)
    contract = _contract(resultado=header)
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


def test_giant_boss_name_handled() -> None:
    header = _header(boss_name="B" * 10_000)
    contract = _contract(resultado=header)
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


def test_giant_single_top_action_title_handled() -> None:
    contract = _contract(top_actions=(_finding(title="T" * 10_000),))
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


def test_giant_three_top_action_titles_handled() -> None:
    contract = _contract(
        top_actions=(
            _finding(title="A" * 5_000),
            _finding(title="B" * 5_000),
            _finding(title="C" * 5_000),
        )
    )
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


def test_giant_setup_handled() -> None:
    setup = _setup_analysis(findings=(_setup_finding(),) * 500)
    contract = _contract(setup=setup)
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


def test_giant_execution_handled() -> None:
    contract = _contract(execucao=_execution(n=50_000))
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


# -- 20-21. unicode / CJK ---------------------------------------------------------------


def test_unicode_and_emoji_in_names_handled() -> None:
    header = _header(char_name="Zàràd🔥💀", boss_name="Königin Fallschnee❄️")
    contract = _contract(resultado=header)
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


def test_cjk_characters_handled() -> None:
    header = _header(char_name="召唤师扎拉德", boss_name="堕落之王萨哈达尔")
    contract = _contract(resultado=header, top_actions=(_finding(title="使用地狱吞噬冷却技能"),))
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result


# -- 22. hostile markdown escaped ------------------------------------------------------


def test_hostile_markdown_in_title_is_escaped() -> None:
    hostile = "**bold** _i_ `code` ~~s~~ | [link](url)"
    contract = _contract(top_actions=(_finding(title=hostile),))
    result = render_discord_summary(contract)
    assert "\\*\\*bold\\*\\*" in result
    assert "\\[link\\]\\(url\\)" in result


def test_hostile_markdown_in_player_name_is_escaped() -> None:
    header = _header(char_name="**Zarad** `[hax]`")
    contract = _contract(resultado=header)
    result = render_discord_summary(contract)
    assert "\\*\\*Zarad\\*\\*" in result
    assert "\\`\\[hax\\]\\`" in result


# -- 23-27. mention neutralization ------------------------------------------------------


def test_at_everyone_neutralized() -> None:
    header = _header(char_name="@everyone")
    contract = _contract(resultado=header)
    result = render_discord_summary(contract)
    assert "@everyone" not in result
    assert "everyone" in result


def test_at_here_neutralized() -> None:
    header = _header(boss_name="@here")
    contract = _contract(resultado=header)
    result = render_discord_summary(contract)
    assert "@here" not in result
    assert "here" in result


def test_user_mention_neutralized() -> None:
    contract = _contract(top_actions=(_finding(title="ping <@123456789012345678>"),))
    result = render_discord_summary(contract)
    assert "<@123456789012345678>" not in result
    assert "123456789012345678" in result


def test_role_mention_neutralized() -> None:
    contract = _contract(top_actions=(_finding(title="ping <@&123456789012345678>"),))
    result = render_discord_summary(contract)
    assert "<@&123456789012345678>" not in result


def test_channel_mention_neutralized() -> None:
    contract = _contract(top_actions=(_finding(title="see <#123456789012345678>"),))
    result = render_discord_summary(contract)
    assert "<#123456789012345678>" not in result


# -- 28. link-injection in user data neutralized ---------------------------------------


def test_markdown_link_injection_in_title_neutralized() -> None:
    hostile = "click [here](https://evil.example/steal-token)"
    contract = _contract(top_actions=(_finding(title=hostile),))
    result = render_discord_summary(contract)
    assert "[here](https://evil.example/steal-token)" not in result
    assert "\\[here\\]\\(https://evil.example/steal-token\\)" in result


# -- 29. empty states never crash --------------------------------------------------------


def test_empty_names_do_not_crash() -> None:
    header = _header(char_name="", boss_name="", class_name="", spec="")
    contract = _contract(resultado=header)
    result = render_discord_summary(contract)
    assert isinstance(result, str)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY


def test_empty_top_actions_setup_execution_confidence() -> None:
    contract = _contract(
        top_actions=(),
        setup=_setup_analysis(findings=()),
        execucao=_execution(n=0),
        confianca=_confidence(
            matched_cohort_members=None, reference_pool_members=None, cohort_warnings=()
        ),
    )
    result = render_discord_summary(contract, report_url=None)
    assert isinstance(result, str)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY


# -- 30. pathological: everything long simultaneously -----------------------------------


def test_worst_case_all_fields_long() -> None:
    header = _header(
        char_name="Z" * 10_000,
        boss_name="B" * 10_000,
        class_name="C" * 10_000,
        spec="D" * 10_000,
        player_dps=999_999_999.9,
        player_percentile=99.9,
    )
    findings = tuple(
        _finding(title="T" * 10_000, estimated_gain_pct=99.9 * sign) for sign in (1, -1, 1)
    )
    setup = _setup_analysis(findings=(_setup_finding(),) * 500)
    execution = _execution(n=50_000)
    confidence = _confidence(
        matched_cohort_members=999_999_999,
        reference_pool_members=999_999_999,
        cohort_warnings=("amostra pequena",) * 50,
        matched_covariates=("x",) * 50,
        relaxed_covariates=("y",) * 50,
    )
    contract = _contract(
        resultado=header,
        top_actions=findings,
        setup=setup,
        execucao=execution,
        confianca=confidence,
    )
    result = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY
    assert _REALISTIC_URL in result, "link block must survive even the worst-case clamp"
    print(f"[CL.4] worst-case pathological size: {len(result)} chars")


# -- 31-32. property-based proofs --------------------------------------------------------


_text_strategy = st.text(min_size=0, max_size=3000)
_gain_strategy = st.one_of(
    st.none(), st.floats(allow_nan=False, allow_infinity=False, min_value=-999, max_value=999)
)
_url_or_none_strategy = st.one_of(st.none(), st.text(min_size=0, max_size=2000))
_int_or_none_strategy = st.one_of(st.none(), st.integers(min_value=-(10**18), max_value=10**18))


@given(
    char_name=_text_strategy,
    boss_name=_text_strategy,
    class_name=_text_strategy,
    spec=_text_strategy,
    title1=_text_strategy,
    title2=_text_strategy,
    title3=_text_strategy,
    gain=_gain_strategy,
    report_url=_url_or_none_strategy,
    matched_cohort_members=_int_or_none_strategy,
    reference_pool_members=_int_or_none_strategy,
)
@settings(max_examples=250)
def test_property_always_within_budget(
    char_name: str,
    boss_name: str,
    class_name: str,
    spec: str,
    title1: str,
    title2: str,
    title3: str,
    gain: float | None,
    report_url: str | None,
    matched_cohort_members: int | None,
    reference_pool_members: int | None,
) -> None:
    header = _header(char_name=char_name, boss_name=boss_name, class_name=class_name, spec=spec)
    findings = tuple(_finding(title=t, estimated_gain_pct=gain) for t in (title1, title2, title3))
    contract = _contract(
        resultado=header,
        top_actions=findings,
        confianca=_confidence(
            matched_cohort_members=matched_cohort_members,
            reference_pool_members=reference_pool_members,
        ),
    )
    result = render_discord_summary(contract, report_url=report_url)
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY


_url_token_strategy = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=200,
)


@given(token=_url_token_strategy)
@settings(max_examples=100)
def test_property_valid_url_always_preserved_verbatim(token: str) -> None:
    report_url = f"https://botgitgud.duckdns.org/r/{token}"
    contract = _contract()
    result = render_discord_summary(contract, report_url=report_url)
    assert f"🔗 [Ver relatório completo]({report_url})" in result
    assert len(result) <= MAX_DISCORD_REPORT_SUMMARY


# -- 33. determinism --------------------------------------------------------------------


def test_deterministic_same_input_same_output() -> None:
    contract = _contract(
        setup=_setup_analysis(findings=(_setup_finding(),)),
        top_actions=(_finding(title="A"), _finding(title="B")),
    )
    a = render_discord_summary(contract, report_url=_REALISTIC_URL)
    b = render_discord_summary(contract, report_url=_REALISTIC_URL)
    assert a == b


# -- 34-35. no SetupFinding leakage into Top 3, no estimated_gain on Setup --------------


def test_top_actions_block_only_reads_finding_fields() -> None:
    """Structural guarantee for item 34: `_top_actions_block`'s own source
    only reads `.title`/`.estimated_gain_pct` — it never imports or
    references `SetupFinding`, trusting the contract's own type/runtime
    guard (RP.0) instead of re-implementing the execution-only rule here.
    """
    source = inspect.getsource(discord_summary_module._top_actions_block)
    assert "SetupFinding" not in source


def test_setup_summary_never_shown_in_top_actions_block() -> None:
    setup = _setup_analysis(findings=(_setup_finding(),))
    contract = _contract(setup=setup, top_actions=(_finding(title="Execution finding"),))
    result = render_discord_summary(contract)
    top3_block = result.split("Top 3")[1].split("📋")[0]
    assert "observação" not in top3_block


# -- 36. no HTML boilerplate ever emitted ------------------------------------------------


def test_never_emits_html_boilerplate() -> None:
    contract = _contract(
        top_actions=(_finding(title="<div>fake</div>"),),
        resultado=_header(char_name="<script>alert(1)</script>"),
    )
    result = render_discord_summary(contract)
    assert "<!DOCTYPE" not in result
    assert "<html" not in result.lower()
    assert "<style" not in result.lower()


# -- 37. renderer never imports discord.py/Store/WCL/filesystem/HTTP --------------------


def test_zero_discord_store_wcl_filesystem_http_imports() -> None:
    tree = ast.parse(inspect.getsource(discord_summary_module))
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
        "requests",
        "botgitgud.ingest.store",
        "botgitgud.wcl",
        "botgitgud.bot",
    )
    for name in imported:
        assert not any(name == p or name.startswith(p + ".") for p in forbidden_prefixes), name

    source = inspect.getsource(discord_summary_module)
    assert "open(" not in source
    assert "Path(" not in source
    assert "socket" not in source


# -- chunk_report_for_discord audit -------------------------------------------------------


def test_chunk_report_for_discord_was_removed_as_confirmed_dead_code() -> None:
    """CL.4 audited `chunk_report_for_discord` (report/text.py) and found
    zero production call sites, only 4 test call sites — left untouched
    then, per explicit user instruction ("não faça limpeza ampla"). CL.5
    reaudited it after wiring the new link-based delivery (which never
    calls it either) and confirmed it was STILL clearly dead — the ticket
    explicitly authorized removing it at that point, and it (plus
    `discord_chunk_max_len`, config.py) was removed. This test pins that
    it no longer exists, so a future reintroduction is deliberate, not
    silent.
    """
    import botgitgud.report.text as text_module

    assert not hasattr(text_module, "chunk_report_for_discord")
    source = inspect.getsource(discord_summary_module)
    assert "chunk_report_for_discord" not in source


# -- clamp_text edge cases ----------------------------------------------------------------


def test_clamp_text_max_chars_zero() -> None:
    assert clamp_text("hello", 0) == ""


def test_clamp_text_max_chars_one() -> None:
    assert clamp_text("hello", 1) == "…"


def test_clamp_text_max_chars_negative() -> None:
    assert clamp_text("hello", -5) == ""


def test_clamp_text_empty_string() -> None:
    assert clamp_text("", 10) == ""
    assert clamp_text("", 0) == ""


def test_clamp_text_exact_fit_no_ellipsis() -> None:
    assert clamp_text("hello", 5) == "hello"


def test_clamp_text_truncates_with_ellipsis() -> None:
    result = clamp_text("hello world", 5)
    assert len(result) == 5
    assert result.endswith("…")


def test_clamp_text_unicode_code_points() -> None:
    text = "😀😀😀😀😀"  # 5 code points, each len()==1 in Python str
    result = clamp_text(text, 3)
    assert len(result) == 3
    assert result.endswith("…")


def test_clamp_text_never_exceeds_max_chars() -> None:
    for n in range(0, 20):
        result = clamp_text("x" * 50, n)
        assert len(result) <= n
