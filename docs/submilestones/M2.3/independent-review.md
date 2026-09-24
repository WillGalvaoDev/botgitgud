# M2.3 — independent review — 2026-09-22

**REQUIRES_CHANGES**. Macro M2 permanece aberto. Nenhuma correção implementada; M3 não iniciada.

Routing: **Opus primeiro**, para resolver R1 (contradição normativa); **Sonnet**, para implementar a decisão resultante e completar as regressões/evidências de R2. Não foi identificada decisão que exija autoridade humana externa aos papéis do workflow.

## Objeto e autoridades

Workspace `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`, HEAD `b6241df` (fechamento M2.2), entrega M2.3 não commitada. Autoridades: M0, SPEC M1, SPECs M2.1/M2.2 fechadas, roadmap e milestone-workflow. SPEC M2.3 v001 revisada integralmente.

SHA-256:

| Arquivo | Hash |
|---|---|
| `docs/m2-3-specification.md` | `f516d18cb77cf1e058cc1280b99771137b7409f84fd340393645f62049ae3588` |
| `src/botgitgud/analysis/pipeline.py` | `0d3a407e785efc9061cc21729e67331c6fd3acce86427e4b6374abfd997f7ed4` |
| `src/botgitgud/analysis/cohort_match.py` | `fc77aea6e5ec990607af47999ca79f88fe7b427a5fd250fa15dfada3287c0b8f` |
| `tests/unit/test_m2_3_comparability_integration.py` | `c5d1397727be4dc12d254ab157781d110b01add2aad96e46463e889043d23ee4` |

## R1 — bloqueante: determinismo contradiz preservação irrestrita da higiene

Contrato afetado: SPEC §8.6 exige que permutar logs buscados não altere `AnalysisResult.comparability` nem seu JSON. Contudo, §2, §4.1 e §8.2 proíbem mudar a deduplicação e exigem saída de `match_cohort` idêntica à anterior para qualquer entrada.

Em `cohort_match.py:157`, `min` escolhe o primeiro candidato quando a chave `(dedup_priority, player_identity)` empata. Duas representações do mesmo pull/jogador podem empatar nessa chave e divergir nos dados disponíveis. A extração preservou esse comportamento anterior; a nova integração não elimina a ambiguidade.

Contraexemplo reproduzido pelo **run_analysis real**, substituindo somente aquisição por logs sintéticos:

- Alvo Warlock/Demonology, duração 300, partição 4.
- Duas referências com ID `REPORT:501:UptimeDuplicate`, mesma classe/spec, partição, duração e prioridade de deduplicação; uma sem uptime e outra com `uptime=0.5`.
- Ordem ausente→observado: ledger N=1, `aura_uptime_fraction:1` N=0.
- Ordem observado→ausente: ledger N=1, `aura_uptime_fraction:1` N=1.
- `comparability` e JSON persistível diferem. Não há erro de colisão em M2.2 porque a higiene já descartou uma representação.

Variante adicional com classe divergente no mesmo ID muda ledger N de 1 para 0 e o estado contábil de `INSUFFICIENT_REFERENCES` para `NO_REFERENCES`.

Script e resultados: `independent-probes.py.txt` e `independent-probes.log.txt`. Comportamento afetado: deduplicação de candidatos, seleção por métrica e proveniência publicada/persistida. A interface contratada aceita candidatos com duplicação e não define pré-condição que exclua esse empate divergente.

**Resolução requerida:** Opus deve reconciliar explicitamente a invariância de permutação com a preservação da higiene para esse caso. O executor não está autorizado a inventar desempate, escolher representação por elegibilidade ou restringir silenciosamente as entradas. Depois da decisão, regressão permanente deve exercitar a integração nas duas ordens. Trata-se de contradição normativa concreta, roteada conforme workflow item 5, não de exigência de robustez universal.

## R2 — bloqueante: AC6 não tem as provas integradas contratadas

SPEC §9 exige oráculos independentes e cenários específicos em testes permanentes. A entrega atual não os demonstra integralmente:

1. **§9.2/AC1:** `test_metric_population_size_differs_from_ledger_size` (linha 188) chama `_stage`, não `run_analysis`, e só verifica populações maiores. Não verifica uma métrica maior e outra menor que o ledger no mesmo replay, nem IDs/valores efetivamente consumidos por `MetricComparison`, contrato e renderização. `_stage` ainda usa `min_n=8`, diferente do `COHORT_TARGET_N` usado em produção. A tabela de evidência reporta 10 candidatos/ledger 9, mas o teste constrói 9 candidatos/ledger 8; a coluna “antes” não é uma execução do matching anterior.
2. **§9.3/AC3:** `test_run_analysis_ledger_insufficient_setup_present_contract_and_render_safe` (linha 369) omite a resposta `report_rankings`. A instrumentação independente confirmou **0 elegíveis**, todos com `PARTITION_UNKNOWN`, e **N=0 nas seis métricas**. O teste não prova ledger insuficiente com uma métrica suficiente; não é o cenário anunciado de insuficiência causada apenas pela banda de duração. A presença de texto sem `Traceback` também não comprova ausência de números de comparação não computada.
3. **§9.2/AC1:** em `test_class_mismatched_reference_stays_out_of_ledger_and_every_metric_population` (linha 165), o alvo é Warlock/Demonology, mas todas as nove referências chamadas `compatible` usam o default Mage/Fire do construtor. Confirmado independentemente: N elegível=0. O teste pode passar com exclusão de todos e não comprova seleção positiva/negativa integrada.
4. **§9.4:** o teste da guarda aspiracional (linha 230) copia a lógica do pipeline para dentro do teste. Não chama nem intercepta a guarda de produção; remover a guarda real não faria esse teste falhar.
5. **§9.1/§9.6/§9.8:** a equivalência compara o wrapper novo com sua própria composição; a permutação compara `_stage`, não proveniência completa de `run_analysis`; o teste de populações diferentes não calcula a identidade contábil alegada na evidência. As regressões gerais M1 são úteis, mas não substituem a decomposição não trivial no replay integrado exigido.

