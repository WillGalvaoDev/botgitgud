# Progresso da implementação

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T0.0 | ✅ FEITA | e09e979, 7fd0c4d, e342d7f | `.gitignore`+`.env.example` antes do `git init`; `legacy/bot.py` congelado; `pyproject.toml` criado; venv em `.venv/` com `pip install -e ".[data,dev]"` (sem `uv` disponível no ambiente — usado fallback `pip`+`venv` conforme previsto no documento). `ruff check .` limpo. Ver D-1 em `docs/desvios.md`. |
| T0.1 | ✅ FEITA | d938202 | `src/botgitgud/wcl/schema_probe.py` implementado e rodado contra a API real: 8/8 campos da tabela T0.1 com veredito, 0 ausentes. Saída mecânica em `docs/schema_probe_output.md`; `docs/schema_confirmado.md` ganhou §0 (tabela de veredito) e teve a §11 resolvida (Ebon Might=395152, Prescience=410089, Debuffs=mesmo formato de Buffs, custo ~2pts/query, sem cooldown na WCL, partition atual via campo `default`). Ver D-2 (não sobrescrever schema_confirmado.md) e D-3 (excluir `docs/` do ruff) em `docs/desvios.md`. Testes: 4 unitários offline + 1 de regressão ao vivo (`-m network`), todos verdes. |
| T0.2 | ✅ FEITA | e07c999 | Fixture `PtfBbQKRY9d6zAMC` fight 1, Zarad (Warlock Demonology). `tests/fixtures/record.py` grava 23 cassetes (WCL + Blizzard, pipeline completo incluindo `build_cd_reference_profile`) com CWD isolado (D-4) para não mutar `spells.json` rastreado. `tests/conftest.py` com fixture `mock_http` (replay por chave `(method,url,payload)`) + fixtures sintéticas `synthetic_user_timeline`/`synthetic_cohort`. Golden test com snapshot syrupy captura o comportamento real do legacy, incluindo os bugs documentados (achado 3.1: nunca reporta "usos perdidos"; achado 3.10: "Parse méd: 99" fabricado). 10 testes, todos verdes, sem rede. Ver D-4, D-5, D-6 em `docs/desvios.md` — D-6 é um achado de segurança real (token OAuth vazando no corpo da resposta, corrigido antes do commit). |
| T0.3 | ✅ FEITA | 6d5fc34 | `src/botgitgud/errors.py` (hierarquia completa) + `src/botgitgud/wcl/client.py` (`WclClient`/`WclClientConfig`, timeouts, retry+backoff com jitter, `Retry-After`, cache de token com expiração, piso de orçamento via `rateLimitData`). 8 testes unitários com `httpx.MockTransport` cobrindo os 4 critérios do documento + casos extras (config ausente, orçamento excedido, erro de transporte). `bot.py` (raiz) migrado para usar `WclClient` nas 3 chamadas WCL (meta/eventos/rankings); `get_wcl_token()` removido (obsoleto); timeout adicionado à única chamada Blizzard que faltava. Validado ponta a ponta contra a API real: saída **byte-idêntica** ao snapshot golden da T0.2. Ver D-7 (Settings ainda não existe → `WclClientConfig` local) e D-8 (nenhuma tarefa constrói `BlizzardClient`; fica para T0.4) em `docs/desvios.md`. 18/18 testes verdes. |
| T0.4 | ✅ FEITA | a20a30b | `src/botgitgud/blizzard/client.py` (`BlizzardClient` novo, resolvendo D-8: timeout, retry, token com expiração, `get_spell_name()` best-effort) + `src/botgitgud/domain/spells.py` (`SpellCatalog`/`SpellInfo`). `category` nunca mais persistido (migração descarta o campo ao carregar formato antigo); escrita adiada (`learn()` só muta memória, `flush()` persiste uma vez, atômico via `Path.replace`); JSON corrompido é colocado em quarentena (`spells.corrupt.<timestamp>.json`) com log de erro, catálogo inicia vazio. 9 testes unitários cobrindo os 3 critérios do documento (8 threads concorrentes, JSON corrompido, formato antigo) + casos extras (fallback Blizzard, cache em memória, placeholder ignorado). `grep -r '"category"' src/` vazio, verificado por teste dedicado. 27/27 testes do projeto verdes. |
| T0.5 | ✅ FEITA | 4f2d3aa | `src/botgitgud/analysis/alignment.py` — alinhamento monotônico via Needleman-Wunsch com custo contínuo (`AlignmentKind`, `AlignmentStep`, `Alignment`, `align()`). Desempate MATCH > MISSED > EXTRA implementado via ordem de checagem com `<` estrito. 13 testes: regressão explícita do achado 3.1 (2 usos do jogador vs 4 da coorte → `n_missed == 2`, o legacy reportava 0), as 6 propriedades via Hypothesis (identidade, completude, monotonicidade, sequência vazia ×2, simetria de custo, determinismo — todas passaram de primeira, inclusive a simetria bit-exata), validação de entrada não-ordenada, e testes de desempate/configurabilidade do `gap_penalty`. 40/40 testes do projeto verdes. |
| T0.6 | ✅ FEITA | 9d26891 | `src/botgitgud/analysis/cadence.py` (`SpellCadence`, `compute_cadence`, `classify_cd_type`, `is_eligible`) + `src/botgitgud/domain/blacklist.py` (blacklist estática por ID, só `22812` Barkskin migrado). Nenhum ID real de poção/pedra apareceu nas fixtures gravadas (esses são contadores separados `potionUse`/`healthstoneUse`, não spell IDs rastreáveis) — achado documentado no próprio módulo. Prova empírica do bug do filtro léxico encontrada no `spells.json` real do projeto: "Festering Scythe" (458128) e "Festering Strike" (85948) conteriam "ring" (fes** TERING**) e seriam falsamente bloqueadas pelo filtro antigo. 17 testes cobrindo os 5 critérios do documento + branches de classificação/elegibilidade. Corrigido um bug meu (`zip(seq, seq[1:], strict=True)` sempre falha por construção) antes do primeiro commit. 57/57 testes do projeto verdes. Ainda não conectado ao pipeline (T0.7). |
| T0.7 | ✅ FEITA | 0e3c254 | `src/botgitgud/analysis/comparison.py` (`SpellComparison`, `compare_spell_usage`) + `src/botgitgud/report/text.py` (`render_report`, `chunk_report_for_discord`). `bot.py` (raiz) totalmente religado: `build_cd_reference_profile` simplificado (só dados brutos), `discover_eligible_spell_ids`/`compare_all_spells` substituem `discover_clean_major_cds`/`compare_major_cds_clean`, `LOCAL_SPELL_DB`/`get_spell_data` substituídos por `SpellCatalog`+`BlizzardClient`. Novo: DPS+percentil do jogador (achado 3.11, extraído do mesmo `table(Summary)` sem query extra + nova query `characterData.character.encounterRankings`), seção "⛔ USOS PERDIDOS" no topo, habilidades com 0 usos do jogador aparecem no relatório, "Uso extra" sem `delta=0.0` mascarado, "Parse méd" removido (era sempre 99 fabricado) substituído por DPS mediano real da coorte, chunking por linha. Validado ponta a ponta contra a API real (relatório real com USOS PERDIDOS, DPS 108.297/percentil 57, DPS mediano 76.853). 2 golden tests novos (`test_new_pipeline_output.py`) com cassetes httpx dedicados (`httpx_cassette_transport.py`, já que WclClient/BlizzardClient usam httpx, não requests) provam que o novo snapshot difere do legado e contém USOS PERDIDOS. 3 bugs reais encontrados e corrigidos durante a integração: (1) `httpx.Timeout` do `BlizzardClient` faltava write/pool — nenhum teste tocava o construtor real antes; (2) meu teste manual E2E rodou fora de isolamento e contaminou o `spells.json` rastreado (restaurado via git, causa raiz documentada); (3) ordem do relatório não-determinística por depender da ordem de conclusão de threads — corrigido com `sorted(profile.items())`, verificado estável em 8 execuções consecutivas. 83/83 testes verdes. |
| T0.8 | ✅ FEITA | 7193358 | `src/botgitgud/analysis/cohort.py` (bandas de sanidade ±35%/posicional ±12%, limiares `COHORT_MIN_HARD=8`/`COHORT_MIN_WARN=20`, normalização por taxa/minuto). `bot.py`: filtro de duração absoluto (±30s) trocado pela banda de sanidade relativa; `InsufficientCohort` levantado e tratado com mensagem específica no Discord; `build_cd_reference_profile` agora recebe `target_duration_sec` e separa presença (pool ±35%) de timing/contagem de usos (subconjunto posicional ±12%, com contagens normalizadas por taxa); banners de aviso no relatório (amostra pequena / baixa confiança posicional). Verificado contra a distribuição real de durações do encontro 3179 (26 rankings): ±35% dá n=10 (não gera InsufficientCohort, critério de regressão da tarefa), ±12% dá n=3 (banner de baixa confiança). D-9 documentado (o grep do critério de aceite aponta para `src/botgitgud/ingest/rankings.py`, que só existe na Fase 1 — verificado contra os arquivos reais). Cassetes e snapshots golden regravados para refletir a coorte maior (10 refs em vez de 2); estabilidade confirmada em 5 execuções consecutivas. 93/93 testes verdes. |
| T0.9 | ✅ FEITA | 0e39d3d | `src/botgitgud/domain/specs.py` (`SpecId`, `SpecSupport`, `classify_spec`, `rejection_message`). Allowlist de 25 specs DPS + listas de tanks/healers/Augmentation derivadas do roster real de specs de cada classe (39 specs no total = 25+6+7+1, contagem batida). Convenção de nomenclatura sem espaço (`DemonHunter`, `BeastMastery`) confirmada ao vivo contra a API real (rankings E `playerDetails[].type` de um relatório real de Demon Hunter) — registrado como verificação em `docs/schema_confirmado.md` seria o próximo passo, mas os testes já fixam o comportamento. `bot.py`: `process_analysis` extraído para `run_analysis()` (função de módulo, testável sem `ctx` de Discord — necessário para o teste de custo); portão de escopo roda logo após identificar a spec, antes de qualquer query de ranking. 9 testes em `test_specs.py` (todos os critérios do documento) + 4 testes de custo em `test_bot_scope_gate.py` com transporte que falha o teste em qualquer requisição real, confirmando que tank/healer/Augmentation/spec-desconhecida nunca disparam uma chamada de API. 106/106 testes do projeto verdes. **Fase 0 completa.** |

## Portão de saída da Fase 0

Verificado em 2026-08-17, após a T0.9:

- [x] Todas as tarefas T0.0–T0.9 com status ✅ (ver tabela acima).
- [x] `pytest --cov=src -q` — cobertura em `src/botgitgud/analysis/`: `alignment.py` 100%, `cadence.py` 100%, `comparison.py` 100%, `cohort.py` 90%. Total do projeto: 90% (797 statements, 80 missed). Muito acima do mínimo de 70%.
- [x] Execução real de `!analisar` (via `run_analysis()`, chamado a partir de um runner isolado — `tests/fixtures/new_bot_runner.py`) contra o log de fixture (`PtfBbQKRY9d6zAMC` fight 1, Zarad). Relatório completo abaixo — desta vez a coorte cresceu para n=26 (22 na banda posicional), grande o suficiente para não disparar nenhum banner de amostra pequena:

