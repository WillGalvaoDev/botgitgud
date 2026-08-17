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
