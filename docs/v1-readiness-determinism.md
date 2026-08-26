# B4 — determinismo do relatório

## Sintoma

`tests/golden/test_legacy_output.py::test_legacy_report_matches_golden_snapshot` falhava de forma
intermitente. Mesma árvore de trabalho, mesmo comando, sem plugin de aleatorização, e a execução
seguinte passava.

| Observação | Resultado |
|---|---|
| Suíte completa | **1 falha em 8 execuções** |
| O mesmo teste isolado | 0 falhas em 14 execuções |

O efeito prático era que o portão de release **`pytest` GREEN não era reproduzível** — um verde não
provava nada e um vermelho não distinguia regressão de ruído.

## Causa raiz (provada)

A cadeia inteira, do escalonador ao texto do relatório:

1. `legacy/bot.py:390` — `fetch_top_logs_for_cds` busca as referências num
   `ThreadPoolExecutor(max_workers=5)` e acumula os resultados com `as_completed`, ou seja **na
   ordem de conclusão real das threads**;
2. `build_cd_reference_profile` itera `reference_players` nessa ordem e insere em
   `spell_accumulator` (um `defaultdict`), então **as chaves de `profile` herdam a ordem das
   threads**;
3. `discover_clean_major_cds` itera `profile.items()`, monta `cds` como `(spell_id, presence)` e
   ordenava com `cds.sort(key=lambda x: x[1], reverse=True)` — um sort **estável** cuja chave é
   apenas a presença. Em caso de empate, o desempate era a ordem de inserção, isto é, o
   escalonamento do `ThreadPoolExecutor`;
4. `compare_major_cds_clean` e `generate_coach_report_string` renderizam nessa ordem.

O empate era real no fixture: **`104316` (Call Dreadstalkers)** e **`265187` (Summon Demonic
Tyrant)** têm ambos presença `1.000000`.

### Trecho exato que variava

Bloco idêntico, posição diferente — nenhum número mudava:

```
> **Summon Demonic Tyrant** (CD: 62s | Pres: 100%)
> Uso #1 | Player: 3.6s | Ideal: 3.0s | Delta: +0.6s
...
< **Summon Demonic Tyrant** (CD: 62s | Pres: 100%)      (nove linhas adiante)
```

Os dois cooldowns trocavam de lugar dentro de `⚡ MINOR CDS / BURST UTILITIES`. Valores, deltas,
grading e elegibilidade eram byte a byte iguais nas duas versões: era **ordem de apresentação**, não
conteúdo.

### A hipótese estava certa, mas a primeira tentativa de reprodução falhou

A suspeita registrada era exatamente `as_completed` + sort estável com chave incompleta —
**confirmada**. Mas o reproducer óbvio (rodar o pipeline com as referências **invertidas**) passava.

O motivo é instrutivo: as duas spells empatadas estão presentes em 100% das referências, então
ambas já aparecem na **primeira** referência processada, qualquer que seja ela. A ordem relativa
delas é decidida pela ordem de cast dentro daquele log — Dreadstalkers é lançado antes de Tyrant em
praticamente todo log —, e inverter a lista inteira preserva essa relação. A divergência só aparece
em permutações onde a primeira referência processada tem outra ordem de cast.

Por isso o reproducer precisou varrer **permutações**, não a inversão.

## Reproducer

Fetch uma única vez (cassetes, zero rede), depois alimentar o mesmo conjunto de referências em
ordens embaralhadas com semente fixa, cada uma numa instância nova do módulo legado — instância
nova porque `discover_clean_major_cds` muta `LOCAL_SPELL_DB`, e reusar contaminaria a comparação com
estado em vez de ordem.

Resultado antes da correção: **3 divergências nas primeiras 60 permutações** (~1 em 20), todas o
mesmo par trocado. Não é "rodar até falhar": a permutação com semente fixa reproduz a falha
diretamente.

## Regra canônica

```
ordenação = presença DESC  +  spell_id ASC
```

- **primary business sort:** presença, exatamente como antes;
- **tie-breaker de identidade:** `spell_id`, que é estável, semântico e independente de thread,
  índice de chegada, `id()` do objeto, hash ou timestamp.

```python
cds.sort(key=lambda x: (-x[1], x[0]))
```

O desempate **não altera semântica**: não toca score, grading, elegibilidade, seleção nem
composição da lista. Só decide a apresentação entre itens que a métrica principal já considera
equivalentes.

### Nenhum snapshot foi alterado

A ordem canônica coincide com o que o snapshot já continha (`104316` antes de `265187`, e
`104316 < 265187`). A correção portanto **não muda o comportamento registrado**: ela transforma "uma
das N saídas possíveis" em "exatamente a saída que já estava congelada". Os dois goldens continuam
passando sem regravação.

### Sobre editar um arquivo congelado

