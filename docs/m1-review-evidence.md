# M1 — evidências da árvore corrigida

> Estado vigente: **MILESTONE_CLOSED**, conforme o
> [registro de fechamento humano](m1-closure.md) de 2026-09-13. As declarações de
> espera por revisão e de M1 aberta abaixo são registros históricos das rodadas
> anteriores, preservados com suas evidências; não descrevem o status atual.

Autoridade: [m1-specification.md](m1-specification.md). Escopo: somente M1.
**Correções dos 12 grupos implementadas e validadas; aguardando nova revisão independente.**
Esta página substitui a declaração anterior, invalidada pela revisão independente.
Não declara fechamento da M1 nem autoriza M2.

## Preservação e prova anterior à correção

A [revisão preservada](reviews/m1-independent-2026-09-13/README.md) contém os
contraexemplos originais, seus hashes e a correspondência entre os 12 grupos do
pedido e a numeração da revisão. Os artefatos foram copiados do diretório temporário
antes de editar a implementação.

As 25 instâncias iniciais falharam por asserção do contrato violado, antes das
correções: [JUnit anterior](reviews/m1-independent-2026-09-13/regressions-before.xml),
[saída anterior](reviews/m1-independent-2026-09-13/regressions-before.log.txt),
[exit 1](reviews/m1-independent-2026-09-13/regressions-before-exit.txt).
O conjunto foi ampliado para 38 instâncias permanentes em
[test_m1_required_changes.py](../tests/unit/test_m1_required_changes.py), incluindo
controles positivos, Parquet, fetch integrado, CLI/Discord e abstenção por estatística.

## Árvore e reprodução

HEAD de base: `8aaf4a3e19326106d75a209b035c348b1121abcb`, com alterações no worktree.
Fingerprint dos 978 arquivos da validação final:
`a770ff55629ad5cb159adc7a5b77bd19e07ad32b196fee6c92c182e392151857`.

Receita: arquivos sob `src`, `tests` e `scripts`, excluindo bytecode e os diretórios
ignorados `_record_scratch*`, mais `pyproject.toml` e `spells.json` da raiz. Ordenar
caminhos relativos POSIX; acumular SHA256 com o caminho UTF-8 e o digest SHA256
binário do conteúdo de cada arquivo. Documentos e artefatos gerados recebem hashes
próprios no audit final, fora desse fingerprint.

A suíte e o gerador usam a cópia isolada
`C:/Users/wsgon/AppData/Local/Temp/m1-final-verified-qsc3qd6c/`, com os arquivos
versionáveis da árvore atual, `data/raw`, `data/spells.json` e metadados Git locais.
Não foram copiados Store de produção ou credenciais reais. O `.env` contém apenas
valores fictícios para os cinco campos obrigatórios; `PYTHONPATH` aponta para o
`src` da cópia. Pyright roda no workspace original, com o venv instalado, após
comprovação de igualdade de bytes com a cópia. Isso evita resolver duas cópias do
pacote instalado em modo editable.

Cada registro em `docs/m1-validation/*.json` contém comando, diretório, início/fim
UTC, duração, exit code e fingerprint; stdout/stderr ficam em `*.log.txt`.

```powershell
python -m pytest -o addopts='' -m 'not network' -q --tb=short tests/unit/test_m1_required_changes.py
python -m pytest -o addopts='' -m 'not network' -q --tb=short tests/unit/test_m0_methodology_contract.py tests/unit/test_damage_scope.py tests/unit/test_dps_gap.py tests/unit/test_m1_acceptance.py tests/unit/test_m1_algebra_properties.py tests/unit/test_m1_astra_regressions.py tests/unit/test_m1_feature_contract.py tests/unit/test_m1_persistence_contract.py tests/unit/test_m1_required_changes.py tests/unit/test_m1_review_round2.py
ruff check src tests scripts
python -m pyright
python -m pytest -o addopts='' -m 'not network' -q --tb=short --junitxml=docs/m1-suite-final.xml
python scripts/m1_review_evidence.py --output docs/m1-evidence-current.json.gz
```

## Correções dos 12 grupos

