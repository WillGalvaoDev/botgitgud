# Progresso da implementação

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T0.0 | ✅ FEITA | e09e979, 7fd0c4d, e342d7f | `.gitignore`+`.env.example` antes do `git init`; `legacy/bot.py` congelado; `pyproject.toml` criado; venv em `.venv/` com `pip install -e ".[data,dev]"` (sem `uv` disponível no ambiente — usado fallback `pip`+`venv` conforme previsto no documento). `ruff check .` limpo. Ver D-1 em `docs/desvios.md`. |
| T0.1 | ✅ FEITA | d938202 | `src/botgitgud/wcl/schema_probe.py` implementado e rodado contra a API real: 8/8 campos da tabela T0.1 com veredito, 0 ausentes. Saída mecânica em `docs/schema_probe_output.md`; `docs/schema_confirmado.md` ganhou §0 (tabela de veredito) e teve a §11 resolvida (Ebon Might=395152, Prescience=410089, Debuffs=mesmo formato de Buffs, custo ~2pts/query, sem cooldown na WCL, partition atual via campo `default`). Ver D-2 (não sobrescrever schema_confirmado.md) e D-3 (excluir `docs/` do ruff) em `docs/desvios.md`. Testes: 4 unitários offline + 1 de regressão ao vivo (`-m network`), todos verdes. |
| T0.2 | ✅ FEITA | e07c999 | Fixture `PtfBbQKRY9d6zAMC` fight 1, Zarad (Warlock Demonology). `tests/fixtures/record.py` grava 23 cassetes (WCL + Blizzard, pipeline completo incluindo `build_cd_reference_profile`) com CWD isolado (D-4) para não mutar `spells.json` rastreado. `tests/conftest.py` com fixture `mock_http` (replay por chave `(method,url,payload)`) + fixtures sintéticas `synthetic_user_timeline`/`synthetic_cohort`. Golden test com snapshot syrupy captura o comportamento real do legacy, incluindo os bugs documentados (achado 3.1: nunca reporta "usos perdidos"; achado 3.10: "Parse méd: 99" fabricado). 10 testes, todos verdes, sem rede. Ver D-4, D-5, D-6 em `docs/desvios.md` — D-6 é um achado de segurança real (token OAuth vazando no corpo da resposta, corrigido antes do commit). |
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
