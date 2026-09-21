"""CORE.1 — a costura completa do produto, num teste só, offline.

M32 aposentou a infraestrutura de relatório HTML (link, capability,
persistência, servidor HTTP). O que resta do produto é: análise offline ->
`contract_for` -> `deliver_completed_report` -> exatamente uma mensagem de
coaching, sem URL nem anexo. Este arquivo prova esse fluxo em runtime, com
os módulos reais e um transporte HTTP falso — nunca lendo o texto-fonte do
produto para inferir comportamento.

Zero rede externa: WCL vem de `_DispatchTransport` (test_pipeline.py), o
benchmark de `FakeWclBackend`/`_seed_ready_benchmark`
(test_complete_analysis_integration.py), o Discord de um duplo local.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import discord
from test_complete_analysis_integration import (
    _responses_with_rich_setup,
    _seed_ready_benchmark,
    _target_of,
)
from test_pipeline import _build_deps, _DispatchTransport, _req

from botgitgud.analysis.pipeline import AnalysisResult, run_analysis
from botgitgud.bot import discord_bot, worker
from botgitgud.bot.delivery import DeliveryContext, deliver_completed_report
from botgitgud.report.coaching_answer import render_coaching_answer
from botgitgud.report.render import contract_for


class _FakeChannel:
    """Captura o que o produto mandaria ao Discord. Nada sai da máquina."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send(self, content: str, **kwargs: Any) -> Any:
        self.calls.append((content, kwargs))


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _analyze_with_ready_benchmark(tmp_path: Path) -> AnalysisResult:
    """Estágios 1-11: request -> WCL (duplo) -> coleta -> coorte -> execução
    -> setup -> Top 3. A primeira passada existe só para descobrir o target
    que o pipeline resolve, para semear o benchmark daquele MESMO alvo.
    """
    first_deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    first = run_analysis(_req(), first_deps, allow_cold_build=True)
    _seed_ready_benchmark(first_deps, _target_of(first))
    first_deps.store.close()

    deps = _build_deps(tmp_path, _DispatchTransport(_responses_with_rich_setup()))
    result = run_analysis(_req(), deps, allow_cold_build=True)
    deps.store.close()
    return result


def test_analysis_to_discord_message_end_to_end(tmp_path: Path) -> None:
    """A costura inteira, offline, do resultado da análise até a mensagem
    que o Discord receberia — uma asserção por estágio.
    """
    result = _analyze_with_ready_benchmark(tmp_path)

    # o pipeline produziu um AnalysisResult utilizável
    assert isinstance(result, AnalysisResult)
    assert result.comparisons
    assert result.performance is not None
    assert result.dps_gap is not None
    assert result.matched_cohort_members is not None
    assert result.matched_cohort_members > 0

    # o contrato canônico de produção tem as seções esperadas
    contract = contract_for(result)
    assert contract.resultado.char_name == "Zarad"
    assert contract.execucao.comparisons

    channel = _FakeChannel()
    outcome = _run(
        deliver_completed_report(
            channel,
            contract=contract,
            artifact_id="core-e2e-product-flow",
            context=DeliveryContext(channel_id="core-e2e"),
        )
    )

    assert outcome.delivered
    assert len(channel.calls) == 1
    content, kwargs = channel.calls[0]

    # a mensagem entregue É a resposta de coaching do mesmo contrato
    assert content == render_coaching_answer(contract)

    # sem URL
    assert "http://" not in content and "https://" not in content

    # sem anexo — a regressão do incidente de soak nunca volta
    assert "file" not in kwargs
    assert "files" not in kwargs
    assert "embed" not in kwargs
    assert "embeds" not in kwargs

    # allowed_mentions bloqueia tudo — `AllowedMentions` não define `__eq__`,
    # então a comparação é pelo payload real que seria enviado ao Discord.
    mentions = kwargs["allowed_mentions"]
    assert isinstance(mentions, discord.AllowedMentions)
    assert mentions.to_dict() == discord.AllowedMentions.none().to_dict()


def test_product_flow_uses_contract_and_coaching_delivery_only() -> None:
    """Checagem estática complementar — não substitui o teste de runtime
    acima, mas continua útil como sinal rápido de que os pontos de
    chamada esperados existem no código de produção.
    """
    bot_source = inspect.getsource(discord_bot)
    worker_source = inspect.getsource(worker)
    assert "contract_for(result)" in bot_source
    assert "deliver_completed_report(" in bot_source
    assert "contract_for(analysis)" in worker_source
    assert "mark_done(job.job_id, report_path=None)" in worker_source
