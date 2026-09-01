# BotGITGUD

Bot de Discord que analisa a performance de um jogador de World of Warcraft contra uma **coorte
pareada** de logs comparáveis do [Warcraft Logs](https://www.warcraftlogs.com/), e responde no
canal com um resumo compacto + um link para o relatório completo.

A pergunta que o projeto resolve não é "qual foi meu DPS?" — isso o Warcraft Logs já responde. É
**"por que meu DPS foi menor que o de quem fez a mesma luta em condições parecidas, e o que
especificamente explica a diferença?"**.

> **Estado atual** — o caminho estatístico (Fases 0–3) está implementado e coberto por testes. O
> candidato de machine learning da Fase 4 é **experimental e nunca foi promovido para produção**
> (ver [Fase 4](#fase-4--experimental-não-promovida)). O deployment em nuvem está **preparado e
> testado offline, mas não provisionado**: não existe VM, domínio, DNS ou certificado TLS ativo
> (ver [Deployment](#deployment)).

## Como funciona

```
Warcraft Logs API
   │
   ├─ ingest/      baixa e persiste eventos, casts, buffs, rankings (DuckDB + Parquet)
   │
   ├─ analysis/    monta a coorte pareada (encontro, spec, dificuldade, duração, covariáveis),
   │               alinha timelines de cooldown, classifica cadência, decompõe o gap de DPS
   │
   ├─ report/      condensa tudo num ReportContract de 5 seções:
   │               RESULTADO · SETUP · EXECUÇÃO · TOP 3 · CONFIANÇA
   │
   └─ bot/         fila persistente + worker → HTML persistido → capability link →
                   UMA mensagem compacta no Discord (≤1800 chars) + link clicável
```

O ponto não-óbvio do desenho é a **coorte**: comparar um jogador com "o melhor do mundo" não
produz conselho acionável. O sistema seleciona logs realmente comparáveis (mesmo encontro, mesma
spec, mesma dificuldade, duração dentro de banda, covariáveis pareadas) e só então mede a
diferença — reportando explicitamente o tamanho da amostra e a confiança quando a coorte é fraca.

### Entrega do relatório

O relatório HTML **nunca** é anexado ao Discord. Ele é persistido em disco, recebe um
*capability link* (token de 256 bits, `secrets.token_urlsafe(32)`, com TTL) e é servido por um
servidor HTTP local em `127.0.0.1:8080`, atrás de um reverse proxy. O Discord recebe uma única
mensagem compacta contendo a URL.

Isso resolve um incidente real: anexar o `.html` fazia o Discord renderizar o arquivo inteiro como
preview de texto no canal. A decisão está registrada em `docs/rc-discord-delivery-resilience.md`.

## Stack

| Camada | Escolha |
|---|---|
| Linguagem | Python 3.11+ |
| Discord | `discord.py` |
| HTTP | `httpx` (APIs), `aiohttp` (report server, já transitivo do discord.py) |
| Armazenamento | DuckDB (warehouse) + Parquet (eventos brutos) |
| Configuração | `pydantic-settings` (typed, via `.env`) |
| Logging | `structlog` (JSONL rotacionado, com redação de segredos) |
| Testes | `pytest`, `hypothesis`, `syrupy` (golden), cassettes HTTP gravadas |
| Qualidade | `ruff`, `pyright` |

## Capacidades principais

- **Coorte pareada** com bandas de duração, covariáveis e piso de amostra; degrada explicitamente
  (avisa) em vez de comparar com uma coorte insuficiente em silêncio.
- **Encounter Benchmark** incremental, com orçamento próprio e prioridade mais baixa que qualquer
  análise interativa.
- **Decomposição do gap de DPS** em componentes atribuíveis, com grading relativo por quantil.
- **Análise de Setup** (talentos, trinkets, stats) separada da análise de **Execução**.
- **Fila persistente + worker** com recuperação de crash: uma análise que exige coorte fria é
  enfileirada e entregue quando termina, sem o usuário repetir o comando.
- **Orçamento de API** respeitado por desenho — piso reservado para o caminho interativo, com
  adiamento explícito quando o orçamento não comporta um build.
- **Supervisão de processo** própria (backoff exponencial, storm breaker, parada limpa por arquivo
  de controle) portável entre Windows e Linux.

## Estrutura do repositório

```
src/botgitgud/
  wcl/          cliente da API Warcraft Logs (retry, orçamento, rate limit)
  blizzard/     cliente da API Blizzard (catálogo de magias)
  ingest/       persistência: DuckDB, Parquet, fetchers
  analysis/     coorte, alinhamento, grading, gap de DPS, benchmark, setup
  report/       ReportContract, render de texto/HTML, gráficos SVG
  bot/          Discord, fila, worker, capability links, report server, entrega
  ops/          supervisor de processo, deploy, preflight, ativação, publicação
  phase4/       trilha experimental de ML (NÃO promovida — ver abaixo)
tests/
  unit/         suíte principal (offline)
  golden/       snapshots de regressão
  fixtures/     cassettes HTTP gravadas + helpers
deploy/         systemd unit, Caddy template, scripts de bootstrap/backup/ativação
docs/           índice em docs/README.md; contratos, runbooks, experimentos, archive/
legacy/         bot.py original congelado — oracle executável da suíte golden, nunca produção
```

## Execução local

Requer Python 3.11+, uma aplicação Discord e credenciais de desenvolvedor da Warcraft Logs e da
Blizzard.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[data,dev]"      # Windows: .venv\Scripts\pip
```

### Configuração

```bash
cp .env.example .env      # preencha as credenciais apenas no arquivo local
```

As cinco credenciais obrigatórias são `DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`,
`BLIZZARD_CLIENT_ID` e `BLIZZARD_CLIENT_SECRET`. O template documenta também todos os defaults de
rede, orçamento, coorte e operação.

`.env` **nunca** é versionado. Alterar parâmetros metodológicos muda as comparações entre
execuções — o `settings_hash` do `RunManifest` registra essa deriva.

### Comandos

```bash
python -m botgitgud.cli serve          # inicia o bot Discord + worker
python -m botgitgud.cli supervise      # supervisor de processo (relança `serve`)
python -m botgitgud.cli analyze  --report <código> --fight <id> --char <nome>
python -m botgitgud.cli build-cohort --encounter <id> --class <classe> --spec <spec> --difficulty <n>
python -m botgitgud.cli ops-status     # saúde local read-only (não chama API)
python -m botgitgud.cli recover-jobs   # devolve jobs presos em `running` para `queued`
```

Comandos de deploy (`deploy-preflight`, `deploy-backup`, `deploy-restore`,
`deploy-render-caddyfile`, `deploy-activation-readiness`, `publication-check`) e a trilha
experimental (`experiment-*`, `discover`, `triage`, `dataset-status`) estão documentados em
`--help` e em `docs/`.

## Testes

A suíte é **offline por padrão**: 2.324 testes rodam sem tocar em nenhuma API. As respostas reais
da Warcraft Logs estão gravadas como cassettes em `tests/fixtures/cassettes/`, com headers
`Authorization` e tokens OAuth redigidos na gravação.

```bash
python -m pytest tests/unit -q
python -m pytest tests/golden -q
ruff check . && ruff format --check .
pyright
```

Os testes marcados `network` são excluídos por padrão (`-m "not network"`, ver `pyproject.toml`);
rodá-los consome orçamento real de API.

## Deployment

O alvo é uma VM Linux ARM64 sob systemd:

```
systemd → supervisor Python → processo do bot → Discord runtime + ReportServer (127.0.0.1:8080)
                                                          ↑
                                              Caddy (HTTPS) faz reverse proxy
```

O systemd supervisiona **apenas** o supervisor Python; a política de restart do bot (backoff,
storm breaker, parada limpa) permanece em `ops/supervisor.py`, para não existirem duas camadas
concorrentes tentando reiniciar o mesmo processo.

Estão prontos e testados offline: bootstrap do host, template de `.env` de produção, preflight,
unit systemd, backup/restore, template do Caddy e o gate de ativação
(`docs/activation-runbook.md`).

**Nada disso está ativo.** Não há VM provisionada, domínio, registro DNS nem certificado TLS
emitido. O primeiro start de produção está deliberadamente bloqueado por código enquanto
`REPORT_PUBLIC_BASE_URL` não apontar para um domínio real — ver `docs/linux-deployment.md`.

Antes de publicar ou empurrar o repositório:

```bash
python -m botgitgud.cli publication-check
```

Ele recusa `.env`, chaves privadas, warehouse DuckDB, backups e dados de runtime versionados por
acidente. **Não substitui** secret scanning dedicado.

## Fase 4 — experimental, não promovida

A Fase 4 investiga um modelo preditivo para complementar a análise estatística. A investigação
está documentada em `docs/fase4-*.md`, incluindo a arquitetura estatística testada, a coleta de
dados e um incidente registrado.

**Nenhum modelo foi promovido para produção.** O caminho servido ao jogador é inteiramente
estatístico; o registry de modelos não é consultado por nenhum caminho de produção. Os comandos
`experiment-*` existem para reproduzir a investigação, não para operar o bot.

## Limitações conhecidas

- O TOP 3 ranqueia apenas achados cujo ganho de DPS é quantificável; os demais permanecem nas
  seções detalhadas.
- Diferenças de talento podem aparecer como `(nodeID, rank)`: as APIs verificadas não expõem nomes
  confiáveis para esses nós.
- A banda posicional conservadora pode reduzir a amostra; o relatório sinaliza baixa confiança ou
  amostra insuficiente em vez de esconder isso.
- Sem fonte verificável de cooldown base, a classificação MAJOR/MINOR usa intervalo e contagem
  observados — isso muda o rótulo da seção, não a evidência comparativa do achado.
- O golden de gap de DPS usa referências truncadas: protege estrutura e regressão, não números
  realistas de coorte. A matemática é coberta por testes sintéticos e property-based.

## Documentação

**[`docs/README.md`](docs/README.md) é o índice** — ele separa explicitamente a documentação
operacional vigente dos contratos técnicos, dos experimentos da Fase 4 e dos registros históricos
(`docs/archive/`), para ninguém executar um procedimento a partir de um documento antigo.

Atalhos: [`runbook.md`](docs/runbook.md) (operação e incidentes),
[`linux-deployment.md`](docs/linux-deployment.md) (deploy Linux/systemd/Caddy),
[`activation-runbook.md`](docs/activation-runbook.md) (ativação do endpoint público),
[`desvios.md`](docs/desvios.md) (registro de desvios, com a justificativa de cada um).

## Licença

[MIT](LICENSE).
