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