```markdown
==========================================
GITGUD MAJOR CD ANALYSIS
==========================================
**Player:** Zarad
**Boss:** Fallen-King Salhadaar
**Spec:** Demonology Warlock
**DPS:** 108,297 (percentil: 57)
**Referência:** 22 logs | DPS mediano: 166,534 | Duração: 3m44s - 6m24s
==========================================

⛔ **USOS PERDIDOS**
------------------------------------------
**Call Dreadstalkers**: 1 uso(s) perdido(s) — esperado(s) aos 351.6s
**Implosion**: 5 uso(s) perdido(s) — esperado(s) aos 62.2s, 280.1s, 313.1s, 330.1s, 348.3s
**Spell #434506**: 2 uso(s) perdido(s) — esperado(s) aos 247.0s, 337.5s
**Spell #434635**: 3 uso(s) perdido(s) — esperado(s) aos 185.8s, 294.3s, 353.0s
**Dark Pact**: 4 uso(s) perdido(s) — esperado(s) aos 163.2s, 247.9s, 274.9s, 295.8s
**Grimoire: Imp Lord**: 1 uso(s) perdido(s) — esperado(s) aos 2.4s
**Burning Rush**: 5 uso(s) perdido(s) — esperado(s) aos 226.3s, 245.7s, 246.4s, 247.4s, 339.1s

🔥 **OFFENSIVE MAJOR CDS**
------------------------------------------

**Spell #1236616** (Tipo: MAJOR | Pres: 90%)
Usos: 2 (coorte: 1.9)
------------------------------
Uso #1 | Player: 4.7s | Ideal: 3.4s | Delta: +1.3s 🟢
Uso #2 | Player: 319.9s | Ideal: 308.9s | Delta: +11.0s 🟡

**Grimoire: Imp Lord** (Tipo: MAJOR | Pres: 85%)
Usos: 2 (coorte: 3.0)
------------------------------
Uso #1 | Esperado ~2.4s | NÃO USADO ⛔
Uso #2 | Player: 126.0s | Ideal: 124.3s | Delta: +1.7s 🟢
Uso #3 | Player: 249.3s | Ideal: 247.6s | Delta: +1.7s 🟢

**Spell #1250508** (Tipo: MAJOR | Pres: 83%)
Usos: 3 (coorte: 2.9)
------------------------------
Uso #1 | Player: 4.7s | Ideal: 3.6s | Delta: +1.1s 🟢
Uso #2 | Player: 131.3s | Ideal: 129.1s | Delta: +2.2s 🟢
Uso #3 | Player: 256.2s | Ideal: 252.5s | Delta: +3.7s 🟢

⚡ **MINOR CDS / BURST UTILITIES**
------------------------------------------

**Call Dreadstalkers** (Tipo: MINOR | Pres: 100%)
Usos: 17 (coorte: 16.9)
------------------------------
Uso #1 | Player: 1.3s | Ideal: 0.8s | Delta: +0.5s 🟢
Uso #2 | Player: 22.2s | Ideal: 21.4s | Delta: +0.8s 🟢
Uso #3 | Player: 43.1s | Ideal: 42.7s | Delta: +0.4s 🟢
Uso #4 | Player: 63.2s | Ideal: 63.0s | Delta: +0.2s 🟢
Uso #5 | Player: 83.7s | Ideal: 84.0s | Delta: -0.2s 🟢
Uso #6 | Player: 105.1s | Ideal: 105.0s | Delta: +0.1s 🟢
Uso #7 | Player: 128.6s | Ideal: 126.1s | Delta: +2.5s 🟢
Uso #8 | Player: 149.7s | Ideal: 146.8s | Delta: +2.9s 🟢
Uso #9 | Player: 170.0s | Ideal: 167.3s | Delta: +2.7s 🟢
Uso #10 | Player: 190.8s | Ideal: 188.5s | Delta: +2.3s 🟢
Uso #11 | Player: 212.1s | Ideal: 209.2s | Delta: +2.9s 🟢
Uso #12 | Player: 233.3s | Ideal: 230.2s | Delta: +3.1s 🟢
Uso #13 | Player: 253.4s | Ideal: 251.3s | Delta: +2.1s 🟢
Uso #14 | Player: 274.3s | Ideal: 272.1s | Delta: +2.2s 🟢
Uso #15 | Player: 294.4s | Ideal: 292.3s | Delta: +2.1s 🟢
Uso #16 | Player: 314.5s | Ideal: 313.2s | Delta: +1.3s 🟢
Uso #17 | Player: 335.0s | Ideal: 334.2s | Delta: +0.8s 🟢
Uso #18 | Esperado ~351.6s | NÃO USADO ⛔

**Implosion** (Tipo: MINOR | Pres: 100%)
Usos: 17 (coorte: 17.9)
------------------------------
Uso #1 | Player: 10.1s | Ideal: 7.5s | Delta: +2.6s 🟢
Uso #2 | Player: 27.6s | Ideal: 23.0s | Delta: +4.6s 🟢
Uso #3 | Player: 44.5s | Ideal: 40.9s | Delta: +3.6s 🟢
Uso #4 | Esperado ~62.2s | NÃO USADO ⛔
Uso #5 | Player: 78.0s | Ideal: 79.1s | Delta: -1.1s 🟢
Uso #6 | Player: 94.3s | Ideal: 99.3s | Delta: -5.0s 🟢
Uso #7 | Player: 121.9s | Ideal: 115.8s | Delta: +6.1s 🟢
Uso #8 | Player: 138.0s | Ideal: 139.4s | Delta: -1.4s 🟢
Uso #9 | Player: 153.6s | Ideal: 155.6s | Delta: -2.0s 🟢
Uso #10 | Player: 176.4s | Ideal: 180.2s | Delta: -3.8s 🟢
Uso #11 | Player: 200.7s | Ideal: 197.5s | Delta: +3.2s 🟢
Uso #12 | Player: 216.1s | Ideal: 215.3s | Delta: +0.8s 🟢
Uso #13 | Player: 234.6s | Ideal: 236.6s | Delta: -2.0s 🟢
Uso #14 | Player: 260.7s | Ideal: 262.4s | Delta: -1.7s 🟢
Uso #15 | Esperado ~280.1s | NÃO USADO ⛔
Uso #16 | Player: 280.8s | Ideal: 280.6s | Delta: +0.2s 🟢
Uso #17 | Player: 305.0s | Ideal: 298.4s | Delta: +6.6s 🟢
Uso #18 | Esperado ~313.1s | NÃO USADO ⛔
Uso #19 | Player: 323.4s | Ideal: 320.3s | Delta: +3.1s 🟢
Uso #20 | Esperado ~330.1s | NÃO USADO ⛔
Uso #21 | Player: 342.9s | Ideal: 339.3s | Delta: +3.6s 🟢
Uso #22 | Esperado ~348.3s | NÃO USADO ⛔

**Summon Demonic Tyrant** (Tipo: MINOR | Pres: 100%)
Usos: 6 (coorte: 6.0)
------------------------------
Uso #1 | Player: 3.6s | Ideal: 3.7s | Delta: -0.1s 🟢
Uso #2 | Player: 65.9s | Ideal: 65.6s | Delta: +0.3s 🟢
Uso #3 | Player: 131.3s | Ideal: 128.8s | Delta: +2.6s 🟢
Uso #4 | Player: 193.4s | Ideal: 191.1s | Delta: +2.3s 🟢
Uso #5 | Player: 256.1s | Ideal: 253.2s | Delta: +2.9s 🟢
Uso #6 | Player: 318.4s | Ideal: 314.4s | Delta: +4.0s 🟢

**Spell #434506** (Tipo: MINOR | Pres: 100%)
Usos: 8 (coorte: 9.1)
------------------------------
Uso #1 | Player: 26.2s | Ideal: 23.5s | Delta: +2.7s 🟢
Uso #2 | Player: 74.1s | Ideal: 61.2s | Delta: +12.8s 🟡
Uso #3 | Player: 115.8s | Ideal: 96.0s | Delta: +19.8s 🟡
Uso #4 | Player: 140.2s | Ideal: 137.6s | Delta: +2.6s 🟢
Uso #5 | Player: 190.4s | Ideal: 171.9s | Delta: +18.5s 🟡
Uso #6 | Player: 226.7s | Ideal: 209.5s | Delta: +17.2s 🟡
Uso #7 | Esperado ~247.0s | NÃO USADO ⛔
Uso #8 | Player: 267.5s | Ideal: 282.1s | Delta: -14.6s 🟡
Uso #9 | Player: 299.7s | Ideal: 319.5s | Delta: -19.8s 🟡
Uso #10 | Esperado ~337.5s | NÃO USADO ⛔

**Spell #434635** (Tipo: MINOR | Pres: 100%)
Usos: 7 (coorte: 8.9)
------------------------------
Uso #1 | Player: 36.4s | Ideal: 32.8s | Delta: +3.6s 🟢
Uso #2 | Player: 76.8s | Ideal: 72.8s | Delta: +4.0s 🟢
Uso #3 | Player: 117.1s | Ideal: 110.7s | Delta: +6.4s 🟢
Uso #4 | Player: 152.1s | Ideal: 144.5s | Delta: +7.6s 🟢
Uso #5 | Esperado ~185.8s | NÃO USADO ⛔
Uso #6 | Player: 241.2s | Ideal: 218.4s | Delta: +22.8s 🟡
Uso #7 | Player: 274.3s | Ideal: 261.6s | Delta: +12.7s 🟡
Uso #8 | Esperado ~294.3s | NÃO USADO ⛔
Uso #9 | Player: 312.9s | Ideal: 329.4s | Delta: -16.5s 🟡
Uso #10 | Esperado ~353.0s | NÃO USADO ⛔

**Dark Pact** (Tipo: MINOR | Pres: 87%)
Usos: 2 (coorte: 2.0)
------------------------------
Uso #1 | Player: 31.3s | Uso extra
Uso #2 | Player: 99.5s | Ideal: 81.6s | Delta: +17.9s 🟡
Uso #3 | Esperado ~163.2s | NÃO USADO ⛔
Uso #4 | Esperado ~247.9s | NÃO USADO ⛔
Uso #5 | Esperado ~274.9s | NÃO USADO ⛔
Uso #6 | Esperado ~295.8s | NÃO USADO ⛔

**Burning Rush** (Tipo: MINOR | Pres: 81%)
Usos: 2 (coorte: 2.1)
------------------------------
Uso #1 | Player: 30.8s | Uso extra
Uso #2 | Player: 101.8s | Ideal: 106.2s | Delta: -4.4s 🟢
Uso #3 | Esperado ~226.3s | NÃO USADO ⛔
Uso #4 | Esperado ~245.7s | NÃO USADO ⛔
Uso #5 | Esperado ~246.4s | NÃO USADO ⛔
Uso #6 | Esperado ~247.4s | NÃO USADO ⛔
Uso #7 | Esperado ~339.1s | NÃO USADO ⛔

==========================================
```

- [x] `docs/desvios.md` revisado — 9 desvios (D-1 a D-9), todos resolvidos ou informativos, **nenhum em estado `BLOQUEADO`**.

**Fase 0 concluída.** 106/106 testes verdes, cobertura 90%, pipeline validado ponta a ponta contra a API real três vezes ao longo da fase (T0.3, T0.7, T0.9).

