# M2.3 — final independent review — 2026-09-24

**MILESTONE_CLOSED. M2.3 e macro M2 fechados por esta revisão independente.** R2/AC6 resolvidos; R3 permanece fechado. Nenhum bloqueio contratual remanescente identificado. Parar após esta entrega; M3 não iniciado.

## Objeto e autoridades

Revisão final dos findings R2/AC6 de [independent-v003-review.md](independent-v003-review.md), da preservação de R3 e das condições de fechamento M2.3/macro M2, conforme [workflow](../../milestone-workflow.md) e [roadmap](../../methodology-roadmap-m2-m6.md). Autoridades metodológicas M0/M1/M2.1/M2.2 preservadas.

Workspace: `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`, HEAD `b6241df` (fechamento M2.2); entrega M2.3 no working tree. SPEC vigente v003: `docs/m2-3-specification.md`, byte-idêntica a `spec-v003.md`, SHA-256 `b39f576234efc2dd7747200c15953fbda4b055e84e7f4926b57a79ecc8a6f372`.

Comparação independente com [independent-v003-artifacts.json](independent-v003-artifacts.json): somente `docs/m2-3-review-evidence.md` e `tests/unit/test_m2_3_comparability_integration.py` mudaram. **SPEC e todos os arquivos de produção previamente revisados estão inalterados.** Hashes e ambiente desta revisão: [independent-final-artifacts.json](independent-final-artifacts.json).

| Artefato atualizado | SHA-256 dos bytes revisados |
|---|---|
| Testes M2.3 | `9ac0b9135421b08062a8c971624c8de0d4044c3b5573aa08e81bc953ef15e0c5` |
| Evidências M2.3 | `b8269fa7e092e6f4335b28a9d2347db8b9e8906cc864e088604664b9692ebda2` |

## R2.1 — resolvido

Os cenários de ledger insuficiente agora verificam o conteúdo Discord: rejeitam números de comparação indisponível e exigem a resposta esperada da fixture. O replay misto verifica positivamente o N quantitativo publicado (16), distinguindo-o do N de entrada do ledger (30) e da população de uptime (34). A métrica suficiente continua comparável mesmo com ledger vazio.

Reexecutei a mutação da revisão anterior, substituindo **somente em memória** o retorno do renderer por `Comparacao da coorte: mediana 123456.7 DPS; gap 99999.9 DPS.`. Desta vez o **teste integrado permanente falhou na asserção de comparação não computada**, conforme esperado. Isso confirma que a regressão protege o cenário real, além do teste unitário do verificador.

A instrumentação da chamada CLI confirmou os argumentos reais `setup`, `confidence`, `core_abilities`, `proc_analysis` e `external_dps_context`, eliminando a verificação baseada em defaults. As asserções de ausência no resultado e contrato permanecem. A saída real CLI/Discord do cenário insuficiente foi conferida e preservada em [independent-final-render.json](independent-final-render.json).

## R2.3 — resolvido

A nova regressão executa os consumidores reais pré-M2.3 carregados de `git show b6241df`, sobre a mesma fixture, com piso/política de produção e o mesmo catálogo; compara-os com os membros efetivamente usados pelo `run_analysis` atual. A tabela atual da evidência coincide com a execução independente:

| Distribuição | Antes | Depois |
|---|---:|---:|
| Entrada do ledger | 31 | 30 |
| Referências quantitativas aceitas | 17 | 16 |
| `gross_ability_dps:1` | 17 | 16 |
| `damage_events_per_second:1` | 17 | 16 |
| `damage_per_event:1` | 17 | 16 |
| `gross_damage_share_pct:1` | 17 | 16 |
| `aura_uptime_fraction:1` | 31 | 34 |
| `player_casts_per_minute:1` | 31 | 34 |

Exclusões de `gross_ability_dps:1`: **14 `UNKNOWN_DAMAGE_COLLECTION` + 4 `TIER_PIECES_BAND_MISMATCH` + 1 `BASIC_ELIGIBILITY_INELIGIBLE`**. Conferido: 35 − 19 = 16. A alegação anterior de 16 referências sem coleta completa foi corrigida. A evidência contém comando reproduzível, tabela medida e regressão permanente; inventário editorial e justificativa do golden continuam presentes.

## R3 — permanece fechado

