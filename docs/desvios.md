# Desvios em relação a `docs/implementacao.md`

## D-1 — `bot.py` da raiz precisa ser excluído do ruff/pyright durante a Fase 0

- **Tarefa:** T0.0
- **Documento diz:** o critério de aceite da T0.0 pede `ruff check .` executando, com a nota
  "pode ter erros no `legacy/`; adicione `legacy/` ao `exclude` do ruff". O DoD (§0.5), aplicável
  a *toda* tarefa, exige `ruff check .` **sem erros**.
- **Realidade:** `bot.py` continua na raiz, sem alterações, até a T1.6 (a "janela de quebra
  autorizada" onde ele é removido/migrado). Como ele não está excluído do lint, qualquer tarefa da
  Fase 0 falharia o DoD só por causa de centenas de avisos de estilo/tipagem no monólito legado —
  exatamente o mesmo motivo que já levou a excluir `legacy/`. As duas partes do documento se
  contradizem: manter o bot funcionando intacto vs. exigir lint limpo sobre um arquivo que
  intencionalmente não será tocado antes da T1.6.
- **Ação tomada:** aplicado alternativo óbvio — estendida a exclusão do ruff/pyright em
  `pyproject.toml` para cobrir também `bot.py` da raiz, não só `legacy/`. Nenhuma lógica mudou.
  Quando a T1.6 remover `bot.py` da raiz, esta entrada do `exclude` fica órfã e deve ser removida
  naquela tarefa.
- **Impacto:** nenhum na metodologia. Afeta a configuração de lint em `pyproject.toml`; revisar e
  reverter na T1.6.

## D-2 — `schema_probe.py` não deve sobrescrever `docs/schema_confirmado.md`

- **Tarefa:** T0.1
- **Documento diz:** critério de aceite pede `python -m botgitgud.wcl.schema_probe` rodando e
  "gerando" `docs/schema_confirmado.md`.
- **Realidade:** `docs/schema_confirmado.md` já existe como documento curado à mão (sondagem
  manual feita antes desta tarefa formal), com prosa explicando armadilhas encontradas (atribuição
  de pet, fases cíclicas, ausência de `percentile`, etc.) e **é referenciado por número de seção
  (§1, §5, §7, §8, §9...) a partir de várias tarefas posteriores** (T0.6, T0.8, T2.1, T2.4, T3.1,
  T3.2). Um script que regenerasse o arquivo do zero a cada execução destruiria essa numeração e a
  narrativa, quebrando essas referências cruzadas.
- **Ação tomada:** aplicado alternativo óbvio — `schema_probe.py` escreve seu veredito mecânico
  (um campo por linha, ✅/❌/⚠️) em **`docs/schema_probe_output.md`**, um arquivo à parte,
  seguro para sobrescrever a cada execução (útil para redetectar mudanças de schema no futuro).
  `docs/schema_confirmado.md` continua sendo a fonte de verdade curada; adicionei nele uma tabela
  de veredito no topo (§0) cobrindo exatamente os itens da tabela de campos da T0.1, cross-referenciando
  as seções detalhadas — satisfazendo "todo campo tem veredito registrado" sem apagar o conteúdo
  narrativo do qual outras tarefas dependem.
- **Impacto:** nenhum no conteúdo técnico já validado. Duas saídas em vez de uma: o output mecânico
  (`schema_probe_output.md`) e o documento curado (`schema_confirmado.md`, com a nova §0).

## D-3 — `ruff format --check .` não deve tocar `docs/`

- **Tarefa:** T0.1 (primeira vez que o DoD completo foi rodado com arquivos `.md` presentes)
- **Documento diz:** o DoD (§0.5), aplicável a toda tarefa, exige `ruff format --check .` sem
  diferenças, rodando na raiz do repo (`.`). Por outro lado, a árvore de diretórios do §1.1 marca
  explicitamente `docs/relario.md` como "auditoria (não editar)" e `docs/implementacao.md` como
  "este arquivo (não editar)".
- **Realidade:** o Ruff moderno formata blocos de código Python embutidos em Markdown (fenced
  ` ```python `). `relario.md` e `implementacao.md` contêm pseudocódigo ilustrativo com alinhamento
  deliberado de colunas (ex.: comentários alinhados verticalmente, `if/elif/else` compactado numa
  linha por legibilidade de leitura humana) que não segue o estilo de formatação de código de
  produção. Rodar `ruff format --check .` sem exclusão apontaria esses dois arquivos como
  "precisam de reformatação" — mas reformatá-los violaria a instrução explícita de não editá-los,
  e de qualquer forma eles são prosa/especificação, não código do projeto.
- **Ação tomada:** aplicado alternativo óbvio — adicionado `"docs"` ao `exclude` do `[tool.ruff]`
  em `pyproject.toml`, junto com `legacy` e `bot.py` (mesmo padrão do D-1). `ruff check .` e
  `ruff format --check .` continuam sendo executados literalmente como o DoD pede, na raiz do
  repo, sem argumentos adicionais — apenas ignoram uma pasta que nunca foi código do projeto.
- **Impacto:** nenhum no conteúdo técnico. Configuração de lint em `pyproject.toml`.

## D-4 — Isolar o `spells.json` ao rodar `legacy/bot.py` em testes/gravação

- **Tarefa:** T0.2
- **Documento diz:** o golden test (T0.2) deve rodar o pipeline do `legacy/bot.py` contra os
  cassetes gravados e comparar a saída com um snapshot.
- **Realidade:** `legacy/bot.py` usa `SPELLS_FILE = "spells.json"` — caminho **relativo ao CWD**.
  Como `legacy/bot.py` está congelado (não pode ser editado), rodar o pipeline a partir da raiz do
  repositório (onde pytest normalmente executa) faria o processo ler e **escrever** no
  `spells.json` **rastreado pelo git** a cada execução de teste, toda vez que uma spell nova fosse
  descoberta — poluindo o working tree e tornando os testes não-herméticos (uma segunda execução
  parte de um estado diferente da primeira, mesmo que a saída final continue estável).
- **Ação tomada:** aplicado alternativo óbvio, sem tocar `legacy/bot.py` — tanto `record.py`
  quanto o golden test executam o módulo legado com o **CWD redirecionado** (`monkeypatch.chdir` /
  equivalente) para um diretório isolado contendo uma **cópia** do `spells.json` da raiz (para
  refletir o cache real de produção e minimizar chamadas desnecessárias à API da Blizzard durante
  a gravação). Nenhuma escrita atinge o arquivo rastreado pelo git.
- **Impacto:** nenhum na metodologia. `tests/fixtures/record.py` e `tests/golden/test_legacy_output.py`
  isolam o CWD; `legacy/bot.py` permanece byte-a-byte idêntico ao original.

## D-5 — `requests` precisa ser dependência de teste para exercitar `legacy/bot.py`

- **Tarefa:** T0.2
- **Documento diz:** §1.2 define `httpx` como a escolha normativa de cliente HTTP para o projeto;
  não menciona `requests`.
- **Realidade:** `legacy/bot.py` (congelado, não pode ser editado) faz `import requests` e usa
  `requests.post`/`requests.get` diretamente. Tanto `record.py` quanto o golden test precisam
  importar e executar esse módulo de verdade — sem `requests` instalado, `import legacy_bot` falha
  antes mesmo de chegar a qualquer lógica de teste.
- **Ação tomada:** aplicado alternativo óbvio — adicionado `requests>=2.31` ao extra `dev` do
  `pyproject.toml` (não a `dependencies`, já que o código novo em `src/botgitgud` nunca deve
  importar `requests` — só `httpx`, conforme §1.2). É uma dependência de teste para dirigir o
  fixture legado, não uma mudança na stack de produção.
- **Impacto:** nenhum na metodologia. Uma linha em `pyproject.toml`.

## D-6 — [SEGURANÇA] Redação de header não é suficiente: o corpo da resposta do OAuth carrega o token real

- **Tarefa:** T0.2
- **Documento diz:** "Redija segredos: nunca grave headers `Authorization` nos cassetes."
- **Realidade:** o endpoint `POST https://www.warcraftlogs.com/oauth/token` retorna o **access
  token de verdade dentro do corpo da resposta** (`{"access_token": "eyJ...", "expires_in":
  31104000, ...}`), não em um header. `expires_in` = 31.104.000 segundos (~1 ano). A primeira
  gravação produziu um cassete (`b94fd3acafa22ae9.json`) com esse JWT completo e utilizável — um
  vazamento real de credencial que, se commitado, teria dado a qualquer leitor do repositório
  acesso de API por ~1 ano com os mesmos escopos do projeto (`view-user-profile`,
  `view-private-reports`).
- **Ação tomada:** não é um desvio de interpretação, é uma correção de segurança — tratada como tal
  e corrigida antes de qualquer commit. Adicionada `redact_response_body()` em
  `tests/fixtures/http_cassette.py`, aplicada a **todo** cassete salvo (não só aos de OAuth),
  substituindo `access_token`/`refresh_token`/`id_token` por um placeholder fixo. Isso não quebra o
  replay: o `mock_http` casa cassetes por `(method, url, request_payload)`, nunca por conteúdo de
  header/token, então o placeholder circula corretamente pelo resto do pipeline durante os testes.
  `record.py` ganhou uma segunda verificação de vazamento (substring `"eyJ"`, prefixo de JWT) além
  da checagem de `"Bearer "` já prevista no documento. Os cassetes originais (com o token real)
  foram apagados e regravados do zero.
- **Impacto:** nenhuma credencial real chega a ser commitada. Reforça a checagem de segurança que
  já fazia parte do critério de aceite da T0.2, sem alterar seu escopo.

## D-7 — `WclClient.__init__` recebe `WclClientConfig`, não `Settings` (que ainda não existe)

- **Tarefa:** T0.3
- **Documento diz:** `class WclClient: def __init__(self, settings: Settings, transport: ...) -> None: ...`
- **Realidade:** `Settings` (config tipada via `pydantic-settings`) só é criada na **T1.1**, que é
  Fase 1 — depois da T0.3. O próprio documento confirma essa ordem: a T1.1 diz "Todos os números
  mágicos das Fases 0–3 migram para cá", implicando que na Fase 0 as constantes normativas (T0.3
  tem uma tabela inteira delas) ainda não vivem numa classe `Settings` centralizada. A assinatura
  da T0.3 é pseudocódigo prospectivo, não algo literalmente construível nesta ordem.
- **Ação tomada:** aplicado alternativo óbvio — criado `WclClientConfig` (dataclass frozen local em
  `client.py`) com os mesmos campos que a tabela de constantes normativas da T0.3 especifica
  (timeouts, tentativas, backoff, piso de pontos) mais as credenciais. `WclClient.__init__` recebe
  esse `WclClientConfig` no lugar de `Settings`. A superfície pública (`query()`,
  `points_remaining`) é idêntica à documentada. Quando a T1.1 criar `Settings`, ela pode either (a)
  adaptar `WclClientConfig` para ler de `Settings`, ou (b) trocar o tipo do parâmetro — troca
  mecânica, sem reescrever a lógica de retry/backoff/auth.
- **Impacto:** nenhum na metodologia ou no comportamento observável do cliente. Apenas o tipo do
  parâmetro de configuração difere do pseudocódigo até a T1.1 unificar.
  **Consequência menor em `bot.py` (raiz):** como `WclClient.query()` já gerencia o token
  internamente, `get_wcl_token()` e o parâmetro `token` threaded por `fetch_player_timeline_data`/
  `fetch_top_logs_for_cds` foram removidos (senão ficariam mortos/inúteis). O cabeçalho separado
  "❌ Falha ao obter token..." do Discord deixa de existir como mensagem distinta — uma falha de
  autenticação agora aparece pela mesma mensagem genérica "❌ Jogador ... não foi encontrado ... ou
  ocorreu um erro na busca", já que `fetch_player_timeline_data` captura a exceção e retorna
  `None` como fazia antes para qualquer outra falha de rede. Validado ponta a ponta contra a API
  real: a saída do `bot.py` atualizado é **byte-idêntica** ao snapshot golden da T0.2 para a
  fixture (mesmo `matched=2`, mesmos deltas). `legacy/bot.py` permanece intocado.

## D-8 — ✅ RESOLVIDO NA T0.4 — Nenhuma tarefa constrói `src/botgitgud/blizzard/client.py`, mas a T0.4 já exige `BlizzardClient`

- **Tarefa:** T0.3 (achado durante o trabalho; afeta T0.4)
- **Documento diz:** a árvore de diretórios (§1.1) lista `src/botgitgud/blizzard/client.py`, e a
  assinatura da T0.4 é `SpellCatalog.__init__(self, path: Path, blizzard: BlizzardClient | None)`.
  Nenhuma das 27 tarefas (T0.0–T4.4) tem "construir BlizzardClient" como entregável explícito.
- **Realidade:** sem essa classe, a T0.4 não consegue satisfazer sua própria assinatura.
- **Ação tomada:** registrado aqui para rastreabilidade; resolvido **na T0.4**, não aqui — T0.3 é
  estritamente sobre `wcl/client.py` (seu próprio título). Como parte da T0.3, apenas corrigido o
  achado 4.3 pontualmente em `bot.py`: adicionado `timeout=` explícito na única chamada
  `requests.post` sem timeout que resta fora do escopo do `WclClient` (`get_blizzard_token()`),
  sem construir uma classe nova — isso já satisfaz o critério de aceite literal da T0.3 ("nenhuma
  chamada requests.* sem timeout permanece em bot.py"). Um `BlizzardClient` completo (com o mesmo
  padrão de robustez do `WclClient`: timeout, retry, cache de token) fica para a T0.4, que é onde a
  assinatura realmente exige o tipo.
- **Impacto:** nenhum na T0.3. A T0.4 deve construir `BlizzardClient` como pré-requisito implícito
  antes de finalizar `SpellCatalog`.

## D-9 — T0.8 referencia `src/botgitgud/ingest/rankings.py`, que é layout da Fase 1

- **Tarefa:** T0.8
- **Documento diz:** critério de aceite `grep -n '"hps"\|healing' src/botgitgud/ingest/rankings.py`
  retorna vazio.
- **Realidade:** o pacote `src/botgitgud/ingest/` (com `rankings.py`, `log_fetcher.py`, `store.py`
  em DuckDB) só é construído na **T1.3/T1.4** (Fase 1). Não existe na Fase 0, e construí-lo agora
  seria adiantar arquitetura inteira de outra fase — o mesmo padrão de gap já visto no D-8.
- **Ação tomada:** aplicado o mesmo padrão das T0.5–T0.7 — a lógica de coorte (bandas de duração,
  limiares de tamanho, normalização por taxa) vive em `src/botgitgud/analysis/cohort.py`, e é
  fiada em `bot.py` (a única "camada de ingestão" que existe na Fase 0). O critério de aceite é
  verificado contra os arquivos que realmente existem: `grep -rn '"hps"\|healing'
  src/botgitgud/analysis/cohort.py bot.py` — vazio, checado por teste dedicado.
- **Impacto:** nenhum na metodologia. Quando a T1.3/T1.4 criar `ingest/rankings.py`, a lógica de
  `cohort.py` deve ser movida/reaproveitada para lá; nenhuma reescrita de lógica, só de localização.

## D-10 — Pseudocódigo de `Settings` na T1.1 tem valores de coorte desatualizados

- **Tarefa:** T1.1
- **Documento diz:** o snippet de `Settings` na T1.1 lista `cohort_min_hard: int = 10`,
  `cohort_min_warn: int = 30`, `duration_tolerance_pct: float = 0.07`,
  `duration_tolerance_floor_s: float = 15.0`.
- **Realidade:** esses são os valores do **rascunho original** da T0.8, antes da correção baseada
  em medição real contra a API (`docs/schema_confirmado.md` §8). A própria seção T0.8 — mais
  adiante no mesmo documento — substitui esses valores explicitamente por
  `SANITY_BAND_PCT=0.35`, `POSITIONAL_BAND_PCT=0.12`, `COHORT_MIN_HARD=8`, `COHORT_MIN_WARN=20`,
  já implementados e testados em `src/botgitgud/analysis/cohort.py`. O snippet da T1.1 não foi
  atualizado para refletir essa revisão.
- **Ação tomada:** aplicado o alternativo óbvio — `Settings` usa os valores corretos (os da T0.8,
  já em produção), não os do rascunho da T1.1. Reverter para os valores antigos quebraria a
  regressão medida da T0.8 (o log de fixture do projeto só tem coorte suficiente com a banda de
  ±35%, não ±7%).
- **Impacto:** nenhum no comportamento já validado. `Settings` documenta a origem correta no
  próprio docstring do módulo.

## D-11 — `Cohort.criteria: CohortCriteria` precisa de um tipo que só é especificado na T1.5

- **Tarefa:** T1.2
- **Documento diz:** `Cohort` (T1.2) tem um campo `criteria: CohortCriteria`, mas `CohortCriteria`
  só é definida no pseudocódigo da T1.5 — que depende da T1.3, posterior à T1.2 na ordem de tarefas.
- **Realidade:** com `from __future__ import annotations`, o Python não reclamaria em tempo de
  execução (anotações viram strings, resolvidas preguiçosamente), mas `pyright` — cujo critério de
  aceite da T1.2 é literalmente "sem erros" — não resolveria a referência a um nome inexistente.
- **Ação tomada:** aplicado o mesmo padrão dos gaps anteriores (D-8, D-9) — `CohortCriteria`
  (incluindo `cohort_id()`) é definida agora em `src/botgitgud/domain/models.py`, junto com `Cohort`,
  que dela depende estruturalmente. A T1.5 reaproveita esta classe em vez de redefini-la; o trabalho
  real da T1.5 (manifesto de execução, wiring do `cohort_id` no relatório) permanece intacto.
- **Impacto:** nenhum na metodologia. `CohortCriteria` já testada nesta tarefa (determinismo do
  hash); os testes de wiring no relatório (critério de aceite específico da T1.5) ficam para lá.

## D-12 — Quatro lacunas na especificação da T1.3 (Store)

- **Tarefa:** T1.3

**(a) `FightRef` não tem `partition`, mas o layout físico e a tabela `logs` da T1.3 exigem.**
`docs/implementacao.md` mostra `raw/encounter_id=<E>/difficulty=<D>/partition=<P>/...` e a coluna
`partition INTEGER` na tabela `logs`, mas `FightRef` (T1.2) não tem esse campo.
**Ação:** adicionado `partition: int | None = None` a `FightRef` em `models.py` — nullable porque
partition é metadado de zona/patch, não algo que vem direto do fight (resolvido separadamente na
ingestão, T1.4/T1.7). Como `FightRef` foi criada nesta mesma sessão e nada mais depende do seu
conjunto de campos ainda, estender é seguro.

**(b) `CohortProfile` é usado na assinatura de `Store` (`read_profile`/`write_profile`) mas nunca
definido** em nenhuma tarefa do documento (T1.2 não tem; T1.5 só define `CohortCriteria`).
**Ação:** definidos `SpellProfile` e `CohortProfile` em `models.py`, formalizando a forma que
`build_cd_reference_profile()` (Fase 0, `bot.py`) já produz ad-hoc como dict
(`{spell_id: {"presence":, "ref_times":, "n_usages_median":}}`).

**(c) A `PRIMARY KEY (report_code, fight_id, player_name)` da tabela `logs` contradiz o princípio
"obrigatório" da própria T1.3**: "Dados brutos imutáveis. Um log escrito nunca é sobrescrito.
Reingestão gera nova linha com `ingested_at` maior; a leitura pega a mais recente." Uma PRIMARY KEY
rejeitaria a segunda inserção da mesma chave lógica — impossibilitando exatamente o comportamento
que o princípio exige.
**Ação:** a PK foi removida da criação da tabela; `(report_code, fight_id, player_name)` continua
sendo a chave lógica para leitura (`read_log` faz `ORDER BY ingested_at DESC LIMIT 1`), mas não é
uma constraint de unicidade do banco. O princípio de imutabilidade, explicitamente marcado como
"obrigatório", prevalece sobre o SQL de exemplo.

**(d) O template de caminho `raw/.../<report_code>_<fight_id>.parquet` colide entre jogadores**
(um fight tem vários jogadores, cada um vira um `PlayerLog` separado) **e entre reingestões** (a
mesma chave lógica sobrescreveria o mesmo arquivo físico, violando (c) na camada de arquivo).
**Ação:** o nome do arquivo passa a incluir o jogador e um timestamp de ingestão:
`<report_code>_<fight_id>_<player_name>_<ingested_at_epoch_ms>.parquet`, garantindo unicidade física
por ingestão sem exigir deduplicação/limpeza de arquivos antigos (aceitável para o volume esperado
de um projeto pessoal/comunidade pequena).

- **Impacto:** nenhum na metodologia central (armazenamento imutável, coorte por hash). Muda
  apenas os detalhes de schema/layout que o pseudocódigo do documento deixou subespecificados ou
  contraditórios entre si.

## D-13 — T1.6 pede 4 subcomandos de CLI, mas `build-cohort` e `backfill` não têm especificação própria

- **Tarefa:** T1.6
- **Documento diz:** "Criar `cli.py` com subcomandos: `analyze`, `build-cohort`, `probe-schema`,
  `backfill`."
- **Realidade:** `analyze` (roda `run_analysis`) e `probe-schema` (chama `wcl/schema_probe.py`,
  T0.1) já têm lógica completa para se conectar. `build-cohort` é explicitamente descrito como
  entregável da **T1.7** ("Job batch de construção de coortes"), tarefa seguinte, ainda não
  implementada. `backfill` não é mencionado em nenhum outro lugar do documento — nenhuma
  especificação de comportamento, argumentos ou objetivo.
- **Ação tomada:** os 4 subcomandos existem em `cli.py` (a estrutura do argparse), mas
  `build-cohort` e `backfill` são stubs documentados: imprimem uma mensagem clara em stderr
  explicando por que ainda não fazem nada (apontando para T1.7 e para esta entrada,
  respectivamente) e retornam código de saída 1. Nenhuma lógica foi inventada para `backfill`.
- **Impacto:** nenhum na metodologia. `build-cohort` ganha implementação real na T1.7; `backfill`
  fica pendente até o usuário/documento especificar o que deve fazer.

## D-14 — Robustez do lote de logs de referência: `playerDetails` como lista e falhas parciais

- **Tarefa:** T1.6
- **Realidade:** ao religar `ingest/rankings.py`/`LogFetcher.fetch_many` no pipeline real pela
  primeira vez (T1.4 só havia sido testada com respostas sintéticas, nunca contra a API ao vivo em
  lote — ver módulo `test_log_fetcher.py`), a gravação das fixtures da T1.6 contra a API real
  encontrou dois problemas que nenhuma tarefa anterior tinha exercitado em escala:
  **(a)** um log de referência real teve `table(dataType: Summary).data.playerDetails` retornado
  como lista vazia `[]` em vez do objeto `{dps, healers, tanks}` esperado, quebrando
  `find_player_in_details` com `AttributeError: 'list' object has no attribute 'get'` e abortando
  o lote inteiro; **(b)** esse mesmo cenário expôs que `LogFetcher.fetch_many` (T1.4) não tolerava
  a falha de uma única referência — uma exceção de qualquer membro do lote (mesmo
  `PlayerNotFound`/`FightNotFound`, que o código já tentava capturar antes de `AttributeError`
  aparecer) propagava e derrubava a coleta de todo o resto do lote.
- **Ação tomada:** `find_player_in_details` (`ingest/wcl_parsing.py`) agora valida com `isinstance`
  em cada nível (`player_details`, cada grupo de papel, cada entrada) e trata qualquer formato
  inesperado como "jogador não encontrado" em vez de estourar. `LogFetcher.fetch_many` passou a
  capturar `PlayerNotFound`/`FightNotFound`/`ApiError` por referência individual, registrar um
  aviso e pular essa referência — o lote inteiro só falha se **nenhuma** referência sobreviver. A
  gravação ao vivo confirmou o comportamento (`failures=1` em um lote de 99, sem abortar).
- **Impacto:** nenhum na metodologia — torna o comportamento já pretendido (coorte tolerante a
  logs individualmente malformados/indisponíveis, espelhando o `except Exception` best-effort do
  `bot.py` original, porém com exceções específicas em vez de um catch-all genérico) real em vez
  de acidental.

## D-15 — Pool ao vivo de `characterRankings` cresceu muito além do que a T0.1 mediu

- **Tarefa:** T1.6
- **Realidade:** `docs/schema_confirmado.md` §8 registrou ~26 entradas no pool de rankings do
  encontro/spec de fixture quando a T0.1 sondou a API. Ao regravar as fixtures da T1.6 meses depois,
  o mesmo pool já tinha ~99 candidatos dentro da banda de sanidade (±35%, T0.8) — o pool de
  `characterRankings` é um leaderboard vivo que só cresce. Gravar o log completo de referência de
  cada um deles (cada cassete de `events` é o log de casts **de todo o raid**, sem filtro de
  `sourceID`, ~1MB+ por página) geraria centenas de MB de fixtures.
- **Ação tomada:** `tests/fixtures/record.py`'s `_fetch_and_truncate_rankings` faz a chamada real de
  `characterRankings` (necessária para não quebrar a cadeia de cassetes) e então **sobrescreve**
  esse mesmo cassete (mesma chave, `method+url+payload`) com uma versão truncada a
  `N_RECORDING_REFS = 10` candidatos e `hasMorePages: False`. Dez foi escolhido por ser o menor
  valor prático acima do piso `COHORT_MIN_HARD` (8, T0.8) com margem para uma falha pontual (D-14).
- **Impacto:** nenhum na metodologia real (o piso de 8 continua sendo a regra de produção,
  inalterada; só a *fixture de teste* está limitada). O golden test da T1.6 (`test_new_pipeline_
  output.py`) passa a rodar contra 10 referências em vez de ~99 — suficiente para exercitar toda a
  lógica de perfil/elegibilidade/alinhamento sem inflar o repositório.

## D-16 — `build-cohort` precisa de `--class`, que a especificação da T1.7 não lista

- **Tarefa:** T1.7
- **Documento diz:** `python -m botgitgud.cli build-cohort --encounter <E> --spec <S> --difficulty
  <D> [--duration-bucket <B>]` — sem nenhuma flag de classe.
- **Realidade:** `characterRankings` (e toda a lógica de coorte já construída em T0.9/T1.6) exige
  **className e specName juntos** — `specName` isolado não desambigua entre classes que
  compartilham nome de spec (ex.: "Frost" existe para Death Knight e Mage, ambos no allowlist de
  25 specs suportadas). Sem `--class`, não há como montar a query nem o `CohortCriteria`.
- **Ação tomada:** adicionada `--class` (obrigatória) ao subcomando, com `dest="klass"` (`class` é
  palavra reservada em Python). Documentado no `--help` da própria flag.
- **Impacto:** nenhum na metodologia. Apenas completa uma flag que a especificação esqueceu, pelo
  mesmo padrão de todo outro argumento obrigatório de `characterRankings` já resolvido antes.

## D-17 — "nunca baixa 100 logs de forma síncrona" pressupõe a fila da T1.8, que ainda não existe

- **Tarefa:** T1.7
- **Documento diz:** "Se o perfil não existir, o bot informa que a coorte está sendo construída e
  enfileira o job — nunca baixa 100 logs de forma síncrona" (caminho interativo).
- **Realidade:** "enfileira o job" pressupõe a fila persistente de jobs que só a **T1.8** constrói.
  Na T1.7, não há fila para enfileirar nada ainda.
- **Ação tomada:** `run_analysis` ganhou `allow_cold_build: bool = True`. O caminho Discord
  (`bot/discord_bot.py`) chama com `allow_cold_build=False`: um cache miss levanta `CohortNotReady`
  (nova exceção, `AnalysisError`), traduzida numa mensagem honesta ("ainda não temos uma coorte
  pronta... rode build-cohort ou aguarde a fila automática") — sem baixar nada síncrono, exatamente
  como o texto exige, mesmo sem uma fila real atrás da mensagem ainda. `cli.py`'s `analyze` (uma
  ferramenta de debug, não o caminho multiusuário que a T1.8 protege) mantém `allow_cold_build=True`
  por padrão, preservando a conveniência de rodar uma análise completa sem pré-construir nada.
- **Impacto:** nenhum na metodologia. Quando a T1.8 construir a fila real, o `except CohortNotReady`
  do Discord vira o gatilho natural para efetivamente enfileirar o job em vez de só avisar.

## D-18 — `RateLimitBudgetExceeded` era engolido como falha pontual em dois lugares

- **Tarefa:** T1.7
- **Realidade:** a T1.7 exige que `build-cohort` detecte `RateLimitBudgetExceeded` e saia com
  código 75 (`EX_TEMPFAIL`), "salvando o progresso parcial". Ao implementar isso, dois pontos já
  existentes (T1.4 e T1.6) capturavam **qualquer** `ApiError` — incluindo `RateLimitBudgetExceeded`,
  que é subclasse — como se fosse uma falha isolada de item/página, e simplesmente seguiam adiante:
  `LogFetcher.fetch_many` (T1.4) tratava um orçamento esgotado como "essa referência falhou, tenta
  a próxima" (e cada tentativa subsequente falharia de novo, silenciosamente); `fetch_ranking_
  candidates` (T1.6) tratava como "essa página falhou, para de paginar", devolvendo os candidatos
  parciais como se fosse um resultado normal. Em nenhum dos dois casos a condição — órçamento da
  conta inteira esgotado, não um defeito de um item específico — chegava a se propagar para fora.
- **Ação tomada:** ambos os pontos agora capturam `RateLimitBudgetExceeded` **antes** do `except
  ApiError` genérico e a relançam. Em `fetch_many`, o progresso já obtido continua sendo persistido
  antes de relançar (drena os futures já em voo, depois relança) — "salva o progresso parcial" fica
  garantido na camada mais baixa, não só em `build-cohort`.
- **Impacto:** nenhum na metodologia; corrige um bug de robustez real que só a implementação da
  T1.7 expôs (nenhuma tarefa anterior precisava distinguir esgotamento de orçamento de uma falha
  pontual). Testado com regressões dedicadas em `test_log_fetcher.py`, `test_rankings.py` e
  `test_cohort_builder.py`.

## D-19 — "writer único serializado... thread dedicada" implementado como lock, não fila+thread

- **Tarefa:** T1.8
- **Documento diz:** "Toda escrita passa por um **writer único serializado** (uma thread dedicada
  consumindo uma fila de escritas). Leituras concorrentes são permitidas. **Nunca** abra conexões
  de escrita a partir dos workers."
- **Realidade:** o requisito real por trás dessa frase é que `duckdb.Connection` não é segura para
  uso concorrente entre threads — a garantia que importa é "nunca duas threads tocam a conexão ao
  mesmo tempo", não especificamente "existe uma thread nomeada consumindo uma fila". Implementar um
  `ThreadPoolExecutor(max_workers=1)`/fila dedicada introduz risco real de deadlock se qualquer
  método de `Store` chamar outro método de `Store` enquanto já executa dentro da própria thread
  writer (submissão aninhada no único worker, que está ocupado esperando por si mesmo).
- **Ação tomada:** `Store` ganhou um único `threading.RLock` guardando **todo** acesso a
  `self._conn` — leituras inclusive (não só escritas: DuckDB também desaconselha uso concorrente
  para leitura na mesma conexão). `bot/jobs.py`'s `JobQueue` reusa a mesma conexão/lock via
  `Store.execute`/`execute_returning`, então toda escrita no processo — logs, cohorts, runs, jobs —
  serializa pelo mesmo mecanismo. O critério de aceite ("8 workers escrevendo simultaneamente, zero
  exceção, todos os registros presentes") é sobre o **comportamento observável**, não sobre a
  mecânica interna, e um lock produz exatamente essa garantia com uma implementação mais simples e
  sem risco de deadlock.
- **Impacto:** nenhum no comportamento observável (testado em
  `test_store.py::test_eight_threads_writing_concurrently_never_raises_and_all_records_land`).

## D-20 — `jobs` precisa de uma coluna `job_type`, ausente do DDL literal da T1.8

- **Tarefa:** T1.8
- **Documento diz:** o `CREATE TABLE jobs` da especificação não tem coluna para distinguir o tipo
  de job.
- **Realidade:** o item 5 do mesmo T1.8 exige explicitamente "duas filas com prioridade" entre
  análises (rápidas) e construções de coorte fria — impossível sem alguma coluna que identifique o
  tipo de cada job na hora de escolher qual reivindicar (`claim_next`) e para decidir se o
  orçamento atual permite aquele tipo (item 3, reserva de 25%).
- **Ação tomada:** adicionada `job_type VARCHAR` (`"analyze" | "build_cohort"`) ao DDL em
  `bot/job_models.py`.
- **Impacto:** nenhum na metodologia — apenas completa uma coluna que a lógica adjacente do próprio
  documento já pressupunha existir.

## D-21 — Piso de abortagem (1000) é maior que a reserva de 25% para a conta real (3600/h)

- **Tarefa:** T1.8
- **Realidade:** com o `limitPerHour` real confirmado da conta (3600, `docs/schema_confirmado.md`
  §2), 25% de reserva = 900 pontos — **menor** que o piso padrão de abortagem da T0.3 (1000). Como
  `BudgetStatus.allows()` aplica o piso incondicionalmente antes de checar a reserva por tipo de
  job, isso significa que, com os números literais desta conta, o piso sempre barra tudo antes que
  a distinção "reserva bloqueia só coorte fria" chegue a importar — não há uma faixa intermediária
  real onde só jobs de coorte pausam enquanto análises continuam.
- **Ação tomada:** a lógica em camadas (piso absoluto → reserva por tipo) foi implementada como
  especificada e continua correta em geral — qualquer conta com `limitPerHour` alto o suficiente
  para que a reserva supere o piso tem a faixa intermediária real. Documentado no docstring de
  `BudgetStatus` para que isso não pareça um bug ao ler o código depois. O teste que exercita essa
  camada (`test_cold_cohort_jobs_pause_below_reserve_while_analyze_jobs_continue`) usa um
  `BudgetStatus` com piso customizado menor, deliberadamente, para exercitar a lógica em seus
  próprios termos.
- **Impacto:** nenhum na metodologia — comportamento correto para qualquer conta cujos números
  componham como o documento pressupõe; apenas não é observável com o `limitPerHour` real desta
  conta específica hoje.

## D-22 — Nenhum teste unitário para `bot/discord_bot.py`

- **Tarefa:** T1.8
- **Realidade:** `bot/discord_bot.py` (a cola assíncrona do Discord — comandos, loop de workers,
  notificações) nunca foi testada no nível de unidade em nenhuma tarefa desta sessão, desde que o
  arquivo foi criado na T1.6 — não existe infraestrutura no projeto para simular um
  `discord.ext.commands.Bot`/event loop real, e construir uma do zero para testar código que é, em
  sua maioria, roteamento fino (parsear → chamar lógica já testada → formatar → enviar) não parecia
  proporcional ao valor.
- **Ação tomada:** toda a lógica de negócio que os comandos chamam (`run_analysis`, `JobQueue`,
  `run_claimed_job`, `BudgetStatus`) tem cobertura de unidade completa e é exercitada isoladamente.
  `discord_bot.py` em si foi revisado manualmente com cuidado (fluxo de exceções, ordem de
  operações, wiring do worker loop) mas fica de fora da cobertura automatizada — consistente com o
  precedente já estabelecido desde a T1.6.
- **Impacto:** cobertura total do repositório (86%) continua bem acima do piso de 75% exigido pelo
  portão de saída da Fase 1, mesmo com `discord_bot.py` em 0%.

## D-23 — Nenhuma tarefa liga `build_bot()` a um processo executável de fato

- **Tarefa:** T1.8
- **Realidade:** desde que `bot/discord_bot.py`'s `build_bot(deps)` foi criada na T1.6, nenhuma
  tarefa do documento (T1.6, T1.7 ou T1.8) jamais a chamou de fato — não havia `bot.run(token)` em
  lugar nenhum do código após a remoção de `bot.py` da raiz. O bot Discord literalmente não tinha
  como ser iniciado como processo real.
- **Ação tomada:** adicionado o subcomando `serve` a `cli.py` (`python -m botgitgud.cli serve`),
  que constrói `Deps` e chama `build_bot(deps).run(token)` — o único lugar que efetivamente inicia
  o processo de longa duração do bot.
- **Impacto:** nenhum na metodologia. Sem isso, toda a infraestrutura da T1.8 (fila, orçamento,
  justiça entre usuários) não teria como rodar de verdade fora dos testes.

## D-24 — `talent_cluster` sempre pré-relaxada em `match_cohort` até a T2.2 existir

- **Tarefa:** T2.1
- **Documento diz:** a covariável `talent_cluster` participa da cascata de degradação como
  qualquer outra — só é relaxada se `n < COHORT_MIN_HARD` após tentar pareá-la.
- **Realidade:** o clustering de builds de talentos por similaridade de Jaccard (que define o que
  "mesmo cluster" significa) é o próprio objeto da T2.2, que ainda não existe nesta tarefa —
  T2.1 é um pré-requisito dela, não o contrário. Não há como *tentar* parear `talent_cluster`
  sem primeiro ter clusters.
- **Ação tomada:** `analysis/cohort_match.py`'s `match_cohort` inicializa `relaxed` já contendo
  `"talent_cluster"` (nunca em `active`) e nunca a testa na cascata — ela aparece em
  `MatchReport.relaxed` desde a primeira chamada, em todo relatório, até a T2.2 implementar
  clustering real e este módulo ser atualizado para usá-lo.
- **Impacto:** nenhum na metodologia — a T2.1 antecipa exatamente este encadeamento (ver a
  ordem `tier_pieces → external_buffs → item_level → talent_cluster → has_augmentation →
  duração`, com `talent_cluster` no meio). Todo relatório até a T2.2 mostra o aviso genérico
  "talentos: mesma build não pareado" — esperado e documentado, não um bug.
- **Resolvido pela T2.2:** `analysis/talent_cluster.py`'s `jaccard_similarity`/
  `JACCARD_THRESHOLD` (0.85) agora alimentam um `_same_talent_cluster(candidate, target)` real em
  `cohort_match.py` — pareamento par-a-par contra o alvo, na mesma forma de
  `item_level`/`tier_pieces` (não o clustering completo da coorte, que é uma preocupação separada
  do achado "BUILD DIVERGENTE"). `talent_cluster` saiu de `relaxed` (fixo, D-24) e entrou em
  `_ALL_COVARIATES` como qualquer outra covariável — pode aparecer em `matched` ou `relaxed`
  dependendo dos dados reais.

## D-25 — Cache de coorte agregada (T1.7) incompatível com matching por jogador (T2.1)

- **Tarefa:** T2.1
- **Tarefa diz:** aplicar a cascata de degradação de covariáveis (`match_cohort`) entre
  `fetch_cohort_logs` e `build_cd_reference_profile`, tanto no caminho quente (coorte já em cache)
  quanto no frio.
- **Realidade:** a T1.7 implementou o caminho quente como um `CohortProfile` *já agregado*
  (`Store.write_profile`/`read_profile`, tabela `cohorts`) — um `Mapping[int, SpellProfile]`
  calculado uma vez por bucket e reutilizado por qualquer jogador que caia nele. Isso é
  estruturalmente incompatível com matching por jogador: as covariáveis do jogador analisado
  (ilvl, tier_pieces, has_augmentation, external_buffs) só são conhecidas no momento da análise,
  então o subconjunto de logs de referência que sobrevive a `match_cohort` é diferente para cada
  jogador — não existe "o" perfil agregado de um bucket sob a T2.1.
- **Ação tomada:** substituída a persistência de `CohortProfile` por uma persistência do **pool de
  candidatos brutos** por `cohort_id` (`Store.write_candidate_pool`/`read_candidate_pool`, tabela
  `cohort_candidates` — mesmos `report_code`/`fight_id`/`player_name`/`duration_s` que
  `characterRankings` já resolve). O que é cacheável entre jogadores é a *lista de candidatos*
  (resultado de uma query cara a `characterRankings`), não a agregação (barata, em memória).
  `analysis/pipeline.py`'s `run_analysis` agora sempre executa
  `fetch_cohort_logs → match_cohort → build_cd_reference_profile` a cada chamada, quente ou fria —
  só a etapa de descoberta de candidatos (`fetch_ranking_candidates`) é pulada quando o pool já
  está em cache. `analysis/cohort_builder.py`'s `build_cohorts` (o job em lote por trás de
  `build-cohort`) para de agregar um perfil e passa a apenas aquecer o pool de candidatos e o
  cache de logs individuais (T1.4) — o valor do job em lote continua sendo evitar a query de
  rankings e os fetches de log síncronos na primeira análise interativa de um bucket, só que sem
  fingir que existe "um" perfil por bucket. `CohortProfile`, `write_profile`/`read_profile`, a
  tabela `cohorts` e o parquet de perfil (`ingest/parquet_codec.py`) foram removidos — ficariam
  mortos, sem nenhum consumidor, após a mudança.
- **Impacto:** o teste `test_second_call_with_a_warm_profile_makes_zero_ranking_queries` (T1.7) foi
  renomeado para `..._warm_candidate_pool_...` e continua validando o invariante real que importa
  (zero queries a `characterRankings` na segunda chamada) — a asserção nunca dependeu do mecanismo
  interno de cache, só do número de queries. `tests/fixtures/record.py` precisou de duas correções
  relacionadas, descobertas ao re-gravar os cassetes para a nova query `GetPlayerBuffs`: (1) a
  banda de truncamento de `_fetch_and_truncate_rankings` (±35%, `SANITY_BAND_PCT`) é mais larga que
  o teto real da cascata de duração de `match_cohort` (±20%, `DURATION_BANDS_PCT[-1]`) — com
  `N_RECORDING_REFS` baixo o suficiente, isso deixava poucos candidatos realmente *alcançáveis*
  pelo matching (4 de 10 na primeira regravação), abaixo de `COHORT_MIN_HARD`; (2) o pool ao vivo
  para esta fixture acabou sendo mais raso do que o D-15 media (a partição atual está no início da
  sua temporada) — a página 1 sozinha não bastava mesmo com `N_RECORDING_REFS` mais alto, então o
  script de gravação passou a paginar como `ingest/rankings.py`'s `fetch_ranking_candidates` já
  faz em produção, em vez de assumir que uma página basta. `N_RECORDING_REFS` subiu de 10 para 24
  como consequência. O snapshot dourado da pipeline legada (`test_legacy_output.ambr`) também
  precisou de `--snapshot-update`: `tests/fixtures/record.py`'s `main()` sempre regrava os
  cassetes legados também, e o pool ao vivo cresceu desde a última gravação — deriva de dados
  ao vivo já prevista pela própria D-15, não uma mudança de comportamento do código legado (que
  segue intocado).

## D-26 — Não há resolução de nome para os nós de talento da nova árvore

- **Tarefa:** T2.2
- **Documento diz:** o achado "BUILD DIVERGENTE" deve citar os talentos que distinguem a build do
  jogador da build dominante por nome (ex.: "<talento A> em vez de <talento B>").
- **Realidade (verificado ao vivo antes de implementar):** `combatantInfo.talentTree[].id` **não**
  resolve via `gameData.ability(id)` — testado ao vivo com IDs reais de `talentTree` (91425,
  91430): ambos retornam `null`, enquanto um spell ID genuíno (104316, Call Dreadstalkers) resolve
  normalmente. Testado também `gameData.__type("GameData").fields` — não há campo `talent` nem
  equivalente. Do lado da Blizzard, `/data/wow/talent/{id}` e `/data/wow/spell-tree-node/{id}`
  retornam 404 ao vivo para o mesmo ID. Não existe nenhum catálogo de nomes de talento neste
  projeto (diferente de `SpellCatalog`, que resolve spell IDs via WCL castsTable + fallback
  Blizzard) — construir um do zero exigiria descobrir um endpoint que não foi localizado nesta
  sessão, escopo bem além do que a T2.2 pede (clustering).
- **Ação tomada:** as diferenças de talento são exibidas por `(nodeID, rank)` em vez de nome —
  `report/build_divergence_text.py`'s `_render_talent_difference` produz
  `"nó 71918 (dominante: rank 2 / você: rank 1)"`. `analysis/talent_cluster.py`'s
  `TalentDifference` carrega `node_id`/`dominant_rank`/`player_rank` (nunca um nome), documentado
  no docstring do módulo para que a ausência de nomes não pareça um bug ao ler o código depois.
- **Impacto:** o achado continua acionável (o jogador consegue localizar o nó pela posição na
  árvore no jogo), só menos legível do que o texto de exemplo do documento. Se a resolução de
  nomes se tornar valiosa, uma tarefa futura deve investigar o endpoint correto (não encontrado
  aqui) antes de construir um catálogo dedicado.

## D-27 — T2.4 não amplia o pool posicional para o pool inteiro de rankings

- **Tarefa:** T2.4
- **Documento diz:** "com o tempo normalizado por intervalo de fase, as métricas posicionais
  deixam de exigir kills de duração parecida. Isto é o que permite usar o pool inteiro de
  rankings (T0.8) para a terceira classe de métricas, em vez de restringir a ±12%" — descrito
  como "ganho colateral" e "efeito colateral desejado", nunca como um passo numerado obrigatório
  (os passos 1-6 da tarefa não mencionam alterar `within_positional_band`/`POSITIONAL_BAND_PCT`).
- **Realidade:** ampliar o pool posicional interage com bastante coisa já testada e estável
  (T0.8's `classify_cohort_size`, os avisos de amostra pequena da T1.6/T1.7, a supressão por `n`
  da T2.3) — uma mudança de escopo real, não uma consequência automática de ter fases. Os
  critérios de aceite literais da T2.4 (3 fases → alinhamentos independentes; luta sem fases →
  idêntico à T0.7; fixture real → 5 intervalos com as chaves documentadas) não dependem dela.
- **Ação tomada:** implementado exatamente o que os passos 1-6 pedem — normalização temporal por
  `(phase_id, ocorrência)`, chaveamento do perfil de referência, alinhamento independente por
  intervalo (`analysis/comparison.py`'s `compare_spell_usage_by_phase`, usado por
  `compare_all_spells` como o único caminho de produção agora, já que
  `player_log.fight.phase_intervals` está sempre populado — luta sem fase é só o caso degenerado
  de 1 intervalo). `analysis/cohort.py`'s `within_positional_band`/`POSITIONAL_BAND_PCT` **não**
  foram tocados — a restrição de banda posicional continua exatamente como a T0.8/T1.6 a
  deixaram.
- **Impacto:** nenhum nos critérios de aceite da T2.4 (todos passam, incluindo contra a fixture
  real). O "efeito colateral desejado" de ampliar o pool fica como trabalho futuro explícito, não
  perdido — se uma tarefa posterior quiser essa ampliação, o chaveamento por fase já existe e
  está testado; só falta decidir a nova política de banda.

## D-28 — Nenhuma API expõe cooldown de habilidade; tabela curada da T2.5 começa vazia

- **Tarefa:** T2.5
- **Documento diz:** fonte 1 — API de spell da Blizzard, "se exposto — verifique e registre";
  fonte 2 — tabela manual curada "para as habilidades que aparecerem nos relatórios reais";
  fonte 3 — `None`, fallback já existente da T0.6.
- **Realidade (verificado ao vivo antes de escrever qualquer valor):** `GET /data/wow/spell/{id}`
  da Blizzard retorna só `id, name, description, media` — nenhum campo de cooldown, testado
  contra 3 spell IDs reais (104316, 1122, 267171). Verificado também (não pedido pelo documento,
  mas o lugar óbvio a checar antes de desistir de uma fonte de API) `gameData.ability(id)` da WCL
  — o tipo GraphQL `GameAbility` expõe só `id, icon, name`, mesma lacuna. Nenhuma das duas APIs
  que este projeto já fala tem esse dado.
- **Ação tomada:** `domain/cooldowns.py` implementa o mecanismo completo (`BASE_COOLDOWNS_S`,
  `get_base_cooldown`, ligado em `analysis/profile.py`'s `discover_eligible_spell_ids` e
  `analysis/comparison.py`'s `compare_all_spells`) mas a tabela em si **começa vazia**,
  deliberadamente. Toda outra tabela curada deste projeto (`domain/blacklist.py`,
  `domain/external_buffs.py`) foi construída a partir de dado real verificado ao vivo — não existe
  equivalente aqui: preencher a tabela significaria declarar valores numéricos de memória, para
  spell IDs de conteúdo muito recente sem nenhuma fonte verificável nesta sessão. Um cooldown
  errado corrompe silenciosamente a classificação MAJOR/MINOR — pior que o "desconhecido" honesto
  que a tabela vazia já produz (mesmo espírito da supressão por amostra da T2.3: "melhor não
  opinar que opinar errado").
- **Impacto:** nenhuma regressão — `base_cooldown` sempre foi `None` em produção antes desta
  tarefa (nem `profile.py` nem `comparison.py` o passavam), então a tabela vazia mantém o
  comportamento idêntico ao pré-T2.5 (confirmado: o snapshot dourado não mudou). O mecanismo está
  completo e testado (`test_cooldowns.py`, extensões em `test_profile.py`/`test_comparison.py`
  provam que um valor curado presente vence — primeiro ramo — e que a ausência degrada para os
  ramos seguintes sem erro); popular a tabela com dados reais fica para quando houver uma fonte
  verificável (tooltip in-game, nota de patch oficial, ou uma API futura).

## D-29 — `downtime_s` sem timestamp de revive; `avg_targets_per_cast` sem agrupamento por instância de cast

- **Tarefa:** T3.1
- **Documento diz:** `deaths, downtime_s` vêm da "tabela `Deaths`"; `avg_targets_per_cast` é
  "alvos únicos atingidos por cast, por habilidade" a partir de eventos de dano. Nenhuma fórmula
  exata é dada para nenhum dos dois (diferente da T3.2, que define cada termo precisamente).
- **Realidade (verificado ao vivo antes de escrever qualquer código):**
  1. `table(dataType: Deaths)` traz `timestamp` da morte e o dano/cura que levou a ela, mas
     **nenhum timestamp de revive/ressurreição**. `table(dataType: Summary)`'s `deathEvents`
     (já buscado, sem query extra) tem a mesma lacuna, mas seu `deathTime` é **relativo ao início
     da luta** (não absoluto como `phaseTransitions[].startTime` — verificado: valores caem dentro
     de `[0, duration_ms]`). Uma varredura de `events(dataType: All)` na janela após uma morte real
     do fixture de Zarad não achou nenhum evento `type: "resurrect"` (o pull termina em wipe, sem
     revive) — não há sinal de "voltou a agir" além dos próprios eventos do jogador.
  2. Nenhuma API agrupa eventos de dano por instância individual de cast (só por `abilityGameID`
     agregado no fight inteiro).
- **Ação tomada:**
  1. `downtime_s` = soma, por morte, de (o próximo `cast` do próprio jogador, ou o fim da luta,
     o que vier primeiro) menos o instante da morte — um personagem morto não pode conjurar, então
     o próximo cast dele é a prova observável mais cedo de que voltou a agir. Implementado em
     `ingest/performance_parsing.py`'s `compute_downtime_s`, usando dados já buscados (nenhuma
     query nova). Nunca fabrica um revive: um jogador que nunca conjura de novo (wipe) corretamente
     fica com downtime até o fim da luta.
  2. `avg_targets_per_cast(a)` = alvos distintos atingidos por `a` no fight inteiro / total de
     casts próprios de `a` — uma média por fight, não uma média estrita por instância de cast.
     Implementado em `ingest/damage_aggregation.py`'s `aggregate_damage_by_ability`. Omitido (não
     `0.0`) para uma habilidade com zero casts próprios (ex.: habilidade só de pet) — "média por
     cast" é indefinida ali, não zero.
- **Impacto:** ambas as heurísticas são construídas só a partir de dados já buscados por outras
  necessidades da T3.1 (sem custo de API extra) e documentadas como aproximações no docstring de
  cada função — nenhuma delas pode silenciosamente inflar ou esconder um achado (downtime nunca
  fica negativo nem subestima um wipe; avg_targets_per_cast nunca finge um valor para uma
  habilidade sem casts). Testado em `tests/unit/test_performance_parsing.py` e
  `tests/unit/test_damage_aggregation.py`, incluindo o caso do wipe sem revive.

## D-30 — Seção "de onde veio o gap de DPS" do snapshot dourado reflete coorte com eventos truncados (limitação de fixture, não bug)

- **Tarefa:** T3.2
- **Documento diz:** nenhum critério de aceite da T3.2 exige verificação contra a fixture real
  (diferente da T3.1, que tem um teste de reconciliação obrigatório) — todos os 5 critérios da T3.2
  são sintéticos/Hypothesis.
- **Realidade:** D-29/T3.1 já truncou (pós-gravação) os cassetes de `events(dataType: DamageDone)`
  de toda referência que não é o próprio fixture, para conter o tamanho do diretório —
  `_MAX_RECORDED_EVENTS_PER_PAGE = 25`. Como **toda referência é do mesmo class/spec do fixture**
  (Demonology Warlock, exigido pela própria `CohortCriteria` da T1.5), toda referência também tem
  muitos pets e um volume de eventos de dano comparável ao de Zarad (dezenas de milhares) — truncar
  para 25 deixa `damage_by_ability` de CADA log de referência gravemente incompleto (só as
  primeiras habilidades cronologicamente atingidas pelo pull sobrevivem). Isso é inofensivo para
  toda feature da T3.1 (nenhuma delas depende de `damage_by_ability` de logs de referência — só a
  reconciliação usa o fixture, que fica intacto) mas corrompe as medianas de coorte `c_r(a)`/`p_r(a)`
  da T3.2, produzindo uma seção "DE ONDE VEIO O GAP DE DPS" no snapshot dourado com números
  pequenos/ruidosos que não refletem uma decomposição real.
- **Ação tomada:** aceito como limitação de fixture, documentada aqui — não é um bug de
  `analysis/dps_gap.py` (a matemática da decomposição é verificada isoladamente e exaustivamente:
  identidade fechada em 1000 casos via Hypothesis, mais os 4 casos sintéticos de valor conhecido
  exigidos pelo documento, `tests/unit/test_dps_gap.py`). Não regravei os cassetes com um limite
  maior porque não há tamanho de truncamento que sirva bem aos dois objetivos ao mesmo tempo
  (fidelidade de coorte vs. tamanho de repositório) para uma coorte inteiramente pet-heavy — um
  valor "razoável" hoje ainda seria arbitrário e ficaria obsoleto assim que outro fixture de spec
  diferente for gravado. Quando a T3.2/T3.3 precisarem de um relatório real colado em
  `docs/progresso.md` com um Top 3 crível (portão de saída da Fase 3), a extração deve rodar contra
  a API ao vivo (como já foi feito para os relatórios reais colados nos portões de saída da Fase 0
  e Fase 1), não contra este fixture truncado.
- **Impacto:** nenhum teste trava por causa disso — o snapshot dourado captura o que quer que o
  código produza de forma autoconsistente (função normal de teste de regressão), e nenhum critério
  de aceite da T3.2 depende de os números da coorte no fixture serem realistas.

## D-31 — Só `BUILD` e `ABILITY_GAP` recebem `estimated_gain_pct` real; as outras 6 categorias de `FindingKind` nunca competem pelo Top 3

- **Tarefa:** T3.3
- **Documento diz:** `Finding.kind` é um dos 8 valores — `BUILD | DEATH | ACTIVE_TIME | UPTIME |
  WASTE | MISSED_CD | CD_TIMING | ABILITY_GAP` — e cada `Finding` carrega `estimated_gain_pct:
  float | None`. Nenhuma fórmula é dada para converter nenhuma categoria específica num ganho de
  DPS — só a regra de score (`estimated_gain_pct × peso_de_confiança`) e o critério de aceite
  "nenhum finding sem `estimated_gain_pct` entra no Top 3" (implicitamente permitindo que um
  finding tenha `None`).
- **Realidade:** `BUILD` (T2.2's `BuildDivergence`) já tinha uma fórmula natural, real e já exibida
  no próprio relatório desde a T2.2 (`(dominant_median_dps - player_median_dps) / player_median_dps
  × 100`, ver `report/build_divergence_text.py`). `ABILITY_GAP` (T3.2) também: `delta_dps_pct` já É
  literalmente um ganho percentual de DPS, calculado com uma fórmula exata e verificada
  (`analysis/dps_gap.py`). As outras 6 categorias (`DEATH`, `ACTIVE_TIME`, `UPTIME`, `WASTE`,
  `MISSED_CD`, `CD_TIMING`) não têm nenhuma fórmula equivalente em lugar nenhum do documento — só
  uma classificação por quantil (🟢/🟡/🔴, T2.3/T3.1), que não é uma % de DPS.
- **Ação tomada:** `analysis/findings.py`'s `build_findings` só constrói `Finding`s para `BUILD` e
  `ABILITY_GAP`, cada um com `estimated_gain_pct` real e verificável. As outras 6 categorias
  continuam com sua própria seção no relatório (`report/text.py`'s "detalhamento por categoria",
  herdado sem mudança da T3.1), só que nunca competem pelo Top 3 — consistente com o próprio
  critério de aceite da T3.3 ("nenhum finding sem `estimated_gain_pct` entra no Top 3": ausência de
  estimativa é um estado válido e previsto, não um erro a esconder). Inventar uma fórmula de
  conversão linear (ex.: "% de downtime × DPS" ou "gap de uptime × valor típico do buff") seria
  apresentar como quantidade real algo sem base — mesmo espírito de D-28/D-29 ("melhor não opinar
  que opinar errado").
- **Impacto:** `Top 3 Ações` fica, nesta versão, restrito a achados de build e de habilidade — os
  dois com maior poder explicativo e única fonte quantificada do roadmap (a própria T3.2 é descrita
  no documento como "maior retorno/esforço"). Se uma fórmula honesta para as outras 6 categorias for
  definida numa tarefa futura, `build_findings` é o único lugar que precisa mudar — o tipo `Finding`
  e `select_top_actions` já suportam qualquer `FindingKind` sem alteração.
