# BotGITGUD

Bot de Discord que analisa a execução de um jogador de World of Warcraft contra uma **coorte
pareada** de logs comparáveis do [Warcraft Logs](https://www.warcraftlogs.com/) e responde no
canal com uma única resposta curta de coaching.

A pergunta que o projeto resolve não é "qual foi meu DPS?" — isso o Warcraft Logs já responde. É
**"por que meu DPS foi menor que o de quem fez a mesma luta em condições parecidas, e o que
especificamente explica a diferença?"**.

> **Estado atual** — o caminho estatístico está implementado e coberto por testes; a
> metodologia de medidas e comparabilidade está fechada até o marco M2, e M3 não foi iniciado
> ([roadmap](docs/roadmap.md)). A trilha de machine learning é **experimental e nunca foi
> promovida para produção**. O deployment Linux está **preparado e testado offline, mas não
> provisionado**.

## Como funciona

```
Warcraft Logs API
   │
   ├─ ingest/      baixa e persiste eventos, casts, buffs, rankings (DuckDB + Parquet)
   │
   ├─ analysis/    coorte pareada (elegibilidade, higiene, covariáveis, população por métrica),
   │               contabilidade de dano, comparações, materialidade e priorização
   │
   ├─ report/      ReportContract → resposta de coaching (Discord) e texto detalhado (CLI)
   │
   └─ bot/         fila persistente + worker → UMA resposta de coaching no Discord
```

O ponto não-óbvio do desenho é a **coorte**: comparar um jogador com "o melhor do mundo" não
produz conselho acionável. O sistema seleciona logs realmente comparáveis — mesmo encontro, spec,
dificuldade e partição, duração e covariáveis compatíveis, com a população definida **por
métrica** — e só então mede a diferença, declarando tamanho de amostra e insuficiência em vez de
comparar com uma coorte fraca em silêncio.

Cada análise concluída entrega exatamente uma resposta no Discord, sem anexo e sem URL.

## Stack

| Camada | Escolha |
|---|---|
| Linguagem | Python 3.11+ |
| Discord | `discord.py` |
| HTTP | `httpx` |
| Armazenamento | DuckDB (warehouse) + Parquet (eventos brutos) |
| Configuração | `pydantic-settings` (typed, via `.env`) |
| Logging | `structlog` (JSONL rotacionado, com redação de segredos) |
| Testes | `pytest`, `hypothesis`, `syrupy` (golden), cassettes HTTP gravadas |
| Qualidade | `ruff`, `pyright` |

## Estrutura do repositório

```
src/botgitgud/
  wcl/          cliente da API Warcraft Logs (retry, orçamento, rate limit)
  blizzard/     cliente da API Blizzard (nomes de spell)
  ingest/       persistência: DuckDB, Parquet, fetchers
  domain/       modelos, specs suportadas, identidade de habilidades, tabelas curadas
  analysis/     coorte, comparabilidade, contabilidade de dano, findings, priorização, setup
  report/       ReportContract, resposta de coaching e texto do CLI
  bot/          Discord, fila, worker e entrega
  ops/          supervisor de processo, deploy, preflight e publicação
  knowledge/    conhecimento de rotação curado (Wowhead)
  phase4/       trilha experimental de ML (não promovida)
tests/
  unit/         suíte principal (offline)
  golden/       snapshot de regressão do pipeline sobre um log real gravado
  fixtures/     cassettes HTTP gravadas + helpers
deploy/         systemd unit e scripts de bootstrap/backup/restore
scripts/        lançadores Windows (Task Scheduler) e manutenção
docs/           documentação (ver abaixo)
```

## Execução local

Requer Python 3.11+, uma aplicação Discord e credenciais de desenvolvedor da Warcraft Logs e da
Blizzard.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[data,ml,dev]"   # Windows: .venv\Scripts\pip
cp .env.example .env                        # preencha as credenciais apenas no arquivo local
```

O extra `ml` é obrigatório: `botgitgud.cli` importa a trilha experimental no topo do módulo. As
cinco credenciais obrigatórias são `DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`,
`BLIZZARD_CLIENT_ID` e `BLIZZARD_CLIENT_SECRET`; o template documenta os demais defaults.
`.env` **nunca** é versionado. Alterar parâmetros metodológicos muda as comparações entre
execuções — o `settings_hash` do `RunManifest` registra essa deriva.

```bash
python -m botgitgud.cli serve          # bot Discord + worker
python -m botgitgud.cli supervise      # supervisor de processo (relança `serve`)
python -m botgitgud.cli analyze  --report <código> --fight <id> --char <nome>
python -m botgitgud.cli build-cohort --encounter <id> --class <classe> --spec <spec> --difficulty <n>
python -m botgitgud.cli ops-status     # saúde local read-only (não chama API)
python -m botgitgud.cli recover-jobs   # devolve jobs presos em `running` para `queued`
```

Comandos de deploy (`deploy-preflight`, `deploy-backup`, `deploy-restore`,
`publication-check`) e experimentais (`discover`, `triage`, `dataset-status`, `experiment-*`)
estão em `--help`. No Discord: `!analisar <Player> <URL>`, `!status` e `!update`.

## Testes

A suíte é **offline por padrão**: respostas reais da Warcraft Logs estão gravadas em
`tests/fixtures/cassettes/`, com `Authorization` e tokens OAuth redigidos.

```bash
python -m pytest tests/unit -q
python -m pytest tests/golden -q
ruff check . && ruff format --check .
pyright
python -m botgitgud.cli publication-check
```

Testes marcados `network` são excluídos por padrão (`-m "not network"`); rodá-los consome
orçamento real de API. O CI roda os mesmos portões em Windows e Linux.

## Documentação

| Documento | Responsabilidade |
|---|---|
| [`docs/product.md`](docs/product.md) | contrato de produto: o que o jogador recebe, escopo e princípios |
| [`docs/architecture.md`](docs/architecture.md) | fluxo, pacotes, armazenamento, coorte, fila, determinismo, testes e registro de decisões vigentes |
| [`docs/methodology.md`](docs/methodology.md) | contratos de medida, contabilidade de dano e comparabilidade (M0–M2) |
| [`docs/roadmap.md`](docs/roadmap.md) | trabalho planejado M3–M6 e decisões abertas |
| [`docs/operations.md`](docs/operations.md) | runbook, supervisão, logs, dados/backup, credenciais, custo de API |
| [`docs/linux-deployment.md`](docs/linux-deployment.md) | deploy Linux/systemd |
| [`docs/phase4.md`](docs/phase4.md) | trilha experimental de ML e sua fronteira com a produção |
| [`docs/schema_confirmado.md`](docs/schema_confirmado.md) | fatos verificados contra a API real do Warcraft Logs |

O histórico de especificações, revisões e evidências de marcos anteriores está no Git.

## Licença

[MIT](LICENSE).
