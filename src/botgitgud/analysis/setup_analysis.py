"""SA.6 — camada pura que combina as quatro comparações de Setup Analysis
(SA.2 talents, SA.3 trinkets, SA.4 set bonus, SA.5 secondary stats) num
único resultado ordenado deterministicamente.

`SetupAnalysis` é SÓ uma coleção — `findings: tuple[SetupFinding, ...]`,
ordenada via `sort_setup_findings` (SA.1), mais metadados de
disponibilidade. **Nenhum `SetupScore`. Nenhuma recomendação global.
Nenhum ganho de DPS agregado.** Cada `SetupFinding` continua evidência
independente; combinar quatro categorias numa lista não é o mesmo que
combiná-las numa nota — essa distinção é o ponto central de todo o
milestone SA (ver `setup_finding.py`'s docstring de módulo).

Degradação honesta por categoria já é responsabilidade de CADA
`compare_*` (SA.2-SA.5) — cada um já lida com setup do jogador ausente,
benchmark ausente, categoria indisponível, evidência insuficiente e
cobertura parcial, de forma independente das outras categorias. SA.6 só
os chama e concatena os resultados: uma categoria degradada nunca impede
as outras de produzir uma comparação real, porque nenhuma delas depende
do resultado de outra.

Mesma regra arquitetural das quatro tarefas anteriores: zero import de
`cohort.py`/`cohort_match.py`/findings da Execution Cohort/DPS gap; zero
Store/WCL/Discord/JobQueue.
"""

from __future__ import annotations

from dataclasses import dataclass

from botgitgud.analysis.benchmark import BenchmarkPolicy, EncounterBenchmarkTarget
from botgitgud.analysis.benchmark_aggregate import EncounterBenchmark
from botgitgud.analysis.setup_finding import SetupFinding, sort_setup_findings
from botgitgud.analysis.setup_setbonus import compare_set_bonus
from botgitgud.analysis.setup_stats import compare_secondary_stats
from botgitgud.analysis.setup_talents import compare_talent_build
from botgitgud.analysis.setup_trinkets import compare_trinkets
from botgitgud.domain.models import SetupProfile


@dataclass(frozen=True, slots=True)
class SetupAnalysis:
    """`findings` já vem na ordem canônica de `sort_setup_findings` — quem
    consome isto (SA.6 é o teto do milestone SA; RP.1+ vai renderizar)
    nunca precisa reordenar. `benchmark_id`/`*_available` são metadados de
    identidade/disponibilidade, não uma nota agregada — ver docstring do
    módulo.
    """

    benchmark_id: str
    findings: tuple[SetupFinding, ...]
    player_setup_available: bool
    benchmark_available: bool


def analyze_setup(
    *,
    target: EncounterBenchmarkTarget,
    policy: BenchmarkPolicy,
    player_setup: SetupProfile | None,
    benchmark: EncounterBenchmark | None,
) -> SetupAnalysis:
    """Chama as quatro comparações (SA.2-SA.5) e combina os resultados,
    ordenados deterministicamente. Cada `compare_*` já valida sozinho que
    `benchmark.target == target` (levanta seu próprio erro de comparação
    quando não bate) — esta função não precisa repetir essa checagem.
    """
    findings: list[SetupFinding] = []
    findings.extend(
        compare_talent_build(
            target=target, policy=policy, player_setup=player_setup, benchmark=benchmark
        )
    )
    findings.extend(
        compare_trinkets(
            target=target, policy=policy, player_setup=player_setup, benchmark=benchmark
        )
    )
    findings.extend(
        compare_set_bonus(
            target=target, policy=policy, player_setup=player_setup, benchmark=benchmark
        )
    )
    findings.extend(
        compare_secondary_stats(
            target=target, policy=policy, player_setup=player_setup, benchmark=benchmark
        )
    )

    return SetupAnalysis(
        benchmark_id=target.benchmark_id,
        findings=sort_setup_findings(findings),
        player_setup_available=player_setup is not None,
        benchmark_available=benchmark is not None,
    )
