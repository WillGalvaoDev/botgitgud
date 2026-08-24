# Roadmap operacional — botgitgud v1.0

**Status do documento:** autoritativo para a release v1.0.
**Criado em:** 2026-08-24.
**HEAD na criação:** `3f7667f`, working tree limpa.
**Escopo desta etapa:** planejamento. Nenhuma tarefa deste roadmap foi executada.

---

## 1. Decisão de produto (formal)

> **botgitgud v1.0 = Fases 0–3 em produção, sem camada de ML exposta ao jogador.**

A **Fase 4 (T4.1–T4.4) está formalmente retirada do escopo da v1.0** por decisão de produto,
baseada na evidência experimental já produzida pelas trilhas SAE e SAD:

| Evidência | Origem | Consequência |
|---|---|---|
| `MODEL_TARGET = REJECT_FOR_CURRENT_PHASE4` | SAD.1 (`df77f26`) | A arquitetura por-`Phase4Target` da T4.1/T4.2 é inviável: mesmo com a campanha 100% completa, o alvo mais denso chegaria a 10 observações e **zero** alvos chegariam a 20. |
| `MODEL_GLOBAL = ADVANCE_TO_VALIDATION` | SAD.1 (`df77f26`) | Existe sinal real e generalizável, mas em uma arquitetura **diferente** da que a Fase 4 original especifica. |
| `SAD.2 = INTERNAL_EXPLANATION_ONLY` | SAD.2 (`f586a13`) | O modelo global **não** está calibrado o suficiente para ser mostrado ao jogador. Nenhuma calibração testada melhorou o MAE agregado; ambas pioraram o viés nos extremos. |

### O que esta decisão explicitamente **não** faz

- **Não** altera, relaxa ou marca como cumprido nenhum portão histórico. O gate de
  "≥5.000 logs por `Phase4Target`" (§FASE 4 de `docs/implementacao.md`) permanece **vigente e não
  cumprido**. Ele não foi renegociado — a Fase 4 inteira saiu do escopo desta release.
- **Não** cumpre T4.1–T4.2–T4.3–T4.4 de forma artificial ou parcial para "fechar" a v1.0.
- **Não** promove nada em `phase4_model_registry` (0 linhas hoje, e assim permanece na v1.0).
- **Não** retoma a campanha experimental, não autoriza pontos novos de API, não instala SHAP e
  não treina modelo algum.

A Fase 4 passa a ser candidata a **v1.1**, e sua retomada exige um roadmap próprio (§10).

---

## 2. Estado atual medido (2026-08-24)

Todos os números abaixo foram medidos diretamente do repositório e do warehouse, não copiados de
documentos anteriores.

### 2.1 Código e testes

| Métrica | Valor |
|---|---|
| Módulos em `src/botgitgud/` | 96 |
| Linhas em `src/` | 15.248 |
| Testes coletados | 911 |
| Cobertura (última medição registrada) | ~90% |
| Arquivos acima de 300 linhas | 10 (todos da trilha experimental/phase4) |
| Branches | apenas `main` |
| Tags | **nenhuma** |
| `pyproject.toml` version | `0.1.0` |
| README na raiz | **ausente** |

### 2.2 Warehouse (`data/`, 173 MB no total)

| Tabela | Linhas | Natureza |
|---|---|---|
| `logs` | 741 | reproduzível (re-buscável na WCL a custo de API) |
| `data/raw/*.parquet` | 741 arquivos, 39 MB | reproduzível |
| `discovery_reports` | 801 | reproduzível |
| `discovery_fights` | 1.963 | reproduzível |
| `discovery_targets` | 24.723 | reproduzível |
| `backfill_checkpoints` | 14 | operacional |
| `experiment_campaigns` | 1 | **único / irreproduzível** (plano congelado) |
| `experiment_campaign_observations` | 1.200 (603 completed / 597 pending) | **único / irreproduzível** |
| `experiment_observation_attempts` | 741 | **único / irreproduzível** (ledger de 8.026 pontos gastos) |
| `experiment_architecture_eval_runs` | 2 | **único** (evidência SAE.8) |
| `experiment_architecture_decision_runs` | 2 | **único** (evidência SAD.1) |
| `experiment_calibration_runs` | 1 | **único** (evidência SAD.2) |
| `phase4_model_registry` | **0** | — |
| `runs` | **0** | — |
| `cohort_candidates` | **0** | — |
| `spells` | **0** | — (o catálogo real vive em `spells.json`) |
| `jobs` | **tabela inexistente** | — |

### 2.3 O fato central desta release

Três medições independentes convergem para a mesma conclusão:

- `runs` = **0 linhas** → `run_analysis` nunca foi executado contra o warehouse de produção.
- `jobs` = **tabela nem existe** → `JobQueue` nunca foi instanciada contra este warehouse, logo
  `serve` nunca subiu de fato.
- `cohort_candidates` = **0 linhas** → nenhum pool de coorte foi aquecido em produção.

> **O produto está tecnicamente completo até a Fase 3 e nunca foi ligado.**
> O trabalho da v1.0 é predominantemente operacional, não algorítmico.

### 2.4 Varredura preliminar de segredos no histórico Git

Executada como leitura incidental para dimensionar R0-02 (a auditoria completa continua sendo
tarefa, ver R0-02):

- 828 caminhos únicos já versionados em todo o histórico.
- Varredura por **nome de arquivo** (`env`, `secret`, `token`, `credential`, `.duckdb`, `.pem`,
  `.key`): o único resultado é `.env.example`.
- `.env` **nunca** aparece em nenhuma árvore do histórico.
- Uma única branch (`main`), nenhuma tag, nenhum remote configurado verificado nesta varredura.

**Ainda não verificado:** se algum valor de credencial foi colado *dentro* de outro arquivo
versionado (doc, teste, log de saída). É isso que R0-02 precisa fechar.

---

## 3. Convenções deste roadmap

### 3.1 Campos de cada tarefa

Toda tarefa registra: **Objetivo**, **Problema**, **Evidência atual**, **Escopo**,
**Fora do escopo**, **Dependências**, **Critérios de aceite**, **Testes/validações**, **Risco**,
**Esforço**, **Automação**, **Status**.

### 3.2 Classificação de automação

| Marca | Significado |
|---|---|
| `AUTOMATABLE` | O agente pode executar do início ao fim e comprovar por comando. |
| `HUMAN_REQUIRED` | Exige ação humana (credenciais reais, console de terceiros, observação ao vivo, aprovação de release). **O agente não deve declarar concluída.** |
| `MIXED` | O agente prepara/executa a parte mecânica; um humano fecha a parte que exige acesso ou julgamento externo. |

### 3.3 Vocabulário de status

`PENDING` · `IN_PROGRESS` · `BLOCKED_ON_HUMAN` · `DONE` · `DROPPED` · `ACCEPTED_RISK`

Todas as tarefas nascem `PENDING`. `ACCEPTED_RISK` significa que a tarefa **não** foi executada e o
proprietário decidiu, explicitamente e por escrito, assumir o risco residual para esta release —
nunca deve ser lido como equivalente a `DONE`.

### 3.4 Esforço

`S` = até ~2h · `M` = ~meio dia · `L` = ~1–2 dias · `XL` = acima disso.
Tarefas `HUMAN_REQUIRED` de observação (soak) têm esforço em tempo de calendário, não de trabalho.

---

## 4. Milestones

| Milestone | Tema | Bloqueia a release? |
|---|---|---|
| **R0** | Security & Release Baseline | Sim |
| **R1** | Runtime Validation | Sim |
| **R2** | Production Consistency | Sim |
| **R3** | Operational Readiness | Sim |
| **R4** | Release Candidate | Sim |
| **R5** | Production Soak & v1.0 | Sim |

---

## R0 — Security & Release Baseline

### R0-01 — Rotação das 5 credenciais expostas

- **Objetivo:** revogar e reemitir todas as credenciais que foram lidas em texto claro durante a
  auditoria do projeto.
- **Problema:** as 5 credenciais de produção foram expostas em texto claro no ambiente de
  desenvolvimento durante a auditoria. Enquanto não forem rotacionadas, qualquer publicação do
  repositório ou vazamento de ambiente compromete três contas de terceiros.
- **Evidência atual:** registrado em `docs/progresso.md` (seção do Data Acquisition Gate) como
  pendência aberta desde 2026-08-20: *"Rotacionar as 5 credenciais expostas (Discord, WCL client
  id/secret, Blizzard client id/secret)"*. `.env` existe na raiz, ignorado pelo git, com os 5
  campos populados.
- **Escopo:**
  1. Discord bot token — revogar e reemitir no Discord Developer Portal.
  2. WCL `client_id` + `client_secret` — reemitir em warcraftlogs.com/api/clients.
  3. Blizzard `client_id` + `client_secret` — reemitir em develop.battle.net.
  4. Atualizar o `.env` local com os valores novos.
  5. Confirmar que as credenciais **antigas** foram efetivamente revogadas (não apenas
     substituídas) — uma credencial antiga que continua válida não conta como rotacionada.
- **Fora do escopo:** qualquer automação de rotação. **O agente não deve tentar rotacionar,
  revogar, ou acessar consoles de terceiros.**
