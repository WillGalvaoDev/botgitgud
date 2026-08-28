"""RP.1 — plain-text renderer for the SETUP section (RP.0's `ReportContract.
setup: SetupAnalysis | None`, SA.6).

**Not wired into `render_report`/`render_html_report` yet.** RP.1 is only
the renderer — RP.2 integrates it into the live report flow. Calling
`render_setup_section` today has zero effect on any existing report,
matching the pattern already established by SA.1-SA.6/EB.4-EB.5 (build the
engine in one ticket, wire it into the live path in the next).

**Observational language only, guaranteed, not just intended.** Every line
this module ever returns is built from SA.1's own approved vocabulary
(`setup_finding.render_observation`/`OBSERVATION_TEMPLATES`) and re-checked
through `setup_finding.validate_setup_language` before being returned —
defense in depth beyond the fact that the source templates are already
validated at import time in `setup_finding.py` itself. No new template
strings are invented here that bypass that guard.

**Plain text only — audits the historical Discord-HTML bug directly.**
`render_header_and_top3` (report/text.py) is what actually reaches a
Discord message as inline text; the full HTML report only ever goes out as
a file attachment (`bot/discord_bot.py`/`bot/worker.py`'s
`render_html_report` call), never as raw message text. A renderer that
emits `<strong>`/`<br>` tags into that inline-text path would show up to
the user as literal angle-bracket garbage — Discord does not interpret
arbitrary HTML. This module NEVER emits an HTML tag; guarded by test
(`test_render_setup_section_never_contains_html_tags`). An HTML-flavored
sibling (for `render_html_report`'s file-attachment path only) belongs to
a later, clearly-separated function if RP.2 needs one — the two must never
share implementation, or a tag from one path could leak into the other.
"""

from __future__ import annotations

from botgitgud.analysis.setup_analysis import SetupAnalysis
from botgitgud.analysis.setup_finding import (
    CaveatCode,
    FindingCategory,
    FindingSubject,
    ObservationCode,
    Publicability,
    SetupFinding,
    render_observation,
    validate_setup_language,
)

_SEPARATOR = "=" * 42


def _subject_label(subject: FindingSubject) -> str:
    if subject.category is FindingCategory.TALENT_BUILD:
        return "Build de talentos"
    if subject.category is FindingCategory.TRINKET:
        return f"Trinket {subject.item_id}"
    if subject.category is FindingCategory.TRINKET_PAIR:
        return f"Par de trinkets {subject.item_id}+{subject.item_id_other}"
    if subject.category is FindingCategory.SET_BONUS:
        return f"Set {subject.set_id}"
    return f"{subject.stat_name}"


def _render_observation_text(finding: SetupFinding) -> str:
    kwargs: dict[str, object] = {}
    if finding.observation is ObservationCode.HIGH_PREVALENCE and finding.prevalence is not None:
        kwargs["prevalence_pct"] = finding.prevalence.prevalence * 100
    return render_observation(finding.observation, **kwargs)


def _prevalence_fragment(finding: SetupFinding) -> str | None:
    if finding.prevalence is None:
        return None
    pct = finding.prevalence.prevalence * 100
    return f"observado em {pct:.0f}% da amostra disponível (n={finding.prevalence.n_available})"


def _distribution_fragment(finding: SetupFinding) -> str | None:
    if finding.distribution is None or finding.distribution.player_value is None:
        return None
    d = finding.distribution
    parts = [f"seu valor: {d.player_value:.0f}"]
    if d.benchmark.p25 is not None and d.benchmark.p75 is not None:
        parts.append(f"faixa interquartil observada: {d.benchmark.p25:.0f}-{d.benchmark.p75:.0f}")
    elif d.benchmark.median is not None:
        parts.append(f"mediana observada: {d.benchmark.median:.0f}")
    return " | ".join(parts)


def _render_finding_line(finding: SetupFinding) -> str:
    label = _subject_label(finding.subject)
    parts = [_render_observation_text(finding)]
    extra = _prevalence_fragment(finding) or _distribution_fragment(finding)
    if extra:
        parts.append(extra)
    if CaveatCode.PARTIAL_SETUP_COVERAGE in finding.caveats:
        parts.append("cobertura parcial de dados de setup na amostra")
    line = f"**{label}**: " + " — ".join(parts)
    validate_setup_language(line)
    return line


def render_setup_section(setup: SetupAnalysis | None) -> list[str]:
    """`None` (benchmark not yet built / not requested — RP.2's degradation
    concern, not this function's) and "nothing publishable" both render as
    an empty section — never a placeholder line pretending there's data.
    `HIDDEN`-publicability findings (missing player setup, missing
    benchmark, missing category data — all of SA.1's own missing-data
    factories) are never rendered here; `CAUTION` findings ARE rendered,
    using their own `INSUFFICIENT_EVIDENCE` template text (SA.2-SA.5 never
    produce a `CAUTION`-publicability finding under any other observation).
    """
    if setup is None:
        return []
    visible = [f for f in setup.findings if f.publicability is not Publicability.HIDDEN]
    if not visible:
        return []

    lines = ["", "🧩 **SETUP**", _SEPARATOR]
    for finding in visible:
        lines.append(_render_finding_line(finding))

    disclaimers: list[str] = []
    if any(CaveatCode.TALENT_NAMES_UNRESOLVED in f.caveats for f in visible):
        disclaimers.append(
            "Nomes de talentos ainda não são resolvidos — identificados por nó/rank."
        )
    if any(CaveatCode.RAW_RATING_ONLY in f.caveats for f in visible):
        disclaimers.append(
            "Secondary stats mostrados como rating bruto, sem conversão para porcentagem."
        )
    if disclaimers:
        lines.append("")
        for disclaimer in disclaimers:
            validate_setup_language(disclaimer)
        lines.extend(disclaimers)

    lines.append(_SEPARATOR)
    return lines