| Grupo | Contrato corrigido e cobertura permanente |
|---|---|
| 1 | Cast válido no mesmo ID e intervalo é necessário à elegibilidade; cast negativo, fora da luta, NaN ou infinito não chega a prioridade/CLI/Discord |
| 2 | Uptime mantém seu scalar independente, mas findings, remediação e materialidade exigem entidade elegível; o controle positivo permite coaching de uptime sem déficit de dano |
| 3 | Ambos os paginadores exigem chave de cursor, intervalo válido e timestamps no intervalo da página; vazio terminal explícito continua COMPLETE; fetch integrado preserva PARTIAL |
| 4 | Accounting valida intervalo/cobertura e coerência da reconciliação persistida, inclusive após Parquet; o V1 histórico sem proveniência mantém tratamento separado |
| 5 | Reconciliação com a autoridade WCL usa igualdade exata; a tolerância algébrica não a substitui |
| 6 | Payload de uptime ausente/null não produz zero; zero explícito continua mensurável |
| 7 | Features/core e perfil de casts respeitam a validação compartilhada; indisponibilidade de casts/uptime não apaga dano elegível |
| 8 | Cabeçalho e conclusão transportam a comparação de dano; uptime transporta sua própria comparação; N de dano, matching e posição são distintos; N<8 bloqueia comparação pública e N<15 não produz grade/prioridade |
| 9 | D=0 com hits>0 preserva eventos/s e dano/evento, sem habilitar por isso o scalar de DPS por habilidade |
| 10 | Soma, normalização, percentuais, componentes e estatísticas não finitos produzem abstenção explícita; nenhuma quantidade inválida vira zero ou prioridade |
| 11 | Pares válidos usam peso original 1/N; unclassified recebe apenas pares sem split; erros de fechamento bloqueiam a comparação; oráculo racional verifica valores independentemente |
| 12 | `AbilityGap` não produz campos legados de dano/count/produto/share; `Finding` não tem `estimated_gain_pct`; `DpsGapReport` usa `gap_vs_reference_pct` na unidade normativa; consumidores usam componentes em DPS diretamente |

## Matriz A01–A25

Referências abaixo são testes permanentes executados pelos comandos registrados.
Integração e replay também fazem parte da suíte offline completa.

| Critério | Evidência principal |
|---|---|
| A01 | `test_m1_acceptance::test_a01_duration_normalization_has_zero_frequency_delta`; D01 de M0 |
| A02 | `test_m0_methodology_contract::test_m0_separate_medians_do_not_explain_overall_gap` |
| A03 | `test_m1_acceptance::test_a03_a04_accounting_and_support_close`; D06 sem referência fictícia |
| A04 | Mesmo teste A03/A04, com suporte como linha separada |
| A05 | Oráculo antes/depois do gate e permutação/exclusões em `test_m1_astra_regressions` |
| A06 | Oráculo com IDs ausentes/hits=0; ausência de mecanismo sem scalar/prioridade |
| A07 | Valores independentes de cada componente em A07, mutação 15/8 rejeitada e oráculo racional |
| A08 | Pet preservado no ledger; U01/U02 e controles de coaching em CLI/Discord |
| A09 | D03 de M0: déficit uniforme visível pelo scalar próprio de DPS |
| A10 | `test_m1_algebra_properties::test_a10_other_damage_cannot_change_own_dps_casts_or_uptime` |
| A11 | D04 de M0: share não vira ordinal de usos |
| A12 | D02 de M0 e `test_a12_new_aggregation_does_not_calculate_targets_per_cast` |
| A13 | `test_m1_feature_contract::test_a13_retired_feature_never_reaches_fitted_columns` |
| A14 | U03, streams vazios válidos, erros/cursor/budget, respostas malformadas e fetch integrado |
| A15 | V1 histórico, legacy/unreconciled e ausência de proveniência em testes de persistência/availability |
| A16 | Inputs inválidos, zero líquido, percentuais indisponíveis e U10 |
| A17 | Oráculo antes/depois do gate com ID desconhecido, suporte, interação e linhas omitidas |
| A18 | U08, N próprio por métrica, N=8 descritivo e N<15 insuficiente para grade |
| A19 | `test_m1_persistence_contract`: migração repetida, round-trip e versões até disco; U04/U05 |
| A20 | `test_pipeline`, `test_complete_analysis_integration`, testes de worker/entrega e U01/U08 |
| A21 | `test_damage_scope::test_gate1_probe_reconciles_all_twenty_players_exactly` |
| A22 | `test_m1_acceptance::test_a22_arithmetic_mean_not_median` |
| A23 | Referência aspiracional separada em `test_dps_gap` e exclusão de membro inválido em `test_m1_astra_regressions` |
| A24 | U06 e U08; ausência/zero explícito e população própria de uptime |
| A25 | `test_m1_feature_contract`: valor/schema alteram hash, ordem não; versão vem do dataset |

## Oráculo, corpus e limites

O oráculo de `test_m1_algebra_properties.py` calcula valores esperados com
`fractions.Fraction`, sem chamar accounting, split ou tolerância da implementação.
Verifica cada componente com 1/N, suporte, união de IDs e soma exibida+outras.
Há controles não vazios para split válido, pares sem split e linha omitida. A07
também fixa numericamente os quatro componentes; a mutação 15/8 e um erro de
2e-8 DPS demonstram que fechamento aparente não basta.

A tolerância é `max(1e-9, 1e-12 * scale)`, com a escala normativa dos termos
comparados. Residual pode superar 1e-9 em escala grande sem violar o contrato.
Não há tolerância relativa padrão de `pytest.approx` ampliando a prova do oráculo.

O gerador final registra censo de estados/razões, N e exclusões por métrica/ID,
D01–D06, exemplos de CLI/Discord e resíduos. O corpus histórico não prova cobertura
de casts e não foi regravado ou usado para backfill. O hash histórico citado na
especificação continua sem equivalência demonstrada: não se declara preservação
de bytes entre aquele snapshot e este corpus. A preservação verificada nesta
execução é a igualdade de hashes antes/depois da leitura do corpus atual.