- **Dependências:** nenhuma. É a raiz do caminho crítico.
- **Critérios de aceite (caso a rotação venha a ser executada):**
  - As 5 credenciais novas estão no `.env` local e o bot autentica com elas (verificado em R1-01).
  - As 5 credenciais antigas estão revogadas e comprovadamente inválidas.
  - Nenhum valor de credencial aparece em nenhum arquivo versionado (verificado por R0-02).
- **Testes/validações:** autenticação WCL e Blizzard bem-sucedida com as credenciais novas
  (exercitada indiretamente por R1-01); tentativa de uso da credencial antiga falha.
- **Risco:** ver risco residual abaixo.
- **Esforço:** S (humano).
- **Automação:** `HUMAN_REQUIRED`
- **Status:** `ACCEPTED_RISK` — decisão do proprietário em 2026-08-24. **A rotação NÃO foi
  executada;** as credenciais atuais permanecem em uso. Deixa de ser BLOCKER desta release.

#### Decisão de segurança registrada (2026-08-24)

O proprietário decidiu conscientemente **não rotacionar as credenciais nesta release**. Isto não é
tarefa concluída nem esquecida: é risco residual formalmente aceito.

**Fatos verificados que sustentam a aceitação** (nenhum presumido):

- `.env` nunca foi versionado — ausente de todas as árvores do histórico.
- R0-02 auditou **91 revisões** por conteúdo, com padrões de token do Discord, `Bearer`,
  atribuição de client secret e OAuth access token.
- **Zero segredos** encontrados no histórico.
- Fixtures e cassetes relevantes verificados: os arquivos com chave `access_token` e os com
  cabeçalho `Authorization` usam redação (correção de D-6, T0.2).
- Nenhum segredo entrou nos commits desta release (`eccad6a`, `cc4f06d`) — varredura do diff
  staged executada antes de cada commit.
- **Nenhum remote configurado** neste repositório; nenhum push público foi feito.

**Risco residual aceito:** enquanto a mesma credencial continuar válida, não é possível provar que
nenhuma cópia dela existe fora do Git. O histórico limpo prova ausência no repositório, não
ausência no mundo.

**Gatilhos que revogam esta aceitação e tornam a rotação obrigatória e imediata:**

1. suspeita de vazamento;
2. segredo encontrado em qualquer arquivo ou log;
3. publicação acidental (push, gist, captura de tela, anexo);
4. compartilhamento do ambiente com terceiros;
5. novo colaborador com acesso ao ambiente;
6. incidente de segurança de qualquer natureza;
7. comprometimento da máquina ou de qualquer conta associada.

O procedimento continua pronto em `docs/credential-rotation-checklist.md`. Nenhum valor de
credencial é reproduzido em nenhum documento deste repositório.

---

### R0-02 — Auditoria do histórico Git quanto a segredos

- **Objetivo:** provar que nenhuma credencial (atual ou anterior) entrou no histórico versionado.
- **Problema:** `.env` está no `.gitignore` hoje, mas o `.gitignore` foi criado na T0.0, *antes* do
  `git init` — a proteção por caminho parece correta, e ainda assim ninguém verificou se um valor
  de segredo foi colado dentro de outro arquivo (doc, saída de teste, cassete, log).
- **Evidência atual (varredura preliminar já executada, §2.4):**
  - 828 caminhos únicos no histórico; nenhum `.env`, `.pem`, `.key`, `.duckdb` versionado.
  - Único arquivo com nome sugestivo: `.env.example` (template vazio, sem valores).
  - **Lacuna aberta:** varredura por *conteúdo* nunca foi feita.
- **Escopo:**
  1. Varredura por conteúdo em todo o histórico (`git log -p --all` ou `git grep` sobre todas as
     revisões) usando **padrões de forma** de segredo (formato de token do Discord, strings de 32/64
     hex, `client_secret=`, cabeçalhos `Authorization: Bearer`), **nunca ecoando valores** no
     terminal nem em documento.
  2. Varredura específica nos cassetes de fixture (`tests/fixtures/`) — D-6 já foi um achado real
     de token OAuth vazando em corpo de resposta gravado; confirmar que a correção da T0.2 cobriu
     todos os cassetes, inclusive os regravados depois (T-DG.0, T3.1, T2.1).
  3. Registrar o resultado em `docs/desvios.md` como desvio novo se houver achado.
- **Fora do escopo:** reescrita de histórico. **Se um segredo for encontrado no histórico, PARE.**
  Não execute `git filter-repo`, `filter-branch`, rebase interativo ou force-push. Apresente um
  plano de reescrita segura (impacto, ordem, backup do repo, coordenação com a revogação de R0-01)
  e aguarde aprovação humana explícita.
- **Dependências:** nenhuma para executar; o resultado condiciona R4-02.
- **Critérios de aceite:**
  - Relatório escrito da varredura (padrões usados, revisões cobertas, resultado) em
    `docs/desvios.md` ou `docs/roadmap-1.0.md`.
  - Zero achados **ou** plano de remediação aprovado por um humano.
  - Nenhum valor de segredo reproduzido em nenhum documento gerado por esta tarefa.
- **Testes/validações:** a própria varredura é a validação; deve ser reproduzível por comando
  documentado.
- **Risco:** médio — um achado transforma a tarefa em reescrita de histórico coordenada.
- **Esforço:** S (sem achados) / L (com achados).
- **Automação:** `MIXED` (varredura automatizável; qualquer remediação é `HUMAN_REQUIRED`)
- **Status:** `DONE` — 91 revisões, zero achados; evidência sanitizada em `docs/desvios.md`.

---

### R0-03 — README raiz

- **Objetivo:** dar ao repositório a porta de entrada que um 1.0 exige.
- **Problema:** não existe nenhum arquivo na raiz que explique o que o projeto é, como instalar,
  configurar ou iniciar. Toda a documentação está em `docs/`, escrita para o processo de
  implementação, não para quem vai operar o bot.
- **Evidência atual:** `ls` da raiz retorna `data/ docs/ legacy/ pyproject.toml spells.json src/
  tests/` — nenhum README.
- **Escopo:** criar `README.md` na raiz contendo, no mínimo:
  1. **Propósito** — o que o bot faz: analisa o uso de cooldowns e métricas de performance de um
     jogador de WoW contra uma coorte pareada de logs comparáveis do Warcraft Logs.
  2. **Requisitos** — Python ≥3.11 (ambiente atual: 3.14.6), conta de desenvolvedor WCL, conta de
     desenvolvedor Blizzard, aplicação Discord.
  3. **Instalação** — `venv` + `pip install -e ".[data,dev]"` (registrar que `uv` não está
     disponível neste ambiente, conforme D-1).
  4. **Configuração do `.env`** — as 5 credenciais obrigatórias e os parâmetros opcionais (ver
     R0-04). **Sem nenhum valor real.**
  5. **Comandos** — todos os subcomandos públicos da CLI com uma linha cada.
  6. **Como iniciar** — `python -m botgitgud.cli serve`.
  7. **Como rodar testes** — `pytest`, `ruff check .`, `ruff format --check .`, `pyright src tests`.
  8. **Arquitetura resumida** — as camadas (`wcl/`, `blizzard/`, `ingest/`, `analysis/`, `report/`,
     `bot/`) e o fluxo de uma análise.
  9. **Limitações conhecidas** — as dívidas aceitas de §6, em linguagem de usuário.
  10. **Comportamento sem Fase 4/ML** — declarar explicitamente que a v1.0 não contém modelo
      preditivo, que `phase4_model_registry` fica vazio e que o bot roda 100% no caminho
      estatístico das Fases 0–3.
- **Fora do escopo:** documentação de operação (isso é o runbook, R3-04); qualquer credencial real.
- **Dependências:** R0-04 (para a seção de `.env`); §6 (para "limitações conhecidas"); R2-02 (a
  lista de comandos depende do destino do `backfill`).
- **Critérios de aceite:**
  - As 10 seções acima existem e estão preenchidas.
  - Cada comando listado no README existe de fato na CLI e não é stub.
  - Um leitor que nunca viu o projeto consegue instalar e iniciar o bot só com o README.
  - `grep` por valores de credencial no README retorna vazio.
- **Testes/validações:** seguir o README do zero em um clone limpo até `serve` iniciar (fecha em
  conjunto com R1-01).
- **Risco:** baixo.
- **Esforço:** M
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — `README.md` criado e alinhado à CLI/configuração reais.

---

### R0-04 — `.env.example` alinhado ao `Settings` real

- **Objetivo:** o template de configuração precisa refletir o que o código realmente lê.
- **Problema:** `.env.example` lista apenas as 5 credenciais. `Settings` (`config.py`) expõe ~30
  parâmetros configuráveis por variável de ambiente — timeouts, retries, piso de pontos de API,
  limiares de coorte, `gap_penalty_s` — nenhum deles documentado. Um operador não tem como saber
  que `API_POINTS_FLOOR` existe, e o runbook (R3-04) vai precisar referenciá-lo.
- **Evidência atual:** `.env.example` = 5 linhas; `config.py` = 5 `SecretStr` + ~25 campos com
  default (`data_dir`, `log_level`, `log_json`, `max_workers`, 7 de WCL, 5 de Blizzard,
  `gap_penalty_s`, 4 de elegibilidade, `discord_chunk_max_len`, 7 de coorte).