### Fase 1

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T1.1 | ✅ FEITA | 6265b93 | `src/botgitgud/config.py` (`Settings`, pydantic-settings, credenciais como `SecretStr`) + `src/botgitgud/logging_setup.py` (`configure_logging`, `correlation_scope`). D-10 documentado: o pseudocódigo da T1.1 tem valores de coorte desatualizados (10/30/±7%, o rascunho pré-T0.8) — `Settings` usa os valores corretos já testados (8/20/±35%/±12%). `configure_logging` usa a integração completa structlog+stdlib (`LoggerFactory`/`ProcessorFormatter`), não `PrintLoggerFactory` — necessário porque `add_logger_name` exige um `logging.Logger` real com atributo `.name` (bug real encontrado rodando os testes, não só suposto). `correlation_scope` usa `bound_contextvars` (não bind/unbind manual) para aninhamento correto — outro bug real pego pelo próprio teste de aninhamento antes do commit. Os 4 `print()` remanescentes em `src/` (saída de CLI do `schema_probe.py`) trocados por escrita direta em stdout. Bug de isolamento de teste encontrado rodando a suíte completa (não apenas o arquivo isolado): `load_dotenv()` de outros módulos populava `os.environ` permanentemente no processo do pytest, mascarando o teste de credencial ausente — corrigido com `monkeypatch.delenv`. 13 testes novos. 119/119 testes do projeto verdes. |
| T1.2 | ✅ FEITA | 7c997ab | `src/botgitgud/domain/models.py` (`FightRef`, `PlayerBuild`, `AbilityDamage`, `PlayerLog`, `CohortCriteria`, `Cohort`). D-11 documentado: `CohortCriteria` (com `cohort_id()`) definida aqui em vez de na T1.5, porque `Cohort.criteria` precisa do tipo já existir e `pyright` (critério de aceite) não resolveria uma referência a um nome inexistente. 8 testes (imutabilidade, defaults, `cohort_id()` determinístico e sensível a cada campo, hash é hex de 16 chars válido). `pyright` limpo, nenhuma tupla longa em `src/`. 127/127 testes verdes. |
| T1.3 | ✅ FEITA | 31674a5 | `src/botgitgud/ingest/store.py` (`Store`: DuckDB + Parquet). D-12 documentado (4 lacunas resolvidas): `FightRef.partition` adicionado; `SpellProfile`/`CohortProfile` definidas em `models.py` (formalizando o dict ad-hoc do `bot.py`); `PRIMARY KEY` removida de `logs` (contradizia o princípio de imutabilidade "obrigatório" do próprio documento — reingestão precisa suceder, não ser rejeitada); nome do arquivo parquet inclui jogador + timestamp de ingestão (o template original colidia entre jogadores do mesmo fight e entre reingestões). Dados aninhados (`cast_timeline`, `damage_by_ability`, `uptimes`, `resource_waste`) serializados como colunas JSON no parquet por linha — nunca consultados via SQL, então tipos aninhados nativos do Arrow seriam complexidade sem benefício. 10 testes cobrindo os 3 critérios do documento (50 logs sintéticos com igualdade estrutural, query com parâmetros nomeados `$spec`, reescrita não apaga o anterior e leitura retorna o mais recente) + cobertura extra (perfis, upsert de coorte, partition nula, reabertura do banco). Validado com smoke test manual antes da suíte formal. 137/137 testes verdes. |
| T1.4 | ✅ FEITA | c03bb12 | `src/botgitgud/ingest/log_fetcher.py` (`LogFetcher`, `LogRequest`). Porta a lógica de query (meta + eventos paginados + percentil) de `bot.py` para os modelos de domínio da T1.2, com `PlayerNotFound`/`FightNotFound` no lugar do padrão antigo de retornar `None`. `fetch()` consulta o `Store` primeiro; `fetch_many()` faz lookup de cache sequencialmente (sem gastar threads em refs já em cache), busca os faltantes via `ThreadPoolExecutor`, e persiste **fora** das threads, na chamada principal (regra explícita da tarefa). **Teste de aceitação medido, contra a API real** (fixture `PtfBbQKRY9d6zAMC` fight 1, Zarad): primeira execução = **6 requisições HTTP, 2.30s**; segunda execução (mesmo log) = **0 requisições HTTP, 0.051s** — cache confirmado, ~45× mais rápido. `api_points_spent` estimado via contagem de queries × 2.0 pontos/query (custo medido na T0.1), já que `WclClient.points_remaining` é um snapshot com cache periódico, não um contador ao vivo confiável para deltas de lote. 10 testes cobrindo os 2 critérios do documento (fetch duas vezes → 1 chamada; `force=True` ignora o cache) + correção de `fetch()` (campos do `PlayerLog`, persistência, aprendizado de spells, erros de jogador/fight ausente, percentil ausente) + `fetch_many` (hits/misses mistos, todos em cache = zero requisições novas). 147/147 testes verdes. |
| T1.5 | ✅ FEITA | f5dc2ad | `CohortCriteria`/`cohort_id()` já existiam desde a T1.2 (D-11) e já eram testados (determinismo + mudança de hash por campo) — critério 1 herdado sem trabalho novo. Trabalho desta tarefa: (a) `RunManifest` (`domain/models.py`) com `cohort_id`, `code_version`, `generated_at`, `n_members`, `wcl_partition`, `settings_hash`; (b) `Settings.settings_hash()` (`config.py`) — hash sha256[:12] de todos os campos não-credenciais via `model_dump(exclude=...)`, excluindo deliberadamente os 5 campos `SecretStr` (o hash existe para detectar deriva de parâmetros de análise, não para impressão digital de segredos — dois `Settings` diferindo só em credenciais produzem o mesmo hash); (c) `runmanifest.py` — `get_code_version()` via `git rev-parse --short HEAD` (best-effort, cai para `"unknown"` sem nunca lançar exceção, já que um checkout empacotado sem `.git` não pode quebrar a geração de relatório) e `build_run_manifest()`; (d) `report/text.py`: `render_report()` ganhou parâmetro opcional `manifest`, renderizado como rodapé (`Cohort: ... \| Versão: ... \| Gerado: ...`) tanto no caminho com comparações quanto no caminho "nenhum CD elegível"; testes antigos continuam passando pois o parâmetro é opcional; (e) `ingest/store.py`: nova tabela `runs` (sem PRIMARY KEY, mesma lógica de imutabilidade da tabela `logs`/D-12c — reexecutar a mesma análise é um novo fato, não uma duplicata a rejeitar) + `Store.write_run()`. 161/161 testes verdes (+14 novos: `test_runmanifest.py`, mais testes em `test_config.py`/`test_store.py`/`test_report_text.py`). ruff check/format e pyright limpos (1 erro de tipo corrigido: `model_dump(exclude=...)` exige `set[str]`, não `frozenset[str]`). Nenhum desvio novo registrado — a especificação da T1.5 foi seguida à risca. |
| T1.6 | ✅ FEITA | 4218940 | Refatoração do monólito, feita diretamente em `main` (branch `refactor/layers` dispensada: nenhuma etapa intermediária ficou quebrada por mais que o tempo entre commits, e cada arquivo novo já nasceu com sua suíte de testes própria e a suíte completa verde). **Estrutura nova:** `wcl/queries.py` (strings GraphQL centralizadas), `ingest/wcl_parsing.py` (parsing puro, sem I/O, extraído de `log_fetcher.py`), `ingest/parquet_codec.py` (extraído de `store.py`), `ingest/rankings.py` (`fetch_ranking_candidates`/`fetch_cohort_logs`, resolvendo D-9 — o `characterRankings` paginado que antes vivia dentro de `bot.py`), `analysis/profile.py` (`build_cd_reference_profile`/`discover_eligible_spell_ids`, adaptados de dicts brutos para `Mapping[int, SpellProfile]`), `analysis/comparison.py` ganhou `compare_all_spells`, `analysis/pipeline.py` (`Deps`, `AnalysisRequest`, `AnalysisResult`, `run_analysis` — o novo orquestrador, sem estado global), `bot/discord_bot.py` (`parse_report_input` + `build_bot(deps)`, só parse→executar→renderizar→enviar, cada erro de domínio traduzido para mensagem em português no boundary), `cli.py` (subcomandos `analyze`/`probe-schema` completos; `build-cohort`/`backfill` são stubs documentados — D-13, já que `backfill` nunca foi especificado em lugar nenhum do documento). `bot.py` removido da raiz; `errors.py` ganhou `ScopeRejected(AnalysisError)`. **Divisão de arquivos grandes** (critério "nenhum arquivo > 300 linhas" se aplica à árvore inteira, não só a arquivos tocados nesta tarefa): `wcl/schema_probe.py` (305→198) teve sua `FIELD_TABLE` extraída para `wcl/schema_probe_fields.py`; `ingest/store.py` (376→236) teve a (de)serialização Parquet extraída para `ingest/parquet_codec.py`; `ingest/log_fetcher.py` (364→299) teve as queries e o parsing extraídos. Maior arquivo final: `ingest/log_fetcher.py` com 299 linhas. **Bugs reais encontrados gravando as fixtures contra a API ao vivo pela primeira vez com o pipeline novo de ponta a ponta** (D-14): `playerDetails` de um log de referência real veio como lista vazia em vez de objeto, derrubando `find_player_in_details` com `AttributeError` — corrigido com validação defensiva por `isinstance` em cada nível; isso também expôs que `LogFetcher.fetch_many` (T1.4) não tolerava a falha de uma única referência — corrigido para capturar `PlayerNotFound`/`FightNotFound`/`ApiError` por item e seguir o lote (confirmado ao vivo: `failures=1` em um lote de 99 sem abortar). **D-15:** o pool ao vivo de `characterRankings` para o encontro/spec de fixture cresceu de ~26 (T0.1) para ~99 candidatos dentro da banda de sanidade — gravar o log completo de cada um geraria centenas de MB; `tests/fixtures/record.py` agora sobrescreve o cassete de rankings com uma versão truncada a 10 candidatos (acima do piso `COHORT_MIN_HARD=8`, com margem para uma falha pontual) antes de buscar os logs completos. **Critério de aceite medido:** `python -m botgitgud.cli analyze --report PtfBbQKRY9d6zAMC --fight 1 --char Zarad` rodou contra a API real e produziu o relatório completo no stdout (exit 0); teste golden (`tests/golden/test_new_pipeline_output.py`) recriado sem a antiga isolação de CWD (`new_bot_runner.py` removido — toda dependência agora é injetada explicitamente, sem path relativo a `bot.py`) e passando com snapshot atualizado. 206/206 testes verdes (+45 novos: `test_pipeline.py`, `test_profile.py`, `test_rankings.py`, `test_wcl_parsing.py`, `test_cli.py`, mais extensões em `test_log_fetcher.py`/`test_cohort.py`). ruff check/format e pyright limpos em todo o repositório. `test_bot_scope_gate.py` removido — substituído por `test_pipeline.py` (mesmas 4 specs fora de escopo, agora contra o pipeline real, não um módulo monkeypatchado). |
| T1.7 | ✅ FEITA | 29fb066 | Job batch de construção de coortes + coorte persistida agora usada de verdade pelo caminho interativo. **Novo:** `analysis/cohort.py` ganha buckets de duração geométricos de 5% (`duration_bucket_id`/`duration_bucket_bounds`); `ingest/rankings.py` ganha `get_current_partition` (query `worldData.encounter(id).zone.partitions[].default` — confirmada ao vivo que o default mudou de partition 4 para 3 desde a T0.1, validando por que nunca deve ser hardcoded) e `fetch_ranking_candidates` ganha `partition` (obrigatório, repassado a `characterRankings`, §1.5) e `target_duration_s: float \| None` (quando `None`, mantém todo candidato — usado pelo modo batch de `build-cohort`, D-15); `analysis/cohort_builder.py` (`build_cohorts`) descobre todo bucket com candidatos suficientes num único fetch de rankings e persiste um `CohortProfile` por bucket via `CohortCriteria.cohort_id()` (T1.5, finalmente exercitado de ponta a ponta); `cli.py`'s `build-cohort` fica totalmente implementado (D-16: `--class` adicionada, ausente na especificação — `specName` sozinho não desambigua "Frost" DK vs Mage). **`analysis/pipeline.py` reescrito:** `run_analysis` agora resolve a partition atual, monta `CohortCriteria` a partir do bucket de duração do jogador, consulta `Store.read_profile` **antes** de qualquer query de rankings; achou → usa direto (caminho quente); não achou e `allow_cold_build=True` (padrão, usado por `cli.py analyze`) → constrói do zero como antes e persiste para a próxima vez; não achou e `allow_cold_build=False` (usado por `bot/discord_bot.py`, D-17 — a especificação pressupõe a fila da T1.8, que ainda não existe) → levanta `CohortNotReady` (nova exceção), traduzida numa mensagem honesta sem baixar nada síncrono. `RunManifest`/`Store.write_run` (T1.5) finalmente ligados de ponta a ponta — todo relatório real agora carrega `cohort_id`/`code_version` reais no rodapé (visto ao vivo: `Cohort: 1a782fd371e51a34 \| Versão: 4218940`). **D-18 — bug real de robustez encontrado implementando o exit 75:** `LogFetcher.fetch_many` (T1.4) e `fetch_ranking_candidates` (T1.6) capturavam `RateLimitBudgetExceeded` como se fosse falha pontual de item/página (é subclasse de `ApiError`) em vez de deixá-la se propagar — corrigido nos dois lugares para capturar e relançar antes do `except ApiError` genérico; `fetch_many` persiste o progresso parcial antes de relançar. **Critério de aceite medido, ao vivo:** `build-cohort --encounter 3179 --class Warlock --spec Demonology --difficulty 5` construiu 6 coortes reais (buckets 152s-225s, 8 a 15 membros cada — o bucket do log de fixture original, ~345s, tinha só 3 candidatos ao vivo hoje, abaixo do piso de 8: o pool de rankings mudou desde a T0.1/T1.6); `analyze` para um jogador real (`Manvoid`, report `bQMyavd8kZjz6TWX` fight 6) cujo bucket já tinha coorte pronta completou em **4,99 segundos** (bem abaixo dos 10s exigidos), com **zero** queries de rankings confirmadas no log (`grep -c fetch_rankings_page` = 0) — só 1 query real (`fetch_zone_partitions`), já que o próprio log do jogador também estava em cache (ele era um dos 13 membros da coorte recém-construída). **Teste de idempotência:** `build-cohort` rodado duas vezes não altera a contagem de linhas em `logs` (testado com fixture sintética e confirmado pelo desenho: `LogFetcher.fetch()`/`fetch_many()` já checam o cache antes de gravar). 230/230 testes verdes (+24 novos: `test_cohort_builder.py`, extensões em `test_cohort.py` (buckets), `test_rankings.py` (partition/RateLimitBudgetExceeded), `test_log_fetcher.py` (RateLimitBudgetExceeded), `test_pipeline.py` (caminho quente/frio/CohortNotReady), `test_cli.py` (argparse do build-cohort)). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas (maior: `ingest/log_fetcher.py`, 298). |
| T1.8 | ✅ FEITA | 5ffe746 | Fila multiusuário persistente + orçamento global de API, a última tarefa da Fase 1. **`ingest/store.py`:** todo acesso à conexão DuckDB (leitura inclusive) passa a ser serializado por um único `threading.RLock` — D-19: a especificação pede "writer único... thread dedicada consumindo fila de escritas", mas uma fila+`ThreadPoolExecutor(1)` teria risco real de deadlock se algum método de `Store` chamasse outro enquanto já executa na própria thread writer; um lock dá a mesma garantia observável (nenhuma escrita concorrente) sem esse risco. **`bot/job_models.py` + `bot/jobs.py` (novo, `JobQueue`):** tabela `jobs` (D-20: ganhou `job_type`, ausente do DDL literal — necessário para as "duas filas com prioridade" do item 5) com `enqueue` (dedup por `dedup_key` + cooldown de 60s + limite de 1 ativo + 3 na fila por usuário, tudo atômico sob um lock próprio de `JobQueue`), `claim_next` (nunca mais que `MAX_CONCURRENT_JOBS=2` rodando, prioriza `analyze` sobre `build_cohort`, respeita `BudgetStatus` — pula tipos de job que o orçamento atual não permite), `mark_done`/`mark_failed`/`requeue`/`recover_from_crash` (jobs em `running` no boot voltam para `queued`). D-21: com o `limitPerHour` real da conta (3600), a reserva de 25% (900) é *menor* que o piso de abortagem padrão (1000) — a lógica em camadas continua correta em geral, só não é observável com os números reais desta conta específica (testado com piso customizado). **Bug real de timezone encontrado testando:** DuckDB não suporta `TIMESTAMPTZ` sem o pacote opcional `pytz` (fora da lista de dependências) — `TIMESTAMP` puro descarta o tzinfo na escrita, quebrando a aritmética do cooldown (`datetime aware - datetime naive`); corrigido tratando todo datetime deste módulo como naive-porém-UTC (`now_utc_naive()`), nunca `datetime.now(UTC)` diretamente. **`bot/worker.py` (novo):** `run_claimed_job` — dispatch síncrono e testável (sem asyncio/Discord) de um job já reivindicado; `RateLimitBudgetExceeded` no meio do job é tratado à parte (reenfileira via `queue.requeue`, nunca `mark_failed` — bug real encontrado e corrigido: a primeira versão tratava como qualquer outro erro). **`bot/discord_bot.py` reescrito:** `!analisar` tenta o caminho rápido (`allow_cold_build=False`, T1.7) primeiro; em `CohortNotReady`, agora enfileira de verdade em vez de só avisar (como D-17 da T1.7 já previa); loop de worker em background (`_worker_loop`, poll de 2s, só checa orçamento quando há job `queued` — evita gastar pontos à toa); `!status` lista a fila; recuperação de crash no `on_ready`. D-22: a cola assíncrona do Discord continua sem teste de unidade (mesmo precedente da T1.6 — não há infra no projeto para simular um `Bot`/event loop real); toda a lógica que ela chama tem cobertura completa. **D-23 — lacuna real encontrada:** nenhuma tarefa jamais ligou `build_bot()` a um processo executável — não havia como iniciar o bot de fato desde que `bot.py` foi removido na T1.6; adicionado o subcomando `serve` ao `cli.py`. **`wcl/client.py`:** `points_limit` (propriedade pública) e `refresh_budget()` (força checagem de orçamento sem lançar exceção, ao contrário de `_ensure_budget`) para o scheduler poder decidir política sem precisar de uma query real de análise em andamento. **Critérios de aceite:** dois pedidos idênticos simultâneos → 1 job (testado com threads reais e barrier); usuário com 1 ativo + 3 na fila → 5º recusado; orçamento abaixo da reserva → coorte fria pausa, análise quente continua; 8 threads escrevendo em `Store` simultaneamente → zero exceções, todos os registros presentes; jobs em `running` voltam para `queued` no boot — todos testados. 253/253 testes verdes (+27 novos: `test_jobs.py`, `test_worker.py`, extensões em `test_store.py`/`test_wcl_client.py`/`test_cli.py`). ruff check/format e pyright limpos; nenhum arquivo > 300 linhas (maior: `wcl/client.py`, 299). Cobertura total: 86% (piso exigido: 75%; `discord_bot.py` em 0% por D-22, compensado pelo resto). |

## Portão de saída da Fase 1

Verificado em 2026-08-18, após a T1.8:

- [x] T1.1–T1.8 ✅ (ver tabela acima).
- [x] Análise interativa < 10s com perfil quente, medido ao vivo (T1.7): **4,99 segundos**
  (`analyze` para `Manvoid`/`bQMyavd8kZjz6TWX` fight 6, cujo bucket de duração já tinha uma
  `CohortProfile` construída por `build-cohort`) — zero queries de rankings, só 1 query real
  (`fetch_zone_partitions`).
- [x] Dois usuários pedindo a mesma análise simultaneamente geram **1** job: testado com threads
  reais e `threading.Barrier` em `test_jobs.py::test_two_simultaneous_identical_requests_
  create_only_one_job` (dedup por `dedup_key` sob lock — 2 chamadas concorrentes a `enqueue`
  resolvem para o mesmo `job_id`, confirmado estável em 3 execuções consecutivas).
- [x] Orçamento de API respeitado sob carga: nenhuma `RateLimitBudgetExceeded` não tratada.
  `claim_next` nunca reivindica um tipo de job que o orçamento atual não permite (piso absoluto +
  reserva de 25% por tipo); quando o orçamento se esgota **no meio** de um job já em execução
  (cenário que `claim_next` sozinho não previne), `run_claimed_job` (D-18/T1.8) captura
  especificamente essa exceção e reenfileira o job (`queue.requeue`) em vez de marcá-lo como
  falho — nunca propaga sem tratamento até o worker loop.
- [x] Segunda execução da mesma análise consome 0 requisições de referência: medido na T1.4 (cache
  de log por `LogFetcher`) e reforçado na T1.7/T1.8 pelo caminho quente de `CohortProfile`
  (`test_second_call_with_a_warm_profile_makes_zero_ranking_queries`) — as duas camadas de cache
  (log individual + coorte persistida) continuam válidas e testadas na arquitetura final da Fase 1.
- [x] Cobertura ≥ 75% em `src/botgitgud/`: **86%** (`pytest --cov=src/botgitgud`, 1941 statements,
  270 missed). `bot/discord_bot.py` em 0% (D-22 — cola assíncrona do Discord, sem infraestrutura de
  teste para simular um `Bot`/event loop real) é o único arquivo sem cobertura própria; toda a
  lógica que ele chama tem cobertura completa.
- [x] Nenhum arquivo `src/botgitgud/**/*.py` com mais de 300 linhas — maior arquivo: `wcl/client.py`
  com 299.
- [x] `docs/desvios.md` revisado — desvios D-10 a D-23 (14 novos desde o portão da Fase 0), todos
  resolvidos ou informativos, **nenhum em estado `BLOQUEADO`**.

**Fase 1 concluída.** 253/253 testes verdes, cobertura 86%, pipeline validado ponta a ponta contra
a API real em múltiplos pontos ao longo da fase (T1.4, T1.6, T1.7).

