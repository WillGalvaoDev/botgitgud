"""Deterministic, evidence-bounded Discord coaching answer.

This module only translates typed M28--M31 decisions.  All prose is selected
from finite tables; finding ``title`` and ``detail`` are never rendered.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from itertools import product
from types import MappingProxyType
from typing import Final

from botgitgud.analysis.dps_gap import AbilityGap
from botgitgud.analysis.findings import ExecutionFinding
from botgitgud.analysis.materiality import (
    MaterialCandidate,
    PositiveObservationBasis,
)
from botgitgud.analysis.performance_features import ScalarFinding
from botgitgud.analysis.remediation import (
    Remediation,
    RemediationBasis,
    RemediationCondition,
    RemediationKind,
)
from botgitgud.analysis.setup_finding import FindingCategory, ObservationCode, Publicability
from botgitgud.report.contract import ReportContract
from botgitgud.report.discord_markdown import safe_field

MAX_DISCORD_COACHING_ANSWER: Final = 1800
_MAX_ABILITY_NAME: Final = 72

# Every non-ability fragment below comes from a finite table, numeric fields
# are bounded by `_fmt_number`, and cohort sizes are emitted only up to four
# digits.  The only variable-length text is therefore the ability name, which
# `_sanitize_ability_name` limits to `_MAX_ABILITY_NAME`.  These deliberately
# conservative per-field budgets prove the Discord ceiling by construction;
# the worst-case test exercises all fields at their maximum simultaneously.
_CONCLUSION_BUDGET: Final = 160
_PRIORITY_BUDGET: Final = 380
_PRIORITY_HEADING_BUDGET: Final = 90
_POSITIVE_BUDGET: Final = 130
_SETUP_BUDGET: Final = 120
_BLOCK_SEPARATOR_BUDGET: Final = 6
COACHING_ANSWER_FIELD_BUDGET: Final = (
    _CONCLUSION_BUDGET
    + _PRIORITY_HEADING_BUDGET
    + 3 * _PRIORITY_BUDGET
    + _POSITIVE_BUDGET
    + _SETUP_BUDGET
    + _BLOCK_SEPARATOR_BUDGET
)

_CONCLUSIONS: Final[Mapping[tuple[str, bool], str]] = MappingProxyType(
    {
        ("red", False): "Seu dano ficou na faixa mais baixa da coorte comparável.",
        ("red", True): (
            "Seu dano ficou na faixa mais baixa da coorte comparável; "
            "estes são os ajustes que mais importam."
        ),
        ("yellow", False): "Seu dano ficou abaixo da maior parte da coorte comparável.",
        ("yellow", True): (
            "Seu dano ficou abaixo da maior parte da coorte comparável; "
            "estes são os ajustes que mais importam."
        ),
        ("green", False): "Seu dano ficou bem colocado na coorte comparável.",
        ("green", True): (
            "Seu dano ficou bem colocado na coorte comparável, "
            "com alguns ajustes específicos ainda relevantes."
        ),
        ("insufficient", False): (
            "A comparação disponível não sustenta uma prioridade de coaching."
        ),
        ("insufficient", True): (
            "A comparação geral é limitada, mas há ajustes específicos sustentados pelos dados."
        ),
        ("missing", False): (
            "A análise não encontrou uma prioridade de coaching sustentada pelos dados."
        ),
        ("missing", True): "A análise encontrou ajustes específicos sustentados pelos dados.",
    }
)

_IMPORTANCE: Final[Mapping[tuple[str, str], str]] = MappingProxyType(
    {
        ("red", "participation"): "É um desvio forte de participação.",
        ("yellow", "participation"): "É um desvio relevante de participação.",
        ("red", "output"): "É um desvio forte de resultado.",
        ("yellow", "output"): "É um desvio relevante de resultado.",
        ("green", "participation"): "É uma observação de participação.",
        ("green", "output"): "É uma observação de resultado.",
        ("insufficient", "participation"): "É uma observação de participação.",
        ("insufficient", "output"): "É uma observação de resultado.",
    }
)

_ACTION_BY_BASIS: Final[Mapping[RemediationBasis, str]] = MappingProxyType(
    {
        RemediationBasis.USE_COUNT: (
            "Compare a quantidade de usos com pulls parecidos e busque mais oportunidades de uso."
        ),
        RemediationBasis.AVERAGE_TARGET_DEFICIT: (
            "Revise quando vale direcionar essa habilidade a mais alvos."
        ),
        RemediationBasis.DAMAGE_PER_USE: (
            "Revise em conjunto o contexto da janela e seus buffs; "
            "os dados não separam qual deles pesou."
        ),
        RemediationBasis.VOLUME_AND_EFFICIENCY: (
            "Revise volume e eficiência em conjunto; os dados não isolam uma causa."
        ),
        RemediationBasis.UNPAIRED_BUFFS: ("Não há uma ação específica segura com esta comparação."),
        RemediationBasis.OBSERVED_DEATH: "Priorize completar a luta vivo.",
        RemediationBasis.ACTIVE_PARTICIPATION: (
            "Busque aumentar sua participação ativa ao longo da luta."
        ),
        RemediationBasis.AGGREGATE_RESOURCE_WASTE: (
            "Trabalhe para reduzir o desperdício agregado do recurso."
        ),
        RemediationBasis.UPTIME_QUANTILE: (
            "Busque aumentar o uptime; estes dados não mostram a causa do desvio."
        ),
        RemediationBasis.UNMAPPED_EVIDENCE: (
            "Não há uma ação específica segura com esta evidência."
        ),
    }
)
_NO_SPECIFIC_ACTION: Final = "Não há uma ação específica segura com esta evidência."


def _action_for_key(
    kind: RemediationKind,
    basis: RemediationBasis,
    condition: RemediationCondition | None,
) -> str:
    # ``condition`` is deliberately part of the lookup key even where the
    # evidence-bounded sentence is the same: the public table is total over
    # the complete enum product and cannot fall through to generated prose.
    del condition
    if kind is RemediationKind.NO_SPECIFIC_ACTION:
        return _NO_SPECIFIC_ACTION
    return _ACTION_BY_BASIS[basis]


RemediationKey = tuple[RemediationKind, RemediationBasis, RemediationCondition | None]
REMEDIATION_PHRASES: Final[Mapping[RemediationKey, str]] = MappingProxyType(
    {
        (kind, basis, condition): _action_for_key(kind, basis, condition)
        for kind, basis, condition in product(
            RemediationKind, RemediationBasis, (*RemediationCondition, None)
        )
    }
)
REMEDIATION_VOCABULARY: Final[frozenset[str]] = frozenset(REMEDIATION_PHRASES.values())

_POSITIVE_PHRASES: Final[Mapping[PositiveObservationBasis, str]] = MappingProxyType(
    {
        PositiveObservationBasis.NO_DEATH: "Ponto positivo: você completou a luta sem morrer.",
        PositiveObservationBasis.ACTIVE_TIME: (
            "Ponto positivo: seu tempo ativo ficou bem colocado na coorte."
        ),
        PositiveObservationBasis.OVERALL_STANDING: (
            "Ponto positivo: seu resultado geral ficou bem colocado na coorte."
        ),
        PositiveObservationBasis.ABILITY_ABOVE_COHORT: (
            "Ponto positivo: {ability} ficou acima da coorte."
        ),
    }
)

_SETUP_PHRASES: Final[Mapping[tuple[FindingCategory, ObservationCode], str]] = MappingProxyType(
    {
        (FindingCategory.TALENT_BUILD, ObservationCode.DIFFERS_FROM_COMMON_PATTERN): (
            "Setup: vale revisar seus talentos, que diferem do padrão comum observado."
        ),
        (FindingCategory.TALENT_BUILD, ObservationCode.LOW_PREVALENCE): (
            "Setup: vale revisar seus talentos, pouco frequentes na amostra observada."
        ),
        (FindingCategory.TRINKET, ObservationCode.DIFFERS_FROM_COMMON_PATTERN): (
            "Setup: vale revisar seu trinket, que difere do padrão comum observado."
        ),
        (FindingCategory.TRINKET, ObservationCode.LOW_PREVALENCE): (
            "Setup: vale revisar seu trinket, pouco frequente na amostra observada."
        ),
        (FindingCategory.TRINKET_PAIR, ObservationCode.DIFFERS_FROM_COMMON_PATTERN): (
            "Setup: vale revisar seu par de trinkets, que difere do padrão comum observado."
        ),
        (FindingCategory.TRINKET_PAIR, ObservationCode.LOW_PREVALENCE): (
            "Setup: vale revisar seu par de trinkets, pouco frequente na amostra observada."
        ),
        (FindingCategory.SET_BONUS, ObservationCode.DIFFERS_FROM_COMMON_PATTERN): (
            "Setup: vale revisar seu bônus de conjunto, que difere do padrão comum observado."
        ),
        (FindingCategory.SET_BONUS, ObservationCode.LOW_PREVALENCE): (
            "Setup: vale revisar seu bônus de conjunto, pouco frequente na amostra observada."
        ),
        (FindingCategory.SECONDARY_STATS, ObservationCode.DIFFERS_FROM_COMMON_PATTERN): (
            "Setup: vale revisar seus atributos secundários, que diferem do padrão comum observado."
        ),
        (FindingCategory.SECONDARY_STATS, ObservationCode.LOW_PREVALENCE): (
            "Setup: vale revisar seus atributos secundários, pouco frequentes na amostra observada."
        ),
    }
)

_COUNTED_MEASUREMENT_PHRASES: Final[Mapping[tuple[str, bool], str]] = MappingProxyType(
    {
        ("DEATH", True): "Foi medida {value} morte",
        ("DEATH", False): "Foram medidas {value} mortes",
    }
)


def render_remediation(remediation: Remediation) -> str:
    """Select one action from the complete, finite M28 vocabulary."""
    return REMEDIATION_PHRASES[(remediation.kind, remediation.basis, remediation.condition)]


def _fmt_number(value: float) -> str:
    if not math.isfinite(value):
        return "indisponível"
    if abs(value) >= 10_000:
        return f"{value:.2g}".replace(".", ",")
    rounded = round(value)
    if abs(value - rounded) < 1e-9:
        return str(rounded)
    return f"{value:.1f}".replace(".", ",")


def _spell_name(contract: ReportContract, spell_id: int | None) -> str | None:
    if spell_id is None:
        return None
    gap = contract.execucao.dps_gap
    if gap is not None:
        for ability in gap.abilities:
            if ability.spell.spell_id == spell_id:
                return ability.spell.name
    performance = contract.execucao.performance
    if performance is not None:
        for uptime in performance.uptimes:
            if uptime.spell.spell_id == spell_id:
                return uptime.spell.name
    return None


def _sanitize_ability_name(name: str) -> str:
    # The shared sanitizer handles Discord syntax and mentions. Breaking a
    # URL scheme additionally guarantees that the no-URL delivery contract
    # also holds for hostile upstream spell names.
    return safe_field(name, _MAX_ABILITY_NAME).replace("://", ":\u200b//")


def _safe_spell(contract: ReportContract, spell_id: int | None) -> str:
    name = _spell_name(contract, spell_id)
    if name is None:
        return "Esta habilidade"
    return _sanitize_ability_name(name)


def _ability_gap(contract: ReportContract, spell_id: int | None) -> AbilityGap | None:
    if spell_id is None:
        return None
    gap = contract.execucao.dps_gap
    if gap is None:
        return None
    for ability in gap.abilities:
        if ability.spell.spell_id == spell_id:
            return ability
    return None


def _cohort_position_clause(cohort_share: ScalarFinding | None) -> str | None:
    """RB-4: the measured position among comparable logs, stated from the
    same graded scalar `AbilityGap.cohort_share` already carries -- the
    identical declared phrasing `_conclusion` uses for overall standing,
    never a new free-form phrase and never `estimated_gain_pct`.
    """
    if cohort_share is None:
        return None
    n = cohort_share.stats.n
    quantile = cohort_share.quantile
    if quantile is None or not (0 < n <= 9999):
        return None
    if quantile <= 1 / n:
        return f"o mais baixo dos {n} comparáveis"
    if quantile <= 0.25:
        return f"entre os 25% mais baixos dos {n} comparáveis"
    return None


def _measured(candidate: MaterialCandidate, contract: ReportContract) -> str:
    finding = candidate.finding
    subject = candidate.remediation.subject
    spell_id = subject if isinstance(subject, int) else None
    ability = _safe_spell(contract, spell_id)

    if isinstance(finding, ExecutionFinding):
        scalar = finding.finding
        median = scalar.stats.p50
        if finding.category == "DEATH":
            measured = _COUNTED_MEASUREMENT_PHRASES[
                (finding.category, scalar.user_value == 1)
            ].format(value=_fmt_number(scalar.user_value))
            if median is not None:
                return f"{measured}, ante mediana {_fmt_number(median)} na coorte."
            return f"{measured}."
        if finding.category == "ACTIVE_TIME":
            user = _fmt_number(scalar.user_value * 100)
            if median is not None:
                return f"Seu tempo ativo foi {user}%, ante {_fmt_number(median * 100)}% na coorte."
            return f"Seu tempo ativo foi {user}%."
        if finding.category == "WASTE":
            if median is not None:
                return (
                    f"Seu desperdício agregado foi {_fmt_number(scalar.user_value)}, "
                    f"ante {_fmt_number(median)} na coorte."
                )
            return f"Seu desperdício agregado foi {_fmt_number(scalar.user_value)}."

    basis = candidate.remediation.basis
    gap = _ability_gap(contract, spell_id)
    position = _cohort_position_clause(gap.cohort_share) if gap is not None else None
    happened = {
        RemediationBasis.USE_COUNT: (
            f"O uso de {ability} foi {position}."
            if position is not None
            else f"A quantidade de usos de {ability} ficou abaixo da coorte."
        ),
        RemediationBasis.AVERAGE_TARGET_DEFICIT: (
            f"{ability} atingiu menos alvos em média do que a coorte."
        ),
        RemediationBasis.DAMAGE_PER_USE: (
            f"O dano por uso de {ability} foi {position}, sem distinguir janela de buffs próprios."
            if position is not None
            else (
                f"O dano por uso de {ability} ficou abaixo da coorte, "
                "sem distinguir janela de buffs próprios."
            )
        ),
        RemediationBasis.VOLUME_AND_EFFICIENCY: (
            f"{ability} ficou abaixo em volume e eficiência, sem uma causa única medida."
        ),
        RemediationBasis.UNPAIRED_BUFFS: (
            f"A comparação de {ability} não separou buffs equivalentes."
        ),
        RemediationBasis.UPTIME_QUANTILE: (
            f"O uptime de {ability} foi {position}; a causa não foi medida."
            if position is not None
            else f"O uptime de {ability} ficou abaixo da coorte; a causa não foi medida."
        ),
        RemediationBasis.UNMAPPED_EVIDENCE: "A evidência medida não permite detalhar o desvio.",
    }
    return happened.get(basis, "A análise mediu um desvio de execução.")


def _semantic_layer(candidate: MaterialCandidate) -> str:
    finding = candidate.finding
    return (
        "participation"
        if isinstance(finding, ExecutionFinding) and finding.category in {"DEATH", "ACTIVE_TIME"}
        else "output"
    )


def _priority(candidate: MaterialCandidate, contract: ReportContract, marker: str) -> str:
    importance = _IMPORTANCE[(candidate.grade, _semantic_layer(candidate))]
    return (
        f"{marker} {_measured(candidate, contract)} {importance} "
        f"{render_remediation(candidate.remediation)}"
    )


def _conclusion(contract: ReportContract, has_priorities: bool) -> str:
    conclusion = contract.conclusion
    grade = conclusion.standing.grade if conclusion is not None else "missing"
    base = _CONCLUSIONS[(grade, has_priorities)]
    if conclusion is None or grade not in {"red", "yellow"}:
        return base
    n = conclusion.sample.matched_n
    quantile = conclusion.standing.quantile
    if 0 < n <= 9999 and quantile is not None and quantile <= 1 / n:
        return (
            f"Seu dano foi o mais baixo dos {n} comparáveis; "
            "estes são os ajustes que mais importam."
            if has_priorities
            else f"Seu dano foi o mais baixo dos {n} comparáveis."
        )
    if grade == "yellow" and 0 < n <= 9999:
        return (
            f"Seu dano ficou entre os 25% mais baixos dos {n} comparáveis; "
            "estes são os ajustes que mais importam."
            if has_priorities
            else f"Seu dano ficou entre os 25% mais baixos dos {n} comparáveis."
        )
    return base


def _positive(contract: ReportContract) -> str | None:
    observation = contract.positive_observation
    if observation is None:
        return None
    if observation.basis is not PositiveObservationBasis.ABILITY_ABOVE_COHORT:
        return _POSITIVE_PHRASES[observation.basis]

    name = _spell_name(contract, observation.subject)
    if name is None:
        return None
    # RB-7's floor is offensive relevance. `core_abilities` is already the
    # player's own core kit (M7); `damage_share` (M8's DAMAGE_SHARE
    # feature, populated only when observable for this pull) is the
    # measured offensive contribution that narrows it further -- never a
    # role classification pulled across the M5 ability-role boundary, and
    # never a different observation than the one M29 selected. No match,
    # or no measured positive contribution, means no praise, never a guess.
    if not any(
        item.name == name and item.damage_share is not None and item.damage_share > 0
        for item in contract.core_abilities
    ):
        return None
    return _POSITIVE_PHRASES[observation.basis].format(ability=_sanitize_ability_name(name))


def _setup(contract: ReportContract) -> str | None:
    if contract.setup is None:
        return None
    for finding in contract.setup.findings:
        if finding.publicability is Publicability.HIDDEN or not finding.actionable:
            continue
        phrase = _SETUP_PHRASES.get((finding.category, finding.observation))
        if phrase is not None:
            return phrase
    return None


def render_coaching_answer(contract: ReportContract) -> str:
    """Render exactly one bounded Discord message, with no URL or I/O."""
    priorities = contract.material_priorities[:3]
    blocks = [_conclusion(contract, bool(priorities))]
    if priorities:
        if any(item.ordering_is_arbitrary for item in priorities):
            priority_lines = "\n".join(_priority(item, contract, "•") for item in priorities)
            blocks.append(
                "Pontos a revisar; itens empatados não têm precedência entre si:\n" + priority_lines
            )
        else:
            blocks.append(
                "\n".join(
                    _priority(item, contract, f"{index}.")
                    for index, item in enumerate(priorities, start=1)
                )
            )
    positive = _positive(contract)
    if positive is not None:
        blocks.append(positive)
    if priorities:
        setup = _setup(contract)
        if setup is not None:
            blocks.append(setup)
    return "\n\n".join(blocks)
