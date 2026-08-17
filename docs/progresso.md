# Progresso da implementação

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T0.0 | ✅ FEITA | (pendente) | `.gitignore`+`.env.example` antes do `git init`; `legacy/bot.py` congelado; `pyproject.toml` criado; venv em `.venv/` com `pip install -e ".[data,dev]"` (sem `uv` disponível no ambiente — usado fallback `pip`+`venv` conforme previsto no documento). Ver D-1 em `docs/desvios.md`. |
| T0.1 | ⬜ PENDENTE | | Grande parte já coberta por `docs/schema_confirmado.md` (sondagem manual feita antes desta sessão). Falta formalizar `schema_probe.py` e cobrir os itens da §11 daquele arquivo. |
| T0.2 | ⬜ PENDENTE | | Log de fixture definido: `PtfBbQKRY9d6zAMC` fight 1, personagem Zarad (Warlock Demonology). |
| T0.3 | ⬜ PENDENTE | | |
| T0.4 | ⬜ PENDENTE | | |
| T0.5 | ⬜ PENDENTE | | |
| T0.6 | ⬜ PENDENTE | | |
| T0.7 | ⬜ PENDENTE | | |
| T0.8 | ⬜ PENDENTE | | |
| T0.9 | ⬜ PENDENTE | | |

## Ações pendentes do usuário

- **Rotacionar as 5 credenciais expostas** (Discord, WCL client id/secret, Blizzard client id/secret) — o `.env` foi lido em texto claro durante a auditoria. Recomendado antes de qualquer push para remoto. Não bloqueia a implementação local.

## Ambiente

- Python 3.14.6 (o documento pedia `>=3.11`; `uv` não está instalado no ambiente, usado `venv` + `pip` conforme fallback previsto em §1.2).
- Dependências `data` (duckdb, pyarrow, polars) e `dev` (pytest, hypothesis, syrupy, ruff, pyright) instaladas sem erro em `.venv/`.
