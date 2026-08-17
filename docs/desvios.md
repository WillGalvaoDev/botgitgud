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