- **Escopo:** estender `.env.example` com todos os campos de `Settings`, comentados, com o valor
  default explícito e uma linha de explicação para os operacionalmente relevantes
  (`API_POINTS_FLOOR`, `LOG_LEVEL`, `LOG_JSON`, `DATA_DIR`, `MAX_WORKERS`). Os parâmetros
  metodológicos (limiares de coorte, `gap_penalty_s`) devem vir com um aviso de que alterá-los muda
  a metodologia e invalida comparações entre execuções (o `settings_hash` do `RunManifest` já
  detecta essa deriva — vale citar).
- **Fora do escopo:** mudar qualquer default; mudar `Settings`; qualquer valor real.
- **Dependências:** nenhuma.
- **Critérios de aceite:**
  - Todo campo de `Settings` aparece em `.env.example`.
  - Nenhum valor de credencial preenchido.
  - Um teste garante que os dois não divergem de novo (ver abaixo).
- **Testes/validações:** teste novo que compara os nomes de campo de `Settings` com as chaves
  declaradas em `.env.example` e falha se algum campo novo for adicionado sem documentar.
- **Risco:** baixo.
- **Esforço:** S
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — template completo e teste de divergência adicionado.

---

## R1 — Runtime Validation

### R1-01 — Smoke test real de `serve` contra Discord e WCL

- **Objetivo:** provar, com execução real, que o produto funciona ponta a ponta como um serviço.
- **Problema:** `python -m botgitgud.cli serve` existe desde a T1.8 (adicionado como resposta à
  D-23) e **nunca foi executado**. Todo o caminho de produção — conexão com o Discord, event loop,
  worker em background, recuperação de crash, entrega de relatório — está inteiramente não
  verificado. É o maior risco isolado da release.
- **Evidência atual:** no warehouse de produção, `runs` = 0 linhas, `cohort_candidates` = 0 linhas,
  e a tabela `jobs` **nem foi criada** (`JobQueue` cria a própria DDL na primeira instanciação).
  As três medições, juntas, provam que nenhum processo `serve` jamais tocou este warehouse.
- **Escopo:** uma execução real, com credenciais válidas (pós-R0-01), verificando na ordem:
  1. o processo inicia sem exceção;
  2. o cliente Discord conecta e autentica;
  3. o bot aparece online no servidor;
  4. `on_ready` roda: `recover_from_crash()` executa e o worker loop é criado exatamente uma vez;
  5. `!status` responde com a fila vazia;
  6. `!analisar <char> <link>` é aceito e parseado;
  7. o caminho rápido (`allow_cold_build=False`) roda e, no miss de coorte, enfileira o job;
  8. o worker reivindica o job (`claim_next`) respeitando o orçamento;
  9. a WCL é consultada de verdade e os pontos são debitados;
  10. o pipeline das Fases 0–3 completa (matching de coorte, alinhamento, grading, gap de DPS,
      Top 3);
  11. o relatório é produzido;
  12. o resultado chega ao canal do Discord no formato definido por R2-01;
  13. um erro controlado (jogador inexistente, spec fora de escopo, fight inválido) produz mensagem
      em português **e não derruba o processo**;
  14. encerramento e restart: nenhum job fica silenciosamente perdido — jobs em `running` voltam
      para `queued` no boot seguinte e são reprocessados.
- **Fora do escopo:** o soak de 24h (R5-01) e as 3 análises de jogadores distintos (R5-02) — este é
  o primeiro contato, não a validação de estabilidade.
- **Dependências:** **R0-01** (rodar o smoke com as credenciais antigas obriga a repetir tudo depois
  da rotação), R2-01 (o item 12 precisa do contrato de entrega já definido), R1-02 (os testes
  determinísticos devem existir antes, para que o smoke encontre só o que teste não alcança).
- **Critérios de aceite:**
  - Os 14 itens acima verificados **por observação real**, com evidência sanitizada registrada em
    `docs/progresso.md` (sem tokens, sem IDs de usuário do Discord, sem nomes de personagem que o
    dono não autorizou).
  - Qualquer item que falhar vira tarefa própria antes do RC; não se declara "passou parcialmente".
- **Testes/validações:** execução manual observada. **O agente não deve marcar esta tarefa como
  concluída, nem inferir sucesso a partir de testes unitários.**
- **Risco:** **alto.** É o primeiro contato do código com o ambiente real; a probabilidade de
  aparecerem defeitos de integração é significativa e o orçamento para corrigi-los precisa existir.
- **Esforço:** M para executar; imprevisível para corrigir o que aparecer.
- **Automação:** `HUMAN_REQUIRED`
- **Status:** `BLOCKED_ON_HUMAN` — requer R0-01, Discord real e autorização de API; **BLOCKER**

---

### R1-02 — D-22: testes determinísticos dos handlers do Discord

- **Objetivo:** cobrir os contratos críticos de `bot/discord_bot.py` com testes determinísticos.
- **Problema:** `bot/discord_bot.py` tem **0% de cobertura** desde a T1.8. É o único arquivo do
  projeto nessa condição, e concentra a tradução de exceções de domínio em mensagens ao usuário —
  ou seja, o comportamento que o jogador realmente vê é o único não testado.
- **Evidência atual:** D-22 em `docs/desvios.md`, reafirmada nas T2.x/T3.4. O arquivo tem 227
  linhas: `parse_report_input`, `_current_budget`, `_notify_outcome`, `_worker_loop`,
  `_enqueue_message`, `build_bot` (com `on_ready`, `cmd_analisar`, `cmd_status`).
- **Escopo:** cobrir por teste os contratos críticos, com fakes de `Context`/`Bot`/`Channel`:
  1. `parse_report_input` — URL completa com `?fight=N`, código de 16 chars puro, entrada inválida
     (função pura, o caso mais barato e hoje descoberto).
  2. `cmd_analisar` — caminho feliz envia resumo + anexo; e **cada** um dos 6 ramos de exceção
     (`PlayerNotFound`/`FightNotFound`, `ScopeRejected`, `CohortNotReady` → enfileira,
     `InsufficientCohort`, `ApiError`) produz a mensagem em português esperada e **não propaga**.
  3. `cmd_status` — fila vazia vs. fila com jobs `queued`/`running`.
  4. `_enqueue_message` — job novo, deduplicado, rejeitado.
  5. `_notify_outcome` — sucesso de `analyze`, sucesso de `build_cohort`, falha, e o caso
     `requeued=True` (que deve ser **silencioso**, sem notificar o usuário — regra da T1.8 §3 hoje
     sem nenhum teste).
  6. `_worker_loop` — pelo menos o invariante de que nenhuma checagem de orçamento é gasta quando
     não há job `queued`, e que `ApiError` na checagem de orçamento não derruba o loop.
  7. `on_ready` — o worker loop é criado exatamente **uma vez** mesmo com reconexões (`on_ready`
     dispara de novo a cada reconexão do Discord; o guard `worker_started` existe e nunca foi
     testado).
- **Fora do escopo:** perseguir percentual de cobertura; simular o event loop real do Discord ou
  uma conexão de rede; substituir R1-01.
- **Dependências:** R2-01 (o teste do caminho feliz de `_notify_outcome` precisa do contrato de
  entrega final).
- **Critérios de aceite:**
  - Os 7 contratos acima têm teste determinístico e verde.
  - Nenhum teste depende de rede, de token, ou de um event loop real do Discord.
  - D-22 é atualizada em `docs/desvios.md` de "sem cobertura" para "contratos críticos cobertos;
    a cola de conexão permanece fora de teste por design".
- **Testes/validações:** a suíte completa continua verde; `ruff`/`pyright` limpos.
- **Risco:** baixo. O risco real é o oposto — deixar como está e descobrir os defeitos em produção.
- **Esforço:** M
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — contratos críticos cobertos com fakes, sem rede/tokens.

---

## R2 — Production Consistency

### R2-01 — Contrato único de entrega do relatório

- **Objetivo:** os dois caminhos que entregam uma análise devem ser semanticamente equivalentes.
- **Problema:** hoje o mesmo comando entrega formatos diferentes dependendo de o cache estar quente
  ou frio — uma diferença invisível para o usuário, que recebe às vezes um anexo HTML e às vezes
  uma parede de texto.
