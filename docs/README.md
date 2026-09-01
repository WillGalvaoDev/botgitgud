# Documentação — BotGITGUD

Mapa da documentação. Comece por aqui para achar o documento certo sem abrir 30 arquivos.

> **Leia isto antes de executar qualquer procedimento:** só os documentos listados em
> [Operação atual](#operação-atual-current) descrevem o sistema como ele é **hoje**. Os
> documentos em [Histórico](#histórico-historical) registram estados passados do projeto —
> comandos e caminhos ali podem não existir mais. Não execute um procedimento a partir de um
> documento histórico.

## Operação atual (CURRENT)

Estes são os documentos operacionais vigentes.

| Documento | O que responde |
|---|---|
| [`runbook.md`](runbook.md) | **Comece aqui para operar.** Runbook operacional: incidentes, saúde, backup, e links para os contratos específicos. |
| [`linux-deployment.md`](linux-deployment.md) | Deployment atual: systemd, supervisor, bootstrap, preflight, backup/restore, Caddy. O que está pronto vs. o que depende da VM. |
| [`activation-runbook.md`](activation-runbook.md) | Sequência canônica para transformar uma VM provisionada em endpoint HTTPS público, plano de validação externa e rollback. Nada aqui foi executado ainda. |
| [`credential-rotation-checklist.md`](credential-rotation-checklist.md) | Gate humano de rotação das cinco credenciais. |
| [`warehouse-policy.md`](warehouse-policy.md) | Política do warehouse DuckDB (dono único, o que pode e o que não pode tocar o arquivo). |

## Arquitetura e contratos técnicos (REFERENCE)

Contratos vigentes, citados diretamente pelos docstrings do código. Descrevem *por que* o
sistema é como é.

| Documento | Assunto |
|---|---|
| [`schema_confirmado.md`](schema_confirmado.md) | Fatos verificados contra a API real da Warcraft Logs. A autoridade sobre o que a API de fato retorna. |
| [`schema_probe_output.md`](schema_probe_output.md) | Saída mecânica do `schema_probe.py` que sustenta o documento acima. |
| [`desvios.md`](desvios.md) | Log de desvios em relação ao plano original, com a justificativa de cada um. Continua recebendo entradas novas. |
| [`v1-process-supervision.md`](v1-process-supervision.md) | Política de supervisão de processo: restart, backoff, storm breaker, parada limpa. |
| [`v1-operational-logging.md`](v1-operational-logging.md) | Trilha operacional durável (JSONL, rotação, session id, redação de segredos). |
| [`v1-readiness-determinism.md`](v1-readiness-determinism.md) | Determinismo do relatório e ordenação canônica — inclui a causa raiz do flake histórico. |
| [`rc-discord-delivery-resilience.md`](rc-discord-delivery-resilience.md) | Resiliência da entrega no Discord e lifecycle do worker. Origem da decisão de nunca anexar HTML. |
| [`production-readiness-cold-build.md`](production-readiness-cold-build.md) | Cold cohort builds: orçamento, adiamento, prioridade. |
| [`spell-cache-runtime-separation.md`](spell-cache-runtime-separation.md) | Separação entre o seed versionado (`spells.json`) e o cache de runtime. |
| [`implementacao.md`](implementacao.md) | Documento de implementação original (T0.x–T3.x). Ainda é a referência das decisões de design de cada módulo, e é citado por dezenas de docstrings. |

## Experimentos — Fase 4 (EXPERIMENTAL)

> **A Fase 4 nunca foi promovida para produção.** O caminho servido ao jogador é inteiramente
> estatístico. Nenhum modelo é consultado por código de produção. Os documentos abaixo registram
> uma investigação — não descrevem o comportamento do bot.

| Documento | Assunto |
|---|---|
| [`fase4-architecture-decision.md`](fase4-architecture-decision.md) | Statistical Architecture Decision Gate — a decisão registrada. |
| [`fase4-statistical-architecture-experiment.md`](fase4-statistical-architecture-experiment.md) | Desenho do experimento de arquitetura estatística. |
| [`fase4-statistical-architecture-results.md`](fase4-statistical-architecture-results.md) | Resultados da primeira avaliação (SAE). |
| [`fase4-global-model-validation.md`](fase4-global-model-validation.md) | Gate de validação e calibração do modelo global. |
| [`fase4-data-acquisition-plan.md`](fase4-data-acquisition-plan.md) | Plano de aquisição de dados (discovery/triage/collect). |
| [`fase4-target-census.md`](fase4-target-census.md) | Censo real de targets da zona 46. |
| [`fase4-multi-target-architecture.md`](fase4-multi-target-architecture.md) | Arquitetura multi-target proposta. |
| [`fase4-experiment-collection.md`](fase4-experiment-collection.md) | Registro da coleta experimental. |
| [`fase4-experiment-incident-001.md`](fase4-experiment-incident-001.md) | Incidente 001 — label e sharing da coleta. |

## Histórico (HISTORICAL)

Registros de estados passados, preservados como evidência. **Não são procedimentos executáveis.**

| Documento | Assunto |
|---|---|
| [`archive/progresso.md`](archive/progresso.md) | Journal tarefa a tarefa da implementação inteira, com commits. |
| [`archive/roadmap-1.0.md`](archive/roadmap-1.0.md) | Roadmap de planejamento da release v1.0. |
| [`archive/release-1.0-progress.md`](archive/release-1.0-progress.md) | Journal autônomo da release 1.0 (gates R0-xx/R1-xx). |
| [`archive/fase3-gate-report.md`](archive/fase3-gate-report.md) | Relatório completo do portão de saída da Fase 3, gerado ao vivo. |
| [`archive/analysis-telemetry-incident.md`](archive/analysis-telemetry-incident.md) | Incidente de telemetria do hot path e a instrumentação que saiu dele. |
| [`relario.md`](relario.md) | Auditoria original do `bot.py` monolítico (2026-08-17). Fica fora de `archive/` porque continua sendo a referência citada pelo código para vários "achados" numerados que o pipeline atual corrige. |

## Onde está o quê — resumo

- **Arquitetura atual do produto:** [`../README.md`](../README.md) (visão geral) + os contratos em
  [Arquitetura e contratos técnicos](#arquitetura-e-contratos-técnicos-reference).
- **Deployment atual:** [`linux-deployment.md`](linux-deployment.md).
- **Activation runbook atual:** [`activation-runbook.md`](activation-runbook.md).
- **Decisões:** [`desvios.md`](desvios.md) (desvios em curso) e
  [`fase4-architecture-decision.md`](fase4-architecture-decision.md) (decisão da Fase 4).
- **Experimentos Phase 4:** seção [Experimentos](#experimentos--fase-4-experimental).
- **Registros históricos:** [`archive/`](archive/).
- **Implementação legada congelada:** [`../legacy/README.md`](../legacy/README.md).