SPEC, quarentena e integração de produção não mudaram. Reexecutados os testes permanentes de R3/R1 e as sondagens adicionais independentes: seis permutações de A/B/C combinadas com homônimo de outro servidor e não-kill do mesmo ID; quatro logs removidos pela quarentena, um pela higiene, contadores fecham, nenhum ID removido reaparece em elegibilidade/ledger/populações. Contrato, manifest e leitura do Store preservam a proveniência e JSON idênticos. Caixa/espaços no nome também mantêm a exclusão dos IDs brutos divergentes da mesma observação normalizada. Nenhuma regressão bloqueante encontrada.

## Critérios e condições de fechamento

| Critério | Revalidação |
|---|---|
| AC1 | IDs/populações consumidos preservados; replay misto e tabela medida confirmam roteamento. |
| AC2 | Exclusão por ID, determinismo e round-trips preservados; R3 permanece resolvido. |
| AC3 | Insuficiência bloqueia dependências do ledger, preservando métrica suficiente e observações independentes; renderização verificada. |
| AC4 | Identidade contábil não trivial conferida: delta total `-333.3333333333333 DPS`, N aceito 16; teste exige resultado quantitativo. |
| AC5 | SPEC e produção preservadas nesta rodada; módulos locais fechados e matching anterior mantidos; nenhuma nova política/query/fórmula/texto. |
| AC6 | R2.1/R2.3 resolvidos; provas permanentes e pacote documental agora correspondem aos resultados reproduzidos. |

M2.1 e M2.2 têm fechamentos independentes registrados em `docs/m2-1-closure.md` e `docs/m2-2-closure.md`, commits `edba6e6` e `b6241df`. A dependência de unidades locais revisadas está satisfeita. AC1–AC6 estão atendidos pelas revisões anteriores preservadas e pela revalidação final. M2.3 é a unidade explícita de integração/closure do macro M2: **esta revisão fecha M2.3 e o macro M2**, sem autorizar início de M3. Os estados abertos registrados nas revisões anteriores permanecem como histórico, superados por esta decisão sobre os hashes identificados acima.

## Verificações e limites

- Foco M2.3, matching, pipeline, persistência/regressões M1, M2.2 e golden: **221 passed, 4 skipped; 1 snapshot passed**.
- Ruff check passou; Ruff format: **333 files already formatted**.
- Pyright: **0 errors, 0 warnings, 0 informations**.
- Sondas independentes: tabela antes/depois, identidade contábil, R3 composto e round-trips passaram; mutação Discord corretamente rejeitada pelo teste integrado; argumentos CLI confirmados.
- Suíte offline completa: **2722 passed, 47 skipped, 1 deselected, 1 failed**, 337 warnings, 243,25 s; **2 snapshots passed**. Única falha: `tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`, exclusivamente `src/botgitgud/orchestrator/__main__.py`, a mesma dívida preexistente registrada desde M2.1. Não foi convertida em PASS nem usada como novo bloqueio. Resultado coincide com a entrega atual: dois testes aprovados adicionais em relação à revisão v003 anterior (2720), 32 adicionais em relação à baseline M2.2 (2690).

Sondas: [script](independent-final-probes.py.txt), [log](independent-final-probes.log.txt). Suíte: [independent-final-suite.log.txt](independent-final-suite.log.txt). Os scripts históricos de revisões anteriores permanecem preservados; suas expectativas de defeito não foram reescritas.

Comandos da revisão, executados na raiz do workspace de produto:

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q tests/unit/test_m2_3_comparability_integration.py tests/unit/test_cohort_match.py tests/unit/test_pipeline.py tests/unit/test_m1_persistence_contract.py tests/unit/test_m1_required_changes.py tests/unit/test_m2_2_metric_population.py tests/golden/test_new_pipeline_output.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pyright
.venv/Scripts/python.exe -B docs/submilestones/M2.3/independent-final-probes.py.txt
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q
```

Suíte ampla com os cinco placeholders públicos `ci-placeholder-not-a-secret` em `DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`, `BLIZZARD_CLIENT_ID` e `BLIZZARD_CLIENT_SECRET`. Corpus opcional `data/raw` ausente neste workspace; replay de `gate1_scope` executado com preservação verificada pelos testes. Não houve acesso ao banco histórico do workspace principal.

Permanecem as limitações declaradas da SPEC: N editorial do ledger versus N por métrica (inventariado para M5.2), menor cobertura de conteúdo do golden real por incompatibilidade de partição, hotfix não verificável, relaxamento não equivale a ajuste, aspiracionais por métrica apenas em proveniência. Nenhuma foi promovida a bloqueio novo fora do contrato. O histórico de falha do orquestrador continua separado da entrega de produto.

Somente artefatos independentes de revisão foram acrescentados. Nenhuma correção de SPEC, produção ou teste permanente foi implementada; nenhum trabalho M3 foi iniciado.