Permanecem fora de M1: matching/hotfixes e compatibilidade refinada (M2), cobertura
completa dos streams de auras/recursos, associação real a instâncias de cast (M4) e
causalidade de rotação. Nenhum treinamento, backfill ou implementação de M2 foi iniciado.

## Resultados finais

| Validação | Resultado da árvore final | Registro |
|---|---|---|
| Regressões dos 12 grupos | 38 passed, exit 0 | [comando](m1-validation/regressions.json), [saída](m1-validation/regressions.log.txt), [JUnit](m1-validation/regressions.xml) |
| Seleção A01–A25 e contratos M0/M1 | 157 passed, exit 0 | [comando](m1-validation/acceptance.json), [saída](m1-validation/acceptance.log.txt), [JUnit](m1-validation/acceptance.xml) |
| Ruff | All checks passed, exit 0 | [comando](m1-validation/ruff.json), [saída](m1-validation/ruff.log.txt) |
| Pyright | 0 errors, 0 warnings, 0 informations, exit 0 | [comando](m1-validation/pyright.json), [saída](m1-validation/pyright.log.txt) |
| Suíte offline completa | 2569 passed, 1 deselected, 337 warnings; 2 snapshots passed; exit 0; 595,95 s | [comando](m1-validation/suite.json), [saída](m1-validation/suite.log.txt), [JUnit](m1-suite-final.xml) |
| Gerador de evidência | exit 0; 500 casos do oráculo independente, 4980 pares com split e 502 linhas omitidas | [comando](m1-validation/evidence.json), [saída](m1-validation/evidence.log.txt), [JSON gzip](m1-evidence-current.json.gz) |

A suíte completa terminou em `2026-09-13T23:40:28.827459+00:00`. O teste excluído
é marcado `network`; nenhuma chamada externa é necessária para estes resultados.
Os avisos estão preservados na saída completa, não foram tratados como falhas.
O [runner preservado](m1-validation/runner.py.txt) registra os argumentos exatos,
incluindo `addopts=` vazio; seus caminhos locais devem ser adaptados ao reproduzir
em outra máquina. Os testes que abrem Store devem usar a cópia isolada descrita acima.

O gerador terminou em `2026-09-13T23:43:12.599172+00:00`. Leu 1361 Parquets,
672 pulls e 869 identidades de jogador: 1274 `PARTIAL/LEGACY_UNSCOPED_SUBTOTAL`,
37 `UNKNOWN/UNRECONCILED_DAMAGE_SCOPE` e 50 `AVAILABLE/LEGACY_RECONCILED_TOTAL`.
Destes últimos, 47 têm comparação pública suficiente. `INVALID` e
`NOT_APPLICABLE` são exercitados nos testes; não são estados encontrados nesse
censo de accounting histórico.

As distribuições abaixo têm 15968 observações por métrica: uma por ID na união
do jogador elegível para accounting com seu pool. Não representam 15968 jogadores
independentes. Cada N conta somente referências disponíveis para aquela métrica/ID.

| Métrica | N mínimo | Mediana de N | P95 de N | N máximo |
|---|---:|---:|---:|---:|
| DPS bruto por habilidade | 0 | 0 | 17 | 46 |
| Casts/min | 0 | 0 | 0 | 0 |
| Eventos de dano/s | 0 | 0 | 0 | 0 |
| Dano/evento | 0 | 0 | 0 | 0 |
| Uptime de aura | 0 | 3 | 47 | 82 |
| Share bruto (%) | 0 | 0 | 17 | 46 |

N=0 nos streams históricos sem cobertura demonstrada é abstenção deliberada.
O JSON contém IDs, valores, exclusões, estados e exemplos de entrega; uptime não
herda a população de dano. O maior residual real foi `5.0652870786649373e-11` DPS
em 50 comparações; o maior residual sintético foi `7.450580596923828e-9` DPS em
500 casos, todos dentro da tolerância normativa calculada em sua própria escala.

Hash do corpus atual, idêntico antes/depois:
`983b0a3ed9294925e70c60da62c871da87d629261d3d9c4568e67c25cd426926`.
SHA256 de `data/spells.json`:
`9c9fb070beb3b4919f624615e4ec6ef37ac25742411190f63fe50ae69a294d5f`.

A [auditoria final](m1-final-evidence-audit.json) verifica os seis registros contra
o fingerprint atual, igualdade de bytes da cópia, hashes da revisão preservada,
JUnit vermelho/verde por grupo e testes executados para A01–A25. Ela comprova o
vínculo dos artefatos, sem substituir a revisão normativa independente.
Reprodução da auditoria, a partir da raiz:

```powershell
python docs/m1-validation/audit.py.txt C:/Users/wsgon/AppData/Local/Temp/m1-final-verified-qsc3qd6c
```

A M1 permanece aberta para revisão independente. M2 não foi iniciada.