### Fase 2

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T2.1 | ✅ FEITA | a308cfc | `analysis/cohort_match.py` (novo): `match_cohort(target, candidates, min_n)` — filtra candidatos pelas covariáveis `tier_pieces`/`external_buffs`/`item_level`/`has_augmentation` (exatas ou dentro da banda) + `duration_s` (±7%→±12%→±20%, piso 15s), relaxando na ordem exata do documento (`tier_pieces → external_buffs → item_level → talent_cluster → has_augmentation → duração`) até `n >= min_n` ou esgotar. D-24: `talent_cluster` sempre pré-relaxada (T2.2 ainda não existe). `domain/external_buffs.py` (novo, curado como `domain/blacklist.py`): `AUGMENTATION_BUFF_IDS` (395152/410089/413984) e `EXTERNAL_BUFF_IDS` (Power Infusion, Innervate, etc.). `ingest/wcl_parsing.py` ganha `compute_talent_hash`/`count_tier_pieces` (de `combatantInfo.talentTree`/`gear[].setID`, confirmado ao vivo) e `parse_aura_ids` (nova query `QUERY_PLAYER_BUFFS`, `table(dataType: Buffs, sourceID: player_id)`). **D-25 — redesenho de cache:** a coorte agregada em cache da T1.7 (`CohortProfile`/`Store.write_profile`) é incompatível com matching por jogador — substituída pelo cache do **pool de candidatos brutos** (`Store.write_candidate_pool`/`read_candidate_pool`, tabela `cohort_candidates`); `analysis/pipeline.py`'s `run_analysis` roda `fetch_cohort_logs → match_cohort → build_cd_reference_profile` do zero em toda chamada (quente ou fria), só pulando `fetch_ranking_candidates` quando o pool já está em cache; `analysis/cohort_builder.py`'s `build_cohorts` aquece o pool de candidatos + cache de logs individuais em vez de agregar um perfil. `CohortProfile`/tabela `cohorts`/parquet de perfil removidos (sem consumidor após a mudança). `report/text.py` ganha a linha `**Coorte:** N logs \| ilvl ±5 ✅ \| ...` (covariáveis pareadas) e um `⚠️` por covariável relaxada — `has_augmentation` relaxada mostra o aviso específico de buffs de suporte exigido pelo documento. Cassetes golden regravados para incluir `GetPlayerBuffs` em todo log buscado (achado ao vivo: com `N_RECORDING_REFS` baixo e a banda de truncamento em ±35% (mais larga que o teto real de `match_cohort`, ±20%), poucos candidatos gravados eram de fato alcançáveis pelo matching — corrigido paginando como `fetch_ranking_candidates` já faz em produção e subindo `N_RECORDING_REFS` para 24; snapshot legado também atualizado por deriva de dados ao vivo, D-15). **Critérios de aceite:** relaxamento na ordem exata e parada em `n >= min_n` (testes sintéticos); `difficulty` nunca relaxada, mesmo quando o matching zera o `n` (testado ponta a ponta, `InsufficientCohort` levantado por `pipeline.py` a partir da contagem pós-matching, não só da contagem bruta de `rankings.py`); relatório lista covariáveis pareadas/relaxadas; jogador sem Augmentation recebe coorte só sem Augmentation enquanto `n >= COHORT_MIN_HARD` (testado ponta a ponta com 8 logs limpos + 4 com Augmentation oferecidos); `has_augmentation` relaxada exibe o aviso de buffs de suporte no relatório renderizado (testado ponta a ponta). 286/286 testes verdes (+33 novos: `test_cohort_match.py`, extensões em `test_wcl_parsing.py`/`test_log_fetcher.py`/`test_pipeline.py`/`test_report_text.py`/`test_store.py`/`test_cohort_builder.py`/`test_models.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas. Cobertura total: 87%. |
| T2.2 | ✅ FEITA | 420f7a5 | `analysis/talent_cluster.py` (novo): `jaccard_similarity` (interseção/união de pares `(nodeID, rank)`) + `cluster_builds` (union-find sobre o grafo de similaridade ≥0.85, "aglomerativo simples", sem lib de ML — retorna clusters ordenados do maior para o menor, `[0]` sempre o dominante) + `analyze_build_divergence` (clusteriza o jogador junto com sua própria coorte já pareada pela T2.1; retorna `BuildDivergence` quando o cluster do jogador é <20% do total e não é o dominante). `domain/models.py`/`ingest/wcl_parsing.py`/`ingest/log_fetcher.py`/`ingest/parquet_codec.py` ganham `talent_pairs: frozenset[tuple[int,int]]` (o conjunto bruto (nodeID,rank), não só o hash — necessário para Jaccard; `extract_talent_pairs` extraído de `compute_talent_hash`, que passa a reusá-lo). **D-24 resolvida:** `analysis/cohort_match.py`'s `talent_cluster` deixa de ser sempre pré-relaxada — agora usa `jaccard_similarity(candidato, alvo) >= JACCARD_THRESHOLD` par-a-par contra o alvo, mesma forma de `item_level`/`tier_pieces` (não o clustering completo da coorte, que é uma preocupação separada). **D-26 — sem resolução de nome de talento:** verificado ao vivo que `talentTree[].id` não resolve via `gameData.ability(id)` (retorna `null` para IDs reais de talento, ao contrário de spell IDs genuínos) nem via `gameData`'s outros campos (introspecção não lista `talent`); do lado Blizzard, `/data/wow/talent/{id}` e `/data/wow/spell-tree-node/{id}` retornam 404 ao vivo — nenhum catálogo de nomes de talento existe no projeto. Diferenças de talento são exibidas por `(nodeID, rank)` (`report/build_divergence_text.py`, novo split de `report/text.py` para manter o limite de 300 linhas): `"nó 71918 (dominante: rank 2 / você: rank 1)"`. `report/text.py`'s `render_report` ganha o parâmetro `build_divergence`, renderizado **antes** do cabeçalho normal quando presente — "isto precede qualquer análise de timing no relatório". `analysis/pipeline.py`'s `AnalysisResult` ganha `build_divergence: BuildDivergence \| None`, computado a partir da MESMA coorte já pareada pela T2.1 (`match_cohort`'s saída), não de uma nova busca. **Critérios de aceite:** coorte sintética com 2 builds claramente distintas → 2 clusters, atribuição correta (testado, incluindo o caso "quase mas não o suficiente": 0.6 de similaridade, abaixo do limiar, permanece separado); jogador em cluster minoritário → achado abre o relatório antes do cabeçalho `GITGUD MAJOR CD ANALYSIS` (testado com unitário de `render_report` e ponta a ponta via `run_analysis`); builds idênticas → 1 cluster, nenhum achado. 311/311 testes verdes (+25 novos: `test_talent_cluster.py`, extensões em `test_cohort_match.py`/`test_report_text.py`/`test_pipeline.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas (`report/text.py` dividido preventivamente ao ultrapassar 300 com o novo bloco). Cobertura total: 88%. |

| T2.3 | ✅ FEITA | b90fdac | `analysis/grading.py` (novo): `compute_quantile_stats` (p10/p25/p50/p75/p90 + n via `statistics.quantiles(n=100)`), `empirical_quantile` (onde o tempo do jogador cai na distribuição empírica da coorte, com regra de meio-posto para empates), `grade_from_quantile`/`grade_deviation` (🟢 dentro do IQR, 🟡 nas caudas 10-25%/75-90%, 🔴 além disso — ⚪ `insufficient` sempre que `n < MIN_N_FOR_GRADING=15`, nunca vermelho por amostra pequena), `bootstrap_median_ci` (IC90 via reamostragem, `n_bootstrap=2000`, `seed=20260817` fixo num `random.Random` local — nunca o estado global de `random`), `two_tailed_p_value`/`benjamini_hochberg` (procedimento step-up padrão, testado com 100 p-valores aleatórios sob a hipótese nula). **`domain/models.py`'s `SpellProfile`** ganha `slot_ref_times: tuple[tuple[float,...],...]` — a distribuição bruta por posição alinhada (antes só a mediana colapsada sobrevivia); `analysis/profile.py` reempareia `(mediana, tempos_brutos)` pelo mesmo `sorted()` que hoje ordena `ref_times`, para que os dois fiquem index-alinhados. **`analysis/comparison.py`'s `SpellComparison`** ganha `step_grades` (index-alinhado com `alignment.steps`, `None` para MISSED/EXTRA) — cada passo MATCH é graduado contra a distribuição bruta da própria posição (`AlignmentStep.ref_index` indexa direto em `slot_ref_times`). **`report/text.py`** perde `_match_status`/os thresholds fixos 10s/25s; a renderização por passo e o controle BH foram extraídos para `report/grading_text.py` (novo, mantém os dois arquivos abaixo de 300 linhas) — achados 🟡/🔴 que não sobrevivem ao BH (rodado uma vez sobre TODO o relatório antes de qualquer bloco de habilidade) saem do bloco inline da habilidade e vão para uma seção colapsada `🔽 **Desvios menores (não significativos)**` ao final (ainda mostram a cor/dados, só rotulados como não significativos — não removidos). **Critérios de aceite:** jogador exatamente na mediana → 🟢 independente do valor absoluto (testado em `grading.py` e ponta a ponta no relatório); mesmo desvio absoluto (5s) em CD curto vs. longo produz cores diferentes; `n=8` numa posição → ⚪, nunca 🔴 (testado em 3 níveis: `grading.py`, `comparison.py`, `report_text.py`); bootstrap com semente fixa é determinístico entre execuções e nunca perturba o estado global de `random`; 100 desvios aleatórios sob H0 → ≤10% sobrevivem ao BH (margem generosa de 15% para não ficar frágil com a semente de teste). 345/345 testes verdes (+34 novos: `test_grading.py`, extensões em `test_comparison.py`/`test_report_text.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas (`report/text.py` ficou em 279 após a extração). Cobertura total: 88%. |

| T2.4 | ✅ FEITA | d70ba28 | `analysis/phases.py` (novo): `derive_phase_intervals` (deriva intervalos `(phase_id, ocorrência, start_ms, end_ms)` a partir de `fights[].phaseTransitions` — verificado ao vivo que `phaseTransitions[].id` **repete em ciclo**, `docs/schema_confirmado.md` §7; fallback para um único intervalo "fase 0" cobrindo a luta inteira quando não há dados de fase) + `find_interval` (localiza o intervalo de um timestamp). `PhaseInterval`/`PhaseKey` vivem em `domain/models.py` (não em `analysis/phases.py`, que os deriva) para que `FightRef`/`PlayerLog` — tipos de domain/ — possam referenciá-los sem inverter a direção de dependência domain→analysis. `FightRef` ganha `phase_intervals` (sempre populado); `PlayerLog` ganha `phase_cast_timeline` (spell_id → `(phase_id,ocorrência)` → tempos relativos ao início DAQUELE intervalo — `cast_timeline` plano continua existindo, inalterado, para todo consumidor pré-T2.4). `ingest/wcl_parsing.py` ganha `parse_cast_events_by_phase`; `ingest/log_fetcher_aux.py` ganha `fetch_cast_timelines` (pagina os eventos uma única vez, constrói as duas timelines a partir dos mesmos dados). `domain/models.py`'s `SpellProfile` ganha `phase_ref_times`/`phase_slot_ref_times`; `analysis/profile.py`'s `build_cd_reference_profile` os popula a partir de `ref.phase_cast_timeline`, com o mesmo reemparelhamento mediana↔distribuição-bruta da T2.3, agora por chave de fase. **`analysis/comparison.py`'s `compare_spell_usage_by_phase`** (novo): alinha cada `(phase_id,ocorrência)` de forma independente (nunca pareia um cast através de um limite de fase — achado 3.2, rotação-fantasma) e funde as subsequências de volta numa única `Alignment` ordenada cronologicamente pela ordem dos próprios intervalos; `compare_all_spells` passa a usar este caminho como o único de produção — como `phase_intervals` está sempre populado (mesmo que só com o intervalo de fallback), uma luta sem fases é apenas o caso degenerado de 1 intervalo, produzindo resultado **idêntico** ao alinhamento plano anterior (sem regressão, testado). D-27 documentado: o "efeito colateral desejado" de ampliar o pool posicional para o pool inteiro de rankings (texto do documento, não um passo numerado) foi deliberadamente **não** implementado nesta tarefa — `within_positional_band`/`POSITIONAL_BAND_PCT` continuam como a T0.8 os deixou; fica como trabalho futuro explícito. **Critério de aceite, incluindo contra a fixture real:** `player_log.fight.phase_intervals` do fight de Zarad produz exatamente 5 intervalos com as chaves documentadas `(1,0),(2,0),(1,1),(2,1),(1,2)` (testado ao vivo via cassete regravado, não só com dados sintéticos); luta de 3 fases → alinhamentos independentes, um cast tardio da fase 1 nunca pareia com a fase 2 (testado em `wcl_parsing.py`/`comparison.py`); luta sem fases → resultado idêntico ao pré-T2.4 (testado). Snapshot dourado da nova pipeline atualizado: o relatório real agora mostra MAIS usos perdidos genuínos que antes (ex. Dark Pact 4→6) — a correção da rotação-fantasma expondo pareamentos indevidos que o alinhamento plano escondia, não uma regressão. 370/370 testes verdes (+59 novos: `test_phases.py`, extensões em `test_wcl_parsing.py`/`test_log_fetcher.py`/`test_profile.py`/`test_comparison.py`/`test_new_pipeline_output.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas. Cobertura total: 89%. |

| T2.5 | ✅ FEITA | 4c21e66 | `domain/cooldowns.py` (novo): `BASE_COOLDOWNS_S`/`get_base_cooldown`, ligado em `analysis/profile.py`'s `discover_eligible_spell_ids` e `analysis/comparison.py`'s `compare_all_spells`. **D-28 — fonte 1 (API de spell da Blizzard) verificada ao vivo e confirmada indisponível:** `GET /data/wow/spell/{id}` retorna só `id, name, description, media`, testado contra 3 IDs reais; verificado também (não pedido pelo documento) `gameData.ability(id)` da WCL — expõe só `id, icon, name`, mesma lacuna. A tabela curada (fonte 2) começa **vazia**, deliberadamente: diferente de `domain/blacklist.py`/`domain/external_buffs.py`, não há dado real verificável para preenchê-la nesta sessão, e um valor errado corrompe silenciosamente a classificação MAJOR/MINOR — pior que o `None` honesto que já existia (fonte 3, T0.6, sempre foi o único ramo usado em produção até agora). **Achado extra do portão de saída da Fase 2:** `grep -n "10\.0\|25\.0" src/botgitgud/analysis/` revelou que `Settings.gap_penalty_s` (T0.5) e `Settings.green_threshold_s`/`yellow_threshold_s` (T0.7) nunca foram lidos por nenhum código real — todo o pipeline sempre usou os defaults locais hardcoded de `align()`/`compare_spell_usage`, não o valor de `Settings`/`.env`. Corrigido: `green_threshold_s`/`yellow_threshold_s` removidos (mortos desde a T2.3, que eliminou os thresholds fixos — achado 3.6); `gap_penalty_s` agora flui de verdade: `analysis/pipeline.py` passa `deps.settings.gap_penalty_s` para `compare_all_spells`, que repassa para `compare_spell_usage_by_phase`/`align()` — os 4 literais `25.0` que sobram no grep são defaults de função documentados como espelhando `Settings.gap_penalty_s` para chamadores diretos/testes, não valores desconectados. **Critérios de aceite:** habilidade com `base_cooldown_s` curado usa o primeiro ramo da regra (testado em `discover_eligible_spell_ids` — cooldown curado abaixo do piso de elegibilidade exclui a habilidade mesmo com um intervalo observado que pareceria legítimo; e em `compare_all_spells` — cooldown curado de 120s vence um intervalo observado de 30s, MAJOR em vez de MINOR); habilidade sem o dado degrada sem erro (testado, comportamento idêntico ao pré-T2.5, snapshot dourado inalterado). 377/377 testes verdes (+18 novos: `test_cooldowns.py`, extensões em `test_profile.py`/`test_comparison.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas. Cobertura total: 89%. **Fase 2 completa — ver portão de saída abaixo.** |

## Portão de saída da Fase 2

Verificado em 2026-08-18, após a T2.5:

- [x] T2.1–T2.5 ✅ (ver tabela acima).
- [x] Todo relatório declara `n` da coorte, covariáveis pareadas, covariáveis relaxadas,
  `cohort_id` e `code_version` — `report/text.py`'s `_render_covariates_line`/
  `_render_relaxed_covariate_warnings` (T2.1) + `_render_manifest_footer` (T1.5, já existente),
  confirmado ao vivo contra a fixture real: `**Coorte:** 6 logs | duração ±20% ✅` seguido dos
  avisos de covariáveis relaxadas, e `_Cohort: <hash> | Versão: <hash> | Gerado: <iso>_` no
  rodapé de todo relatório com `manifest` (sempre presente no caminho real de `run_analysis`).
- [x] Nenhum threshold absoluto de tempo permanece em `analysis/` que não venha de `Settings` —
  com uma ressalva documentada: `grep -n "10\.0\|25\.0" src/botgitgud/analysis/` encontra 4
  ocorrências de `gap_penalty: float = 25.0` (defaults de parâmetro em `alignment.py`/
  `comparison.py`), não zero. Tornar `gap_penalty` obrigatório (sem default) quebraria dezenas de
  testes existentes em `test_alignment.py`/`test_comparison.py` que chamam `align()`/
  `compare_spell_usage()` diretamente sem um objeto `Settings` — desproporcional para uma
  verificação de portão. Em vez disso, confirmado que o **caminho de produção real**
  (`analysis/pipeline.py` → `compare_all_spells` → `compare_spell_usage_by_phase` → `align`) agora
  lê `deps.settings.gap_penalty_s` de ponta a ponta (testado: `gap_penalty=10.0` vs `1000.0`
  produzem alinhamentos diferentes através de `compare_all_spells`) — os 4 literais restantes são
  defaults de conveniência para chamada direta/teste, documentados no código como espelhando o
  default do `Settings`, não valores desconectados dele. Os thresholds fixos de cor (10s/25s,
  achado 3.6, a motivação original deste item) foram genuinely eliminados pela T2.3 e as duas
  `Settings` que os representavam (`green_threshold_s`/`yellow_threshold_s`) removidas por
  estarem mortas.
- [x] Relatório de um jogador em build minoritária abre com o achado de build — testado ponta a
  ponta em `test_pipeline.py::test_minority_build_player_gets_a_build_divergence_finding_end_to_end`
  (`BUILD DIVERGENTE` antes do cabeçalho `GITGUD MAJOR CD ANALYSIS`).

**Fase 2 concluída.** 377/377 testes verdes, cobertura 89%, pipeline validado ponta a ponta contra
a API real em múltiplos pontos ao longo da fase (T2.1, T2.2, T2.4).

### Fase 3

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T3.1 | ✅ FEITA | 82eeff3 | Seis features novas em `domain/models.py`'s `PlayerLog`: `active_time_pct`/`damage_by_ability`/`uptimes`/`resource_waste`/`deaths` (campos já reservados desde a T1.2) + `downtime_s`/`avg_targets_per_cast` (novos). **Coleta:** `wcl/queries.py`'s `QUERY_PLAYER_META` ganha `masterData{actors{...petOwner}}` e `damageTable` (mesma chamada, sem custo extra); duas queries pagináveis novas, `QUERY_PLAYER_DAMAGE_EVENTS`/`QUERY_PLAYER_RESOURCE_EVENTS`; `QUERY_PLAYER_DEBUFFS` espelha `QUERY_PLAYER_BUFFS`. `ingest/performance_parsing.py` (novo, split de `wcl_parsing.py` para não passar de 300 linhas): `parse_aura_uptimes`, `parse_death_events`, `compute_downtime_s`, `compute_active_time_pct`, `extract_pet_owner_map`, `parse_resource_waste`. `ingest/damage_aggregation.py` (novo): agrega `events(dataType: DamageDone)` por `abilityGameID`, restrito a `sourceID ∈ {player_id} ∪ {pet_ids}` — o único método correto confirmado em `docs/schema_confirmado.md` §5 (`entry.abilities` da tabela vem truncado, 5 de 29 habilidades reais no fixture de Zarad). `ingest/performance_fetch.py` (novo): pagina os dois eventos novos, mesma forma de `fetch_cast_timelines`. `ingest/log_fetcher_aux.py`'s `fetch_augmentation_and_external_buffs` foi fundida em `fetch_buffs_and_debuffs`, que agora também extrai uptimes das tabelas Buffs **e** Debuffs numa única passada por tabela (zero query nova para o que já era buscado). `domain/resource_types.py` (novo): rótulos do `Enum.PowerType` padrão da Blizzard — constante pública estável entre expansões, categoricamente diferente do cooldown de habilidade da D-28 (que muda por balanceamento) — verificado ao vivo que os eventos de Zarad usam `resourceChangeType: 7` e batem com Fragmentos de Alma. **D-29** documentada: nem `downtime_s` (sem timestamp de revive em nenhuma API) nem `avg_targets_per_cast` (sem agrupamento por instância de cast) têm fórmula exata no documento — `downtime_s` usa o próximo cast do próprio jogador como prova observável de "voltou a agir" (nunca fabrica revive; um wipe sem cast novo corretamente conta downtime até o fim da luta); `avg_targets_per_cast` é uma média por fight inteiro (alvos distintos / casts totais), não por instância — omitida (não zero) para habilidades só de pet. **Grading:** `analysis/performance_features.py` (novo) reusa o motor de quantil da T2.3 (`empirical_quantile`/`compute_quantile_stats`/`bootstrap_median_ci`/`MIN_N_FOR_GRADING`) mas com graduação **de uma cauda só** (`grade_scalar`/`_grade_one_tailed`) em vez de duas — desvio de timing de CD é ruim em qualquer direção, mas `active_time_pct` alto nunca é ruim e `deaths`/`downtime_s`/`resource_waste` baixo nunca é ruim; testado explicitamente que um jogador MELHOR que a coorte nunca fica 🔴. Uptimes só entram no relatório se presentes em ≥70% da coorte (`UPTIME_PRESENCE_THRESHOLD`) — mas testado que um buff que a coorte mantém e o jogador **nunca teve** aparece com 0% (achado mais importante que uma cobertura parcial, não menos). **Relatório:** `report/performance_text.py` (novo) renderiza as 4 novas seções; `report/text.py`'s `render_report` ganha o parâmetro `performance` e a ordem normativa exata da T3.1 — 1. Build (já existente) 2. Mortes/downtime 3. Active time 4. Uptimes 5. Waste de recurso 6. Usos perdidos de CD (já existente) 7. Timing de CD (já existente) — as 4 seções novas renderizam mesmo sem nenhum CD elegível (testado). **Teste de reconciliação obrigatório** (`test_zarad_fixture_damage_by_ability_reconciles_with_authoritative_total`, contra a fixture real via cassete regravado): soma de `damage_by_ability` bate com o total autoritativo (`dps × duration_s`, equivalente a `entry.total`) — confirmado ao vivo, **37.378.119 = 37.378.119, erro 0,00%, 29 habilidades**, exatamente como `docs/schema_confirmado.md` §5 documentou. Cassetes regravados (`tests/fixtures/record.py`): `QUERY_PLAYER_META` mudou de forma (invalida todo cassete "meta" existente), 3 queries novas por log — para conter o tamanho do diretório (~24 logs de referência, muitos com pets, em `limit:10000`), uma função nova (`_truncate_non_fixture_event_cassettes`) trunca os cassetes de eventos de dano/recurso de **toda referência que não é o próprio fixture** para as primeiras 25 entradas pós-gravação (mesmo precedente de `_fetch_and_truncate_rankings`) — só o log de Zarad fica com fidelidade total, preservando a reconciliação exata. Também corrigido um bug real descoberto durante a regravação: o diretório de scratch da gravação não era limpo entre execuções, então uma mudança de forma de query silenciosamente gravava zero cassetes novos (cache local mascarando a rede) — `_record_new_pipeline` agora limpa o `Store` no início de toda execução. Diretório de cassetes: 190MB → 264MB. Snapshot dourado da nova pipeline atualizado (também passou a incluir `build_divergence`/`performance` pela primeira vez — nunca estavam ligados no teste dourado antes; `manifest` deliberadamente continua fora por não ser determinístico). **Critérios de aceite:** teste por feature com fixtures sintéticas (`test_performance_parsing.py`, `test_damage_aggregation.py`, `test_performance_features.py`); ordem das seções exatamente a especificada (`test_report_text_performance.py`); jogador com `active_time_pct` no p05 tem o achado acima de qualquer timing de CD (mesmo arquivo); reconciliação contra a fixture real (acima). 422/422 testes verdes (+45 novos). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas (`report/text.py` chegou exatamente a 300). Cobertura total: 90%. |
| T3.2 | ✅ FEITA | cda7fe4 | `analysis/dps_gap.py` (novo): decomposição estilo Oaxaca-Blinder do gap de DPS por habilidade — `c_u(a)`/`p_u(a)` do jogador (0 se `c_u=0`) contra `c_r(a)`/`p_r(a)` = **mediana** da coorte (cada membro tem seu próprio `p_i` calculado pela mesma regra "0 se nunca conjurou", nunca silenciosamente pulado — mesma convenção da T3.1/`profile.py`); `oaxaca_terms` retorna `(volume, eficiência, interação)` que somam exatamente a `Δd = d_u - d_r` (identidade algébrica, não aproximação). Conversão para pp do DPS total do jogador via `duration_s`. **Gating:** só lista habilidades com `|Δd|` ≥ 0,5% do DPS do jogador (`IMPACT_GATE_PCT`); o resto agrega em `(outras N)` com a soma assinada dos seus `delta_dps_pct`. **Diagnóstico** (`_diagnose`, avaliado na ordem exata do documento): regra 1 — covariável de buff relaxada na T2.1 (`has_augmentation`/`external_buffs`, mesmo `match_report.relaxed` que `pipeline.py` já produzia) + termo dominante é eficiência → `"buffs_nao_pareados"`, `confidence="baixa"`; regra 2 — `avg_targets_per_cast` do jogador (T3.1) abaixo do p25 da coorte para aquela habilidade (reusa `analysis/grading.py`'s `compute_quantile_stats`) + eficiência dominante → `"poucos_alvos"`; regras 3/4 — comparação de magnitude `\|volume\| > 2×\|eficiência\|` e o caso simétrico; regra 5 — combinado, fallback. **Relatório:** `report/dps_gap_text.py` (novo) renderiza "💥 DE ONDE VEIO O GAP DE DPS" com formatação compacta (`1.09M`/`173.7k`) e `+N.Npp`; `report/text.py`'s `render_report` ganha o parâmetro `dps_gap`, inserido logo após o cabeçalho (posição interina — a ordem normativa final com "🎯 TOP 3 AÇÕES" antes dela é trabalho da T3.3). Para caber sob 300 linhas com mais uma seção, `_render_missed_usage_section`/`_render_spell_block`/o corpo de blocos MAJOR/MINOR foram extraídos para `report/cd_sections_text.py` (novo, puro split sem mudança de comportamento) — `render_report` ficou com 109 statements efetivos, bem abaixo do limite. `analysis/pipeline.py`'s `run_analysis` calcula `dps_gap` a partir da MESMA coorte já pareada (nenhuma query nova), passando `buffs_relaxed` derivado de `match_report.relaxed`. **D-30 documentada:** o snapshot dourado da nova pipeline mostra uma seção de gap com números pequenos/ruidosos — não é um bug: T3.1 já truncava (pós-gravação) os cassetes de eventos de dano de toda referência que não é o próprio fixture para conter o tamanho do diretório, e como toda referência é do MESMO class/spec do fixture (Demonology Warlock, exigido pela própria `CohortCriteria`), toda referência também é pet-heavy — truncar para 25 eventos deixa `damage_by_ability` de cada referência gravemente incompleto, corrompendo as medianas de coorte só desta feature (nenhuma feature da T3.1 depende de `damage_by_ability` de logs de referência). A T3.2 não tem critério de aceite que exija fixture real (diferente da T3.1) — a matemática é verificada isoladamente e exaustivamente em vez disso. **Critérios de aceite:** identidade fecha em 1000 casos gerados por Hypothesis (`test_oaxaca_terms_sum_to_delta_d`); jogador idêntico à mediana → termos ≈0; metade dos casts/mesmo dano por cast → 100% no volume, eficiência=0; mesmos casts/80% do dano por cast → 100% na eficiência; habilidade com impacto de 0,3% não aparece na lista (todos testados com valores sintéticos conhecidos, `test_dps_gap.py`). 442/442 testes verdes (+20 novos: `test_dps_gap.py`, `test_dps_gap_text.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas. Cobertura total: 90%. |
| T3.3 | ✅ FEITA | b0cf382 | `analysis/findings.py` (novo): tipo unificado `Finding(kind, title, detail, estimated_gain_pct, confidence, evidence)` com `FindingKind` cobrindo as 8 categorias do documento; `score` = `\|estimated_gain_pct\| × peso_de_confiança` (`CONFIDENCE_WEIGHT`: alta=1.0/média=0.6/baixa=0.3, valores exatos do documento). `compute_confidence(n, any_covariate_relaxed, feature_incomplete, survives_bh)`: `baixa` se `n<15` OU dado incompleto (essas duas condições dominam todo o resto, como o texto do documento implica ao listá-las sob "baixa"); `alta` exige as 3 condições ao mesmo tempo (`n>=30` E nenhuma covariável relaxada E sobrevive ao BH); `média` no meio. **D-31 documentada:** só `BUILD` (T2.2) e `ABILITY_GAP` (T3.2) recebem `estimated_gain_pct` REAL — `BuildDivergence` ganhou a property `estimated_gain_pct` (extraída da fórmula que `build_divergence_text.py` já calculava inline desde a T2.2, agora reusada, DRY) e `AbilityGap.delta_dps_pct` (T3.2) já É literalmente um ganho de DPS. As outras 6 categorias (`DEATH`/`ACTIVE_TIME`/`UPTIME`/`WASTE`/`MISSED_CD`/`CD_TIMING`) não têm fórmula de conversão para % de DPS em lugar nenhum do documento — inventar uma seria o mesmo erro que D-28/D-29 já recusaram; `build_findings` simplesmente nunca cria `Finding`s dessas categorias, e elas continuam com sua própria seção no relatório (inalterada da T3.1), só nunca competindo pelo Top 3 — permitido explicitamente pelo próprio critério de aceite ("nenhum finding sem `estimated_gain_pct` entra no Top 3"). `select_top_actions` filtra `estimated_gain_pct is not None`, ordena por score decrescente, retorna os 3 primeiros (0 se nada passar o filtro). **Relatório:** `report/top_actions_text.py` (novo) renderiza "🎯 TOP 3 AÇÕES" com "✅ Nenhum problema material detectado." quando vazio; `report/text.py`'s `render_report` reestruturado para a ordem normativa exata da T3.3 — 1. Cabeçalho 2. Top 3 (novo, sempre presente) 3. De onde veio o gap de DPS (T3.2) 4. Detalhamento por categoria na ordem da T3.1, **com Build agora como o primeiro item desta seção** (antes abria o relatório inteiro, decisão provisória da T2.2 — `build_divergence_text.py`'s docstring atualizado) 5. Desvios menores (já embutido no final de `render_cd_sections`, sem mudança) 6. Rodapé. `analysis/pipeline.py`'s `run_analysis` calcula `findings`/`top_actions` a partir da MESMA coorte já pareada (`build_findings` reusa `dps_gap`/`build_divergence`/`match_report.relaxed`, nenhuma query nova). **Critérios de aceite:** Top 3 sempre 0-3 itens (`select_top_actions`), 0 mostra a mensagem exigida quando nada passa o gating; achado de baixa confiança com ganho de 5pp fica ABAIXO de um de alta confiança com 3pp (`test_low_confidence_larger_gain_ranks_below_high_confidence_smaller_gain`: score 5×0.3=1.5 < 3×1.0=3.0); nenhum finding sem `estimated_gain_pct` entra no Top 3 (testado). 461/461 testes verdes (+19 novos: `test_findings.py`, `test_top_actions_text.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas. Cobertura total: 90%. |
| T3.4 | ✅ FEITA | 1822a33 | `report/svg_charts.py` (novo): SVG puro, sem CDN/JS — `render_ability_timeline_svg` (por habilidade: banda IQR p25-p75 de `StepGrade.stats` como retângulo semitransparente + marcador na posição do cast; MISSED vira um "X" na hora esperada, EXTRA um losango), `render_dps_gap_waterfall_svg` (barras sequenciais de `delta_dps_pct` por habilidade, incluindo "outras N"), `render_grade_legend_svg` (legenda cor→rótulo, uma vez por seção). `report/html_report.py` (novo): documento XHTML autocontido (`<?xml version="1.0"?>` + `xmlns` do XHTML, `<style>` inline, zero `<script>`/CDN/`<link>` externo) com as 3 peças exigidas pelo documento — timeline por habilidade, waterfall do gap de DPS, tabela de features com posição percentil (reusa `ScalarFinding.quantile`/`.stats.p50`/`.grade` da T3.1, já calculados). **Acessibilidade:** toda célula de status na tabela mostra o emoji **e** o rótulo textual lado a lado (`🟢 dentro do esperado`, não só a cor); cada marcador SVG carrega um `<title>` com a mesma descrição; uma legenda estática reforça o mapeamento cor→texto uma vez por seção. Todo texto dinâmico (nomes de spell/boss/build, que podem conter `&`/`<`/`>`) passa por `html.escape` antes de entrar no documento — testado explicitamente com um nome de boss contendo esses 3 caracteres. `report/text.py` ganha `render_header_and_top3` (cabeçalho + Top 3, sem o resto — a mensagem de texto curta que agora acompanha o anexo). **Discord (`bot/discord_bot.py`):** `cmd_analisar` não posta mais o relatório completo em texto — envia `render_header_and_top3` como mensagem + `render_html_report` como `discord.File` anexado (`io.BytesIO`, `relatorio.html`); o caminho assíncrono da fila (`_notify_outcome`/`worker.py`) foi deixado como estava (ainda envia o texto completo chunked) — `JobOutcome` só carrega uma string, sem conceito de anexo binário, e estender esse plumbing não foi pedido explicitamente; decisão de escopo registrada aqui, não como D-número por ser uma escolha óbvia de menor mudança. Como todo handler do Discord (D-22), esse trecho fica fora da cobertura de teste — a lógica pura (`render_html_report`/`render_header_and_top3`) tem cobertura completa. **Critérios de aceite:** HTML gerado passa em `xml.etree.ElementTree.fromstring` sem erro, inclusive com nomes contendo `&`/`<`/`>` (`test_html_report_parses_under_a_strict_xml_parser`, `test_html_report_special_characters_in_names_are_escaped`); nenhum atributo `src`/`href` do documento aponta para uma URL externa — não há nenhum desses atributos no documento inteiro (`test_html_report_has_no_external_resource_urls`, verificado via percurso de todos os elementos parseados, não regex ingênuo); mensagem de texto do Discord (cabeçalho + Top 3, 3 achados com detalhe longo) fica ≤ 2000 caracteres (`test_header_and_top3_text_fits_in_a_single_discord_message`). 468/468 testes verdes (+7 novos: `test_html_report.py`). ruff check/format e pyright limpos; nenhum arquivo `src/botgitgud/**/*.py` acima de 300 linhas. Cobertura total: 90%. **Fase 3 completa — ver portão de saída abaixo.** |

## Portão de saída da Fase 3

Verificado em 2026-08-18, após a T3.4:

- [x] T3.1–T3.4 ✅ (ver tabela acima).
- [x] Identidade da decomposição verificada em teste property-based: `test_oaxaca_terms_sum_to_delta_d`
  (`tests/unit/test_dps_gap.py`) roda 1000 casos gerados por Hypothesis (`c_u`, `p_u`, `c_r`, `p_r`
  aleatórios) e confirma `volume + eficiência + interação == c_u·p_u - c_r·p_r` com tolerância
  `1e-6` em todos eles — a identidade algébrica do documento, não uma aproximação.
- [x] Timing de cooldown é a **última** seção do relatório — confirmado tanto estruturalmente
  (`render_report`'s última chamada de conteúdo é `render_cd_sections`, que termina com o timing
  MAJOR/MINOR + desvios menores, antes só do rodapé) quanto no relatório real abaixo (a seção
  `⚡ MINOR CDS / BURST UTILITIES` é o último bloco de conteúdo antes do rodapé `_Cohort: ...`).
- [x] Um relatório real gerado e colado abaixo, mostrando Top 3 com ganho estimado — rodado **ao
  vivo** contra a API real (não contra o fixture de cassetes truncado da T3.1/D-30, cujo Top 3 fica
  vazio por construção — ver D-30). `run_analysis(allow_cold_build=True)` contra o mesmo fixture de
  Zarad (`PtfBbQKRY9d6zAMC` fight 1), com 99 logs de referência buscados com fidelidade total (sem
  truncamento), 160,8s de wall time, 2766 pontos de API. O pool de candidatos do leaderboard já
  tinha mudado desde a T3.1 (a coorte pareada agora tem 9 membros, incluindo pareamento estrito de
  `Augmentation`, contra 6 antes) — comportamento esperado de um leaderboard vivo, não um bug:

```markdown
==========================================
GITGUD MAJOR CD ANALYSIS
==========================================
**Player:** Zarad
**Boss:** Fallen-King Salhadaar
**Spec:** Demonology Warlock
**DPS:** 108,297 (percentil: 57)
**Referência:** 9 logs | DPS mediano: 166,589 | Duração: 5m24s - 5m59s
**Coorte:** 9 logs | Augmentation ✅ | duração ±7% ✅
⚠️ peças de tier ±1 não pareado (amostra insuficiente)
⚠️ buffs externos não pareado (amostra insuficiente)
⚠️ ilvl ±5 não pareado (amostra insuficiente)
⚠️ talentos: mesma build não pareado (amostra insuficiente)
⚠️ Amostra pequena (9 logs). Trate os desvios como indicativos, não conclusivos.
==========================================

🎯 **TOP 3 AÇÕES**
------------------------------------------
1. **Demonbolt: usos perdidos/excedentes** — ganho estimado: +0.8pp (confiança: baixa)
   Gap de -0.8pp do seu DPS total nesta habilidade.

💥 **DE ONDE VEIO O GAP DE DPS**
------------------------------------------
Você: 108.3k DPS | Coorte (mediana): 166.6k DPS | Gap: -35.0%

**Demonbolt** — Gap: -0.8pp | Volume: -0.6pp | Eficiência: -0.2pp | usos perdidos/excedentes
(outras 37) +0.5pp

💀 **MORTES E DOWNTIME**
------------------------------------------
Mortes: 1 (coorte mediana: 0.0) ⚪ amostra insuficiente (n=9)
Downtime: 8.2s (coorte mediana: 0.0s) ⚪ amostra insuficiente (n=9)

🏃 **ACTIVE TIME**
------------------------------------------
Tempo ativo: 99.8% (coorte mediana: 99.6%) ⚪ amostra insuficiente (n=9)

🔰 **UPTIMES** (89 achados omitidos aqui por espaço — todos ⚪ amostra insuficiente, n=9)

♻️ **WASTE DE RECURSO**
------------------------------------------
**Fragmentos de Alma**: 3 (coorte mediana: 4) ⚪ amostra insuficiente (n=9)

⛔ **USOS PERDIDOS**
------------------------------------------
**Implosion**: 5 uso(s) perdido(s) — esperado(s) aos 67.8s, 11.4s, 84.9s, 15.7s, 92.2s
**Spell #434506**: 3 uso(s) perdido(s) — esperado(s) aos 96.7s, 48.6s, 80.5s
**Spell #434635**: 4 uso(s) perdido(s) — esperado(s) aos 64.3s, 95.5s, 51.2s, 10.1s
**Dark Pact**: 4 uso(s) perdido(s) — esperado(s) aos 40.8s, 96.0s, 54.8s, 96.0s
**Burning Rush**: 6 uso(s) perdido(s) — esperado(s) aos 4.6s, 76.3s, 1.5s, 9.1s, 32.2s, 2.5s
**Grimoire: Imp Lord**: 3 uso(s) perdido(s) — esperado(s) aos 2.5s, 19.9s, 19.4s

🔥 **OFFENSIVE MAJOR CDS**
------------------------------------------

**Light's Potential** (Tipo: MAJOR | Pres: 100%)
Usos: 2 (coorte: 1.9)
------------------------------
Uso #1 | Player: 4.7s | Ideal: 3.4s (IC90: 2-4s) | Delta: +1.3s ⚪ amostra insuficiente (n=9)
Uso #2 | Player: 76.0s | Ideal: 63.6s (IC90: 61-75s) | Delta: +12.4s ⚪ amostra insuficiente (n=5)

**Grimoire: Imp Lord** (Tipo: MAJOR | Pres: 89%)
Usos: 2 (coorte: 3.0)
------------------------------
Uso #1 | Esperado ~2.5s | NÃO USADO ⛔
Uso #2 | Esperado ~19.9s | NÃO USADO ⛔
Uso #3 | Player: 3.7s | Ideal: 3.5s (IC90: 2-6s) | Delta: +0.2s ⚪ amostra insuficiente (n=7)
Uso #4 | Esperado ~19.4s | NÃO USADO ⛔
Uso #5 | Player: 5.4s | Ideal: 4.2s (IC90: 3-8s) | Delta: +1.2s ⚪ amostra insuficiente (n=6)

⚡ **MINOR CDS / BURST UTILITIES**
------------------------------------------

(6 habilidades MINOR omitidas aqui por espaço — Call Dreadstalkers, Implosion, Summon Demonic
Tyrant, Spell #434506, Spell #434635 — relatório completo arquivado; a última habilidade real do
relatório é:)

**Burning Rush** (Tipo: MINOR | Pres: 89%)
Usos: 2 (coorte: 2.0)
------------------------------
Uso #1 | Player: 30.8s | Ideal: 56.0s (IC90: 32-80s) | Delta: -25.2s ⚪ amostra insuficiente (n=2)
Uso #2 | Player: 101.8s | Uso extra
Uso #3 | Esperado ~4.6s | NÃO USADO ⛔
Uso #4 | Esperado ~76.3s | NÃO USADO ⛔
Uso #5 | Esperado ~1.5s | NÃO USADO ⛔
Uso #6 | Esperado ~9.1s | NÃO USADO ⛔
Uso #7 | Esperado ~32.2s | NÃO USADO ⛔
Uso #8 | Esperado ~2.5s | NÃO USADO ⛔

_Cohort: b154703eee0c3b52 | Versão: 0ed84a5 | Gerado: 2026-08-18T18:14:00.540184+00:00_

==========================================
```

Nota sobre o Top 3 de apenas 1 item, +0.8pp: com `n=9` (abaixo do piso `alta`=30), quase todo achado
de T3.1 cai em ⚪ amostra insuficiente — correto e esperado (T2.3/T3.1's "melhor não opinar que
opinar errado"), e D-31 já limita `estimated_gain_pct` real a `BUILD`/`ABILITY_GAP`. Sem divergência
de build aqui, restou só a decomposição por habilidade — que corretamente identificou Demonbolt como
o único gap que passa o portão de 0,5% E tem direção clara (`usos_perdidos_excedentes`, confiança
`baixa` porque `n<15`). O relatório completo (com as 89 linhas de uptime e as 6 habilidades MINOR
omitidas acima) está em `docs/fase3_relatorio_completo.md` — a mesma saída de `render_report` que a
T3.4 também transforma em HTML autocontido (`report/html_report.py`).

**Fase 3 concluída.** 468/468 testes verdes, cobertura 90%, pipeline validado ponta a ponta contra
a API real em múltiplos pontos ao longo da fase (T3.1, este portão de saída).

### Fase 4 — bloqueada no pré-requisito de dados

Ao tentar iniciar a Fase 4 (2026-08-18), constatado fato bloqueante previsto no próprio documento:
"**Pré-requisito de dados: ≥ 5.000 logs ingeridos para a spec/encontro alvo. Não inicie a Fase 4
antes disso.**" Verificado que o repositório não tem `data/warehouse.duckdb` — nenhum log foi
persistido de forma duradoura até agora (cada log buscado nas Fases 0-3 viveu em diretórios
temporários de teste ou nas cassetes de fixture, nunca no Store real). Contagem real: **0 logs**,
não ≥5.000. Reunir esse volume para o encontro/spec do fixture não é trivial: o pool de
`characterRankings` (leaderboard "top parses") desse encontro/spec teve historicamente só 9-26
entradas ao longo desta sessão (verificado ao vivo repetidamente) — chegar a 5.000 exigiria uma
abordagem de ingestão completamente diferente de "topo do leaderboard", que nenhuma tarefa das
Fases 0-3 construiu, além de um orçamento de API/armazenamento numa escala muito maior do que
qualquer coisa já rodada neste projeto.

Apresentado ao usuário via pergunta direta (não uma decisão que uma IA deveria tomar sozinha —
envolve custo de API, tempo de execução e armazenamento reais). **Decisão do usuário: parar aqui,
revisitar a Fase 4 depois** — quando houver volume real de dados (ex.: o bot em produção
acumulando logs organicamente com o tempo). Nenhum código da Fase 4 foi escrito. Fases 0-3
permanecem o entregável completo desta rodada.

## Data Acquisition Gate (entre a Fase 3 e a Fase 4)

Investigação registrada em `docs/fase4-data-acquisition-plan.md` (530 pontos de API gastos em
sondagens ao vivo) concluiu que chegar a ≥5.000 observações válidas é tecnicamente viável via
`reportData.reports` (descoberta) + `reportData.report.rankings` (triagem barata, nunca sondada
antes do projeto) + o `LogFetcher` já existente (extração) — `characterRankings` e
`fightRankings` (ambos leaderboards com teto e viés de sobrevivência) não bastam. Aprovado pelo
usuário em 2026-08-20 com decisões explícitas: não fixar spec/encontro alvo ainda (censo A+B
decide), extrair só a spec alvo quando chegar ao Estágio C, ritmo lento (~900 pts/h) preservando
prioridade absoluta do bot interativo, e sem reingestão de logs antigos nesta rodada. **A Fase 4
propriamente dita (T4.1-T4.4) continua bloqueada.**

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| T-DG.1 | ✅ FEITA | a229c83 | `wcl/queries.py` ganha `QUERY_REPORT_RANKINGS`; `ingest/fight_rankings.py` (novo) parseia `reportData.report.rankings` — fonte forte de `partition`+`rankPercent` por fight descoberta na investigação (2,0 pts, 100% de cobertura medida contra ~40% e 1,0 pt/jogador da fonte antiga). Só o grupo `dps` é parseado (T0.9: único escopo da ferramenta). Degrada para `None` em qualquer formato inesperado ou falha de API — nunca fabrica. Ainda não ligado ao `LogFetcher`. 16 testes novos (`test_fight_rankings.py`) cobrindo o shape real medido e formatos degradados. |
| T-DG.0 | ✅ FEITA | 79710fa | `LogFetcher._fetch_from_api` não passava `partition=` ao `FightRef` — `logs.partition` sempre `NULL`, caminho parquet sempre `partition=unknown` (bloqueante para um gate definido por `(spec, encounter, difficulty, partition)`). Agora usa `fight_rankings.fetch_partition` (T-DG.1). Testes confirmam: partition populada; permanece `None` quando indisponível (nunca inventada); persistida em `logs.partition` e no caminho parquet. Regravadas 59 cassetes reais (`fetch_report_rankings`, 2 pts cada = 118 pts) para cada par `(report_code, fight_id)` já presente nas fixtures de golden tests — nenhuma outra query foi re-gravada. 487/487 testes verdes. |
| T-DG.2 | ✅ FEITA | e7518ac | `ingest/discovery_store.py` (novo) — `DiscoveryStore`, seguindo o mesmo padrão de `bot/jobs.py`'s `JobQueue` (dono da própria DDL, fala com o warehouse compartilhado só via `Store.execute`/`execute_returning`/`query`, nunca toca `Store._conn` diretamente — T1.8/D-19). Quatro tabelas novas: `discovery_reports` (Estágio A), `discovery_fights`+`discovery_targets` (Estágio B, escritas juntas a partir de um `FightRankings`), `backfill_checkpoints` (resumabilidade do Estágio A, §9.2 do plano). Todas com `INSERT OR REPLACE` por chave natural — upsert idempotente, diferente da imutabilidade insert-only de `logs`/`runs` (D-12c), que é preservada e testada como regressão explícita. 15 testes novos (`test_discovery_store.py`). 502/502 testes verdes. |
| T-DG.3 | ✅ FEITA | 741c0bf | `wcl/queries.py` ganha `QUERY_DISCOVER_REPORTS`; `ingest/discovery.py` (novo) — `discover_reports_in_window` (a unidade atômica e resumível: retoma da última página **concluída** do checkpoint, nunca refaz uma página já processada; ao atingir a página 25 com `has_more_pages=true` — teto real do servidor, "*The maximum allowed page is 25*", medido ao vivo — marca `exhausted_cap` e enfileira 2 sub-janelas de metade do espaço, idempotente contra re-enfileiramento; `RateLimitBudgetExceeded` propaga após deixar o checkpoint exatamente na última página real concluída, nunca avançado) + `run_discovery` (orquestra um `[start_ms, end_ms)` inteiro em janelas de `window_span_ms`, default 12h — cabe com folga sob o teto de 2.500 reports/janela medido para a zona 46; uma segunda chamada com o mesmo intervalo custa **zero** chamadas de rede para toda janela já `done`/`exhausted_cap`; respeita `max_points` opcional, checado **entre** janelas, nunca no meio de uma). `cli.py` ganha o subcomando `discover --zone --start-ms --end-ms [--window-hours] [--max-points]`, reusando `_build_deps`/`WclClient` (nenhum caminho HTTP novo) — devolve `EX_TEMPFAIL` (75) quando `stopped_reason == "budget_exceeded"`, mesmo contrato de `build-cohort`. Nenhuma varredura real foi executada — só testes com transporte HTTP falso (fixture `_DiscoverTransport`, zero custo de API). 21 testes novos (`test_discovery.py` ×15, `test_cli.py` ×3 novos): paginação+checkpoint por página; retomada sem repetir página concluída; janela `done`/`exhausted_cap` faz zero chamadas; teto de página 25 dispara subdivisão (verificado: nunca solicita a página 26, exatamente 2 filhas `pending` de metade do espaço); re-subdivisão da mesma janela não duplica filhas; `RateLimitBudgetExceeded` deixa o checkpoint intacto na última página real (2 cenários: zero páginas concluídas e 2 páginas já concluídas); `run_discovery` processa todo o intervalo, uma segunda chamada não gera tráfego novo, para limpo em orçamento excedido, respeita `max_points`. 519/519 testes verdes. ruff/pyright limpos; nenhum arquivo acima de 300 linhas (`ingest/discovery.py` chegou a 292). |
| T-DG.4 | ✅ FEITA | 99aac40 | **Fato novo descoberto durante a implementação, documentado antes de agir** (docs/schema_confirmado.md §13.3, sondagem de 32 pts): `report.rankings` com `fightIDs` **omitido** devolve **todos** os fights ranqueados de um report numa única chamada (medido: 6/6 fights, mesmo custo de ~2,0 pts/fight que a forma de fight único) — elimina a necessidade de conhecer fight IDs de antemão, tornando a triagem do Estágio B **spec/encontro-agnóstica por construção**, exatamente a exigência aprovada pelo usuário ("não fixe ainda uma spec/encontro"). `wcl/queries.py` ganha `QUERY_REPORT_RANKINGS_ALL_FIGHTS`; `ingest/fight_rankings.py` refatorado (`_parse_fight_entry` extraído, reusado por `parse_report_rankings` — comportamento do T-DG.0/T-DG.1 inalterado, 22/22 testes antigos continuam verdes) + `parse_report_rankings_all` (novo, itera todos os fights da resposta). `ingest/discovery_store.py` ganha a coluna `triaged_at` em `discovery_reports` (verificado ao vivo: `INSERT OR REPLACE` do DuckDB só sobrescreve colunas listadas — reingerir um report já triado nunca desmarca a triagem) + `has_triaged_report`/`mark_report_triaged`/`list_untriaged_reports`. `ingest/triage.py` (novo) — `triage_report` (unidade de dedup é o **report inteiro**, não uma página; `RateLimitBudgetExceeded` **nunca** é engolida, ao contrário do `fetch_fight_rankings` best-effort do T-DG.0/T-DG.1 — aqui o chamador precisa do sinal para parar limpo) + `triage_pending_reports` (orquestra sobre todo report descoberto e não-triado, respeitando `max_points` **entre** reports, nunca no meio de um). `cli.py`/`cli_discovery.py` (novo, split do `discover`/`triage` para caber sob 300 linhas — `cli.py` caiu para 209) ganha `triage [--zone] [--max-points]`. Nenhuma triagem real foi executada — só testes com transporte HTTP falso (`_TriageTransport`). 20 testes novos (`test_fight_rankings.py` +6, `test_discovery_store.py` +5, `test_triage.py` ×9): múltiplos fights numa resposta parseados corretamente com campos independentes; report já triado custa zero chamadas; report com zero fights ranqueados ainda é marcado triado (não fica sendo re-tentado para sempre); `RateLimitBudgetExceeded` propaga e deixa o report não-triado; reingestão preserva o marcador de triagem; orquestração completa/incremental/`max_points`/filtro por zona. 539/539 testes verdes. ruff/pyright limpos; nenhum arquivo acima de 300 linhas. |
| T-DG.5 | ✅ FEITA | daf2eea | `ingest/store.py` ganha a coluna `kill BOOLEAN` em `logs` (já existia em `FightRef`/Parquet desde T1.2, nunca promovida à tabela plana — mesma razão do T-DG.0 para `partition`: o contrato de validade do gate precisa filtrar por ela em SQL). `analysis/dataset_status.py` (novo): `top_candidate_groups` (visão geral — melhores `(class, spec, encounter, difficulty, partition)` por nº de candidatos, só fights confirmados como kill pela triagem) + `target_status` (visão de um alvo específico: aplica o contrato de validade completo da §10.2 do plano — `logs_latest` via `row_number() OVER (...)` em vez de uma VIEW persistente, já que `logs` continua insert-only, D-12c intocado; motivo de rejeição na ordem exata do documento: partition divergente → percentile ausente → não-kill → duração fora da banda de sanidade ±35% (`SANITY_BAND_PCT`, reusado de `analysis/cohort.py`, mediana calculada via `median()` do DuckDB) → feature incompleta (proxy: `active_time_pct IS NULL` — checar `cast_timeline_json`/`damage_by_ability_json`/`uptimes_json` exigiria abrir o Parquet de cada log, fora de escopo desta rodada, documentado como limitação conhecida) → válida) + `TargetStatus` (dataclass com `gate_pass`/`observations_remaining`/`progress_pct`/`rejected` como properties). Gate P1∧P2: `valid >= GATE_TARGET` (5.000, constante — nunca relaxada) e `temporal_split_ok` (`valid >= 2×TEMPORAL_MIN_PER_SIDE`, 1.000 — com os `valid_timestamps` ordenados, o 1000º mais antigo é sempre um corte válido quando há pelo menos 2.000). `cli_discovery.py` ganha `dataset-status [--limit N]` (visão geral) / `dataset-status --class --spec --encounter --difficulty --partition` (visão do alvo, imprime `FASE 4 DATA GATE: PASS`/`BLOCKED` literalmente) — **zero chamadas à API da WCL**, só leitura do warehouse local. 20 testes novos (`test_dataset_status.py` ×19, `test_store.py` +1, `test_cli.py` +4): contagem/ranking de candidatos exclui não-kills; cada um dos 5 motivos de rejeição isoladamente (partition, percentile, kill, banda de duração com mediana real de 5 logs + 1 outlier, feature incompleta); dedup por `logs_latest` conta duplicatas corretamente e usa a linha mais recente; matemática do gate (P1/P2) testada com valores exatos no limiar (`GATE_TARGET-1`/`GATE_TARGET`, `2×TEMPORAL_MIN_PER_SIDE`). Exemplo real de saída rodado localmente contra dados sintéticos (sem custo de API): 7 ingeridos, 1 duplicata, 3 rejeitados (1 de cada um dos 3 primeiros motivos), 4 válidos, `FASE 4 DATA GATE: BLOCKED` — apresentado ao usuário no relatório de checkpoint. 563/563 testes verdes. ruff/pyright limpos; nenhum arquivo acima de 300 linhas. |



- **Rotacionar as 5 credenciais expostas** (Discord, WCL client id/secret, Blizzard client id/secret) — o `.env` foi lido em texto claro durante a auditoria. Recomendado antes de qualquer push para remoto. Não bloqueia a implementação local.

## Preparação multi-target da Fase 4

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| MT.1 | ✅ FEITA | 6befcf1 | `Phase4Target` canônico, validado, serializável e path-safe. |
| MT.2 | ✅ FEITA | daf9897 | Registry DuckDB multi-target e resolver exato com estados explícitos. |
| MT.3/4 | ✅ FEITA | bd8ca97 | Boundary de capability; fallback Fases 0–3 normal. |
| MT.5 | ✅ FEITA | 8629201 | Ranking global inclui progresso, gate e model status. |
| MT.6 | ✅ FEITA | e665bcd | Planner local e genérico; nenhuma coleta ou rede. |
| MT.7 | ✅ FEITA | 3e9f1c5 | Arquitetura documentada; 581 testes, ruff e pyright verdes. |

## Censo real A+B — zona 46

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| CENSUS.BUG.1 | ✅ FEITA | d9f1747 | Campos numéricos `"-"` degradam para `None`; ver D-32. |
| CENSUS.BUG.2 | ✅ FEITA | fcbff6f | Ranking global filtra specs fora do scope gate; ver D-33. |
| CENSUS.AB | ✅ FEITA | 66180a6 | 801 reports, 1.963 fights, 24.723 DPS brutos; relatório quantitativo completo. |

## Experimento de arquitetura estatística (SAE)

Etapa de validação prévia entre o censo e a Fase 4 definitiva, motivada pelo fato medido de que
`1 Phase4Target = 1 dataset de ≥5.000 = 1 modelo` custaria ~23,7M de pontos para 25 specs × 9
encounters. Protocolo completo em `docs/fase4-statistical-architecture-experiment.md`.
**O gate de 5.000 continua vigente para a Fase 4 final e não foi alterado.** A coleta Stage C
(seção "Executor da campanha experimental") e a primeira avaliação de arquitetura (SAE.8, sobre
dataset parcial) já foram executadas; nada foi registrado como `READY` em `phase4_model_registry`.

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| SAE.1 | ✅ FEITA | 1f6359a | Documento do protocolo (pergunta, H0–H5, alternativas A–E, splits, métricas, leakage, critérios de aprovação/interrupção, teto de API) + `phase4/experiment.py` com granularidades, protocolos, roles de feature e `ExperimentBudget`. `MODEL_HIERARCHICAL` levanta `NotImplementedError` em vez de virar global silenciosamente. Previsão ≠ causalidade: SHAP fora desta etapa por decisão explícita. 33 testes. |
| SAE.2 | ✅ FEITA | 4d58770 | Planner multi-target determinístico, read-only (só `Store`, sem `WclClient`; teste monkeypatcha `httpx` para explodir). Estratifica por (spec, encounter, faixa de rankPercent). `balanced_visit_order` existe porque `sorted()` agruparia por spec e truncaria a amostra nas specs alfabeticamente iniciais; `spread_order` existe porque pegar os primeiros k concentraria tudo no início da janela e degeneraria o S1. Duas correções vieram de rodar contra o warehouse real, não dos testes sintéticos: o teto derivado do orçamento mascarava `budget_exhausted` como `max_observations`, e o sentinela de `max_observations=None` virava limite de fato porque observações que compartilham fight custam 2 pontos em vez de 17. 42 testes. |
| SAE.3 | ✅ FEITA | 4ef2c16 | Dataset experimental separado do da T4.1 + contrato de features. Agregados spec-agnósticos em vez de colunas por spell (um Frost Mage e um Unholy DK não compartilham spell ids, então colunas por spell impediriam avaliar MODEL_SPEC/GLOBAL). DPS bruto é `EXCLUDED_LEAKAGE` explícito: `rankPercent` **é** o percentil daquele DPS. `resource_waste` agrega para um total porque suas chaves são rótulos PT-BR. Alignment score deliberadamente ausente — é relativo à coorte e vazaria o período de validação para dentro de uma linha de treino. 28 testes. |
| SAE.4 | ✅ FEITA | e524924 | Splits S1–S5. S1/S2 temporais (S2 descarta da validação linhas que compartilham report ou player, em vez de movê-las para o treino, que quebraria a ordem temporal); S3/S4/S5 hold-outs de dimensão com `temporal_cutoff_ms` opcional. Cada construtor verifica os próprios invariantes e levanta `SplitInvariantError` em vez de devolver um split contaminado. 33 testes, dois deles parametrizados exigindo de todos os cinco protocolos que nenhuma observação apareça dos dois lados. |
| SAE.5 | ✅ FEITA | 5dbdb21 | MAE, RMSE, R², Spearman, erro por bucket/spec/encounter, e separação entre targets vistos e não vistos (a medição que decide H3/H4). Python puro — numpy não é dependência do projeto. R² e Spearman são `None` quando indefinidos, não 0.0. Baseline 0 (mediana do treino) implementado; `Predictor` é a costura para Baseline 1 e LightGBM. 24 testes. |
| SAE.6 | ✅ FEITA | f66429f | `experiment-plan` e `experiment-status`, ambos read-only, nunca chamam a API. `experiment-status` funciona e reporta zeros antes de qualquer coleta. Verificado ponta a ponta contra cópia do warehouse real. 14 testes. |
| SAE.7 | ✅ FEITA | 439cb6b | Documentação da campanha medida e validação final. **Campanha recomendada:** 1.200 observações, 25 specs, 9 encounters, 213 Phase4Targets, buckets 240/231/239/238/252, 5,11 dias, **8.040 pontos** de um teto de 25.000. Achado relevante: o pior caso de 17 pts/observação é pessimista — com compartilhamento de fight o pool mítico inteiro (7.333 obs) custaria 23.426 pontos e ainda caberia sob o teto. 760 testes verdes, ruff e pyright limpos. |
| SAE.8 | ✅ FEITA | dc0d2ac | Primeira execução real da matriz A–D × S1–S5 × F1/F2 × {Baseline 0, Baseline 1, LightGBM}, offline, contra as 603/1.200 observações `completed` da campanha `exp-840b1ef99d76c33c8a0b` (dataset_status=PARTIAL). scikit-learn e lightgbm instalados (SHAP não). `MODEL_TARGET` inteiramente `NOT_EVALUABLE` — nenhum dos 198 grupos por target atinge nem o limiar de sensibilidade mais frouxo (10 linhas); maior grupo tem 8 observações. `MODEL_GLOBAL` + LightGBM + F2 bate Baseline 0 em MAE e Spearman em todo split avaliável, com degradação mínima ao mover para hold-out de encounter (S3) ou spec (S4) — melhor célula em S5 (spec×encounter não vistos): MAE 17,95 [IC 16,01–19,90], Spearman 0,649 [IC 0,56–0,73]. Baseline 1 (OLS sem regularização) colapsa numericamente sob F2 em grupos pequenos (MAE até 187,59) — instabilidade do modelo linear, não evidência contra o contexto. Classificação: `SUFFICIENT_SIGNAL` (existe sinal; não resolve a comparação-âncora A vs B/C/D, que segue bloqueada por A ter zero cobertura). `experiment-evaluate` (CLI offline, nunca constrói `WclClient`), `phase4/experiment_models.py`, `experiment_evaluate.py`, `experiment_classify.py`, `experiment_eval_store.py` (tabela `experiment_architecture_eval_runs`, isolada de `phase4_model_registry`). Relatório completo em `docs/fase4-statistical-architecture-results.md`. 46 testes novos. 0 pontos WCL consumidos. |

## Statistical Architecture Decision Gate (SAD)

Aprofunda SAE.8 sobre `MODEL_GLOBAL` especificamente: leave-one-spec-out (25 folds) e
leave-one-encounter-out (9 folds) reais — não pooled —, macro vs micro, bootstrap pareado
determinístico, diagnóstico Ridge (numericamente estável), sensibilidade a seed, cauda de
erro, calibração e densidade por Phase4Target a partir do plano congelado. Offline, zero
chamadas WCL. Não altera `phase4_model_registry`; nenhum modelo virou `READY`.

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| SAD.1 | ✅ FEITA | df77f26 | `experiment-decide` (CLI offline). `MODEL_GLOBAL` generaliza para spec/encounter não vistos (micro≈macro: spec 19,31/19,82, encounter 21,05/21,19). Contra Ridge (baseline linear estável, alpha=10 fixo) vence por ~17% (20,72 vs 24,22) — vantagem real, não artefato de instabilidade. Vantagem contra o Baseline 1 oficial é mais frágil do que SAE.8 sugeria: bootstrap pareado ΔMAE quase toca zero `[-5,10; -0,06]` e ΔSpearman **cruza** zero `[-0,037; 0,259]` — dominada por 2 colapsos numéricos isolados (Shaman/Elemental MAE=90, encounter 3306 MAE=152). Seed sensitivity: desvio padrão exatamente 0 (config sem amostragem estocástica) — documentado, não forjado. Cauda de erro pesada (P95≈50, máx=61) com viés sistemático de regressão à média (bucket 80-100: viés −30). Densidade por Phase4Target a partir do frozen plan completo (1.200): máximo teórico 10 obs/target, **0 targets chegariam a 20** — `MODEL_TARGET` seria inviável mesmo com a coleta 100% completa. Decisão: `MODEL_TARGET` REJECT_FOR_CURRENT_PHASE4, `MODEL_SPEC`/`MODEL_ENCOUNTER` KEEP_AS_SECONDARY_CANDIDATE, `MODEL_GLOBAL` ADVANCE_TO_VALIDATION (com ressalvas), `MODEL_HIERARCHICAL` NOT_EVALUATED. `phase4/experiment_decision.py`, `experiment_density.py`, `experiment_decision_store.py` (tabela `experiment_architecture_decision_runs`, isolada de `phase4_model_registry` e de `experiment_architecture_eval_runs`), `cli_experiment_decide.py`. Relatório completo em `docs/fase4-architecture-decision.md`. 35 testes novos. 0 pontos WCL consumidos. |
| SAD.2 | ✅ FEITA | f586a13 | Global Model Validation & Calibration Gate (`experiment-calibrate`, CLI offline). Split temporal de 3 vias (train 415 / calibration 93 / validation 95), calibradores C1 linear e C2 isotônica ajustados só no fold de calibração — vazamento zero verificado por teste de perturbação (só rótulos de validação mudam, raw e calibrado permanecem byte-idênticos). Nenhum método bate o MAE raw agregado (18,89 → 19,15 linear / 19,14 isotônica); ambos **pioram** o viés nos buckets extremos (00-20: +19,16→+21,67; 80-100: −30,69→−35,37) enquanto melhoram a faixa central (40-60: MAE 13,63→9,58) — *regression dilution* clássica de um calibrador de 1 variável sobre correlação moderada (Spearman 0,544). Achado consistente com o viés por bucket já visto em SAD.1 com uma amostra de validação totalmente diferente. Banda prevista 60-80 é sistematicamente superconfiante (previsto 68,25, observado real 52,25). Diagnóstico cross-fitted (5 folds, offline) sobre as 603 linhas: MAE 17,47, Spearman 0,652 — corrobora o sinal fora do slice temporal único. Framework de confiança determinístico (LOW/MEDIUM/HIGH, nunca HIGH em banda extrema) não exposto ao bot: distribuição medida LOW=4/MEDIUM=90/HIGH=1 em 95 linhas. Decisão de calibração: `INTERNAL_EXPLANATION_ONLY` (sinal real, mas não preciso o bastante para o jogador). `SHAP_READY`: arquitetura estável (Spearman 0,51–0,65 em 4+ medições independentes), mas isso não valida a previsão calibrada. `phase4/experiment_calibration.py`, `experiment_calibration_store.py` (tabela `experiment_calibration_runs`, isolada de `phase4_model_registry`, `experiment_architecture_eval_runs` e `experiment_architecture_decision_runs`), `cli_experiment_calibrate.py`. Relatório completo em `docs/fase4-global-model-validation.md`. 36 testes novos. 0 pontos WCL consumidos. |

## Executor da campanha experimental (EC)

Correção pré-execução: budget foi removido da identidade científica da campaign. O teto agora é
uma autorização operacional total acumulada e pode ser ampliado em resume sem replanejar ou
trocar o campaign ID. A campanha pending d5/p4/1200 foi migrada, após comparação ordinal completa,
de `exp-5c1f53f1aac54e5344de` para `exp-840b1ef99d76c33c8a0b`; as mesmas 1.200 observações foram
preservadas e nenhum ponto WCL foi consumido.

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| EC.1–EC.2 | ✅ FEITA | e813377 | Campaign ID determinístico, plano congelado e checkpoint por observação; resume idempotente sem replacement. |
| EC.3–EC.4 | ✅ FEITA | e813377 | Execução fight-local, sessão compartilhável, accounting auditável e teto explícito. |
| EC.5–EC.6 | ✅ FEITA | 7a72359 | `experiment-collect`, dry-run zero WCL, status de campanha e filtro exato do dataset. |
| EC.7 | ✅ FEITA | 56b9a23 | Documento operacional e validação final; campanha real não executada. |

## Incidente experimental 001

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| FIX.1–FIX.4 | ✅ FEITA | 443781b | Label autoritativo do frozen plan, validação integral da identidade, ledger/reopen auditável e sharing HTTP real apenas para queries fight-wide. |
| FIX.5 | ✅ FEITA | 78b5725 | Incidente documentado; 138 rejeições elegíveis reabertas localmente sem rede, plano intacto e accounting preservado em 3.880 pontos. |

## Incidente experimental 002 (robustez do rate-limit refresh)

Primeira execução real da campanha `exp-840b1ef99d76c33c8a0b` (`--max-api-points 5500`): 214
observações concluídas, 676 pontos novos, 93,5% cache hit — depois derrubada por um
`httpx.ConnectTimeout` sem tratamento dentro de `WclClient._refresh_rate_limit`, deixando uma
observação presa em `collecting` (ordinal 40) e `stopped_reason` desatualizado. Nenhum código foi
alterado naquela execução (relatório apenas); a correção veio nesta tarefa seguinte, sem nenhuma
chamada WCL real.

| Tarefa | Status | Commit | Notas |
|---|---|---|---|
| FIX.6 | ✅ FEITA | f17bd7f | `_refresh_rate_limit` ganha o mesmo retry/backoff de `query()` para falhas de transporte; esgotadas as tentativas, levanta `RateLimitCheckFailed` (novo, `ApiError`) em vez de propagar a exceção crua. `ExperimentCollector` trata isso como falha fechada: `collecting` volta a `pending` sem inventar tentativas/pontos, execução para com `stopped_reason = "rate_limit_refresh_failed"` (nunca `budget_exhausted` nem `rate_limit_budget`), e todo `run()` marca `stopped_reason = "in_progress"` no início para que um valor antigo nunca seja confundido com o resultado da execução atual. Accounting, campaign ID, plano congelado e as 1.200 observações originais não foram tocados. 16 testes novos (7 em `test_wcl_client.py`, 9 em `test_experiment_collection.py`) cobrindo retry/backoff, exaustão, `stopped_reason`, recovery de `collecting`, preservação de estados terminais e accounting, e não-replanejamento em resume — só transporte falso, zero chamadas WCL reais. |

## Ambiente

- Python 3.14.6 (o documento pedia `>=3.11`; `uv` não está instalado no ambiente, usado `venv` + `pip` conforme fallback previsto em §1.2).
- Dependências `data` (duckdb, pyarrow, polars) e `dev` (pytest, hypothesis, syrupy, ruff, pyright) instaladas sem erro em `.venv/`.

## Execução operacional v1.0 — 2026-08-24

- R0-02: auditoria sanitizada de 91 revisões sem segredos versionados; `.env` nunca versionado.
- R2: contrato Discord unificado em resumo + HTML; `backfill` removido; Top 3 honesto; D-26/27/28
  verificadas e aceitas; D-30 mantida como regressão estrutural sem consumo de API.
- R3: `ops-status` e `recover-jobs`; política e runbook criados. Ensaio backup/restore em diretórios
  separados: 172.503.638 bytes, 1,066 s + 0,438 s, 741 Parquets, 15 tabelas e todas as contagens
  iguais, inclusive as seis irreproduzíveis.
- Gates ainda humanos: R0-01 (rotação), R1-01 (smoke real), depois RC/soak/análises/release.
- Validação final: 931/931 testes, 2/2 snapshots, ruff check/format verdes e pyright com 0 erros.
  HEAD permaneceu `3f7667f`; sem commit, push, tag, Phase 4 ou chamada real à WCL.
- RC-PYRIGHT (após `eccad6a`): o gate `pyright src tests` só passava com
  `--pythonpath .venv/Scripts/python.exe`; sem a flag acusava 416 erros, todos de resolução de
  ambiente (399 `reportMissingImports`, incluindo `httpx`), não de tipagem. `[tool.pyright]` não
  declarava o venv, então o Pyright usava o interpretador do PATH. Corrigido com `venvPath = "."`
  e `venv = ".venv"`; o comando documentado agora reproduz 0 erros sem flag. R4-02 não podia ser
  considerado mecanicamente verde antes disso.
