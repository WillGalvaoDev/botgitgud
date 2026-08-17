# BotGITGUD — Documento de Implementação

**Versão:** 1.0
**Data:** 2026-08-17
**Documento de origem:** [`docs/relario.md`](./relario.md) (auditoria)
**Público-alvo:** agente de implementação autônomo

---

## 0. Como usar este documento

### 0.1 Regras invioláveis

1. **Execute as tarefas na ordem numérica.** Cada tarefa declara pré-requisitos. Não pule, não reordene, não agrupe.
2. **Uma tarefa = um commit.** Mensagem: `[T<id>] <título da tarefa>`. Nunca misture tarefas em um commit.
3. **O bot deve continuar funcionando ao fim de cada tarefa.** Se uma tarefa quebra o comando `!analisar`, ela está incompleta. A única exceção é a T1.6, que declara explicitamente uma janela de refatoração.
4. **Não implemente nada que não esteja neste documento.** Se você identificar uma melhoria fora de escopo, registre em `docs/backlog.md` e siga adiante.
5. **Todo critério de aceite é verificável por comando.** Se você não conseguiu rodar o comando de verificação, a tarefa não está pronta.
6. **Constantes numéricas deste documento são normativas.** Elas foram escolhidas deliberadamente. Não "ajuste" valores sem seguir o §0.2.

### 0.2 Protocolo de escalonamento (o que fazer quando houver dúvida)

Você **não deve adivinhar**. Quando encontrar qualquer uma das situações abaixo:

- um campo GraphQL especificado aqui não existe no schema real da API;
- um valor de retorno tem formato diferente do documentado aqui;
- duas partes deste documento se contradizem;
- uma constante normativa produz resultado absurdo na validação;
- a tarefa depende de um dado que a API não fornece;

faça **exatamente isto**:

1. **Pare a tarefa.**
2. Adicione uma entrada em `docs/desvios.md` (crie o arquivo se não existir) usando o template do §0.3.
3. Se existir um caminho alternativo **óbvio e equivalente** (ex.: o campo se chama `fightID` em vez de `fight_id`), aplique-o, documente no desvio e continue.
4. Se **não** existir caminho óbvio, marque a tarefa como `BLOQUEADA` em `docs/progresso.md` e **pergunte ao usuário**. Enquanto isso, siga para a próxima tarefa que não dependa da bloqueada.

### 0.3 Template de desvio

```markdown
## D-<n> — <título curto>
- **Tarefa:** T<id>
- **Documento diz:** <o que a spec afirma>
- **Realidade:** <o que você observou, com evidência: resposta da API, erro, etc.>
- **Ação tomada:** <aplicado alternativo X | BLOQUEADO aguardando usuário>
- **Impacto:** <nenhum | afeta tarefas T<a>, T<b>>
```

### 0.4 Rastreamento de progresso

Crie e mantenha `docs/progresso.md`:

```markdown
| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T0.0 | ✅ FEITA | abc1234 | |
| T0.1 | 🟡 EM ANDAMENTO | | |
| T0.2 | ⛔ BLOQUEADA | | ver D-1 |
| T0.3 | ⬜ PENDENTE | | |
```

Atualize **antes** de começar e **depois** de terminar cada tarefa.

### 0.5 Definição de Pronto (DoD) — aplica-se a toda tarefa

Uma tarefa só está pronta quando **todos** estes itens passam:

- [ ] O critério de aceite específico da tarefa foi verificado por execução de comando.
- [ ] `ruff check .` sem erros.
- [ ] `ruff format --check .` sem diferenças.
- [ ] `pyright` sem erros nos módulos tocados.
- [ ] `pytest` verde (todos os testes, não só os novos).
- [ ] Nenhum segredo novo em arquivo versionado.
- [ ] `docs/progresso.md` atualizado.
- [ ] Commit criado com a mensagem no formato correto.

---

## 1. Estado alvo

### 1.1 Estrutura de diretórios final

```
BotGITGUD/
├── pyproject.toml
├── .gitignore
├── .env                          # NUNCA versionado
├── .env.example                  # versionado, sem valores
├── README.md
├── docs/
│   ├── relario.md                # auditoria (não editar)
│   ├── implementacao.md          # este arquivo (não editar)
│   ├── progresso.md              # você mantém
│   ├── desvios.md                # você mantém
│   └── backlog.md                # você mantém
├── src/botgitgud/
│   ├── __init__.py
│   ├── config.py                 # Settings (pydantic-settings)
│   ├── logging_setup.py          # structlog
│   ├── errors.py                 # hierarquia de exceções
│   ├── wcl/
│   │   ├── __init__.py
│   │   ├── client.py             # HTTP, OAuth, retry, rate limit
│   │   ├── queries.py            # strings GraphQL
│   │   └── schema_probe.py       # introspecção (T0.1)
│   ├── blizzard/
│   │   ├── __init__.py
│   │   └── client.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── models.py             # dataclasses do domínio
│   │   └── spells.py             # SpellCatalog
│   ├── ingest/
│   │   ├── __init__.py
│   │   ├── log_fetcher.py
│   │   ├── rankings.py
│   │   └── store.py              # DuckDB + Parquet
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── alignment.py          # alinhamento monotônico
│   │   ├── cohort.py             # seleção de coorte
│   │   ├── profile.py            # perfil estatístico
│   │   ├── damage_gap.py         # decomposição de DPS
│   │   ├── features.py           # uptimes, active time, recursos
│   │   └── findings.py           # priorização
│   ├── report/
│   │   ├── __init__.py
│   │   ├── builder.py            # monta o objeto Report
│   │   ├── text.py               # renderer Discord
│   │   └── html.py               # renderer web
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── discord_bot.py
│   │   └── jobs.py               # fila de análises
│   └── cli.py                    # entrypoint de batch/debug
├── data/                         # NUNCA versionado
│   ├── raw/                      # parquet de eventos
│   ├── warehouse.duckdb
│   └── cache/
├── tests/
│   ├── conftest.py
│   ├── fixtures/                 # cassetes de API + logs sintéticos
│   ├── unit/
│   └── golden/
└── legacy/
    └── bot.py                    # cópia congelada do original (T0.0)
```

### 1.2 Stack normativa

| Camada | Escolha | Justificativa |
|---|---|---|
| Python | 3.11+ | `tomllib`, melhor `asyncio`, tipos modernos |
| Gerenciador | `uv` (fallback: `pip` + `venv`) | rápido, lockfile determinístico |
| Config | `pydantic-settings` | validação tipada de `.env` |
| HTTP | `httpx` | timeouts nativos, sync+async, HTTP/2 |
| Logs | `structlog` | logging estruturado com contexto |
| Dados | `duckdb` + `pyarrow` | warehouse local sem infra |
| DataFrames | `polars` | rápido, API explícita, sem índice implícito |
| Testes | `pytest`, `pytest-cov`, `hypothesis`, `syrupy` | unit + property + golden |
| Lint/Format | `ruff` | substitui black+isort+flake8 |
| Tipos | `pyright` (modo `standard`) | |
| Discord | `discord.py` (já em uso) | manter |
| ML (Fase 4) | `lightgbm`, `shap`, `scikit-learn` | só instalar na Fase 4 |

**Não** adicione dependências fora desta lista sem registrar um desvio.

### 1.3 Convenções de código

- **Type hints obrigatórios** em toda função pública. `from __future__ import annotations` no topo de cada módulo.
- **Dataclasses frozen** para modelos de domínio: `@dataclass(frozen=True, slots=True)`.
- **Nunca retorne tuplas com mais de 2 elementos.** Use dataclass.
- **Nunca use `except Exception:` genérico** fora do boundary mais externo (handler do Discord e `cli.py`). Capture exceções específicas.
- **Erros de domínio** herdam de `BotGitGudError` (`errors.py`). O boundary externo traduz para mensagem de usuário.
- **Logging:** `log = structlog.get_logger(__name__)`. Nunca `print()` em código de produção.
- **Sem estado global mutável.** Caches são objetos injetados, não variáveis de módulo.
- **Idioma:** código, nomes e docstrings em **inglês**; mensagens ao usuário final em **português**.

### 1.4 Escopo de specs (normativo)

**A ferramenta analisa exclusivamente specs de DPS puro.** Estão fora de escopo, de forma definitiva:

| Fora de escopo | Motivo |
|---|---|
| **Tanks** (todas as specs) | métrica de sucesso não é dano; rankings de `dps` não descrevem performance de tank |
| **Healers** (todas as specs) | idem; exigiria pipeline paralelo baseado em `hps` |
| **Evoker Augmentation** | spec de suporte: parte substancial da sua contribuição é **atribuída a outros jogadores** via buffs (Ebon Might, Prescience). O dano pessoal não representa a performance, e a decomposição de gap de DPS da T3.2 é estruturalmente inválida para ela. |

**Consequências diretas para a implementação:**

