# M2.2 — independent re-review — 2026-09-22

**REQUIRES_CHANGES** — permanece uma pendência documental de R3/AC6. R1 e R2 corrigidos, com regressões permanentes. Nenhum novo defeito bloqueante de implementação demonstrado nesta rodada. Routing: Sonnet, para completar o artefato de evidência já contratado. Não requer decisão humana, mudança de política ou início de M2.3.

## Versão e autoridades

Revisão da árvore `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`, sobre HEAD `edba6e62d144811724efe3a2f9bdf0d6023c64ee`. Correção ainda não commitada. Workflow, roadmap, contratos M0/M1 e SPEC M2.1 mantidos. SPEC M2.2 v001 inalterada.

SHA-256 confirmados nesta revisão:

| Artefato | SHA-256 |
|---|---|
| SPEC M2.2 | `ea35eb66efe593076a1fe8efed9b337731f0a970097877ca72fe33ab19d84968` |
| `metric_population.py` | `8aff6e01430c2e6fa7b0406ca42a0869f941f4b1186574bb40e80b3287aa5e2a` |
| `test_m2_2_metric_population.py` | `daff74b6353ac37eb62d70aa64b883591b0667c18db0f30ac61a82dbee483e41` |

## Revalidação dos achados

**R1 — resolvido.** `_stage_a` agrupa logs e decisões por ID, rejeita grupos divergentes e aceita cópias iguais. Os contraexemplos de classe e de disponibilidade agora levantam `ValueError` em ambas as ordens, tanto na seleção quanto na sensibilidade. A rejeição impede a associação cruzada sem escolher qual log deve prevalecer; não implementa política de deduplicação de produto. As quatro regressões permanentes descritas na entrega existem e passam. Sondagens adicionais confirmaram cópias iguais nas seis métricas, com conjuntos de diferentes tamanhos.

**R2 — resolvido.** Exclusões descritivas e aspiracionais são ordenadas na própria saída. As duas regressões permanentes passam. Sondagens independentes verificaram serialização sem `sort_keys`, ambas as ordens de entrada e os caminhos aspiracionais disponível/indisponível, nas seis métricas.

**R3 — parcialmente resolvido; bloqueio remanescente.** Foram acrescentados e executados os 24 casos de isolamento covariável × métrica, o censo real das seis métricas e a sensibilidade sobre metadados reais. A tabela real foi preservada em `docs/m2-2-review-evidence.md` §4.2. Falta ainda a tabela por nível das fixtures sintéticas, com N e IDs efetivamente selecionados.

Contrato preciso: SPEC §12.9 exige `covariate_sensitivity` “executada sobre as fixtures e sobre os metadados reais disponíveis, com a tabela por nível preservada nas evidências”; §10.3 define os campos da tabela e AC6 exige o pacote completo. Esta era parte do achado R3 original, não um requisito novo.

Reprodução documental: a única tabela com `level_index`, `relaxed_so_far`, `duration_band_pct`, `n` e `member_ids` no pacote de entrega é a de §4.2, inteiramente vazia (`n=0`). Para o cenário sintético de duas referências compatíveis e seis fora de tier/ilvl/duração, o documento apenas aponta `test_covariate_sensitivity_reports_every_ladder_level_without_stopping_at_the_floor` e informa que existem seis linhas. Não preserva essas seis linhas, os N por nível ou seus membros. O teste executa a função e confere parte dos resultados, mas não exporta o artefato exigido. Assim, a evidência preservada não permite inspecionar os efeitos não vazios do relaxamento contratados em B04.

Alteração necessária: completar o pacote de evidências com a tabela sintética por nível e identificação da fixture/comando que a reproduz. Não há necessidade demonstrada de alterar o seletor, a SPEC ou iniciar integração. O revisor não preencheu essa lacuna no lugar do executor.

## Acceptance criteria e contraexemplos adicionais

| Critério | Resultado |
|---|---|
| AC1 | Passa nos casos contratados das seis métricas; sondagem independente das 24 combinações isoladas também confere os membros do seletor principal. |
| AC2 | Ordem do ladder, registro dos deltas zero e parada no piso preservados; testes passam. |
| AC3 | Contraexemplo anterior eliminado; inelegíveis não são promovidos pelas regressões e sondagens. |
| AC4 | Identidades, subconjunto, N, guarda de DPS finito e exclusões separadas preservados; verificados também os dois caminhos de disponibilidade aspiracional. |
| AC5 | Disponibilidade e N por métrica/spell preservados; cópias iguais não inflam N; colisões ambíguas não produzem resultados dependentes de ordem. |
| AC6 | **Pendente:** tabela sintética B04 não preservada. Escopo local e verificações estáticas atendidos. |

Não foi demonstrada nova regressão bloqueante no código. Não foram impostos novos critérios de aquisição, cobertura universal, calibração ou integração. Ausência de corpus opcional e limitação da auditoria temporal continuam declaradas; não são novos bloqueios. A menção a “26 casos” no texto da entrega é um erro de contagem não bloqueante: são 24, corretamente discriminados ao final do próprio documento.

## Validação independente

Comandos no workspace de produto, com `.venv` local:

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q tests/unit/test_m2_2_metric_population.py tests/unit/test_m2_1_reference_eligibility.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pyright
.venv/Scripts/python.exe -B docs/submilestones/M2.2/independent-rereview-probes.py.txt
```

Resultados: **119 passed, 2 skipped** nos testes locais M2.1+M2.2; Ruff lint passou, 331 arquivos já formatados; Pyright 0 erros/avisos; **50 verificações independentes passaram**. O script das sondagens está preservado ao lado deste relatório. O script antigo permanece intacto como evidência histórica do defeito anterior.

A suíte ampla foi reiniciada após a retomada solicitada, pois a sessão anterior não estava mais disponível para recuperar o resultado final. Com os cinco placeholders públicos do CI, comando:

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q
```

Resultado amplo independente: **1 failed, 2690 passed, 47 skipped, 1 deselected, 337 warnings**, em 217,50 s; 2 snapshots passaram. Única falha: `tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`, já documentada na baseline M2.1 (orquestrador). Os totais coincidem com a entrega corrigida; não surgiu regressão adicional na suíte. Log preservado em `independent-rereview-suite.log.txt`; saída Pyright em `independent-rereview-pyright.log.txt`. O comando pytest falhou, como esperado pela dívida conhecida; o status do shell após exibir o log não é usado como status de aprovação dos testes.

Nenhuma implementação, teste permanente, SPEC ou evidência do executor foi corrigida pela revisão. Foram acrescentados somente relatório, sondagens e logs independentes. M2.3 não iniciada.
