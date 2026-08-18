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
| T1.1 | ✅ FEITA | 6265b93 |
| T1.2 | ✅ FEITA | 7c997ab |
| T1.3 | ✅ FEITA | 31674a5 |
| T1.4 | ✅ FEITA | c03bb12 |
| T1.5 | ✅ FEITA | (pendente) | `CohortCriteria`/`cohort_id()` já existiam desde a T1.2 (D-11) e já eram testados (determinismo + mudança de hash por campo) — critério 1 herdado sem trabalho novo. Trabalho desta tarefa: (a) `RunManifest` (`domain/models.py`) com `cohort_id`, `code_version`, `generated_at`, `n_members`, `wcl_partition`, `settings_hash`; (b) `Settings.settings_hash()` (`config.py`) — hash sha256[:12] de todos os campos não-credenciais via `model_dump(exclude=...)`, excluindo deliberadamente os 5 campos `SecretStr` (o hash existe para detectar deriva de parâmetros de análise, não para impressão digital de segredos — dois `Settings` diferindo só em credenciais produzem o mesmo hash); (c) `runmanifest.py` — `get_code_version()` via `git rev-parse --short HEAD` (best-effort, cai para `"unknown"` sem nunca lançar exceção, já que um checkout empacotado sem `.git` não pode quebrar a geração de relatório) e `build_run_manifest()`; (d) `report/text.py`: `render_report()` ganhou parâmetro opcional `manifest`, renderizado como rodapé (`Cohort: ... \| Versão: ... \| Gerado: ...`) tanto no caminho com comparações quanto no caminho "nenhum CD elegível"; testes antigos continuam passando pois o parâmetro é opcional; (e) `ingest/store.py`: nova tabela `runs` (sem PRIMARY KEY, mesma lógica de imutabilidade da tabela `logs`/D-12c — reexecutar a mesma análise é um novo fato, não uma duplicata a rejeitar) + `Store.write_run()`. 161/161 testes verdes (+14 novos: `test_runmanifest.py`, mais testes em `test_config.py`/`test_store.py`/`test_report_text.py`). ruff check/format e pyright limpos (1 erro de tipo corrigido: `model_dump(exclude=...)` exige `set[str]`, não `frozenset[str]`). Nenhum desvio novo registrado — a especificação da T1.5 foi seguida à risca. | `src/botgitgud/ingest/log_fetcher.py` (`LogFetcher`, `LogRequest`). Porta a lógica de query (meta + eventos paginados + percentil) de `bot.py` para os modelos de domínio da T1.2, com `PlayerNotFound`/`FightNotFound` no lugar do padrão antigo de retornar `None`. `fetch()` consulta o `Store` primeiro; `fetch_many()` faz lookup de cache sequencialmente (sem gastar threads em refs já em cache), busca os faltantes via `ThreadPoolExecutor`, e persiste **fora** das threads, na chamada principal (regra explícita da tarefa). **Teste de aceitação medido, contra a API real** (fixture `PtfBbQKRY9d6zAMC` fight 1, Zarad): primeira execução = **6 requisições HTTP, 2.30s**; segunda execução (mesmo log) = **0 requisições HTTP, 0.051s** — cache confirmado, ~45× mais rápido. `api_points_spent` estimado via contagem de queries × 2.0 pontos/query (custo medido na T0.1), já que `WclClient.points_remaining` é um snapshot com cache periódico, não um contador ao vivo confiável para deltas de lote. 10 testes cobrindo os 2 critérios do documento (fetch duas vezes → 1 chamada; `force=True` ignora o cache) + correção de `fetch()` (campos do `PlayerLog`, persistência, aprendizado de spells, erros de jogador/fight ausente, percentil ausente) + `fetch_many` (hits/misses mistos, todos em cache = zero requisições novas). 147/147 testes verdes. | `src/botgitgud/ingest/store.py` (`Store`: DuckDB + Parquet). D-12 documentado (4 lacunas resolvidas): `FightRef.partition` adicionado; `SpellProfile`/`CohortProfile` definidas em `models.py` (formalizando o dict ad-hoc do `bot.py`); `PRIMARY KEY` removida de `logs` (contradizia o princípio de imutabilidade "obrigatório" do próprio documento — reingestão precisa suceder, não ser rejeitada); nome do arquivo parquet inclui jogador + timestamp de ingestão (o template original colidia entre jogadores do mesmo fight e entre reingestões). Dados aninhados (`cast_timeline`, `damage_by_ability`, `uptimes`, `resource_waste`) serializados como colunas JSON no parquet por linha — nunca consultados via SQL, então tipos aninhados nativos do Arrow seriam complexidade sem benefício. 10 testes cobrindo os 3 critérios do documento (50 logs sintéticos com igualdade estrutural, query com parâmetros nomeados `$spec`, reescrita não apaga o anterior e leitura retorna o mais recente) + cobertura extra (perfis, upsert de coorte, partition nula, reabertura do banco). Validado com smoke test manual antes da suíte formal. 137/137 testes verdes. | `src/botgitgud/domain/models.py` (`FightRef`, `PlayerBuild`, `AbilityDamage`, `PlayerLog`, `CohortCriteria`, `Cohort`). D-11 documentado: `CohortCriteria` (com `cohort_id()`) definida aqui em vez de na T1.5, porque `Cohort.criteria` precisa do tipo já existir e `pyright` (critério de aceite) não resolveria uma referência a um nome inexistente. 8 testes (imutabilidade, defaults, `cohort_id()` determinístico e sensível a cada campo, hash é hex de 16 chars válido). `pyright` limpo, nenhuma tupla longa em `src/`. 127/127 testes verdes. | `src/botgitgud/config.py` (`Settings`, pydantic-settings, credenciais como `SecretStr`) + `src/botgitgud/logging_setup.py` (`configure_logging`, `correlation_scope`). D-10 documentado: o pseudocódigo da T1.1 tem valores de coorte desatualizados (10/30/±7%, o rascunho pré-T0.8) — `Settings` usa os valores corretos já testados (8/20/±35%/±12%). `configure_logging` usa a integração completa structlog+stdlib (`LoggerFactory`/`ProcessorFormatter`), não `PrintLoggerFactory` — necessário porque `add_logger_name` exige um `logging.Logger` real com atributo `.name` (bug real encontrado rodando os testes, não só suposto). `correlation_scope` usa `bound_contextvars` (não bind/unbind manual) para aninhamento correto — outro bug real pego pelo próprio teste de aninhamento antes do commit. Os 4 `print()` remanescentes em `src/` (saída de CLI do `schema_probe.py`) trocados por escrita direta em stdout. Bug de isolamento de teste encontrado rodando a suíte completa (não apenas o arquivo isolado): `load_dotenv()` de outros módulos populava `os.environ` permanentemente no processo do pytest, mascarando o teste de credencial ausente — corrigido com `monkeypatch.delenv`. 13 testes novos. 119/119 testes do projeto verdes. |

## Ações pendentes do usuário

- **Rotacionar as 5 credenciais expostas** (Discord, WCL client id/secret, Blizzard client id/secret) — o `.env` foi lido em texto claro durante a auditoria. Recomendado antes de qualquer push para remoto. Não bloqueia a implementação local.

## Ambiente

- Python 3.14.6 (o documento pedia `>=3.11`; `uv` não está instalado no ambiente, usado `venv` + `pip` conforme fallback previsto em §1.2).
- Dependências `data` (duckdb, pyarrow, polars) e `dev` (pytest, hypothesis, syrupy, ruff, pyright) instaladas sem erro em `.venv/`.