- **Evidência atual (confirmada no código, não inferida):**
  - **Caminho interativo** — `discord_bot.py:196-208`: `render_header_and_top3(...)` como mensagem
    curta **+** `render_html_report(...)` como `discord.File("relatorio.html")`.
  - **Caminho assíncrono** — `worker.py:74-82`: `_run_analyze` devolve `render_report(...)` (texto
    completo) dentro de `JobOutcome.message`, e `discord_bot.py:86-89` o quebra em chunks
    ```` ```markdown ```` de até 1900 caracteres.
  - **Causa raiz:** `JobOutcome` (`worker.py:30-35`) carrega apenas `message: str` — não tem como
    representar um anexo binário. A T3.4 registrou explicitamente essa decisão de escopo ao migrar
    só o caminho interativo.
- **Escopo:**
  1. Adotar o contrato: **relatório HTML como artefato principal nos dois caminhos**, com mensagem
     curta no Discord contendo status/resumo.
  2. Menor mudança que atinge isso: estender `JobOutcome` com os campos opcionais que o caminho
     assíncrono precisa (o resumo curto e o HTML renderizado), populados por `_run_analyze`, e
     fazer `_notify_outcome` enviar resumo + `discord.File` quando presentes.
  3. `render_report` (texto completo) **permanece** — é usado pela CLI `analyze` e pelos golden
     tests, e não deve ser removido.
  4. O caminho `build_cohort` continua entregando texto simples (não produz relatório).
  5. Antes de implementar, reconfirmar o comportamento no código (o levantamento acima é de
     2026-08-24) e escolher a menor solução que preserve a equivalência semântica.
- **Fora do escopo:** redesenhar o relatório HTML; mudar o conteúdo do relatório; alterar
  `render_report`.
- **Dependências:** nenhuma. **Bloqueia** R1-01 (item 12) e R1-02 (item 5).
- **Critérios de aceite:**
  - Uma análise entregue pelo caminho quente e a mesma análise entregue pelo caminho da fila
    produzem **o mesmo artefato principal** (HTML) e o mesmo tipo de mensagem curta.
  - Nenhum caminho posta o relatório completo inline no Discord.
  - A mensagem curta cabe em uma única mensagem do Discord nos dois caminhos.
- **Testes/validações:** **regressão automatizada obrigatória** — um teste que exercita os dois
  caminhos com o mesmo `AnalysisResult` e afirma equivalência de contrato (mesmo tipo de artefato,
  mesmo resumo). O teste existente
  `test_header_and_top3_text_fits_in_a_single_discord_message` deve passar a cobrir também o
  caminho assíncrono.
- **Risco:** baixo-médio — mexe em `JobOutcome`, consumido por `worker.py` e `discord_bot.py`; a
  superfície é pequena e coberta por `test_worker.py`.
- **Esforço:** M
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — caminhos quente/fila entregam resumo + HTML equivalentes.

---

### R2-02 — Resolver o subcomando `backfill`

- **Objetivo:** nenhum comando público deliberadamente quebrado na v1.0.
- **Problema:** `backfill` é exposto na CLI e sempre retorna exit 1 com
  *"backfill ainda não implementado — sem especificação (docs/desvios.md D-13)"*.
- **Evidência atual:** `cli.py:149-153` (`_cmd_backfill`) e `cli.py:221-222` (registro do parser).
  D-13 documenta que o comando foi listado uma única vez na T1.6 e **nunca especificado em lugar
  nenhum** do documento de implementação.
- **Escopo:**
  1. Buscar por dependência real: referências a `backfill` em `docs/`, em código
     (`ingest/backfill_planner.py` existe e é usado pela trilha de descoberta — verificar se há
     acoplamento com o subcomando ou se são coisas distintas), e em `backfill_checkpoints` (14
     linhas no warehouse, escritas por `discovery.py`).
  2. **Se não houver requisito real:** remover o subcomando da interface pública, remover
     `_cmd_backfill`, atualizar D-13 para `RESOLVIDO — comando removido`, ajustar os testes de
     argparse.
  3. **Se houver dependência real:** documentar a necessidade e **apresentá-la antes** de
     implementar qualquer funcionalidade nova. Implementar feature nova em cima de um stub sem
     especificação não é trabalho de v1.0.
- **Fora do escopo:** implementar backfill. Remover `ingest/backfill_planner.py` ou os checkpoints
  de descoberta (são de outra trilha, funcionais e usados).
- **Dependências:** nenhuma. Alimenta R0-03 (lista de comandos) e o portão RC.
- **Critérios de aceite:**
  - `python -m botgitgud.cli --help` não lista nenhum comando que sempre falha.
  - D-13 fechada com decisão registrada.
  - Se removido: nenhum teste referencia mais o subcomando; a suíte segue verde.
- **Testes/validações:** teste de argparse ajustado; suíte completa verde.
- **Risco:** baixo.
- **Esforço:** S
- **Automação:** `AUTOMATABLE` (a decisão "existe requisito real?" é `MIXED` se a busca for
  ambígua)
- **Status:** `DONE` — stub removido; discovery planner/checkpoints preservados.

---

### R2-03 — D-31: honestidade da seção "Top 3 ações"

- **Objetivo:** garantir que o relatório não sugira que todas as categorias competiram
  quantitativamente pelo Top 3, quando estruturalmente 6 das 8 nunca podem.
- **Problema:** `select_top_actions` filtra `estimated_gain_pct is not None`. Apenas `BUILD` e
  `ABILITY_GAP` têm essa métrica; `DEATH`, `ACTIVE_TIME`, `UPTIME`, `WASTE`, `MISSED_CD` e
  `CD_TIMING` **nunca** são sequer construídas como `Finding`. O resultado é uma afirmação que pode
  ser factualmente falsa: quando nada passa o filtro, a seção imprime
  **"✅ Nenhum problema material detectado."** — mesmo que logo abaixo, no mesmo relatório, estejam
  listadas 5 mortes, active time no p05 e 7 usos de cooldown perdidos.
- **Evidência atual:** `analysis/findings.py` (`build_findings` só emite as 2 categorias),
  `report/top_actions_text.py:17-19` (a mensagem citada), D-31 em `docs/desvios.md`.
- **Escopo:**
  1. Corrigir o **texto**, não o algoritmo: a seção deve declarar sobre o que ela realmente
     ranqueia (achados com ganho de DPS quantificável) e apontar o leitor para as demais seções.
  2. Substituir a mensagem de lista vazia por uma formulação verdadeira — algo como *"Nenhum
     achado com ganho de DPS quantificável. Veja as seções abaixo para achados sem estimativa de
     ganho."* — em vez de afirmar ausência de problemas.
  3. Aplicar a mesma correção no relatório HTML (`report/html_report.py`), que reusa o Top 3.
  4. Registrar a limitação em `docs/desvios.md` (atualizar D-31) e no README (R0-03, seção
     "limitações conhecidas").
- **Fora do escopo:** **inventar `estimated_gain_pct` para as 6 categorias sem fórmula
  defensável.** Isso repetiria exatamente o erro que D-28/D-29 recusaram e que a T3.3 evitou de
  propósito. Também fora: mudar `select_top_actions`, os pesos de confiança, ou a estrutura de
  seções.
- **Dependências:** nenhuma.
- **Critérios de aceite:**
  - Com uma coorte sintética em que o jogador tem problemas reais apenas em categorias sem
    `estimated_gain_pct`, o relatório **não** afirma que nada foi detectado.
  - O texto da seção deixa explícito o critério de entrada no ranking.
  - Texto e HTML dizem a mesma coisa.
- **Testes/validações:** teste novo com o cenário acima (jogador com mortes/active time ruins e
  zero achados quantificáveis) afirmando a mensagem correta; snapshots golden atualizados
  deliberadamente, com a mudança de texto justificada no commit.
- **Risco:** baixo (texto), mas o **impacto de não corrigir é alto**: é a única inconsistência
  conhecida entre o que o relatório afirma e o que ele mediu.
- **Esforço:** S
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — texto e HTML explicitam o escopo quantificável.

---

### R2-04 — D-30: avaliar se o golden snapshot é uma regressão enganosa

- **Objetivo:** decidir, com critério, se a fixture do golden test precisa ser corrigida.
- **Problema:** a seção "de onde veio o gap de DPS" do snapshot dourado reflete uma coorte cujos
  eventos de dano foram truncados a 25 entradas por referência (medida de contenção de tamanho da
  T3.1). As medianas de coorte dessa feature específica estão gravemente incompletas — o snapshot
  registra números que **não** correspondem ao que o algoritmo produz com dados reais.
- **Evidência atual:** D-30 em `docs/desvios.md`; `tests/fixtures/record.py`
  (`_truncate_non_fixture_event_cassettes`); diretório de cassetes em ~264 MB.
- **Escopo:**
  1. Avaliar se o snapshot, nessa seção, ainda funciona como guarda de regressão ou se apenas
     congela um artefato de fixture (uma mudança real no algoritmo de gap de DPS seria detectada?
     Um erro real passaria despercebido?).
  2. **Se for enganoso:** corrigir a *fixture* — regravar sem truncamento apenas as referências
     necessárias, ou excluir explicitamente essa seção do snapshot com justificativa no código do
     teste. Ponderar o custo em tamanho de diretório e em pontos de API da regravação.
  3. Registrar a decisão em D-30.
- **Fora do escopo:** **alterar o algoritmo de decomposição de DPS para agradar o snapshot.** A
  matemática da T3.2 é verificada independentemente por teste property-based (identidade de Oaxaca
  fechando em 1.000 casos gerados por Hypothesis) e não deve ser tocada aqui.
- **Dependências:** nenhuma. Se a regravação exigir chamadas reais à WCL, depende de R0-01
  (credenciais válidas) e de autorização de pontos.
- **Critérios de aceite:**
  - Decisão registrada com justificativa técnica.
  - Se corrigida: o snapshot passa a refletir dados representativos, e o teste continua verde.
  - Se aceita como está: D-30 registra por que o snapshot ainda é útil apesar da limitação, e a
    seção afetada fica explicitamente marcada como não-autoritativa.
- **Testes/validações:** suíte de golden tests verde depois da decisão.
- **Risco:** baixo; o custo em pontos de API de uma regravação precisa ser autorizado se for a
  opção escolhida.
- **Esforço:** S (avaliar) / M (regravar)
- **Automação:** `MIXED` (a regravação consome API real e exige autorização)
- **Status:** `DONE` — aceito como guarda estrutural não autoritativa; zero API consumida.

---

### R2-05 — Verificar e registrar as dívidas aceitas (D-26, D-27, D-28)

- **Objetivo:** transformar "dívida conhecida" em "dívida verificada, aceita e documentada".
- **Problema:** três dívidas estão sendo aceitas para a v1.0 (§6). Uma dívida aceita sem
  verificação é só um defeito não investigado.
- **Evidência atual:** D-26, D-27, D-28 em `docs/desvios.md`.
- **Escopo:**
  1. **D-28 (tabela de cooldowns vazia)** — a aceitação é **condicional**: verificar que o fallback
     (classificação MAJOR/MINOR a partir do intervalo observado) produz resultado *semanticamente
     seguro*, isto é, que uma classificação errada degrada a apresentação sem gerar achado falso. Se
     a verificação falhar, D-28 vira `FIX_BEFORE_V1` e esta tarefa muda de milestone.
  2. **D-27 (pool posicional não ampliado)** — confirmar que os banners de baixa confiança e o
     grading `⚪ amostra insuficiente (n<15)` cobrem honestamente o caso, de modo que a
     interpretação pública do relatório não seja comprometida.
  3. **D-26 (nomes de talento)** — confirmar que a apresentação por `(nodeID, rank)` está clara o
     bastante no relatório para não confundir, e registrar a ausência de fonte confiável (WCL e
     Blizzard já verificadas ao vivo, com 404/campos ausentes).
  4. Registrar as três como `ACCEPTED_V1_DEBT` em `docs/desvios.md`, com data e justificativa.
  5. Traduzir as três para linguagem de usuário na seção "limitações conhecidas" do README.
- **Fora do escopo:** resolver qualquer uma delas. D-26 e D-28 não têm fonte de dado disponível;
  D-27 é trabalho de metodologia, não de release.
- **Dependências:** alimenta R0-03.
- **Critérios de aceite:**
  - As três classificadas explicitamente em `docs/desvios.md`.
  - D-28 com a verificação de segurança semântica documentada (não apenas afirmada).
  - README com as três em linguagem de usuário.
- **Testes/validações:** para D-28, um teste que demonstre o comportamento do fallback no caso
  ambíguo, se a verificação revelar um caso interessante.
- **Risco:** baixo, exceto se a verificação de D-28 falhar.
- **Esforço:** S
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — D-26/27/28 verificadas e classificadas `ACCEPTED_V1_DEBT`.

---

## R3 — Operational Readiness

### R3-01 — Mecanismos operacionais mínimos que o runbook exige

- **Objetivo:** garantir que todo procedimento do runbook seja executável com o código existente.
- **Problema:** o runbook (R3-04) precisa responder "como identificar um job travado em `running`"
  e "o que fazer quando o orçamento está próximo do piso". Hoje **não existe** nenhuma forma de
  inspecionar a fila ou o orçamento fora do Discord: `!status` exige o bot vivo e conectado — que é
  justamente o cenário que o runbook precisa diagnosticar. `recover_from_crash()` existe, mas só é
  chamada em `on_ready`, ou seja, exige reiniciar o bot inteiro.
- **Evidência atual:** `discord_bot.py:135` (`recover_from_crash` só em `on_ready`); a CLI expõe
  `analyze`, `build-cohort`, `probe-schema`, `backfill`, `serve`, `discover`, `triage`,
  `dataset-status`, `experiment-*` — nenhum comando de saúde, fila ou orçamento.
- **Escopo:**
  1. **Primeiro** fazer a análise de lacunas: escrever os 10 procedimentos do runbook em rascunho e
     marcar cada passo que o código atual **não** suporta.
  2. Implementar apenas o mínimo que fechar essas lacunas. Candidatos prováveis, a confirmar pela
     análise:
     - um comando de saúde read-only (orçamento WCL restante e piso, fila de jobs por estado,
       tamanho/integridade do warehouse, contagens básicas);
     - um comando para reverter jobs presos em `running` sem reiniciar o bot (reusando
       `JobQueue.recover_from_crash`, que já existe e é testada).
  3. Nada além do que um procedimento do runbook exigir.
- **Fora do escopo:** dashboard, métricas, alertas, healthcheck HTTP, qualquer observabilidade
  além do necessário para os 10 procedimentos.
- **Dependências:** o rascunho do runbook (R3-04) informa esta tarefa; a implementação desta
  desbloqueia a versão final daquela. Executar em duas passadas.
- **Critérios de aceite:**
  - Todo passo do runbook final é executável por um comando real.
  - Nenhum comando novo consome pontos de API sem ser explicitamente pedido.
  - Comandos de saúde são read-only e funcionam com o bot parado.
- **Testes/validações:** testes unitários dos comandos novos (a trilha `experiment-*` já
  estabeleceu o padrão: `build_store` injetável, teste que garante que o módulo nunca importa
  `WclClient` quando é read-only local).
- **Risco:** médio — é a única tarefa de R3 que escreve código de produção novo; o risco é o escopo
  crescer. A regra de "só o que um procedimento exige" é a contenção.
- **Esforço:** M
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — `ops-status` e `recover-jobs` fecham as lacunas reais.

---

### R3-02 — Política do warehouse: classificação e desenho

- **Objetivo:** decidir, com base em fatos, o que precisa ser preservado e o que é descartável.
- **Problema:** 173 MB de dados sem nenhuma política de backup, retenção, rotação ou recuperação.
  Parte desses dados é reproduzível a custo de API; parte é **irreproduzível** e representa 8.026
  pontos já gastos e toda a base de evidência das decisões SAE/SAD.
- **Evidência atual:** ver §2.2 — inventário completo por tabela já levantado.
- **Escopo:** produzir a política, com esta classificação como ponto de partida:

  | Classe | Conteúdo | Tratamento proposto |
  |---|---|---|
  | **Irreproduzível / auditoria** | `experiment_campaigns`, `experiment_campaign_observations`, `experiment_observation_attempts`, `experiment_architecture_eval_runs`, `experiment_architecture_decision_runs`, `experiment_calibration_runs` | Backup obrigatório antes de qualquer operação destrutiva. Nunca purgar na v1.0. É a base de evidência da decisão de tirar a Fase 4 do escopo, e o insumo da v1.1. |
  | **Reproduzível a custo de API** | `logs` (741), `data/raw/*.parquet` (39 MB), `discovery_reports` (801), `discovery_fights` (1.963), `discovery_targets` (24.723), `cohort_candidates` | Backup desejável (recomprar custa pontos), purgável sob critério documentado. |
  | **Operacional / regenerável** | `runs`, `jobs`, `backfill_checkpoints`, `spells` | Sem backup especial; recriável. |

  Documentar também:
  - **Crescimento esperado** — estimar a partir do tamanho por log medido (`data/raw` ≈ 39 MB /
    741 logs ≈ 54 KB por log em parquet, mais a linha em `logs`) e do volume plausível de uso do
    bot em produção.
  - **Backup** — o quê, com que frequência, para onde, como verificar.
  - **Retenção** — por quanto tempo cada classe vive.
  - **Rotação/arquivamento** — quando e como (proposta: arquivar parquet antigo, nunca deletar
    tabela experimental).
  - **Recuperação** — o procedimento, e o que é perdido em cada cenário.
- **Fora do escopo:** **deletar, purgar ou mover qualquer dado durante esta tarefa.** Ela produz
  documento e classificação, nada mais.
- **Dependências:** nenhuma. Alimenta R3-03 e R3-04.
- **Critérios de aceite:**
  - Documento com as 4 políticas (backup, retenção, rotação, recuperação) e a classificação por
    tabela.
  - Toda classe de dado do warehouse está classificada — nenhuma tabela sem destino.
  - A implementação proposta é proporcional a uma v1.0 pequena (arquivo copiado e verificado, não
    infraestrutura).
- **Testes/validações:** nenhuma execução destrutiva. A validação real é R3-03.
- **Risco:** baixo.
- **Esforço:** S
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — política em `docs/warehouse-policy.md`.

---

### R3-03 — Backup e restore: implementar e testar de verdade

- **Objetivo:** um backup nunca testado não é um backup.
- **Problema:** não existe procedimento de backup, e o warehouse de 134 MB carrega dados
  irreproduzíveis.
- **Evidência atual:** §2.2; nenhum script, comando ou documento de backup existe no repositório.
- **Escopo:**
  1. Procedimento de backup proporcional: cópia consistente do `warehouse.duckdb` (com o bot
     parado, ou usando o mecanismo de cópia consistente do DuckDB) + `data/raw/` + `spells.json`.
  2. Procedimento de restore.
  3. **Ensaio real de recuperação**: restaurar em um diretório limpo e verificar por contagem de
     linhas que todas as tabelas de §2.2 voltaram íntegras — em especial as 6 tabelas
     irreproduzíveis.
  4. Registrar tempo e tamanho medidos do ensaio.
- **Fora do escopo:** backup remoto/nuvem, agendamento automático, criptografia. Uma v1.0 de um bot
  pessoal não precisa disso, e o dado não contém informação sensível de terceiros além de nomes
  públicos de personagens.
- **Dependências:** R3-02.
- **Critérios de aceite:**
  - Backup e restore executados **de verdade**, pelo menos uma vez, com evidência de contagem.
  - Zero perda nas 6 tabelas irreproduzíveis no ensaio.
  - O procedimento está no runbook e é executável por comando documentado.
- **Testes/validações:** o ensaio de restore é a validação. Se houver script, ele ganha teste.
- **Risco:** baixo, desde que o ensaio use um diretório de destino separado — **nunca** restaurar
  por cima do warehouse de produção durante o teste.
- **Esforço:** S
- **Automação:** `MIXED` (o ensaio pode ser executado pelo agente; a política de onde guardar o
  backup é decisão humana)
- **Status:** `DONE` — ensaio separado íntegro; destino permanente continua escolha humana.

---

### R3-04 — Runbook operacional

- **Objetivo:** um documento que responde concretamente o que fazer quando algo dá errado.
- **Problema:** não existe. Toda a operação hoje depende de conhecimento não escrito.
- **Evidência atual:** `docs/` tem 16 arquivos, todos de implementação, análise ou experimento —
  nenhum de operação.
- **Escopo:** criar `docs/runbook.md` respondendo, com comandos reais:
  1. Orçamento da API WCL próximo do piso (`api_points_floor`, default 1.000, teto medido 3.600/h):
     como detectar, o que o código já faz sozinho (`claim_next` pula jobs que o orçamento não
     permite; reserva de 25% para o caminho interativo) e o que o operador deve fazer.
  2. Orçamento esgotado: comportamento esperado (`RateLimitBudgetExceeded` → job reenfileirado, não
     falho — T1.8 §3/D-18), quanto tempo até o reset (`pointsResetIn`), o que **não** fazer.
  3. Como identificar job travado em `running`.
  4. Como recuperar/reprocessar um job com segurança (`recover_from_crash` e o que ela garante).
  5. Discord desconecta: comportamento do `on_ready` na reconexão, o guard de worker único, o que
     verificar.
  6. WCL indisponível: retry/backoff já embutidos (4 tentativas, backoff exponencial com jitter),
     `RateLimitCheckFailed` (FIX.6), quando intervir.
  7. Warehouse corrompido: sintomas, o que **não** fazer, como isolar.
  8. Como restaurar backup (aponta para R3-03).
  9. Como parar e reiniciar o bot com segurança (o que acontece com jobs em voo).
  10. Comandos de verificação de saúde básica (dependem de R3-01).
- **Fora do escopo:** procedimentos que o código atual **não suporta**. Se um procedimento precisar
  de mecanismo inexistente, ele vira escopo de R3-01 — não vira texto aspiracional no runbook.
- **Dependências:** R3-01 (mecanismos), R3-02 e R3-03 (backup/restore). Executar em duas passadas:
  rascunho → análise de lacunas → R3-01 → runbook final.
- **Critérios de aceite:**
  - Os 10 itens respondidos com comandos reais e executáveis.
  - Nenhum passo depende de mecanismo inexistente.
  - Um operador que não escreveu o código consegue seguir cada procedimento.
- **Testes/validações:** executar pelo menos os procedimentos não destrutivos (1, 3, 9, 10) uma vez
  e confirmar que funcionam como escrito.
- **Risco:** baixo.
- **Esforço:** M
- **Automação:** `AUTOMATABLE` (com a validação de execução em `MIXED`)
- **Status:** `DONE` — 10 procedimentos executáveis em `docs/runbook.md`.

---

## R4 — Release Candidate

### R4-01 — Triagem dos 10 arquivos acima de 300 linhas

- **Objetivo:** decidir por arquivo, sem churn cosmético às vésperas da release.
- **Problema:** os portões das Fases 1, 2 e 3 exigiam "nenhum arquivo `src/botgitgud/**/*.py` acima
  de 300 linhas" e todos foram verificados nessa condição. A trilha experimental/phase4 quebrou a
  convenção sem registrar desvio formal.
- **Evidência atual (medida):**

  | Arquivo | Linhas | Trilha |
  |---|---|---|
  | `phase4/experiment_decision.py` | 439 | experimental (SAD.1) |
  | `phase4/experiment_calibration.py` | 433 | experimental (SAD.2) |
  | `phase4/experiment_evaluate.py` | 411 | experimental (SAE.8) |
  | `phase4/experiment_store.py` | 384 | experimental (EC) |
  | `cli_experiment.py` | 377 | experimental (EC.5-6) |
  | `phase4/experiment_models.py` | 356 | experimental (SAE.8) |
  | `ingest/log_fetcher.py` | 352 | **produção** |
  | `wcl/client.py` | 326 | **produção** |
  | `cli_experiment_calibrate.py` | 324 | experimental (SAD.2) |
  | `analysis/dataset_status.py` | 317 | data gate |

- **Escopo:**
  1. Dividir **somente** onde houver responsabilidades múltiplas claras, dificuldade concreta de
     teste/manutenção, ou violação de boundary arquitetural.
  2. Atenção especial aos dois arquivos de **produção** (`log_fetcher.py`, `wcl/client.py`) — são
     os que a v1.0 realmente executa. Ambos estavam sob 300 nos portões anteriores (299 e 299) e
     cresceram depois; vale entender o que entrou.
  3. Para os 8 restantes (trilha experimental, não executada pela v1.0): registrar **desvio formal
     em `docs/desvios.md`** declarando que a convenção de 300 linhas não se aplica à trilha
     experimental/Phase 4, com justificativa.
- **Fora do escopo:** **refactor cosmético em massa.** Dividir arquivo só para satisfazer uma
  convenção histórica, sem benefício de manutenção, é churn e risco de regressão antes da release.
- **Dependências:** nenhuma. Deve rodar depois de R1/R2/R3 para não conflitar com mudanças em voo.
- **Critérios de aceite:**
  - Decisão registrada por arquivo (dividir / aceitar com desvio).
  - Nenhum split feito sem justificativa de manutenção escrita.
  - Se houver split: suíte verde, `pyright` e `ruff` limpos, zero mudança de comportamento.
- **Testes/validações:** suíte completa antes e depois; golden tests idênticos.
- **Risco:** o risco está em **fazer demais**, não em fazer de menos.
- **Esforço:** S (aceitar com desvio) / M (se algum split se justificar)
- **Automação:** `AUTOMATABLE`
- **Status:** `DONE` — triagem por arquivo e desvio formal, sem refactor cosmético.

---

### R4-02 — Executar o portão de Release Candidate

- **Objetivo:** verificar objetivamente, item a item, que a release está pronta para o soak.
- **Problema:** sem um portão explícito, "pronto" vira opinião.
- **Evidência atual:** os portões das Fases 0–3 seguiram exatamente esse formato e funcionaram.
- **Escopo:** executar e registrar o checklist de §8, item por item, com o comando ou a evidência
  que comprova cada um. Qualquer item reprovado bloqueia a promoção a RC e vira tarefa.
- **Fora do escopo:** bump de versão, commit de release, tag — tudo isso é R5, **depois** do soak.
- **Dependências:** R0-01, R0-02, R0-03, R0-04, R1-01, R1-02, R2-01, R2-02, R2-03, R2-04, R2-05,
  R3-01, R3-02, R3-03, R3-04, R4-01. **Toda tarefa anterior.**
- **Critérios de aceite:** os 16 itens de §8 verdes, com evidência registrada em
  `docs/progresso.md`.
- **Testes/validações:** `pytest` completo, `ruff check .`, `ruff format --check .`,
  `pyright src tests`.
- **Risco:** baixo em si; alto em revelar pendências.
- **Esforço:** S (se tudo estiver pronto)
- **Automação:** `MIXED` (itens mecânicos automatizáveis; a confirmação de credenciais revogadas e
  de `serve` validado é humana)
- **Status:** `PENDING_DEPENDENCY` — parte mecânica executada; aguarda R0-01/R1-01.

---

## R5 — Production Soak & v1.0

### R5-01 — Soak de ≥24 horas em execução contínua

- **Objetivo:** provar estabilidade sob tempo real, não sob teste.
- **Problema:** o bot nunca rodou por mais de uma execução pontual. Vazamento de memória, deriva de
  event loop, reconexão do Discord, esgotamento e reset de orçamento e jobs órfãos só aparecem com
  tempo.
- **Evidência atual:** o processo nunca subiu (§2.3).
- **Escopo:** manter `serve` rodando por ≥24h contínuas, registrando:
  - crashes; restarts; jobs recebidos; jobs concluídos; jobs falhos; jobs presos;
  - consumo de pontos de API ao longo do período; latência aproximada por análise;
  - falhas de WCL; falhas/desconexões de Discord.
- **Fora do escopo:** teste de carga; múltiplos servidores Discord; qualquer atividade de coleta
  experimental.
- **Dependências:** R4-02 (o RC precisa passar antes do soak).
- **Critérios de aceite:**
  - ≥24h contínuas.
  - Zero crashes não recuperados.
  - Zero jobs perdidos silenciosamente (todo job termina em `done` ou `failed`, ou está
    legitimamente `queued`).
  - **O piso de pontos da API WCL permanece ≥ 1.000 durante todo o período** (valor atual de
    `api_points_floor`; só muda por decisão explícita posterior).
  - Evidências **sanitizadas** registradas em `docs/progresso.md` — sem tokens, sem IDs de usuário
    do Discord, sem dados pessoais.
- **Testes/validações:** observação real. **O agente não deve declarar concluída.**
- **Risco:** médio — é aqui que aparecem os defeitos que nenhum teste pega.
- **Esforço:** 24h+ de calendário, esforço humano baixo (observação).
- **Automação:** `HUMAN_REQUIRED`
- **Status:** `PENDING_DEPENDENCY` — aguarda RC e observação humana real.

---

### R5-02 — Três análises reais de jogadores distintos

- **Objetivo:** provar que o produto entrega valor real a usuários reais.
- **Problema:** todas as análises até hoje foram contra a mesma fixture (Zarad,
  `PtfBbQKRY9d6zAMC` fight 1) ou contra dados sintéticos. Um único caso nunca exercita o
  relaxamento de covariáveis, `InsufficientCohort`, divergência de build, nem o portão de escopo
  em condições variadas.
- **Evidência atual:** `runs` = 0 linhas no warehouse de produção.
- **Escopo:** ≥3 análises de personagens/specs/encontros **diferentes**, cobrindo idealmente: um
  caso de coorte quente, um de coorte fria (que passe pela fila), e um caso de erro esperado (spec
  fora de escopo ou coorte insuficiente) para confirmar a mensagem ao usuário.
- **Fora do escopo:** publicar dados de terceiros sem autorização.
- **Dependências:** R4-02. **Não** depende de R5-01: pode ser executada **durante** o soak ou
  depois dele, e não precisa esperar as 24h terminarem para começar. Executar durante o soak é
  inclusive preferível — cada análise real vira carga observada pelo soak.
- **Critérios de aceite:**
  - 3 relatórios reais produzidos e entregues no Discord.
  - Relatórios registrados em `docs/progresso.md` de forma **sanitizada** (sem IDs de Discord; usar
    nomes de personagem apenas se públicos e não sensíveis).
  - Nenhum crash, nenhuma mensagem em inglês vazando para o usuário, nenhum stack trace exposto.
- **Testes/validações:** inspeção humana dos relatórios.
- **Risco:** baixo.
- **Esforço:** S (humano)
- **Automação:** `HUMAN_REQUIRED`
- **Status:** `PENDING_DEPENDENCY` — aguarda RC/Discord/API e inspeção humana.

---

### R5-03 — Bump de versão e commit de release

- **Objetivo:** marcar a release **depois** que ela foi provada, nunca antes.
- **Problema:** versionar cedo cria uma v1.0 que não corresponde a nada validado.
- **Evidência atual:** `pyproject.toml` = `0.1.0`; nenhuma tag no repositório.
- **Escopo:**
  1. `pyproject.toml`: `version = "0.1.0"` → `"1.0.0"`.
  2. `docs/progresso.md`: seção de fechamento da v1.0 com as evidências de R4-02, R5-01, R5-02.
  3. Commit de release.
- **Fora do escopo:** a tag (R5-04). Qualquer mudança funcional.
- **Dependências:** R5-01, R5-02.
- **Critérios de aceite:** versão bumpada, progresso atualizado, suíte verde, commit criado no
  formato do projeto.
- **Testes/validações:** suíte completa uma última vez antes do commit.
- **Risco:** baixo.
- **Esforço:** S
- **Automação:** `MIXED` (execução automatizável; a autorização de que a release está aprovada é
  humana)
- **Status:** `PENDING_DEPENDENCY` — aguarda R5-01/R5-02 e aprovação.

---

### R5-04 — Tag `v1.0.0`

- **Objetivo:** publicar o marco.
- **Problema:** —
- **Evidência atual:** nenhuma tag existe no repositório.
- **Escopo:** criar a tag `v1.0.0` apontando para o commit de release.
- **Fora do escopo:** push para remoto — **e nenhum push deve ocorrer antes de R0-01 e R0-02 terem
  fechado.**
- **Dependências:** R5-03, e aprovação humana explícita.
- **Critérios de aceite:** tag criada com autorização humana registrada.
- **Testes/validações:** —
- **Risco:** baixo.
- **Esforço:** S
- **Automação:** `HUMAN_REQUIRED`
- **Status:** `PENDING_DEPENDENCY` — aguarda commit e autorização humana; nenhuma tag criada.

---

## 5. Dependências e caminho crítico

### 5.1 Grafo (simplificado)

```
R0-01 ACCEPTED_RISK (fora do caminho crítico desde 2026-08-24)
                             ┌──> R1-01 (smoke real) ──> R4-02 (RC) ─┬─> R5-01 (soak) ────┬─> R5-03 ─> R5-04