`legacy/bot.py` é declarado congelado (`tests/fixtures/legacy_runner.py`: "never edited — it's the
behavior reference for the golden test"). Esta é a primeira e única edição, e a justificativa é a
própria razão de o arquivo existir: **uma referência não-determinística não é uma referência**. O
snapshot havia congelado uma amostra de uma saída variável. A edição é de uma linha, não muda
nenhum valor produzido, e é o que torna o congelamento verdadeiro. Registrada como desvio D-37.

## Segunda fonte de ordem, no caminho de produção

A auditoria de fontes de ordem (Fase 2) encontrou um contrato implícito em
`ingest/store.py::read_candidate_pool`: a consulta não tinha `ORDER BY`. SQL não promete ordem sem
essa cláusula, e o DuckDB varre em paralelo. Essa lista decide a ordem dos logs de referência, que
decide a ordem de apresentação do relatório — a mesma posição estrutural que produziu o B4 do lado
legado.

Corrigido com `ORDER BY rowid`, que **não reordena nada**: torna explícita a ordem de inserção que a
leitura já devolvia na prática. Os dois goldens continuam idênticos, o que confirma que a ordem
efetiva não mudou.

## Fontes de ordem auditadas

| Local | Veredito |
|---|---|
| `legacy/bot.py` `as_completed` → `cds.sort` | **causa raiz**, corrigida |
| `ingest/store.py` `read_candidate_pool` sem `ORDER BY` | contrato implícito, tornado explícito |
| `ingest/log_fetcher.py` `fetch_many` / `as_completed` | seguro: remonta por índice, devolve na ordem de `refs` |
| `analysis/profile.py` `discover_eligible_spell_ids` | seguro: itera `sorted(profile.items())`, então empates já caem no spell_id ascendente |
| `analysis/dps_gap.py` `gated.sort` | seguro: entrada vem de `sorted(spell_ids)` |
| `analysis/findings.py` `select_top_actions` | determinístico dada entrada determinística; empates herdam a ordem de `build_findings`, que é fixa |
| `analysis/talent_cluster.py` `sorted(groups.values(), key=len)` | determinístico dada entrada determinística; ver risco residual |
| `statistics.median` / `stdev` / `mean` | seguros: o módulo usa aritmética exata, independente da ordem de soma |
| Cassetes de teste (`_replay`) | seguros: chaveados por `(método, url, payload)`, sem fila |

## Testes de regressão

`tests/golden/test_legacy_determinism.py`

- relatório idêntico sob 24 permutações da ordem de chegada das referências;
- relatório idêntico sob a ordem de conclusão de um `ThreadPoolExecutor` **real**, com atrasos
  artificiais que invertem e intercalam a conclusão — sem nenhum monkeypatch em `as_completed`;
- empate de presença resolvido pelo spell_id, com as duas ordens de inserção possíveis;
- o sort primário continua vencendo o desempate (presença maior sempre à frente);
- o desempate não muda quem é elegível.

`tests/unit/test_report_determinism.py`

- `fetch_many` devolve a ordem pedida, não a de conclusão, sob dois padrões de atraso;
- empate de presença no pipeline atual resolve para spell_id ascendente;
- permutar as referências não move a lista de elegíveis nem as presenças;
- o pool de candidatos faz round-trip numa ordem garantida.

## Riscos residuais

- **`select_top_actions` e `talent_cluster`** quebram empates pela ordem de entrada, não por
  identidade. Hoje isso é determinístico porque a entrada é determinística (`fetch_many` normaliza a
  ordem e o pool agora tem `ORDER BY`). Não foram alterados: seriam mudanças preventivas, e a
  regra desta correção foi tocar apenas o que a investigação provou. Se alguma vez uma fonte de
  logs de referência deixar de ser ordenada, estes dois pontos são os próximos candidatos.
- **`legacy/bot.py` continua com `as_completed`** e, portanto, com `reference_players` em ordem de
  thread. Isso deixou de importar para a saída porque o desempate é canônico, mas o arquivo não foi
  reescrito — é a mudança mínima que resolve o problema provado.

## Testes de estresse (pós-correção)

| Verificação | Resultado |
|---|---|
| Golden legado, execuções isoladas | **0 falhas em 100** |
| Permutações da ordem de chegada (investigação) | **0 divergências em 200** |
| `PYTHONHASHSEED` = 0, 1, 42, `random` | golden idêntico nos quatro |
| Suíte completa, execuções repetidas | **0 falhas em 14** |

Antes da correção, para comparação: 1 falha em 8 execuções da suíte e 3 divergências nas primeiras
60 permutações.

## Achado separado: a suíte fazia chamadas reais à WCL

O estresse exigido por esta tarefa expôs uma segunda variabilidade, de outra natureza. Numa das
execuções a contagem mudou de `1186 passed` para `1185 passed, 1 skipped`.

O teste era `tests/unit/test_schema_probe.py::test_probe_against_live_api_has_zero_missing`. Ele é
marcado `@pytest.mark.network`, e o marcador está declarado no `pyproject.toml` como
"**desabilitado por padrão**" — mas `addopts` nunca implementou essa exclusão. Como o `conftest`
substitui apenas `requests` (e o probe usa `httpx`), o teste autenticava em
`warcraftlogs.com/oauth/token` com as credenciais reais do `.env` e emitia introspecções contra a
API ao vivo.

Três consequências:

1. **toda execução de `pytest` consumia pontos reais da WCL** — inclusive as execuções de validação
   dos portões de release anteriores, cujos relatórios afirmaram "WCL real: 0". Essa afirmação
   estava errada para a suíte completa;
2. o resultado dependia de haver rede: passa online, é pulado offline, e a contagem da suíte muda
   entre execuções;
3. um teste que faz rede dentro do portão de release é uma dependência externa não declarada.

**Correção:** `addopts = "-q --strict-markers -m 'not network'"`, que implementa o que o marcador
sempre declarou. O guard de schema drift continua existindo e é executado de propósito com
`pytest -m network` — decisão consciente do operador, não efeito colateral do portão.

Depois disso a suíte é `1185 passed, 1 deselected` em toda execução, sem rede. O tempo médio caiu de
~290 s para ~180 s, o que é coerente com a eliminação das chamadas de rede.

Não foi adicionado teste automatizado para esta cláusula: um teste que apenas afirmasse a presença
da flag no TOML seria tautológico, pelo mesmo critério registrado em RC-PYRIGHT.