1. `metric` de ranking é **sempre `"dps"`**. Não há branching por papel. (Simplifica a T0.8.)
2. Existe um **portão de escopo** no início do pipeline (T0.9) que rejeita entrada fora de escopo com mensagem clara, **antes** de gastar qualquer requisição de coorte.
3. Nenhuma feature, tabela ou renderer de healing/tanking deve ser implementada. Se você se pegar escrevendo `hps`, `healing`, `absorb` ou `damage_taken` como métrica primária, está fora de escopo.
4. **`damage_taken` continua sendo coletado** apenas como feature explicativa de downtime (T3.1 #5), nunca como métrica de avaliação.

### 1.5 Escopo de conteúdo (normativo)

Decidido pelo usuário:

| Dimensão | Decisão | Implementação |
|---|---|---|
| Versão do jogo | **Somente Retail** | rejeitar reports de Classic/SoD. `gameVersion` **não** existe em `Report` — está em `table(...).data.gameVersion` (`docs/schema_confirmado.md` §3). Valide por aí. |
| Tipo de conteúdo | **Somente Raid** | rejeitar Mythic+ e outros. Valide que o fight tem `encounterID` de raid e `difficulty ∈ {3,4,5}`. M+ tem estrutura de coorte incompatível (nível de chave, afixos, rota — não tempo de kill). |
| Partition | **Somente a atual** | sempre passar a partition corrente explicitamente; nunca misturar partitions numa coorte. |
| Uso | **Servidor Discord multiusuário** | ver **T1.8** — fila persistente, dedup e orçamento global de API. |

Essas rejeições ocorrem no mesmo portão da T0.9, antes de gastar pontos de API.

**Importante — Augmentation fora de escopo como *sujeito*, mas relevante como *contexto*.** Um Augmentation Evoker no raid infla substancialmente o dano dos DPS ao redor. Se o jogador analisado não tem um Augmentation no grupo e a coorte de referência tem, a comparação é inválida. Por isso, a presença de buffs externos entra como **covariável de matching** (T2.1) e como feature **não-controlável** (T4.1). Isto não é opcional.

---

## FASE 0 — Correções críticas e rede de segurança

> **Meta:** eliminar bugs que produzem conselhos errados, sem mudar a arquitetura.
> **Ao fim da Fase 0:** o bot ainda é `bot.py` monolítico, mas correto, testado e versionado.

---

### T0.0 — Segurança, versionamento e dependências

**Pré-requisitos:** nenhum.

**Passos:**

1. Crie `.gitignore` **antes** de qualquer `git init`:

```gitignore
.env
.venv/
venv/
__pycache__/
*.py[cod]
data/
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/
*.duckdb
*.duckdb.wal
.DS_Store
```

2. Crie `.env.example` com as mesmas chaves do `.env`, valores vazios:

```
DISCORD_TOKEN=
WCL_CLIENT_ID=
WCL_CLIENT_SECRET=
BLIZZARD_CLIENT_ID=
BLIZZARD_CLIENT_SECRET=
```

3. `git init` + commit inicial. Verifique com `git status --porcelain` que `.env` **não** aparece.

4. Copie o `bot.py` atual para `legacy/bot.py` e commit. Esta cópia é a referência de comportamento e **nunca mais será editada**.

5. Crie `pyproject.toml`:

```toml
[project]
name = "botgitgud"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "discord.py>=2.3",
    "httpx>=0.27",
    "pydantic-settings>=2.2",
    "structlog>=24.1",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
data = ["duckdb>=1.0", "pyarrow>=16", "polars>=1.0"]
ml = ["lightgbm>=4.3", "shap>=0.45", "scikit-learn>=1.5"]
dev = ["pytest>=8", "pytest-cov>=5", "hypothesis>=6", "syrupy>=4", "ruff>=0.5", "pyright>=1.1"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/botgitgud"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF", "ANN", "PTH"]
ignore = ["ANN401"]

[tool.pyright]
include = ["src", "tests"]
typeCheckingMode = "standard"
pythonVersion = "3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
markers = ["network: requer acesso real à API (desabilitado por padrão)"]
```

6. Crie o ambiente e instale: `uv venv && uv pip install -e ".[data,dev]"`.

**⚠️ Ação para o usuário (não é sua):** as credenciais em `.env` estavam em texto claro numa pasta sincronizada com OneDrive. Registre em `docs/progresso.md` a recomendação de **rotacionar as 5 chaves** (Discord, WCL client id/secret, Blizzard client id/secret). Não tente rotacionar você mesmo.

**Critério de aceite:**
- `git status --porcelain` não lista `.env`.
- `git log --oneline` mostra ao menos 2 commits.
- `python -c "import discord, httpx, duckdb, polars"` executa sem erro.
- `ruff check .` executa (pode ter erros no `legacy/`; adicione `legacy/` ao `exclude` do ruff).

---

### T0.1 — Sondagem do schema da API (elimina adivinhação)

**Pré-requisitos:** T0.0.

**Objetivo:** confirmar, contra a API real, todo campo GraphQL que este documento assume. Nenhuma tarefa posterior deve descobrir surpresas de schema.

> ⚠️ **Leia [`docs/schema_confirmado.md`](./schema_confirmado.md) ANTES de começar.** A maior parte
> desta tarefa **já foi executada** contra a API real em 2026-08-17. Os itens marcados ✅ lá estão
> confirmados e **não devem ser re-verificados**. Sua tarefa é apenas: (a) verificar os itens
> listados em §11 daquele arquivo, e (b) reconfirmar rapidamente que nada mudou desde então.
>
> Aquele documento contém **quatro correções que invalidam suposições da versão anterior desta
> spec**. Elas já foram propagadas para as tarefas abaixo, mas leia-as para entender o porquê:
> §5 (dano de pet), §7 (fases se repetem), §8 (pool minúsculo e ausência de `percentile`), §9
> (fonte real do parse).

**Passos:**

1. Crie `src/botgitgud/wcl/schema_probe.py` com uma função `probe() -> dict` que executa introspecção GraphQL e imprime a existência (ou ausência) de cada item da tabela abaixo.

2. Verifique **exatamente** estes campos:

| Caminho | Uso previsto | Tarefa que depende |
|---|---|---|
| `rateLimitData { limitPerHour, pointsSpentThisHour, pointsResetIn }` | orçamento de API | T0.3 |
| `reportData.report.fights { id, encounterID, name, startTime, endTime, kill, difficulty, size, phaseTransitions }` | metadados da luta | T0.6, T2.4 |
| `reportData.report.table(dataType: Summary)` → `playerDetails`, `combatantInfo` | spec, ilvl, talentos | T2.1 |
| `reportData.report.table(dataType: DamageDone)` | dano por habilidade | T3.2 |
| `reportData.report.table(dataType: Buffs / Debuffs)` | uptimes | T3.1 |
| `reportData.report.events(dataType: Casts / Resources)` | timeline e recursos | T0.6, T3.1 |
| `worldData.encounter.characterRankings(className, specName, metric, page, difficulty, partition, bracket)` | coorte | T2.1 |
| Campos de cada ranking: `name`, `duration`, `percentile`, `amount`, `report{code,fightID,startTime}`, `bracketData`, `talents`, `gear`, `server`, `guild`, `faction` | matching | T2.1 |

3. Grave o resultado em `docs/schema_confirmado.md`, com uma linha por campo: `✅ existe` / `❌ não existe` / `⚠️ existe com nome diferente: <nome>`.

4. Para cada `❌` ou `⚠️`, abra um desvio (§0.3).

**Critério de aceite:**
- `python -m botgitgud.wcl.schema_probe` roda e gera `docs/schema_confirmado.md`.
- Todo campo da tabela acima tem um veredito registrado.
- Toda divergência tem entrada em `docs/desvios.md`.

**Nota:** esta tarefa exige credenciais válidas. Se a autenticação falhar, marque `BLOQUEADA` e pergunte ao usuário.

---

### T0.2 — Fixtures e teste golden do comportamento atual

**Pré-requisitos:** T0.1.

**Objetivo:** congelar o comportamento atual antes de mudá-lo, para que toda alteração posterior seja visível e intencional.

**Passos:**

1. **O log de fixture já foi definido pelo usuário e verificado contra a API:**

   | | |
   |---|---|
   | Report | `PtfBbQKRY9d6zAMC` |
   | Fight | `1` |
   | Personagem | `Zarad` — Warlock **Demonology** |
   | Encounter | `3179` — Fallen-King Salhadaar, difficulty 5, size 20 |
   | Duração | 345,1 s · dano 37.378.119 · ilvl 283 · 20 pets |

   Este único log satisfaz **também** o requisito da T3.1 (spec com pet): Demonology deriva
   **70,4%** do dano de pets. Detalhes completos em `docs/schema_confirmado.md` §1.

2. Crie `tests/fixtures/record.py`: um script que executa o fluxo do `legacy/bot.py` **gravando** toda resposta HTTP em `tests/fixtures/cassettes/<hash_da_query>.json`.
   - Chave do cassete: `sha256(url + json.dumps(payload, sort_keys=True))[:16]`.
   - **Redija segredos**: nunca grave headers `Authorization` nos cassetes.
   - Limite: grave no máximo **5** logs de referência (não 100), para manter as fixtures pequenas.

3. Crie `tests/conftest.py` com uma fixture `mock_http` que intercepta chamadas e responde a partir dos cassetes. Requisição sem cassete correspondente → `pytest.fail()` com a chave faltante.

4. **Fixtures sintéticas** (obrigatórias, independentemente do passo 1) — crie `tests/fixtures/synthetic.py` gerando:
   - `synthetic_user_timeline`: 3 habilidades, tempos conhecidos, escrito à mão.
   - `synthetic_cohort`: 10 timelines de referência com propriedades conhecidas (ex.: habilidade A sempre usada 4×, habilidade B usada 1× por metade da coorte).
   - Estas fixtures são a base dos testes unitários das Fases 0–3 e **não dependem de rede**.

5. Crie `tests/golden/test_legacy_output.py`: roda o pipeline do `legacy/bot.py` contra os cassetes e compara a string final com um snapshot (`syrupy`).

**Critério de aceite:**
- `pytest tests/golden -q` passa, sem rede.
- `pytest tests/ -q -m "not network"` passa.
- `tests/fixtures/cassettes/` não contém a substring `Bearer `.

---

### T0.3 — Cliente HTTP robusto (timeout, retry, rate limit)

**Pré-requisitos:** T0.0.

**Escopo:** criar `src/botgitgud/wcl/client.py` e usá-lo a partir do `bot.py`. Não refatorar o resto ainda.

**Especificação — `WclClient`:**

```python
class WclClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None: ...
    def query(self, query: str, variables: dict[str, Any], *, op_name: str) -> dict[str, Any]: ...
    @property
    def points_remaining(self) -> float | None: ...
```

**Constantes normativas:**

| Parâmetro | Valor | Observação |
|---|---|---|
| Timeout de conexão | `5.0 s` | |
| Timeout de leitura | `30.0 s` | |
| Timeout total do pool | `60.0 s` | |
| Máximo de tentativas | `4` (1 original + 3 retries) | |
| Backoff | exponencial base `1.0 s`, fator `2.0`, **jitter total** (`random.uniform(0, delay)`) | |
| Códigos que fazem retry | `429, 500, 502, 503, 504` + erros de rede/timeout | |
| Códigos que **não** fazem retry | `400, 401, 403, 404, 422` | erro imediato |
| Respeito a `Retry-After` | obrigatório; se presente, ignora o backoff calculado | |
| Piso de pontos de API | `1000` | abaixo disso, `RateLimitBudgetExceeded` |

**Comportamento obrigatório:**

- Token OAuth cacheado **com expiração**: guardar `expires_at = now + expires_in - 60s`. Renovar quando expirado. (Corrige o achado 4.4.)
- Antes de cada rajada de queries, consultar `rateLimitData` (confirmado em T0.1). Se `limitPerHour - pointsSpentThisHour < 1000`, levantar `RateLimitBudgetExceeded` com o tempo até o reset.
- Toda resposta GraphQL com chave `errors` não vazia → levantar `WclGraphQLError` com as mensagens. **Nunca** retornar dados parciais silenciosamente.
- Log estruturado por requisição: `op_name`, `status`, `duration_ms`, `attempt`, `points_spent`.

**Hierarquia de erros em `errors.py`:**

```
BotGitGudError
├── ConfigError
├── ApiError
│   ├── AuthError
│   ├── RateLimitBudgetExceeded
│   ├── WclGraphQLError
│   └── TransientApiError
├── DataError
│   ├── PlayerNotFound
│   ├── FightNotFound
│   └── InsufficientCohort
└── AnalysisError
```

**Critério de aceite:**
- Teste unitário: transporte mockado retorna `429` com `Retry-After: 1` duas vezes e depois `200` → cliente retorna sucesso em 3 tentativas.
- Teste unitário: transporte retorna `401` → uma única tentativa, levanta `AuthError`.
- Teste unitário: token expirado força reautenticação (verificar com relógio injetado).
- Teste unitário: resposta com `errors` não vazio levanta `WclGraphQLError`.
- Nenhuma chamada `requests.*` sem timeout permanece em `bot.py` (`grep -n "requests\." bot.py` só deve mostrar chamadas migradas ou removidas).

---

### T0.4 — Catálogo de spells seguro e imutável durante a análise

**Pré-requisitos:** T0.0.

**Corrige:** achados 3.8, 3.12, 4.1.

**Especificação — `src/botgitgud/domain/spells.py`:**

```python
@dataclass(frozen=True, slots=True)
class SpellInfo:
    spell_id: int
    name: str
    source: Literal["wcl", "blizzard", "unknown"]

class SpellCatalog:
    """Thread-safe, append-only spell name store."""
    def __init__(self, path: Path, blizzard: BlizzardClient | None) -> None: ...
    def get(self, spell_id: int) -> SpellInfo: ...
    def learn(self, spell_id: int, name: str, source: str) -> None: ...
    def flush(self) -> None: ...
```

**Mudanças obrigatórias em relação ao atual:**

1. **Remover completamente o campo `category`** do arquivo de spells. A classificação `trackable`/`non-trackable` deixa de ser persistida. Ela passa a ser calculada em memória, por análise, no contexto `(spec, encounter)`. **Isto é a correção do achado 3.8** — sem ela a ferramenta não é reprodutível.
   - Migração: ao carregar um `spells.json` no formato antigo, descarte `category` e preserve `name` + `source`.
   - Renomeie o arquivo para `data/spells.json` (fora do diretório versionado).

2. **Thread-safety:** todo acesso mutante protegido por `threading.Lock`.

3. **Escrita atômica:** escrever em `<path>.tmp` e então `os.replace(tmp, path)`. Nunca `open(path, "w")` direto.

4. **Escrita adiada:** `learn()` só muta memória. A persistência acontece em `flush()`, chamado **uma vez** ao fim da análise, na thread principal. Isso elimina a corrida de 5 threads escrevendo o mesmo arquivo.

5. **Backup na carga:** se o JSON estiver corrompido, renomear para `spells.corrupt.<timestamp>.json`, logar `error` e começar vazio. **Nunca** falhar silenciosamente (corrige 4.5/achado do `except` mudo).

**Critério de aceite:**
- Teste: 8 threads chamando `learn()` concorrentemente + `flush()` final → arquivo válido, todos os IDs presentes.
- Teste: JSON corrompido na carga → arquivo `.corrupt.*` criado, catálogo inicia vazio, log de erro emitido.
- Teste: arquivo no formato antigo (com `category`) carrega e o campo é descartado.
- `grep -r '"category"' src/` não retorna nada.

---

### T0.5 — Alinhamento monotônico de sequências (correção central)

**Pré-requisitos:** T0.2 (fixtures sintéticas).

**Corrige:** achado 3.1 — a falha mais grave do projeto.

**Especificação — `src/botgitgud/analysis/alignment.py`:**

```python
class AlignmentKind(StrEnum):
    MATCH = "match"     # cast do jogador pareado com uso esperado
    MISSED = "missed"   # uso esperado sem cast correspondente
    EXTRA = "extra"     # cast do jogador sem uso esperado

@dataclass(frozen=True, slots=True)
class AlignmentStep:
    kind: AlignmentKind
    user_index: int | None      # índice em user_times, None se MISSED
    ref_index: int | None       # índice em ref_times, None se EXTRA
    user_time: float | None
    ref_time: float | None
    delta: float | None         # user_time - ref_time, apenas em MATCH

@dataclass(frozen=True, slots=True)
class Alignment:
    steps: tuple[AlignmentStep, ...]
    total_cost: float
    n_matched: int
    n_missed: int
    n_extra: int

def align(
    user_times: Sequence[float],
    ref_times: Sequence[float],
    *,
    gap_penalty: float = 25.0,
) -> Alignment: ...
```

**Algoritmo (programação dinâmica, Needleman-Wunsch com custo contínuo).**
`m = len(user_times)`, `n = len(ref_times)`. Ambas as listas **devem estar ordenadas de forma crescente** — valide e levante `ValueError` se não estiverem.

```
D[0][0] = 0
D[i][0] = i * gap_penalty            # i casts do jogador sem par → EXTRA
D[0][j] = j * gap_penalty            # j usos de referência sem par → MISSED

D[i][j] = min(
    D[i-1][j-1] + abs(user_times[i-1] - ref_times[j-1]),   # MATCH
    D[i-1][j]   + gap_penalty,                              # EXTRA
    D[i][j-1]   + gap_penalty,                              # MISSED
)
```

Backtracking a partir de `D[m][n]` produz os passos em ordem cronológica. Em caso de empate, a precedência é **MATCH > MISSED > EXTRA** (determinismo obrigatório).

**Por que `gap_penalty = 25.0`:** o custo de um pareamento é a diferença em segundos. Com penalidade 25, um cast a mais de 25s do candidato mais próximo é tratado como "uso perdido + uso extra" (custo 50) apenas se isso for mais barato que parear (custo > 50s de desvio). Na prática: desvios até ~50s ainda pareiam; além disso, o algoritmo corretamente reporta um uso perdido. Se a validação da T0.7 mostrar comportamento indesejado, ajuste **apenas** via desvio documentado.

**Propriedades obrigatórias (testes com Hypothesis):**

| Propriedade | Asserção |
|---|---|
| Identidade | `align(x, x).total_cost == 0` e todos os passos são `MATCH` |
| Completude | `n_matched + n_extra == len(user_times)` e `n_matched + n_missed == len(ref_times)` |
| Monotonicidade | os `user_index` dos MATCH são estritamente crescentes; idem `ref_index` |
| Sequência vazia | `align([], r)` → `len(r)` passos MISSED; `align(u, [])` → `len(u)` passos EXTRA |
| Simetria de custo | `align(u, r).total_cost == align(r, u).total_cost` |
| Determinismo | duas chamadas com a mesma entrada produzem passos idênticos |

**Teste de regressão explícito (o bug do relatório):**

```python
def test_missed_casts_are_reported():
    user = [10.0, 130.0]                       # 2 usos
    ref  = [10.0, 130.0, 250.0, 370.0]         # coorte usa 4
    a = align(user, ref)
    assert a.n_matched == 2
    assert a.n_missed == 2                     # o legacy reportava 0
    assert [s.ref_time for s in a.steps if s.kind is AlignmentKind.MISSED] == [250.0, 370.0]
```

**Critério de aceite:** todos os testes acima passam. A função ainda **não** está conectada ao `bot.py` (isso é T0.7).

---

### T0.6 — Correção de `avg_cd_duration` e da classificação MAJOR/MINOR

**Pré-requisitos:** T0.4.

**Corrige:** achado 3.3 (bug direto).

**Problema atual** (`legacy/bot.py:465-474`): quando uma habilidade tem apenas um slot, `avg_cd_duration` recebe o **instante do primeiro cast**, não um cooldown.

**Especificação:**

1. Substituir o campo único `avg_cd_duration` por dois campos com semântica distinta:

```python
@dataclass(frozen=True, slots=True)
class SpellCadence:
    observed_interval_median: float | None   # None quando há < 2 usos — NUNCA um instante
    observed_interval_iqr: float | None
    n_usages_median: float                   # mediana da contagem de usos na coorte
    base_cooldown: float | None              # do catálogo; None se desconhecido
```

2. **Nunca** derivar cooldown do instante de um cast. Se há menos de 2 usos, `observed_interval_median` é `None`. Ponto final.

3. **Classificação MAJOR/MINOR** passa a usar esta regra, nesta ordem de precedência:

```
se base_cooldown conhecido:
    MAJOR se base_cooldown >= 90s, senão MINOR
senão se observed_interval_median conhecido:
    MAJOR se observed_interval_median >= 90s, senão MINOR
senão:                                       # uso único, cooldown desconhecido
    MAJOR se n_usages_median <= 1.5, senão MINOR
```

4. **Filtro de elegibilidade** (substitui `discover_clean_major_cds`) passa a ser:

```
elegível se:
    presence >= 0.70                                     # inalterado
  E (base_cooldown is None ou base_cooldown >= 15.0)
  E (observed_interval_median is None ou >= 15.0)
  E spell_id not in blacklist_estática
```

Note que a condição sobre intervalo é **permissiva quando desconhecida** — o comportamento atual descarta habilidades de uso único, que são justamente os cooldowns mais longos e importantes.

5. **`base_cooldown`** vem do catálogo de spells. Na Fase 0, o catálogo ainda não tem esse dado → sempre `None`. Isso é aceitável: a regra degrada corretamente. Preenchê-lo é a T2.5.

6. **Blacklist:** remover o filtro por substring (`"potion"`, `"ring"`, …). Substituir por um conjunto explícito de IDs em `src/botgitgud/domain/blacklist.py`, com um comentário por entrada explicando o motivo. Migre `MAJOR_CD_BLACKLIST` (id 22812) e adicione os IDs de poção/pedra que você observar nas fixtures. **Filtro léxico é proibido** (achado 3.12).

**Critério de aceite:**
- Teste: habilidade com 1 uso aos 200s → `observed_interval_median is None`, **não** 200.
- Teste: habilidade com usos em `[10, 130, 250]` → `observed_interval_median == 120.0`.
- Teste: habilidade de uso único e `base_cooldown=None` → classificada `MAJOR` e **elegível** (o legacy a descartava).
- Teste: `"Ring of Peace"` não é filtrada por nome.
- `grep -n 'potion\|healthstone\|trinket' src/` não retorna filtros léxicos.

---

### T0.7 — Integrar alinhamento + cadência ao pipeline e ao relatório

**Pré-requisitos:** T0.5, T0.6.

**Escopo:** substituir `compare_major_cds_clean` e ajustar o texto do relatório.

**Especificação da comparação:**

```python
@dataclass(frozen=True, slots=True)
class SpellComparison:
    spell: SpellInfo
    cd_type: Literal["MAJOR", "MINOR"]
    cadence: SpellCadence
    presence: float
    alignment: Alignment
    reference_n: int
```

Para cada habilidade elegível:
- `user_times` = timeline do jogador (ordenada).
- `ref_times` = sequência de referência (na Fase 0, ainda as medianas por slot; a T2.4 substitui isso).
- `alignment = align(user_times, ref_times)`.
- Habilidades com `user_times` vazia **devem** ser incluídas se `presence >= 0.70` — hoje são puladas (`legacy/bot.py:561`), escondendo o caso "você não usou esta habilidade nenhuma vez", que é o pior erro possível.

**Mudanças no relatório (`generate_coach_report_string`):**

1. **Nova seção no topo, antes de tudo:** `⛔ USOS PERDIDOS`, listando por habilidade a contagem de `MISSED` e os tempos esperados. Se não houver, omitir a seção.
2. Cada habilidade exibe `Usos: <n_user> (coorte: <n_ref_mediana>)`.
3. Passos `EXTRA` são exibidos como `Uso extra aos Xs` — não mais com `delta = 0.0` mascarado.
4. Cabeçalho passa a mostrar o **DPS e o percentil do próprio jogador** (achado 3.11).
   - **DPS:** `entry.total / duração` da tabela `DamageDone` do log do usuário.
   - **Percentil:** vem de `characterData.character.encounterRankings` → `ranks[].rankPercent`,
     casando `ranks[].report.code` + `fightID` com a análise em curso (✅ confirmado,
     `docs/schema_confirmado.md` §9). **Não** está em `characterRankings`.
   - Se indisponível, exiba `n/d` — **nunca** um valor inventado.
5. Cabeçalho passa a mostrar `n` da coorte explicitamente: `Referência: N logs`.
6. **Remova** o parâmetro `parses`, hoje propagado por 3 funções e nunca usado (achado 3.12/anexo).
7. ⚠️ **Remova o campo "Parse méd" da coorte inteiramente.** Verificação contra a API
   (`docs/schema_confirmado.md` §8): `characterRankings` **não tem campo `percentile`**. O
   `r.get("percentile", 99.0)` de `legacy/bot.py:361` retorna `99.0` **sempre** — o cabeçalho
   "Parse méd: 99 (min 99 - max 99)" é ficção em 100% dos relatórios já gerados. O parse médio da
   coorte não é obtenível do leaderboard sem uma query por personagem. **Remova o campo em vez de
   inventá-lo.** Em seu lugar, exiba o **DPS mediano da coorte**, que vem de `amount` (✅ existe).

**Chunking do Discord (achado 4.8):** substituir o split cego em 1900 chars por quebra **por linha**: acumule linhas até 1900 caracteres, quebre no limite de linha, e reabra o bloco de código em cada chunk.

**Critério de aceite:**
- Teste golden atualizado: o novo snapshot difere do da T0.2 e a diferença contém a seção `USOS PERDIDOS`.
- Teste: relatório com uma habilidade de `presence=0.9` e 0 usos do jogador aparece na seção de usos perdidos.
- Teste: nenhum chunk enviado ao Discord quebra uma linha ao meio; todos os chunks têm ≤ 2000 caracteres incluindo as cercas de código.
- Teste: percentil ausente renderiza `n/d`.

---

### T0.8 — Coorte: limiares mínimos e filtro de duração relativo

**Pré-requisitos:** T0.3.

**Corrige:** achados 3.9, 3.10, e a ausência de guarda de tamanho amostral.

> ⚠️ **Medição real que muda esta tarefa** (`docs/schema_confirmado.md` §8): o pool de
> `characterRankings` para Warlock/Demonology no encounter 3179 tem **26 logs no total**
> (`hasMorePages: false`). Aplicando ±7% ao kill de 345 s de Zarad restam **2 logs**. Com ±20%,
> **3 logs**. `COHORT_MAX = 100` é inalcançável e `COHORT_MIN_HARD = 10` recusaria a análise.
>
> `characterRankings` é um **leaderboard**, não uma amostra da população. Para conteúdo recente
> ou specs menos populares, ele tem dezenas de entradas.
>
> **Portanto: duração deixa de ser filtro e vira covariável de ajuste.** Descartar 24 de 26
> observações para ficar com 2 é estatisticamente pior do que usar as 26 e normalizar.

**Especificação:**

| Parâmetro | Valor normativo |
|---|---|
| **Banda de sanidade de duração** | `±35%` — exclui kills estruturalmente diferentes, não parea |
| **Ajuste de duração** | por normalização de métrica (ver abaixo), não por filtro |
| `COHORT_MIN_HARD` | **`8`** — abaixo disso, recusar a análise (`InsufficientCohort`) |
| `COHORT_MIN_WARN` | **`20`** — abaixo disso, gerar relatório **com aviso destacado** no topo |
| `COHORT_MAX` | `100` (raramente atingido na prática) |
| Páginas máximas de ranking | `10` |

**Normalização por classe de métrica** — esta tabela é normativa e resolve o problema de duração
sem descartar observações:

| Classe de métrica | Exemplos | Tratamento |
|---|---|---|
| **Invariante à duração** | uptime %, active time %, dano por cast, alvos por cast, waste por minuto | usar o pool inteiro, sem ajuste |
| **Escala com a duração** | nº de casts, dano total | normalizar para **taxa por minuto** antes de comparar |
| **Posicional no tempo** | instante do N-ésimo cast | exige normalização por fase (T2.4) — até lá, restringir ao subconjunto dentro de ±12% e marcar `confidence="baixa"` se `n < 8` |

Só a terceira classe depende de kills de duração parecida. As duas primeiras — que são as de maior
poder explicativo segundo a T3.1 — funcionam com o pool inteiro.

**Comportamento:**
- Abaixo de `COHORT_MIN_HARD`: mensagem ao usuário explicando que não há kills comparáveis suficientes e sugerindo o motivo provável (encontro pouco popular / duração atípica). **Não** gere relatório.
- Entre `HARD` e `WARN`: relatório normal, com banner `⚠️ Amostra pequena (N logs). Trate os desvios como indicativos, não conclusivos.`
- O parâmetro `metric` do ranking é **sempre `"dps"`** (ver §1.4). Não implemente branching por papel — o portão da T0.9 garante que só chegam aqui specs de DPS suportadas.

**Critério de aceite:**
- Teste: coorte com 7 membros → `InsufficientCohort`, sem relatório.
- Teste: coorte com 15 membros → relatório com banner de aviso.
- Teste: métrica invariante (uptime %) usa todos os membros dentro da banda de ±35%.
- Teste: métrica que escala (nº de casts) é comparada como taxa por minuto, não valor absoluto.
- **Teste com a fixture real:** o log de Zarad (345 s, pool de 26) **deve produzir relatório**, não `InsufficientCohort`. Este é o teste de regressão desta mudança.
- `grep -n '"hps"\|healing' src/botgitgud/ingest/rankings.py` retorna vazio.

---

### T0.9 — Registro de specs suportadas e portão de escopo

**Pré-requisitos:** T0.8.
**Implementa:** §1.4.

**Especificação — `src/botgitgud/domain/specs.py`:**

```python
@dataclass(frozen=True, slots=True)
class SpecId:
    class_name: str      # como retornado pela API, ex. "DemonHunter"
    spec_name: str       # como retornado pela API, ex. "Havoc"

class SpecSupport(StrEnum):
    SUPPORTED = "supported"
    OUT_OF_SCOPE_TANK = "tank"
    OUT_OF_SCOPE_HEALER = "healer"
    OUT_OF_SCOPE_SUPPORT = "support"     # Augmentation
    UNKNOWN = "unknown"

def classify_spec(spec: SpecId) -> SpecSupport: ...
```

**Allowlist explícita** — 25 specs de DPS. Codifique como constante, não como heurística:

| Classe | Specs suportadas |
|---|---|
| Death Knight | Frost, Unholy |
| Demon Hunter | Havoc |
| Druid | Balance, Feral |
| Evoker | Devastation |
| Hunter | Beast Mastery, Marksmanship, Survival |
| Mage | Arcane, Fire, Frost |
| Monk | Windwalker |
| Paladin | Retribution |
| Priest | Shadow |
| Rogue | Assassination, Outlaw, Subtlety |
| Shaman | Elemental, Enhancement |
| Warlock | Affliction, Demonology, Destruction |
| Warrior | Arms, Fury |

Mantenha também as listas de **tanks**, **healers** e **Evoker Augmentation** para poder emitir a mensagem de rejeição correta em vez de um genérico "não suportado".

**Comportamento do portão:**

- Roda **imediatamente após** identificar a spec do jogador no log, **antes** de qualquer query de ranking. Nenhum ponto de API é gasto com entrada fora de escopo.
- `SUPPORTED` → prossegue.
- `OUT_OF_SCOPE_TANK` → `"Análise de tanks está fora do escopo desta ferramenta. Ela avalia apenas specs de DPS."`
- `OUT_OF_SCOPE_HEALER` → `"Análise de healers está fora do escopo desta ferramenta. Ela avalia apenas specs de DPS."`
- `OUT_OF_SCOPE_SUPPORT` → `"Augmentation Evoker não é suportado: boa parte do seu dano é atribuída a outros jogadores, o que invalida a comparação de DPS pessoal."`
- `UNKNOWN` → `"Spec não reconhecida: <class>/<spec>. Isto pode ser uma spec nova."` + `log.warning` com os valores crus, para que o allowlist possa ser atualizado.

**Regra:** `UNKNOWN` **nunca** é tratado como suportado. Falhar fechado é obrigatório — analisar uma spec desconhecida contra um ranking de `dps` pode produzir conselhos sem sentido silenciosamente.

**Nomenclatura:** os nomes de classe/spec retornados pela API podem divergir dos usados na tabela acima (`"DemonHunter"` vs `"Demon Hunter"`, `"BeastMastery"` vs `"Beast Mastery"`). Confirme os valores exatos contra as fixtures da T0.2 e normalize numa única função `_normalize(name: str) -> str` (remover espaços, lowercase). Registre em `docs/schema_confirmado.md` os valores literais observados.

**Critério de aceite:**
- Teste: cada uma das 25 specs suportadas classifica como `SUPPORTED`.
- Teste: `Evoker/Augmentation` → `OUT_OF_SCOPE_SUPPORT` com a mensagem específica.
- Teste: `Evoker/Devastation` → `SUPPORTED` (não confundir as duas).
- Teste: `Priest/Discipline` → `OUT_OF_SCOPE_HEALER`; `Warrior/Protection` → `OUT_OF_SCOPE_TANK`.
- Teste: `Mage/Chronomancer` (inventada) → `UNKNOWN`, com `log.warning` emitido.
- **Teste de custo:** análise de um tank não dispara **nenhuma** query de ranking (verificar contagem de requisições no transporte mockado).

---

### Portão de saída da Fase 0

Não avance para a Fase 1 sem:

- [ ] Todas as tarefas T0.0–T0.9 com status ✅.
- [ ] `pytest --cov=src -q` com cobertura ≥ 70% em `src/botgitgud/analysis/`.
- [ ] Uma execução real de `!analisar` contra um log verdadeiro, com o relatório colado em `docs/progresso.md`.
- [ ] `docs/desvios.md` revisado — nenhum desvio em estado `BLOQUEADO`.

---

## FASE 1 — Fundação de dados e arquitetura

> **Meta:** eliminar o custo insustentável de API e o monólito.
> **Ao fim da Fase 1:** análise interativa responde em segundos; perfis de coorte vêm de batch.

---

### T1.1 — Configuração tipada e logging estruturado

**Pré-requisitos:** Fase 0 completa.

**`src/botgitgud/config.py`:**

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: SecretStr
    wcl_client_id: SecretStr
    wcl_client_secret: SecretStr
    blizzard_client_id: SecretStr
    blizzard_client_secret: SecretStr

    data_dir: Path = Path("data")
    log_level: str = "INFO"
    log_json: bool = False
    max_workers: int = 4
    cohort_max: int = 100
    cohort_min_hard: int = 10
    cohort_min_warn: int = 30
    duration_tolerance_pct: float = 0.07
    duration_tolerance_floor_s: float = 15.0
    gap_penalty_s: float = 25.0
    api_points_floor: int = 1000
```

- **Todos** os números mágicos das Fases 0–3 migram para cá. `grep` por literais numéricos em `analysis/` não deve encontrar constantes de negócio.
- `SecretStr` obrigatório para credenciais: garante que um log acidental imprima `**********`.

**`src/botgitgud/logging_setup.py`:** configurar `structlog` com processadores: timestamp ISO, nível, nome do logger, `correlation_id` via contextvar, renderer console (dev) ou JSON (`log_json=True`).

- **Remova `DEBUG = True` e todos os `print()`** do pipeline (achado 4.9). Os dumps de amostras brutas por slot viram `log.debug(...)` com dados agregados, nunca listas completas de 100 logs.

**Critério de aceite:**
- `grep -rn "print(" src/` retorna vazio.
- Teste: `str(settings.wcl_client_secret)` não contém o segredo.
- Toda linha de log de uma análise carrega o mesmo `correlation_id`.

---

### T1.2 — Modelos de domínio

**Pré-requisitos:** T1.1.

Crie `src/botgitgud/domain/models.py` com dataclasses `frozen=True, slots=True`. Estas substituem **todas** as tuplas de 8–9 posições (achado 4.7).

```python
@dataclass(frozen=True, slots=True)
class FightRef:
    report_code: str
    fight_id: int
    encounter_id: int
    boss_name: str
    difficulty: int
    duration_s: float
    kill: bool

@dataclass(frozen=True, slots=True)
class PlayerBuild:
    character_name: str
    server: str | None
    class_name: str
    spec_name: str
    role: Literal["dps", "healer", "tank"]   # detectado para poder REJEITAR (T0.9), não para ramificar
    item_level: float | None
    talent_hash: str | None
    tier_pieces: int | None
    external_buffs: frozenset[int] = frozenset()   # spell_ids de buffs externos recebidos (T2.1)

@dataclass(frozen=True, slots=True)
class PlayerLog:
    fight: FightRef
    build: PlayerBuild
    dps: float | None
    percentile: float | None
    cast_timeline: Mapping[int, tuple[float, ...]]      # spell_id -> tempos ordenados
    active_time_pct: float | None = None
    damage_by_ability: Mapping[int, AbilityDamage] = field(default_factory=dict)
    uptimes: Mapping[int, float] = field(default_factory=dict)
    resource_waste: Mapping[str, float] = field(default_factory=dict)
    deaths: int = 0

@dataclass(frozen=True, slots=True)
class AbilityDamage:
    spell_id: int
    total: float
    hits: int
    casts: int

@dataclass(frozen=True, slots=True)
class Cohort:
    cohort_id: str            # hash determinístico das covariáveis — ver T1.5
    criteria: CohortCriteria
    members: tuple[PlayerLog, ...]
    built_at: datetime
```

**Regra:** nenhuma função em `analysis/` recebe ou retorna `dict` cru ou tupla. Só estes tipos.

**Critério de aceite:**
- `pyright` sem erros.
- `grep -nE "return .*,.*,.*,.*," src/` não retorna tuplas longas.

---

### T1.3 — Camada de armazenamento (DuckDB + Parquet)

**Pré-requisitos:** T1.2.

**`src/botgitgud/ingest/store.py`:**

```python
class Store:
    def __init__(self, data_dir: Path) -> None: ...
    def has_log(self, report_code: str, fight_id: int, player: str) -> bool: ...
    def read_log(self, report_code: str, fight_id: int, player: str) -> PlayerLog | None: ...
    def write_log(self, log: PlayerLog) -> None: ...
    def read_profile(self, cohort_id: str) -> CohortProfile | None: ...
    def write_profile(self, profile: CohortProfile) -> None: ...
    def query(self, sql: str, **params: Any) -> pl.DataFrame: ...
```

**Layout físico:**

```
data/
├── warehouse.duckdb            # tabelas de dimensão + índice
├── raw/
│   └── encounter_id=<E>/difficulty=<D>/partition=<P>/<report_code>_<fight_id>.parquet
└── profiles/
    └── <cohort_id>.parquet
```

**Tabelas no DuckDB:**

```sql
CREATE TABLE IF NOT EXISTS logs (
    report_code   VARCHAR, fight_id INTEGER, player_name VARCHAR, server VARCHAR,
    encounter_id  INTEGER, difficulty INTEGER, partition INTEGER,
    class_name    VARCHAR, spec_name VARCHAR, role VARCHAR,
    duration_s    DOUBLE,  dps DOUBLE, percentile DOUBLE,
    item_level    DOUBLE,  talent_hash VARCHAR, tier_pieces INTEGER,
    active_time_pct DOUBLE, deaths INTEGER,
    parquet_path  VARCHAR, ingested_at TIMESTAMP,
    PRIMARY KEY (report_code, fight_id, player_name)
);

CREATE TABLE IF NOT EXISTS cohorts (
    cohort_id VARCHAR PRIMARY KEY, criteria_json VARCHAR,
    n_members INTEGER, built_at TIMESTAMP, code_version VARCHAR
);

CREATE TABLE IF NOT EXISTS spells (
    spell_id BIGINT PRIMARY KEY, name VARCHAR, source VARCHAR,
    base_cooldown_s DOUBLE, updated_at TIMESTAMP
);
```

**Princípios obrigatórios:**
- **Dados brutos imutáveis.** Um log escrito nunca é sobrescrito. Reingestão gera nova linha com `ingested_at` maior; a leitura pega a mais recente.
- **Nada de estado mutável de análise no store.** O store guarda fatos, não decisões.

**Critério de aceite:**
- Teste: escrever 50 logs sintéticos e ler de volta com igualdade estrutural.
- Teste: `store.query("SELECT count(*) FROM logs WHERE spec_name = $spec", spec="Havoc")` funciona.
- Teste: reescrever o mesmo log não apaga o anterior; a leitura retorna o mais recente.

---

### T1.4 — Cache de ingestão (a correção do custo de API)

**Pré-requisitos:** T1.3.

**Corrige:** achado 4.2 — a maior falha arquitetural.

**Especificação — `src/botgitgud/ingest/log_fetcher.py`:**

```python
class LogFetcher:
    def __init__(self, client: WclClient, store: Store, catalog: SpellCatalog) -> None: ...
    def fetch(self, report_code: str, fight_id: int, player: str, *, force: bool = False) -> PlayerLog: ...
    def fetch_many(self, refs: Sequence[LogRequest], *, max_workers: int) -> list[PlayerLog]: ...
```

**Regra de cache:** `fetch()` consulta o store primeiro. Log de uma luta finalizada é **imutável para sempre** — nunca refazer download, exceto com `force=True`.

**Concorrência:** `fetch_many` usa `ThreadPoolExecutor(max_workers=settings.max_workers)`. O `SpellCatalog` já é thread-safe (T0.4). Nenhuma escrita em disco dentro das threads: acumule e persista no fim.

**Instrumentação obrigatória:** ao fim de `fetch_many`, logar `cache_hits`, `cache_misses`, `api_points_spent`, `wall_time_s`.

**Critério de aceite:**
- Teste: chamar `fetch()` duas vezes para o mesmo log → o transporte HTTP mockado registra requisições apenas na primeira.
- **Teste de aceitação medido:** rodar a mesma análise duas vezes. A segunda execução deve consumir **0 requisições** de log de referência. Registre os números em `docs/progresso.md`.

---

### T1.5 — Identidade de coorte determinística

**Pré-requisitos:** T1.3.

**Objetivo:** tornar toda análise reproduzível e auditável.

```python
@dataclass(frozen=True, slots=True)
class CohortCriteria:
    encounter_id: int
    difficulty: int
    partition: int
    class_name: str
    spec_name: str
    metric: str
    duration_min_s: float
    duration_max_s: float
    ilvl_min: float | None = None
    ilvl_max: float | None = None
    talent_cluster: str | None = None

    def cohort_id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
```

**Manifesto de execução:** todo relatório gerado grava um `RunManifest` com: `cohort_id`, `code_version` (hash do git), `generated_at`, `n_members`, `wcl_partition`, `settings_hash`. O manifesto vai no rodapé do relatório e numa tabela `runs` do DuckDB.

**Critério de aceite:**
- Teste: dois `CohortCriteria` iguais produzem o mesmo `cohort_id`; mudar qualquer campo muda o hash.
- Teste: o relatório renderizado contém `cohort_id` e `code_version`.

---

### T1.6 — Refatoração do monólito (janela de quebra autorizada)

**Pré-requisitos:** T1.1–T1.5.

**Esta é a única tarefa em que o bot pode ficar temporariamente quebrado.** Faça-a numa branch `refactor/layers` e só faça merge quando o teste golden passar.

**Passos:**

1. Mover cada função do `bot.py` para o módulo correspondente do §1.1, adaptando assinaturas para os modelos da T1.2.
2. Criar `src/botgitgud/analysis/pipeline.py` com o orquestrador:

```python
@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    report_code: str
    fight_id: int
    character_name: str

def run_analysis(req: AnalysisRequest, deps: Deps) -> Report: ...
```

`Deps` é um container de dependências injetadas (`client`, `store`, `catalog`, `settings`). **Sem singletons, sem globais.**

3. `bot/discord_bot.py` fica reduzido a: parsear input → enfileirar job → renderizar `Report` → enviar. Nenhuma lógica de análise.
4. Criar `cli.py` com subcomandos: `analyze`, `build-cohort`, `probe-schema`, `backfill`. Isso permite testar o pipeline sem Discord.
5. **Excluir `bot.py` da raiz.** A cópia congelada permanece em `legacy/`.

**Critério de aceite:**
- `python -m botgitgud.cli analyze --report <code> --fight <n> --char <nome>` produz o relatório no stdout.
- Teste golden passa contra os cassetes.
- `wc -l src/botgitgud/**/*.py` — nenhum arquivo com mais de 300 linhas.
- `bot.py` não existe mais na raiz.

---

### T1.7 — Job batch de construção de coortes

**Pré-requisitos:** T1.6.

**Especificação:** `python -m botgitgud.cli build-cohort --encounter <E> --spec <S> --difficulty <D> [--duration-bucket <B>]`

- Baixa rankings, ingere os logs (usando o cache da T1.4), calcula o `CohortProfile` e o persiste.
- **Buckets de duração:** discretizar a duração em faixas de 5% para permitir reuso entre jogadores com kills parecidos. Um jogador é servido pelo bucket que contém sua duração.
- Idempotente: reexecutar não duplica dados.
- Respeita o orçamento de pontos de API: se `RateLimitBudgetExceeded`, salva o progresso parcial e sai com código 75 (`EX_TEMPFAIL`).

**Caminho interativo passa a ser:** 1 fetch do log do usuário + 1 lookup de perfil. Se o perfil não existir, o bot informa que a coorte está sendo construída e enfileira o job — **nunca** baixa 100 logs de forma síncrona.

**Critério de aceite:**
- **Teste de aceitação medido:** com o perfil pré-computado, `!analisar` completa em **< 10 segundos**. Registre o tempo medido em `docs/progresso.md`.
- Teste: rodar `build-cohort` duas vezes não altera a contagem de linhas em `logs`.

---

### T1.8 — Fila multiusuário e orçamento global de API

**Pré-requisitos:** T1.7.
**Motivo:** decisão #9 = **servidor Discord multiusuário**.

> ⚠️ **Restrição medida:** `rateLimitData.limitPerHour = **3600**` para esta conta
> (`docs/schema_confirmado.md` §2). **Esse orçamento é da conta inteira, compartilhado por todos os
> usuários do servidor.** Uma coorte fria de 26 logs, cada um exigindo ~7 páginas de eventos de
> dano + páginas de casts, consome centenas de requisições. **Sem controle global, um único
> usuário esgota a cota de todos.**

**Componentes obrigatórios:**

1. **Fila persistente** (tabela `jobs` no DuckDB, não em memória):

```sql
CREATE TABLE IF NOT EXISTS jobs (
    job_id VARCHAR PRIMARY KEY,
    dedup_key VARCHAR,          -- report_code:fight_id:character
    discord_user_id VARCHAR, discord_channel_id VARCHAR,
    status VARCHAR,             -- queued | running | done | failed | cancelled
    created_at TIMESTAMP, started_at TIMESTAMP, finished_at TIMESTAMP,
    error VARCHAR, report_path VARCHAR
);
```

2. **Deduplicação:** dois pedidos com o mesmo `dedup_key` enquanto o primeiro está `queued`/`running`
   compartilham o resultado. Nunca execute a mesma análise duas vezes em paralelo. O mesmo vale
   para construção de coorte: um `cohort_id` já em construção não dispara um segundo job.

3. **Orçamento global de pontos (token bucket):**

| Parâmetro | Valor normativo |
|---|---|
| Teto por hora | ler de `rateLimitData.limitPerHour` (não hardcode 3600) |
| Reserva para caminho interativo | **25%** do teto — jobs de coorte fria nunca a consomem |
| Piso de abortagem | `api_points_floor = 1000` (T0.3) |
| Verificação | antes de cada job, e a cada 50 requisições dentro de um job |

Se o orçamento acabar no meio de um `build-cohort`, **salve o progresso parcial** (logs já
ingeridos ficam no store) e re-enfileire o job para depois de `pointsResetIn`.

4. **Justiça entre usuários:** no máximo **1 job ativo** e **3 enfileirados** por usuário do
   Discord. Cooldown de **60 s** entre pedidos do mesmo usuário. Pedido excedente recebe recusa
   explicativa, não silenciosa.

5. **Concorrência de workers:** `max_concurrent_jobs = 2`. Análises com perfil quente (rápidas)
   têm prioridade sobre construções de coorte fria — use duas filas com prioridade, não uma.

6. **Escrita no DuckDB:** o DuckDB permite **um único processo escritor**. Toda escrita passa por
   um **writer único serializado** (uma thread dedicada consumindo uma fila de escritas). Leituras
   concorrentes são permitidas. **Nunca** abra conexões de escrita a partir dos workers.

7. **Feedback ao usuário:** ao enfileirar, responder com posição na fila e estimativa. Ao concluir,
   responder na thread/canal original com menção ao autor. Comando `!status` mostra a fila.

8. **Retomada após reinício:** jobs em `running` no boot são revertidos para `queued` — o bot pode
   cair no meio de uma análise.

**Critério de aceite:**
- Teste: dois pedidos idênticos simultâneos → 1 job criado, 2 respostas entregues.
- Teste: usuário com 1 job ativo + 3 na fila → o 5º pedido é recusado com mensagem explicativa.
- Teste: orçamento abaixo da reserva → jobs de coorte fria pausam, análises quentes continuam.
- Teste: `build-cohort` interrompido por orçamento → logs já ingeridos permanecem no store; ao
  reprocessar, apenas os faltantes são baixados.
- Teste: 8 workers tentando escrever no store simultaneamente → nenhuma exceção de escrita
  concorrente do DuckDB, todos os registros presentes.
- Teste: jobs em `running` no boot voltam para `queued`.

---

### Portão de saída da Fase 1

- [ ] T1.1–T1.8 ✅.
- [ ] Análise interativa < 10s com perfil quente (medido e registrado).
- [ ] Dois usuários pedindo a mesma análise simultaneamente geram **1** job.
- [ ] Orçamento de API respeitado sob carga: nenhuma `RateLimitBudgetExceeded` não tratada.
- [ ] Segunda execução da mesma análise consome 0 requisições de referência (medido).
- [ ] Cobertura ≥ 75% em `src/botgitgud/`.
- [ ] Nenhum arquivo com mais de 300 linhas.

---

## FASE 2 — Metodologia estatística

> **Meta:** tornar as conclusões válidas.

---

### T2.1 — Matching multivariado de coorte

**Pré-requisitos:** Fase 1 completa.
**Corrige:** achado 3.4.

**Covariáveis obrigatórias** (extraídas de `combatantInfo` e dos campos de ranking confirmados na T0.1):

| Covariável | Critério de matching | Se indisponível |
|---|---|---|
| `encounter_id` | exato | — |
| `difficulty` | **exato** | abortar |
| `partition` | exato (padrão: partition atual) | usar atual |
| `class_name` / `spec_name` | exato | abortar |
| `duration_s` | ±7% (piso 15s) | — |
| `item_level` | ±5 níveis do jogador | ignorar covariável, registrar no manifesto |
| — *fonte* | usar `bracketData` do ranking (✅ existe, ex. `292`) — evita fetch adicional. Do log do usuário: `maxItemLevel`/`minItemLevel` em `playerDetails`, ou `entry.itemLevel` na tabela `DamageDone` | |
| `talent_cluster` | mesmo cluster (T2.2) | ignorar covariável, registrar |
| `tier_pieces` | ±1 peça | ignorar covariável |
| **`has_augmentation`** | **exato (booleano)** | ignorar covariável, registrar |
| `external_buffs` (Power Infusion, Innervate, etc.) | mesmo conjunto | ignorar covariável, registrar |

**Sobre `has_augmentation`** (ver §1.4): a presença de um Evoker Augmentation no raid altera materialmente o dano de todos os DPS ao redor. Comparar um jogador sem Augmentation contra uma coorte inteira com Augmentation produz um gap que **não é culpa do jogador** e não é acionável. Detecte via os buffs recebidos pelo jogador (Ebon Might / Prescience — confirme os spell IDs contra as fixtures da T0.2 e registre em `docs/schema_confirmado.md`).

Quando esta covariável for relaxada, o relatório **deve** exibir: `⚠️ Buffs de suporte não pareados — parte do gap de dano por cast pode não ser controlável por você.`

**Degradação controlada:** se após aplicar todas as covariáveis `n < COHORT_MIN_HARD`, relaxe **nesta ordem exata** e registre cada relaxamento no manifesto e no relatório:
`tier_pieces` → `external_buffs` → `item_level` → `talent_cluster` → `has_augmentation` → duração (±7% → ±12% → ±20%).
**Nunca** relaxe `difficulty`, `spec` ou `partition`.

Note que `has_augmentation` é o **penúltimo** a ser relaxado, à frente apenas da duração: é a covariável de maior impacto sobre dano por cast depois do item level.

O relatório deve exibir: `Coorte: 43 logs | ilvl ±5 ✅ | talentos: mesma build ✅ | duração ±7% ✅` ou, quando relaxado, `⚠️ ilvl não pareado (amostra insuficiente)`.

**Critério de aceite:**
- Teste: coorte sintética onde só 3 membros passam no filtro estrito → o relaxamento acontece na ordem especificada e para assim que `n >= 10`.
- Teste: `difficulty` nunca é relaxada, mesmo com `n = 0` (levanta `InsufficientCohort`).
- O relatório lista quais covariáveis foram pareadas e quais foram relaxadas.
- Teste: jogador **sem** Augmentation no raid → coorte contém apenas logs sem Augmentation, enquanto `n >= COHORT_MIN_HARD`.
- Teste: quando `has_augmentation` é relaxada, o relatório contém o aviso de buffs de suporte não pareados.

---

### T2.2 — Clustering de build de talentos

**Pré-requisitos:** T2.1.
**Implementa:** recomendação 6.3b.

> ⚠️ **Duas correções verificadas** (`docs/schema_confirmado.md` §4 e §8):
> 1. **`combatantInfo.talents` vem VAZIO (`[]`).** Os talentos reais estão em
>    **`combatantInfo.talentTree`**: `[{id, rank, nodeID}, ...]`. Use `talentTree`.
> 2. **`characterRankings` não retorna `talents` nem `gear`.** Não há atalho: o clustering exige o
>    `combatantInfo` de cada log de referência, obtido no fetch que a T1.4 já faz e cacheia.

1. Normalizar o loadout de cada membro em um conjunto de `(nodeID, rank)` a partir de
   `combatantInfo.talentTree`. O hash de build é o `sha256` desse conjunto ordenado.
2. Agrupar por **similaridade de Jaccard ≥ 0.85** (clustering aglomerativo simples; não precisa de biblioteca de ML).
3. Nomear cada cluster pelos talentos que o distinguem do maior cluster.
4. Determinar o cluster do jogador.

**Novo achado de primeira classe — divergência de build:**

Se o jogador está num cluster que representa **< 20%** da coorte, o relatório deve abrir com:

```
🧬 BUILD DIVERGENTE
Sua build aparece em 4% dos top parses (2/47 logs).
A build dominante (68%, 32/47) difere em: <talento A> em vez de <talento B>.
DPS mediano da build dominante: 1.24M vs 1.09M na sua build (Δ +13,8%).
⚠️ Antes de otimizar rotação, avalie a troca de build.
```

**Isto precede qualquer análise de timing no relatório.** Otimizar a rotação de uma build inferior é conselho de baixo valor.

**Critério de aceite:**
- Teste: coorte sintética com 2 builds claramente distintas → 2 clusters, atribuição correta.
- Teste: jogador em cluster minoritário → o achado de build aparece como primeiro item do relatório.
- Teste: builds idênticas → 1 cluster, nenhum achado de divergência.

---

### T2.3 — Grading por quantil empírico e bootstrap

**Pré-requisitos:** T2.1.
**Corrige:** achados 3.6 (thresholds fixos), 3.7 (comparações múltiplas), e o `stdev` morto.

**1. Perfil por quantis.** Para cada habilidade e cada posição alinhada, armazenar os quantis empíricos da coorte: `p10, p25, p50, p75, p90`, além de `n`.

**2. Grading relativo:**

```
seja q = quantil empírico do tempo do jogador na distribuição de referência
🟢 se 0.25 <= q <= 0.75      (dentro do IQR)
🟡 se 0.10 <= q < 0.25 ou 0.75 < q <= 0.90
🔴 se q < 0.10 ou q > 0.90
```

Isto adapta a escala automaticamente por habilidade: 3s de desvio num CD de 30s e 20s num CD de 3min recebem tratamento proporcional. **Elimina os thresholds 10s/25s.**

**3. Bootstrap.** Intervalo de confiança de 90% para a mediana de referência, com `n_bootstrap = 2000` e semente fixa (`seed=20260817`) para reprodutibilidade. Exibir: `Ideal: 82s (IC90: 76–89s)`.

**4. Supressão por incerteza.** Se `n < 15` para uma posição específica, **não emitir cor** — exibir `⚪ amostra insuficiente (n=<k>)`. Melhor não opinar que opinar errado.

**5. Controle de comparações múltiplas.** Aplicar Benjamini-Hochberg (FDR = 0.10) sobre o conjunto de desvios de um relatório. Achados que não sobrevivem ao controle vão para uma seção colapsada `Desvios menores (não significativos)`.

**Critério de aceite:**
- Teste: jogador exatamente na mediana → 🟢, independentemente do valor absoluto.
- Teste: mesma diferença absoluta (5s) em habilidade de CD curto vs. longo produz cores diferentes.
- Teste: `n=8` para uma posição → ⚪, nunca 🔴.
- Teste: bootstrap com semente fixa é determinístico entre execuções.
- Teste: com 100 desvios aleatórios sob a hipótese nula, ≤ 10% sobrevivem ao BH.

---

### T2.4 — Normalização temporal por fase

**Pré-requisitos:** T2.1.
**Corrige:** achado 3.2 (rotação-fantasma).

> ⚠️ **Correção baseada em medição real** (`docs/schema_confirmado.md` §7). As fases **se repetem
> em ciclo**. No log de Zarad, `phaseTransitions` é:
> `[{id:1},{id:2},{id:1},{id:2},{id:1}]` — ou seja, `id` é o **identificador da fase**, não um
> índice sequencial. Chavear o perfil apenas por `phase_id` misturaria a 1ª e a 3ª ocorrência da
> fase 1, recriando exatamente a "rotação-fantasma" que esta tarefa existe para eliminar.

1. Extrair `phaseTransitions` da luta (✅ confirmado que existe).
2. Derivar os **intervalos de fase** como `(phase_id, ocorrência, t_início, t_fim)` — para o log de
   Zarad: `(1,0), (2,0), (1,1), (2,1), (1,2)`.
3. Converter cada timestamp de cast em `(phase_id, ocorrência, tempo_relativo_ao_início_do_intervalo)`.
4. Construir o perfil de referência chaveado por **`(phase_id, ocorrência)`**, nunca por `phase_id` sozinho.
5. Alinhar (T0.5) **dentro de cada intervalo**, separadamente.
6. Referências com número diferente de ocorrências de uma fase (kill mais rápido = menos ciclos)
   contribuem apenas para os intervalos que possuem. Registre `n` por intervalo — ele cai nos
   ciclos tardios, e a supressão por amostra pequena da T2.3 deve valer aqui.

**Ganho colateral desta tarefa:** com o tempo normalizado por intervalo de fase, as métricas
posicionais deixam de exigir kills de duração parecida. Isto é o que permite usar o pool inteiro
de rankings (T0.8) para a terceira classe de métricas, em vez de restringir a ±12%.

**Fallback obrigatório:** encontros sem fases declaradas usam uma única "fase 0" abrangendo a luta inteira. O comportamento degrada para o da Fase 0 sem quebrar.

**Efeito colateral desejado:** com normalização por fase, a tolerância de duração pode ser relaxada (a T2.1 já prevê ±12%/±20% no relaxamento), ampliando as coortes.

**Critério de aceite:**
- Teste: luta com 3 fases → alinhamentos independentes por fase; um cast tardio da fase 1 não pareia com um cast da fase 2.
- Teste: luta sem fases → resultado idêntico ao da T0.7 (não há regressão).
- **Teste com a fixture real:** o fight de Zarad produz exatamente 5 intervalos, com chaves
  `(1,0), (2,0), (1,1), (2,1), (1,2)`. Um cast na 3ª ocorrência da fase 1 **não** pode ser
  agregado junto com casts da 1ª ocorrência.

---

### T2.5 — Cooldowns base no catálogo

**Pré-requisitos:** T0.6, T1.3.

Preencher `spells.base_cooldown_s` para que a classificação MAJOR/MINOR da T0.6 use o primeiro ramo (dado, não inferência).

**Fontes, em ordem de preferência:**
1. API de spell da Blizzard (campo de cooldown, se exposto — verifique e registre em `docs/schema_confirmado.md`).
2. Tabela manual curada em `src/botgitgud/domain/cooldowns.py` para as habilidades que aparecerem nos relatórios reais.
3. `None` — o fallback da T0.6 continua válido.

**Não bloqueie** a Fase 2 nesta tarefa. É incremental: preencha o que conseguir.

**Critério de aceite:**
- Teste: habilidade com `base_cooldown_s` conhecido usa o primeiro ramo da regra de classificação.
- Teste: habilidade sem o dado degrada para os ramos seguintes sem erro.

---

### Portão de saída da Fase 2

- [ ] T2.1–T2.5 ✅.
- [ ] Todo relatório declara: `n` da coorte, covariáveis pareadas, covariáveis relaxadas, `cohort_id`, `code_version`.
- [ ] Nenhum threshold absoluto de tempo permanece em `analysis/` (`grep -n "10\.0\|25\.0" src/botgitgud/analysis/` só encontra constantes vindas de `Settings`).
- [ ] Relatório de um jogador em build minoritária abre com o achado de build.

---

## FASE 3 — Cobertura de métricas e quantificação de impacto

> **Meta:** medir o que realmente determina parse e responder "quanto isso me custou".

---

### T3.1 — Features de performance além de casts

**Pré-requisitos:** Fase 2 completa.
**Implementa:** recomendação 6.5.

Estender `PlayerLog` (campos já reservados na T1.2). Implemente **nesta ordem de prioridade**:

| # | Feature | Fonte | Regra de cálculo |
|---|---|---|---|
| 1 | `active_time_pct` | tabela `Summary` | tempo ativo / duração |
| 2 | `uptimes[spell_id]` | tabelas `Buffs`/`Debuffs` | soma das janelas / duração; só buffs presentes em ≥70% da coorte |
| 3 | `damage_by_ability` | tabela `DamageDone` | total, hits, casts por habilidade |
| 4 | `resource_waste` | eventos `resourcechange` | soma de `waste` por tipo de recurso |
| 5 | `deaths`, `downtime_s` | tabela `Deaths` | contagem + tempo morto |
| 6 | `avg_targets_per_cast` | eventos de dano | alvos únicos atingidos por cast, por habilidade |

**⚠️ Atribuição de dano de pet — armadilha confirmada, leia `docs/schema_confirmado.md` §5.**

Medições reais no log de Zarad (Demonology, 70,4% do dano vem de pets):

| Item | Valor |
|---|---|
| `entry.total` | **37.378.119** ← autoritativo, **já inclui pets** |
| `sum(entry.abilities)` | 19.315.924 — **truncado em 5 habilidades** |
| `sum(entry.pets)` | 26.297.092 |
| Agregação por eventos (jogador + 20 pets) | **37.378.119** — 29 habilidades, **erro 0,00%** |

**Regras normativas:**

1. ❌ **NÃO some `entry.pets` ao `entry.total`** — o total já os inclui; somar causa dupla contagem.
2. ❌ **NÃO use `entry.abilities` para o detalhamento por habilidade** — vem truncado em 5 de 29 no
   caso medido. A decomposição da T3.2 construída sobre ele estaria silenciosamente errada, e
   **apenas para algumas classes**, o que torna o bug quase invisível.
3. ✅ **Método correto:** agregar eventos `events(dataType: DamageDone)` por `abilityGameID`,
   somando `amount + absorbed`, onde
   `sourceID ∈ {player_id} ∪ {actor.id : actor.petOwner == player_id}`.
   O mapeamento vem de `masterData.actors { id name type subType petOwner }` (✅ confirmado).
4. ⚠️ `table(dataType: DamageDone, sourceID: N)` **não** é atalho válido — muda o formato da
   resposta, misturando alvos e fontes.

**Custo:** os eventos de dano deste log consumiram **7 páginas** com `limit: 10000`. Contabilize
na T1.8. Como logs são imutáveis e cacheados para sempre (T1.4), o custo é único por log.

**Teste obrigatório (reconciliação):** para a fixture de Zarad, a soma de `damage_by_ability`
agregada por eventos deve igualar `entry.total` da tabela `DamageDone` **com tolerância de 1%**.
O valor esperado é `37.378.119`. Foi verificado que bate exatamente — se o seu código não bater,
o erro é seu, não da API.

**Sobre `avg_targets_per_cast`:** com todas as specs no escopo, a variância entre encontros single-target e de cleave é enorme e afeta specs de forma desigual. Esta feature é o que permite distinguir "seu dano por cast está baixo porque você errou a janela" de "seu dano por cast está baixo porque você atingiu menos alvos". Ela alimenta o diagnóstico da T3.2.

**Cada feature gera um achado comparativo** com o mesmo tratamento da T2.3 (quantil na coorte + supressão por amostra pequena).

**Ordem no relatório** — esta ordenação é normativa, pois reflete poder explicativo:
`1. Build → 2. Mortes/downtime → 3. Active time → 4. Uptimes → 5. Waste de recurso → 6. Usos perdidos de CD → 7. Timing de CD`

O timing de cooldown — hoje a única coisa medida — passa a ser o **último** item.

**Critério de aceite:**
- Teste por feature contra fixtures sintéticas com valores conhecidos.
- Teste: a ordem das seções do relatório é exatamente a especificada.
- Teste: jogador com `active_time_pct` no p05 da coorte tem esse achado acima de qualquer achado de timing.

---

### T3.2 — Decomposição do gap de DPS ⭐

**Pré-requisitos:** T3.1.
**Implementa:** recomendação 6.4 nível 1. **Maior retorno/esforço do roadmap.**

**Definições.** Para cada habilidade `a`:

```
c_u(a) = nº de casts do jogador
d_u(a) = dano total do jogador com a
p_u(a) = d_u(a) / c_u(a)             # dano por cast (0 se c_u = 0)

c_r(a) = mediana de casts na coorte
p_r(a) = mediana do dano por cast na coorte
d_r(a) = c_r(a) * p_r(a)
```

**Decomposição (estilo Oaxaca-Blinder):**

```
Δd(a)     = d_u(a) - d_r(a)
volume(a) = (c_u(a) - c_r(a)) * p_r(a)                    # erro de rotação / usos
eficiência(a) = (p_u(a) - p_r(a)) * c_r(a)                # janela, buffs, gear, alvos
interação(a)  = (c_u(a) - c_r(a)) * (p_u(a) - p_r(a))

verificação obrigatória: volume + eficiência + interação == Δd  (tolerância 1e-6)
```

Converter para DPS dividindo pela duração, e para percentual do DPS total do jogador.

**Saída do relatório:**

```
💥 DE ONDE VEIO O GAP DE DPS
Você: 1.09M DPS  |  Coorte (mediana): 1.24M DPS  |  Gap: −12,1%

Habilidade            Gap DPS    Volume    Eficiência   Diagnóstico
Chaos Strike          −6,3pp     −1,1pp     −5,2pp      dano/cast baixo → fora de buff
Eye Beam              −3,8pp     −3,8pp      0,0pp      2 usos perdidos
Blade Dance           −1,4pp     −0,2pp     −1,2pp      poucos alvos atingidos
(outras 11)           −0,6pp
```

**Regra de diagnóstico** (normativa), avaliada nesta ordem:

1. Se `has_augmentation` ou `external_buffs` foram **relaxados** na T2.1 e o termo dominante é `eficiência` →
   `"dano por cast abaixo — buffs de suporte não pareados, possivelmente não controlável"` e marque o finding como `confidence="baixa"`.
2. Se `avg_targets_per_cast` do jogador está abaixo do p25 da coorte e o termo dominante é `eficiência` →
   `"menos alvos atingidos"`.
3. `|volume| > 2 × |eficiência|` → `"usos perdidos/excedentes"`.
4. `|eficiência| > 2 × |volume|` → `"dano por cast abaixo — janela ou buffs próprios"`.
5. caso contrário → `"volume e eficiência combinados"`.

As regras 1 e 2 existem porque o termo de eficiência absorve indiscriminadamente causas controláveis (errar a janela de burst) e não-controláveis (não ter Augmentation no raid, encontro com menos alvos). Sem elas, a ferramenta culpa o jogador por circunstâncias do grupo — exatamente o tipo de conselho errado que a auditoria identificou.

**Gating por tamanho de efeito:** só listar habilidades com `|Δd|` ≥ **0,5%** do DPS total do jogador. O resto agrega em "outras N". (Recomendação 6.3g.)

**Critério de aceite:**
- Teste: a identidade da decomposição fecha (soma dos 3 termos == Δd) em 1000 casos gerados por Hypothesis.
- Teste: jogador idêntico à mediana da coorte → todos os termos ≈ 0.
- Teste: jogador com metade dos casts e mesmo dano/cast → gap 100% em `volume`, `eficiência` = 0.
- Teste: jogador com mesmos casts e 80% do dano/cast → gap 100% em `eficiência`.
- Teste: habilidade com impacto de 0,3% não aparece na lista.

---

### T3.3 — Priorização e "Top 3 ações"

**Pré-requisitos:** T3.2.
**Implementa:** recomendação 6.6.

```python
@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind             # BUILD | DEATH | ACTIVE_TIME | UPTIME | WASTE | MISSED_CD | CD_TIMING | ABILITY_GAP
    title: str
    detail: str
    estimated_gain_pct: float | None    # ganho estimado de DPS, em pontos percentuais
    confidence: Literal["alta", "média", "baixa"]
    evidence: Mapping[str, Any]
```

**Score de prioridade:** `estimated_gain_pct × peso_de_confiança`, com pesos `alta=1.0`, `média=0.6`, `baixa=0.3`.

**Confiança** é determinada por:
- `alta`: `n >= 30`, covariáveis principais pareadas, sobrevive ao BH.
- `média`: `15 <= n < 30` **ou** alguma covariável relaxada.
- `baixa`: `n < 15` **ou** feature derivada de dado incompleto.

**Estrutura obrigatória do relatório:**

```
1. Cabeçalho: jogador, boss, DPS, percentil, gap vs. coorte, n e qualidade da coorte
2. 🎯 TOP 3 AÇÕES  — os 3 findings de maior score, com ganho estimado
3. 💥 De onde veio o gap de DPS (T3.2)
4. Detalhamento por categoria, na ordem da T3.1
5. Desvios menores (não significativos)
6. Rodapé: cohort_id, code_version, timestamp
```

**Critério de aceite:**
- Teste: relatório sempre tem entre 1 e 3 itens no Top 3 (0 apenas se nenhum achado passa o gating — nesse caso, mensagem "nenhum problema material detectado").
- Teste: um achado de `baixa` confiança com ganho de 5pp fica abaixo de um de `alta` confiança com 3pp.
- Teste: nenhum finding sem `estimated_gain_pct` entra no Top 3.

---

### T3.4 — Relatório HTML

**Pré-requisitos:** T3.3.

O relatório completo já não cabe em texto. Gerar HTML autocontido (CSS inline, sem CDN):

- Timeline por habilidade: casts do jogador sobrepostos à banda IQR da coorte.
- Waterfall da decomposição de DPS.
- Tabela de features com posição percentil do jogador.
- Discord passa a enviar: cabeçalho + Top 3 em texto, e o HTML como anexo.

**Requisito de acessibilidade:** não codificar informação **apenas** por cor. Todo 🟢/🟡/🔴 acompanha rótulo textual.

**Critério de aceite:**
- Teste: HTML gerado passa em um parser XML estrito.
- Teste: não há nenhuma URL externa no HTML (`http://`/`https://` só em texto, nunca em `src`/`href` de recurso).
- Teste: mensagem do Discord ≤ 2000 caracteres com o anexo presente.

---

### Portão de saída da Fase 3

- [ ] T3.1–T3.4 ✅.
- [ ] Um relatório real gerado e colado em `docs/progresso.md`, mostrando Top 3 com ganho estimado.
- [ ] Identidade da decomposição verificada em teste property-based.
- [ ] Timing de cooldown é a **última** seção do relatório.

---

## FASE 4 — Modelagem e validação

> **Meta:** atribuição estatística e prova de que o conselho funciona.
> **Pré-requisito de dados:** ≥ 5.000 logs ingeridos para a spec/encontro alvo. Não inicie a Fase 4 antes disso.

---

### T4.1 — Dataset de treino

Materializar uma tabela de features a partir do store: uma linha por `(log, jogador)`, colunas = features da T3.1 + covariáveis + alvo (`percentile`).

**Separação obrigatória de colunas** em dois grupos declarados no código:
- `CONTROLLABLE`: active time, uptimes, contagens de cast, score de alinhamento, waste, mortes.
- `NON_CONTROLLABLE`: ilvl, tier, buffs externos recebidos, duração, composição.

**Split temporal, nunca aleatório:** treino em logs anteriores a uma data de corte, validação nos posteriores. Split aleatório vaza informação entre logs do mesmo raid/pull.

---

### T4.2 — Modelo + SHAP

- LightGBM regressor prevendo `percentile`.
- Baseline obrigatório para comparação: regressão linear sobre as 5 features principais. **Se o LightGBM não superar o baseline por margem clara na validação, use o baseline** — modelo complexo sem ganho é passivo.
- SHAP por log para atribuição individual.

**Regra inviolável:** **somente features `CONTROLLABLE` podem virar recomendação.** Features não-controláveis entram no modelo para controle estatístico, mas o relatório nunca diz "aumente seu ilvl" como achado acionável. Um teste automatizado deve garantir isso.

**Aviso obrigatório no código e no relatório:** SHAP explica o modelo, não causalidade. Rotule esses achados como `confidence="média"` no máximo, nunca `alta`.

**Critério de aceite:**
- Teste: nenhum finding gerado por SHAP referencia uma feature de `NON_CONTROLLABLE`.
- Métrica de validação (MAE em percentis) registrada em `docs/progresso.md`.

---

### T4.3 — Backtesting e calibração

**Implementa:** recomendação 6.8. **Esta tarefa é o que separa a ferramenta de um gerador de opinião.**

1. **Correlação:** verificar que cada feature reportada correlaciona com percentil no conjunto de validação. Feature sem correlação **deve ser removida do relatório**.
2. **Calibração:** para achados com `estimated_gain_pct`, comparar ganho previsto vs. realizado em logs onde a feature de fato melhorou. Plotar calibração; se sistematicamente otimista, aplicar fator de correção documentado.
3. **Controle de regressão à média:** ao medir melhora de um jogador entre pulls, compare contra um grupo de controle de jogadores com percentil inicial similar. Sem isso, você mede ruído.

**Critério de aceite:**
- `docs/validacao.md` gerado com correlação por feature e curva de calibração.
- Toda feature exibida no relatório tem correlação documentada.

---

### T4.4 — SimulationCraft (opcional)

Só inicie após T4.3. Integrar SimC para o contrafactual gear/talentos. Registre o escopo em `docs/backlog.md` e confirme com o usuário antes de começar — é a tarefa de maior custo e menor certeza do roadmap.

---

## Apêndice A — Mapa de rastreabilidade

Todo achado da auditoria tem uma tarefa. Use esta tabela para verificar cobertura.

| Achado (relario.md) | Severidade | Tarefa |
|---|---|---|
| 3.1 Nearest-neighbor enviesado | 🔴 | T0.5, T0.7 |
| 3.2 Rotação-fantasma por slot | 🔴 | T2.4 |
| 3.3 `avg_cd_duration` = instante do cast | 🔴 | T0.6 |
| 3.4 Coorte mal especificada | 🔴 | T0.8, T0.9, T2.1 |
| 3.5 Viés de sobrevivência | 🔴 | T2.3 (quantis), T4.1 (controláveis) |
| 3.6 Thresholds absolutos | 🟠 | T2.3 |
| 3.7 Comparações múltiplas | 🟠 | T2.3 (BH), T3.2 (gating) |
| 3.8 Estado global mutável | 🟠 | T0.4, T1.5 |
| 3.9 Filtro de duração absoluto | 🟠 | T0.8 |
| 3.10 Percentil fabricado (99.0) | 🔴 *(agravado)* | T0.7 — verificado: o campo **não existe** na API, logo o valor é fabricado em **100%** dos relatórios, não apenas quando ausente |
| 3.11 Parse do jogador ausente | 🟠 | T0.7 |
| 3.12 Blacklist por substring | 🟡 | T0.6 |
| 3.13 Jogador identificado por nome | 🟡 | T2.1 |
| 4.1 Race condition no JSON | 🔴 | T0.4 |
| 4.2 Custo de API insustentável | 🔴 | T1.4, T1.7, **T1.8** |
| 4.3 Sem timeout/retry/rate limit | 🔴 | T0.3 |
| 4.4 Token sem expiração | 🟠 | T0.3 |
| 4.5 Sem deps/git/testes | 🟠 | T0.0, T0.2 |
| 4.6 Higiene de credenciais | 🟠 | T0.0 |
| 4.7 Arquitetura monolítica | 🟠 | T1.2, T1.6 |
| 4.8 Entrega do relatório | 🟡 | T0.7, T3.4 |
| 4.9 DEBUG hardcoded | 🟡 | T1.1 |
| 6.5 Métricas ausentes | — | T3.1 |
| 6.4 Quantificação de impacto | — | T3.2, T4.2 |
| 6.6 Priorização | — | T3.3 |
| 6.8 Validação | — | T4.3 |

---

## Apêndice B — Constantes normativas (fonte única da verdade)

| Constante | Valor | Onde vive | Tarefa |
|---|---|---|---|
| Timeout conexão / leitura | 5.0 s / 30.0 s | `WclClient` | T0.3 |
| Máximo de tentativas | 4 | `WclClient` | T0.3 |
| Backoff base / fator / jitter | 1.0 s / 2.0 / total | `WclClient` | T0.3 |
| Piso de pontos de API | 1000 | `Settings.api_points_floor` | T0.3 |
| `gap_penalty` do alinhamento | 25.0 s | `Settings.gap_penalty_s` | T0.5 |
| Limiar MAJOR/MINOR | 90.0 s | `Settings` | T0.6 |
| Piso de intervalo para elegibilidade | 15.0 s | `Settings` | T0.6 |
| Presença mínima | 0.70 | `Settings` | T0.6 |
| Banda de sanidade de duração | ±35% | `Settings` | T0.8 |
| Tolerância p/ métricas posicionais (pré-T2.4) | ±12% | `Settings` | T0.8 |
| `COHORT_MIN_HARD` | **8** | `Settings` | T0.8 |
| `COHORT_MIN_WARN` | **20** | `Settings` | T0.8 |
| `COHORT_MAX` | 100 (raramente atingido) | `Settings` | T0.8 |
| Teto de pontos de API | ler de `rateLimitData` (medido: 3600/h, **conta inteira**) | `WclClient` | T1.8 |
| Reserva p/ caminho interativo | 25% do teto | `Settings` | T1.8 |
| Jobs por usuário | 1 ativo + 3 na fila; cooldown 60 s | `Settings` | T1.8 |
| `max_concurrent_jobs` | 2 | `Settings` | T1.8 |
| Páginas máximas de ranking | 10 | `Settings` | T0.8 |
| Specs suportadas | 25 (allowlist explícita) | `domain/specs.py` | T0.9 |
| `metric` de ranking | sempre `"dps"` | `ingest/rankings.py` | T0.8 |
| Tolerância soma pet/total | 1% | teste | T3.1 |
| `max_workers` | 4 | `Settings` | T1.4 |
| Bucket de duração (batch) | 5% | `build-cohort` | T1.7 |
| Similaridade de cluster de talentos | Jaccard ≥ 0.85 | `analysis/cohort.py` | T2.2 |
| Limiar de build minoritária | < 20% da coorte | `analysis/findings.py` | T2.2 |
| Quantis do perfil | 10/25/50/75/90 | `analysis/profile.py` | T2.3 |
| `n` mínimo para emitir cor | 15 | `Settings` | T2.3 |
| Iterações de bootstrap / semente | 2000 / 20260817 | `analysis/profile.py` | T2.3 |
| FDR (Benjamini-Hochberg) | 0.10 | `analysis/findings.py` | T2.3 |
| Gating de tamanho de efeito | 0,5% do DPS total | `Settings` | T3.2 |
| Pesos de confiança | alta 1.0 / média 0.6 / baixa 0.3 | `analysis/findings.py` | T3.3 |
| Logs mínimos para Fase 4 | 5.000 | — | T4.1 |

---

## Apêndice C — Checklist de decisões que exigem o usuário

Estas **não** são suas para decidir. Pergunte quando chegar na tarefa.

**Todas as decisões estruturais foram resolvidas.** Nenhuma tarefa está bloqueada.

| # | Decisão | Tarefa | Status |
|---|---|---|---|
| 1 | Log de fixture | T0.2 | ✅ `PtfBbQKRY9d6zAMC` fight 1, **Zarad** |
| 2 | Log de spec com pet | T3.1 | ✅ **o mesmo** — Zarad é Demonology, 70,4% do dano de pets |
| 3 | Escopo: tanks e healers | §1.4 | ✅ fora de escopo |
| 4 | Escopo: Evoker Augmentation | §1.4 | ✅ fora de escopo (mas é covariável — T2.1) |
| 5 | Versão do jogo | §1.5 | ✅ somente Retail |
| 6 | Tipo de conteúdo | §1.5 | ✅ somente Raid |
| 7 | Partition | §1.5 | ✅ somente a atual |
| 8 | Uso | §1.5 / T1.8 | ✅ servidor Discord multiusuário |
| 9 | Rotacionar as 5 credenciais expostas | T0.0 | ⚠️ **pendente do usuário** — não bloqueia |
| 10 | Manter `!analisar` ou migrar para slash commands | T1.6 | manter na Fase 1 |
| 11 | Hospedagem do relatório HTML (anexo vs. link) | T3.4 | anexo |
| 12 | Investir na integração SimulationCraft | T4.4 | avaliar após T4.3 |

### D.1 — Orçamento de API: por que o batch é sob demanda

Com 25 specs suportadas, o pré-cômputo exaustivo é inviável:

```
25 specs × ~8 bosses × 3 dificuldades ≈ 600 coortes
× ~26 logs por coorte × ~10 requisições por log ≈ 156.000 requisições
```

Contra um teto **medido** de `limitPerHour = 3600` **para a conta inteira**
(`docs/schema_confirmado.md` §2), compartilhado por todos os usuários do servidor.

**Portanto o `build-cohort` da T1.7 é obrigatoriamente sob demanda (lazy), nunca exaustivo:**

- Uma coorte só é construída quando alguém pede análise daquela combinação.
- Coortes já construídas são reutilizadas por todos os jogadores da mesma spec/boss — é aqui que
  está o ganho, e ele é grande num servidor com muitos usuários.
- O job de batch existe para **construir em background sem travar o usuário**, não para pré-popular
  o universo.
- A T1.8 implementa a fila, a deduplicação e o orçamento global que tornam isso seguro.

**Ordem de grandeza esperada:** a primeira análise de uma combinação spec/boss é lenta (minutos,
enfileirada). Todas as seguintes na mesma combinação são rápidas (< 10 s). Comunique isso ao
usuário na mensagem de enfileiramento.

---

## Apêndice D — Ordem de execução condensada

```
FASE 0  T0.0 → T0.1 → T0.2 → T0.3 → T0.4 → T0.5 → T0.6 → T0.7 → T0.8 → T0.9   [portão]
FASE 1  T1.1 → T1.2 → T1.3 → T1.4 → T1.5 → T1.6 → T1.7 → T1.8          [portão]
FASE 2  T2.1 → T2.2 → T2.3 → T2.4 → T2.5                               [portão]
FASE 3  T3.1 → T3.2 → T3.3 → T3.4                                      [portão]
FASE 4  T4.1 → T4.2 → T4.3 → (T4.4 opcional)
```

Dependências que permitem paralelismo, caso haja mais de um executor:
- T0.3 e T0.4 são independentes entre si (ambas só dependem de T0.0).
- T0.5 e T0.6 são independentes entre si.
- T0.9 depende apenas de T0.8; pode correr em paralelo a T0.5/T0.6/T0.7.
- T2.2, T2.4 e T2.5 são independentes entre si (todas dependem de T2.1, exceto T2.5 que depende de T0.6+T1.3).
- Todo o resto é estritamente sequencial.
