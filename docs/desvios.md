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