R0-02 (auditoria git) ───────┤                              ^        └─> R5-02 (3 análises) ┘
R0-04 (.env.example) ──> R0-03 (README) ───────────────────>┤            (pode correr durante o soak)
                                                            │
R2-01 (contrato entrega) ──> R1-02 (testes Discord) ───────>┤
R2-02 (backfill) ──────────────────────────────────────────>┤
R2-03 (D-31) ──────────────────────────────────────────────>┤
R2-04 (D-30) ──────────────────────────────────────────────>┤
R2-05 (dívidas) ──> R0-03                                   │
                                                            │
R3-02 (política) ──> R3-03 (backup/restore) ──┐             │
R3-04 (runbook rascunho) ──> R3-01 (mecanismos) ──> R3-04 (final) ──> ┤
                                                            │
R4-01 (>300 linhas) ───────────────────────────────────────>┘
```

### 5.2 Caminho crítico

**R2-01 → R1-02 → R1-01 → R4-02 → R5-01 → R5-03 → R5-04**

Justificativa: R2-01 e R1-02 devem preceder R1-01 para que o smoke encontre apenas o que teste
determinístico não alcança. R4-02 exige tudo. O soak é irredutível em tempo de calendário.

**R0-01 saiu do caminho crítico em 2026-08-24.** Enquanto a rotação estava pendente, ela bloqueava
R1-01 — fazer o smoke com credenciais que seriam revogadas obrigaria a repeti-lo. Com a decisão de
`ACCEPTED_RISK`, o smoke roda com as credenciais atuais e não precisa ser refeito. Se qualquer
gatilho de R0-01 ocorrer, a rotação volta a ser obrigatória e o smoke precisa ser repetido depois
dela.

**R5-02 não está no caminho crítico:** ela parte de R4-02, corre em paralelo com R5-01
(preferencialmente durante o soak, gerando carga real observada) e se junta antes de R5-03. Com
esforço `S` contra as 24h de R5-01, ela nunca é o gargalo — a menos que uma análise real revele um
defeito, caso em que a correção entra no caminho crítico.

**Componente irredutível:** ~24h de soak + o tempo humano de R0-01 e R1-01. Tudo o mais é
paralelizável.

### 5.3 Ordem de execução recomendada

1. **Onda 1 (paralela, agente):** R0-04, R2-01, R2-02, R2-03, R2-05, R3-02.
2. **Onda 2 (agente):** R1-02 (precisa de R2-01), R0-03 (precisa de R0-04/R2-02/R2-05), R3-04
   rascunho → R3-01 → R3-04 final, R3-03, R2-04, R4-01.
3. **Onda 3 (humano):** R0-01, R0-02 (a varredura pode ir na onda 1; a remediação, se houver, é
   aqui).
4. **Onda 4:** R1-01 (humano) → correções do que aparecer → R4-02.
5. **Onda 5:** R5-01 + R5-02 (humano) → R5-03 → R5-04.

---

## 6. Triagem de dívidas técnicas para a v1.0

| Dívida | Assunto | Classificação | Justificativa | Tarefa |
|---|---|---|---|---|
| **D-26** | Nomes de nós de talento | `ACCEPTED_V1_DEBT` | Não existe fonte confiável: WCL (`gameData.ability`) e Blizzard (`/data/wow/talent/{id}`, `/data/wow/spell-tree-node/{id}`) já verificadas ao vivo, retornam `null`/404. Exibir `(nodeID, rank)` é honesto; inventar nome seria pior. | R2-05 |
| **D-27** | Pool posicional não ampliado | `ACCEPTED_V1_DEBT` | Não compromete a interpretação pública: o grading já degrada para `⚪ amostra insuficiente` abaixo de n=15 e o relatório exibe banners de baixa confiança posicional. É melhoria metodológica, não correção. | R2-05 (verificação) |
| **D-28** | Tabela de cooldowns base vazia | `ACCEPTED_V1_DEBT` **(condicional)** | Aceitável **somente** se o fallback (classificação por intervalo observado) for semanticamente seguro — ele sempre foi o único ramo em produção. A verificação é obrigatória; se falhar, reclassificar para `FIX_BEFORE_V1`. | R2-05 |
| **D-30** | Golden snapshot com eventos truncados | `FIX_BEFORE_V1` **(condicional)** | Corrigir a **fixture** se ela tornar a regressão enganosa. Nunca alterar o algoritmo para agradar o snapshot — a matemática da T3.2 é verificada independentemente por property-based test. | R2-04 |
| **D-31** | 6 de 8 categorias nunca entram no Top 3 | **duas partes** | **Texto/UI: `FIX_BEFORE_V1`** — "✅ Nenhum problema material detectado." pode ser factualmente falso e é a única inconsistência conhecida entre o que o relatório afirma e o que ele mediu. **Fórmulas ausentes: `ACCEPTED_V1_DEBT`** — não existe conversão defensável para % de DPS nessas 6 categorias, e **inventá-la está proibido**. | R2-03 |
| **D-13** | `backfill` stub | `REMOVE_FROM_V1_OUTPUT` (provável) | Comando público que sempre falha não pertence a um 1.0. Decisão final depende da busca por requisito real. | R2-02 |
| **D-22** | `discord_bot.py` sem cobertura | `FIX_BEFORE_V1` | É o código que o usuário efetivamente vê e o único arquivo em 0%. Cobrir contratos críticos, não percentual. | R1-02 |
| **Convenção 300 linhas** | 10 arquivos acima | `ACCEPTED_V1_DEBT` (provável, trilha experimental) | Refactor cosmético em massa antes da release é churn e risco. Decisão por arquivo, com atenção aos 2 de produção. | R4-01 |

---

## 7. Resumo de automação

| Tarefa | Automação |
|---|---|
| R0-01 Rotação de credenciais | `HUMAN_REQUIRED` |
| R0-02 Auditoria do histórico Git | `MIXED` |
| R0-03 README | `AUTOMATABLE` |
| R0-04 `.env.example` | `AUTOMATABLE` |
| R1-01 Smoke test real | `HUMAN_REQUIRED` |
| R1-02 Testes dos handlers Discord | `AUTOMATABLE` |
| R2-01 Contrato de entrega | `AUTOMATABLE` |
| R2-02 `backfill` | `AUTOMATABLE` |
| R2-03 D-31 Top 3 | `AUTOMATABLE` |
| R2-04 D-30 fixture | `MIXED` |
| R2-05 Dívidas aceitas | `AUTOMATABLE` |
| R3-01 Mecanismos operacionais | `AUTOMATABLE` |
| R3-02 Política do warehouse | `AUTOMATABLE` |
| R3-03 Backup/restore testado | `MIXED` |
| R3-04 Runbook | `AUTOMATABLE` |
| R4-01 Arquivos >300 linhas | `AUTOMATABLE` |
| R4-02 Portão RC | `MIXED` |
| R5-01 Soak 24h | `HUMAN_REQUIRED` |
| R5-02 Três análises reais | `HUMAN_REQUIRED` |
| R5-03 Bump + commit de release | `MIXED` |
| R5-04 Tag `v1.0.0` | `HUMAN_REQUIRED` |

**11 automatizáveis · 5 mistas · 5 humanas.**

---

## 8. Portão de Release Candidate

Todos os itens devem estar verdes para promover a RC. Verificado por R4-02.

- [ ] `ruff check .` — GREEN
- [ ] `ruff format --check .` — GREEN
- [ ] `pyright src tests` — GREEN
- [ ] Suíte completa (`pytest`) — GREEN
- [ ] Nenhum segredo conhecido no histórico Git (R0-02)
- [ ] R0-01 tem decisão explícita de segurança: rotação concluída **OU** risco residual
      formalmente aceito pelo proprietário — no estado atual, **risco formalmente aceito**
      (`ACCEPTED_RISK`, 2026-08-24), com os gatilhos de revogação registrados em R0-01
- [ ] README completo (R0-03)
- [ ] Runbook completo (R3-04)
- [ ] `serve` validado em execução real (R1-01)
- [ ] `!status` validado (R1-01)
- [ ] `!analisar` validado ponta a ponta (R1-01)
- [ ] Worker/fila validados, incluindo recuperação após restart (R1-01)
- [ ] Contratos síncrono e assíncrono semanticamente equivalentes (R2-01)
- [ ] Nenhum comando público conhecido que seja stub (R2-02)
- [ ] Backup e restore do warehouse documentados **e testados** (R3-03)
- [ ] Nenhum desvio em `docs/desvios.md` marcado `BLOQUEADO` sem decisão explícita registrada

---

## 9. Portão final da v1.0

Além de todo o portão RC:

- [ ] Soak de **≥24h contínuas** concluído (R5-01)
- [ ] Zero crashes não recuperados durante o soak
- [ ] Zero jobs perdidos silenciosamente durante o soak
- [ ] Piso de pontos da API WCL permaneceu **≥ 1.000** durante todo o soak
- [ ] Métricas do soak registradas (crashes, restarts, jobs recebidos/concluídos/falhos/presos,
      consumo de API, latência, falhas WCL, falhas Discord)
- [ ] **≥3 análises reais** de jogadores distintos, com relatórios sanitizados registrados (R5-02)
- [ ] Nenhum dado pessoal e nenhum token em documento versionado
- [ ] `pyproject.toml` = `1.0.0` (R5-03)
- [ ] Commit de release criado (R5-03)
- [ ] Tag `v1.0.0` criada com autorização humana (R5-04)
- [ ] `phase4_model_registry` continua com **0 linhas** — a v1.0 não expõe ML

---

## 10. Post-v1.0 / v1.1 — Fase 4 e ML

**Nada desta seção pertence à v1.0. Nada aqui deve ser executado.**

Movido conceitualmente para esta seção:

| Item | Estado congelado |
|---|---|
| T4.1 — Dataset de treino | Não iniciada. A especificação original (por `Phase4Target`) é inviável. |
| T4.2 — Modelo + SHAP | Não iniciada. `shap` **não instalado**; `scikit-learn` e `lightgbm` presentes. |
| T4.3 — Backtesting e calibração | Não iniciada. Começaria com um resultado negativo já conhecido (SAD.2). |
| T4.4 — SimulationCraft | Não iniciada. O próprio documento exige confirmação humana antes de começar. |
| Retomada das 597 observações pendentes | **Não autorizada.** À taxa medida (8.026 pts / 603 obs ≈ 13,3 pts/obs), completar custaria ~7.900 pontos adicionais. O teto atual (8.040) está esgotado em 8.026. |
| Instalação de SHAP | Não autorizada. |
| `MODEL_GLOBAL` | `ADVANCE_TO_VALIDATION` (SAD.1) — congelado, não promovido. |
| Calibração | `INTERNAL_EXPLANATION_ONLY` (SAD.2) — congelado. |
| Promoção em `phase4_model_registry` | Não autorizada. Tabela permanece vazia. |

### Condição de retomada

> Qualquer retomada da Fase 4 exige um **roadmap próprio de Phase 4**, construído a partir dos
> resultados de SAE e SAD — **não** uma continuação da arquitetura `MODEL_TARGET` original, que já
> está classificada `REJECT_FOR_CURRENT_PHASE4` por evidência quantitativa.

Esse roadmap futuro precisará, no mínimo:
- partir de `MODEL_GLOBAL` como arquitetura, não de modelos por target;
- tratar `INTERNAL_EXPLANATION_ONLY` como o ponto de partida, e definir o que precisaria mudar para
  que uma previsão calibrada chegasse ao jogador;
- reconciliar formalmente o gate histórico de "≥5.000 logs por `Phase4Target`" com a arquitetura
  global — por emenda explícita e datada, nunca declarando o gate antigo como cumprido;
- decidir e autorizar explicitamente o orçamento de API da retomada.

---

## 11. Decisões humanas ainda necessárias

1. ~~Quando executar a rotação de credenciais (R0-01).~~ **Decidido em 2026-08-24:** não rotacionar
   nesta release; risco residual formalmente aceito (`ACCEPTED_RISK`). Revisar imediatamente se
   qualquer um dos 7 gatilhos listados em R0-01 ocorrer.
2. **Como proceder se R0-02 encontrar segredo no histórico** — reescrever histórico é decisão
   humana, com plano apresentado antes.
3. **Destino do `backfill` (R2-02)** — remover é a recomendação, mas confirme se você tem algum uso
   previsto.
4. **Se a regravação de fixture da D-30 vale o custo em pontos de API** (R2-04).
5. **Onde guardar o backup do warehouse** (R3-03) — local, disco externo, nuvem.
6. **Quando fazer o smoke test real (R1-01)** e quando iniciar a janela de 24h do soak (R5-01).
7. **Quais personagens/logs usar nas 3 análises reais** (R5-02), e o que pode ser publicado nos
   documentos.
8. **Aprovação final da release** antes de R5-03/R5-04.
9. **Se o piso de 1.000 pontos de API deve mudar** — a proposta atual é mantê-lo; alterá-lo exige
   decisão explícita.

---

## 12. Registro formal de decisão

**Data:** 2026-08-24
**Decisão:** a Fase 4 (T4.1–T4.4) foi **retirada do escopo da v1.0** por decisão de produto.
**Base:** evidência experimental produzida pelas trilhas SAE (`dc0d2ac`) e SAD (`df77f26`,
`f586a13`) — `MODEL_TARGET = REJECT_FOR_CURRENT_PHASE4`, `MODEL_GLOBAL = ADVANCE_TO_VALIDATION`,
`SAD.2 = INTERNAL_EXPLANATION_ONLY`.
**Alcance:** nenhum portão histórico foi alterado, relaxado ou marcado como cumprido. O gate de
≥5.000 logs por `Phase4Target` permanece vigente e não cumprido; a Fase 4 inteira saiu do escopo
desta release em vez de ser renegociada.
**Consequência:** botgitgud v1.0 entrega as Fases 0–3 em produção, sem camada de ML exposta ao
jogador. A Fase 4 é candidata a v1.1, sujeita a roadmap próprio (§10).