**Resolução requerida:** completar os testes permanentes e corrigir as tabelas/afirmações de evidência para os cenários já exigidos pela SPEC. Não é necessário nova coleta, novo seletor ou redesign. As sondagens independentes abaixo demonstram que os cenários básicos podem passar, mas não substituem as regressões permanentes expressamente contratadas.

## Resultados positivos independentes

`independent-probes.py.txt` executa `run_analysis`, com Store temporário e aquisição substituída, mantendo seleção, cálculos, contrato, manifest e renderização reais:

| Cenário | Ledger | Métricas | Resultado |
|---|---:|---|---|
| Populações distintas, com uma referência de classe incompatível | 30 | DPS/eventos: 16; uptime/casts: 34 | IDs do cálculo coincidem com proveniência; referência incompatível excluída; contrato e JSON preservados; CLI/Discord renderizam |
| Mesmo cenário com ordem invertida e IDs únicos | 30 | mesmas populações | Proveniência e JSON idênticos |
| Referências com duração 390 vs. alvo 300 | 0 | DPS/uptime: 16, com grade; casts: 0 | Análise retorna, setup presente, ledger insuficiente não bloqueia a métrica suficiente |
| Duplicata com uptime divergente, duas ordens | 1 | uptime: 0 ou 1 | **R1 reproduzido** |

No cenário misto, delta contábil total = `-333.3333333333333 DPS`; soma das parcelas por habilidade + suporte + residual coincide com o total sobre os IDs aceitos. Encode/decode da proveniência do manifest é exato; `ConfidenceSummary.comparability` preserva o objeto. As verificações não alteram arquivos de produção nem dados históricos.

Comparação adicional contra o **código anterior real**, carregado de `git show HEAD:src/botgitgud/analysis/cohort_match.py`: 36 combinações de políticas v1/v2, tamanhos 0/7/8/15/30/35 e pisos 8/15/25, com self, wipe, duplicatas de pull e jogador. Listas e todos os campos de `MatchReport` coincidem. Isso sustenta a preservação da higiene e também confirma por que R1 não pode ser resolvido por mudança silenciosa do refactor.

## Acceptance criteria e macro M2

| Critério | Conclusão |
|---|---|
| AC1 | Roteamento correto nas sondagens integradas; provas permanentes incompletas em R2. |
| AC2 | Round-trips e propagação passam; invariância obrigatória de proveniência sob permutação falha em R1. |
| AC3 | Ledger vazio com métrica suficiente funciona na sonda; teste permanente exigido não demonstra esse caso. |
| AC4 | Suíte M0/M1 e fechamento contábil não trivial da sonda passam; falta a prova permanente integrada alegada no pacote. |
| AC5 | M2.1/M2.2 preservados; refactor equivalente nos casos conferidos; conflito com §8.6 requer Opus, não alteração unilateral. |
| AC6 | **Não atendido: R2.** |

M2.1 e M2.2 têm fechamentos independentes documentados e commits `edba6e6`/`b6241df`. A pré-condição de unidades locais está satisfeita. O workflow e o roadmap exigem fechamento independente de M2.3 para fechar o macro; R1/R2 impedem esse fechamento. Portanto **macro M2 não fechado**. Nenhum trabalho M3 foi iniciado.

## Verificações, comandos e limitações

Ambiente: Windows, `.venv` local do produto. Comandos independentes:

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q tests/unit/test_m2_3_comparability_integration.py tests/unit/test_cohort_match.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pyright
.venv/Scripts/python.exe -B docs/submilestones/M2.3/independent-probes.py.txt
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q
```

- Foco M2.3 + matching: **46 passed, 3 skipped**.
- Ruff passou; 333 arquivos já formatados; Pyright: 0 erros/avisos.
- Suíte ampla com os cinco placeholders públicos CI: **2706 passed, 47 skipped, 1 deselected, 1 failed**, 337 warnings, 281,58 s; 2 snapshots passaram. Única falha: `test_no_raw_print_calls_anywhere_in_src`, dívida preexistente do orquestrador. Não usada como novo bloqueio.
- Logs: `independent-suite.log.txt`, `independent-pyright.log.txt`, `independent-probes.log.txt`.
- O snapshot golden passa a refletir ausência de referências compatíveis por partição; a remoção de conteúdo está explicada pela entrega. Isso reduz a cobertura real de conteúdo, sem autorizar coleta nova ou restaurar comparações incompatíveis.
- Corpus opcional ausente e dívida editorial ledger-N versus métrica-N continuam declarados; não ampliam o escopo desta revisão.

Somente artefatos independentes de revisão foram acrescentados. Nenhuma implementação, SPEC ou teste permanente foi corrigido.
