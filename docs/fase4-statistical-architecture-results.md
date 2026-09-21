# Resultados da primeira avaliação de arquitetura estatística (SAE)

**Data:** 2026-08-21
**Campanha:** `exp-840b1ef99d76c33c8a0b` (offline; zero chamadas WCL nesta rodada)
**Execução:** `botgitgud experiment-evaluate --campaign exp-840b1ef99d76c33c8a0b`
**run_id:** `eval-96cec267d3d64a31`
**dataset_status:** **PARTIAL** — 603 de 1.200 observações planejadas (50,25%)

Este documento reporta a primeira execução real da matriz A–D × S1–S5 × F1/F2 ×
{Baseline 0, Baseline 1, LightGBM} contra os dados já coletados. É uma leitura
**preliminar** de uma campanha parcial — não é validação final da Fase 4 e não substitui o
gate de 5.000 (`docs/fase4-data-acquisition-plan.md` §10.3).

---

## 1. Executive summary

Existe sinal preditivo real e mensurável em `MODEL_GLOBAL`: LightGBM com o conjunto de
features F2 bate Baseline 0 em MAE e Spearman em todo split onde pôde ser avaliado (S1, S3,
S4, S5), com intervalo de bootstrap que exclui o baseline. A melhor célula medida
(S5, `MODEL_GLOBAL`, F2, LightGBM) obteve **MAE 17,95 [IC 16,01–19,90]** e
**Spearman 0,649 [IC 0,56–0,73]** sobre 217 observações de validação. `MODEL_TARGET`
(granularidade A) é **inteiramente não-avaliável** nesta rodada — nenhum dos 198 grupos
por target atinge sequer o limiar de sensibilidade mais frouxo testado (10 linhas de
treino); o maior grupo por target no dataset parcial tem 8 observações. Isso significa que
a comparação-âncora do protocolo (§13 do documento de arquitetura: degradação de B/C/D
*relativa* a A) **não pode ser respondida ainda**, independentemente do resultado de
B/C/D. Classificação desta rodada: **SUFFICIENT_SIGNAL** (existe sinal, ele sobrevive a
hold-outs de spec/encounter), mas isso não resolve a pergunta de granularidade — ver §23.

---

## 2. Dataset utilizado

| Campo | Valor |
|---|---|
| campaign_id | `exp-840b1ef99d76c33c8a0b` |
| dataset_fingerprint | `ds-1920e6a79ac4d20ea560` |
| planned (frozen plan) | 1.200 |
| available (`status=completed`) | **603** |
| feature_schema_version | `sae3-v1` |
| dependency_versions | scikit-learn 1.9.0, lightgbm 4.7.0 |
| seed | `20260821` (fixo em toda a execução) |
| specs | 25 |
| encounters | 9 |
| Phase4Targets cobertos | 198 (de 213 no plano congelado) |

Distribuição por faixa de rankPercent (acumulada, não desta rodada):

| 00-20 | 20-40 | 40-60 | 60-80 | 80-100 |
|---:|---:|---:|---:|---:|
| 128 | 106 | 116 | 124 | 129 |

---

## 3. Limitações da campanha parcial

O plano congelado tem 1.200 observações; 597 continuam `pending`. Todo resultado abaixo
usa apenas as 603 `completed`. Nenhum número aqui deve ser lido como se already
representasse a campanha completa — em particular, a granularidade mais afetada
(`MODEL_TARGET`) é justamente a que mais precisa de volume por grupo, e é a que mais sofre
com o corte pela metade.

---

## 4. Feature contract

- **F1 (`f1_controllable_only`)**: apenas features `CONTROLLABLE` (comportamento do
  jogador: `c_active_time_pct`, `c_deaths`, `c_total_casts`, etc.).
- **F2 (`f2_full_covariates`)**: F1 + `CONTEXT` + `NON_CONTROLLABLE` (item level, tier
  pieces, duração, raid size, buffs externos, augmentation, encounter/difficulty/partition
  numéricos).
- `alignment_score` e `total_parses` confirmados ausentes de ambas as famílias
  (`EXCLUDED_LEAKAGE_COLUMNS`, testado em `test_experiment_models.py`).
- **Limitação medida, não escondida**: o registro de features declara `ctx_class` e
  `ctx_spec` como `CONTEXT`/categóricas, mas `experimental_dataset.build_features` não os
  materializa como colunas numéricas — só `ctx_encounter_id/difficulty/partition` existem
  de fato no dataset. `MODEL_GLOBAL`, portanto, **não tem nenhuma representação direta da
  identidade da spec** nesta rodada; qualquer capacidade de distinguir specs vem
  indiretamente de covariáveis correlacionadas (ilvl, duração, etc.), nunca de uma
  categoria de spec explícita. Isso é exatamente a limitação que o brief pediu para expor,
  e nenhuma codificação categórica foi adicionada para escondê-la.
- Uma coluna candidata só entra no espaço de features de um grupo se tiver variância
  não-nula **no fold de treino daquele grupo** (`FittedFeatureSpace.fit`) — nunca decidido
  pela validação.

---

## 5. Splits

S1/S2 priorizados conforme pedido; S3/S4/S5 executados como *leave-one-out*: uma dobra por
encounter presente (S3, 9 dobras), uma por spec presente (S4, 25 dobras) e uma por
combinação spec×encounter presente com as duas dimensões vistas separadamente (S5, até 213
dobras candidatas). Todas as dobras de uma mesma célula são fundidas num único
`evaluate_split` — a métrica reportada é sobre a união de todas as observações de
validação de todas as dobras daquela (granularidade, split, família, modelo).

**S2 produziu números idênticos a S1 em toda célula.** Isso significa que nenhuma
observação precisou ser descartada por `report_code`/`player_name` compartilhado entre
treino e validação — o sinal de S1 não é explicado por memorização de indivíduo ou report
específico.

**Ressalva sobre seen/unseen em S3/S4/S5** (leave-one-out multi-dobra): como o mesmo
target pode estar no lado de treino em uma dobra e no lado de validação em outra,
`seen_targets`/`unseen_targets` (agregado por `trained_target_ids` ao longo de *todas* as
dobras da célula) tende a marcar quase tudo como "visto" mesmo quando, dobra a dobra, a
combinação específica nunca apareceu junto. Essa distinção seen/unseen é confiável para
S1/S2 (uma única dobra); para S3/S4/S5 ela é menos informativa do que a comparação
dobra-a-dobra em si, que é o que realmente testa H3/H4.

---

## 6. Baselines

| Papel | Implementação |
|---|---|
| Baseline 0 | mediana do treino (`MedianBaseline`, já existente) |
| Baseline 1 | `sklearn.linear_model.LinearRegression`, sem regularização, sem tuning |
| LightGBM | `LGBMRegressor`, config conservadora fixa: `n_estimators=200, max_depth=4, num_leaves=15, learning_rate=0.05, min_child_samples=5, n_jobs=1, deterministic=True` |

`min_child_samples=5` (abaixo do default 20) é necessário porque a maioria dos grupos
experimentais aqui é pequena; com o default, LightGBM se recusaria a dividir quase todo nó
e degeneraria silenciosamente para prever a média.

---

## 7. Resultados MODEL_PER_TARGET (A)

**36/36 células NOT_EVALUABLE** (5 splits × 2 famílias × 3 modelos, MODEL_TARGET x S1 e S2
contam 6 cada, S3/S4/S5 idem — total 30 na matriz default, todas sem nenhum grupo
elegível). Nenhum dos 198 grupos por target atinge `min_train_rows_per_group=20`, nem
mesmo o limiar de sensibilidade mais frouxo testado (10). Ver §15 para a tabela completa.

---

## 8. Resultados MODEL_PER_SPEC (B)

S1 (temporal, prioridade desta rodada):

| Família | Modelo | n | MAE | IC MAE | Spearman | IC Spearman |
|---|---|---:|---:|---|---:|---|
| F1 | Baseline 0 | 75 | 25.11 | [21.76, 28.89] | 0.031 | [-0.20, 0.26] |
| F1 | Baseline 1 | 75 | 44.91 | [27.20, 73.02] | 0.452 | [0.24, 0.62] |
| F1 | LightGBM | 75 | 27.28 | [23.24, 31.55] | 0.265 | [0.07, 0.45] |
| F2 | Baseline 0 | 75 | 25.11 | [21.76, 28.89] | 0.031 | [-0.20, 0.26] |
| F2 | Baseline 1 | 75 | 71.98 | [48.24, 100.81] | 0.378 | [0.16, 0.57] |
| F2 | LightGBM | 75 | 27.05 | [22.72, 31.67] | 0.218 | [0.01, 0.43] |

**Nenhum modelo bate Baseline 0 simultaneamente em MAE e Spearman aqui** — Baseline 1 e
LightGBM melhoram Spearman claramente, mas pioram MAE (a mediana por spec é, neste
dataset parcial, um preditor pontual difícil de vencer em erro absoluto com grupos de
~20-30 linhas de treino). `MODEL_SPEC × S4` é **NOT_EVALUABLE por construção**: a spec
retida fica com zero linhas de treino sob essa mesma granularidade.

---

## 9. Resultados MODEL_PER_ENCOUNTER (C)

S1: LightGBM bate Baseline 0 nas duas famílias — F1 MAE 25.04 vs 28.24 (Spearman 0.320 vs
0.107), F2 MAE 21.98 vs 28.24 (Spearman 0.520 vs 0.107). Baseline 1 colapsa em F2:
**MAE 187,59** — ver §11. `MODEL_ENCOUNTER × S3` é **NOT_EVALUABLE por construção**
(o encounter retido some do treino desta mesma granularidade).

---

## 10. Resultados MODEL_GLOBAL (D)

A granularidade com mais sinal e a única com todos os 5 splits avaliáveis:

| Split | Família | Modelo | n | MAE | IC MAE | Spearman | IC Spearman |
|---|---|---|---:|---:|---|---:|---|
| S1 | F2 | Baseline 0 | 152 | 27.19 | [24.71, 29.71] | n/d | — |
| S1 | F2 | Baseline 1 | 152 | 23.33 | [20.89, 25.82] | 0.391 | [0.25, 0.53] |
| S1 | F2 | LightGBM | 152 | 20.72 | [18.22, 23.12] | 0.509 | [0.38, 0.62] |
| S3 | F2 | LightGBM | 603 | 21.05 | [19.65, 22.37] | 0.507 | — |
| S4 | F2 | LightGBM | 603 | 19.34 | [18.20, 20.50] | 0.593 | — |
| S5 | F2 | LightGBM | 217 | **17.95** | [16.01, 19.90] | **0.649** | [0.56, 0.73] |

Degradação de S1→S3 (encounter não visto): +1,6% MAE, Spearman praticamente igual.
S1→S4 (spec não vista): **melhora** (MAE -6,7%, Spearman +16,5%) — sem sinal de colapso.
S5 (spec×encounter simultaneamente não vistos) é o melhor resultado da matriz inteira.

---

## 11. F1 (controllable-only) vs F2 (full covariates)

| Granularidade/Split | Modelo | MAE F1 | MAE F2 | Δ |
|---|---|---:|---:|---:|
| MODEL_GLOBAL / S1 | LightGBM | 24.26 | 20.72 | -14,6% |
| MODEL_GLOBAL / S5 | LightGBM | 21.58 | 17.95 | -16,8% |
| MODEL_ENCOUNTER / S1 | LightGBM | 25.04 | 21.98 | -12,2% |
| MODEL_GLOBAL / S1 | Baseline 1 | 23.69 | 23.33 | -1,5% |
| MODEL_SPEC / S1 | Baseline 1 | 44.91 | 71.98 | **+60,3%** |
| MODEL_ENCOUNTER / S1 | Baseline 1 | 71.27 | 187.59 | **+163,2%** |

Com LightGBM, F2 sempre ganha de F1 — contexto não-controlável (ilvl, duração, raid size,
buffs externos) carrega sinal incremental real além do comportamento do jogador sozinho.
Com Baseline 1, F2 frequentemente **piora drasticamente**: números de MAE de 71,98 e
187,59 não são "contexto atrapalha" — são instabilidade numérica de OLS sem regularização
sob covariáveis correlacionadas em grupos pequenos (condição da matriz de features medida
diretamente em ~2×10⁵–10⁶ para esses grupos, com predições fora do intervalo [0,100]).
Baseline 1 é exatamente "regressão linear simples" como pedido — nenhuma regularização foi
adicionada para esconder essa instabilidade. **Conclusão honesta**: quanto da capacidade
preditiva vem de contexto depende inteiramente da capacidade do modelo em absorvê-lo;
árvores absorvem F2 de forma limpa, regressão linear crua não.

---

## 12. Temporal generalization (S1/S2)

S2 == S1 em toda célula (ver §5): nenhuma contaminação de report/player precisou ser
removida. Não há degradação temporal aparente além do que S1 já mede — não há razão, com
este dataset, para achar que o desempenho piora especificamente no futuro além do que a
métrica de S1 já captura.

---

## 13. Spec hold-out (S4)

`MODEL_GLOBAL` não colapsa para spec nunca vista — na verdade melhora ligeiramente
(MAE 19,34, Spearman 0,593 vs S1's 20,72/0,509). `MODEL_SPEC × S4` é estruturalmente
NOT_EVALUABLE. `MODEL_ENCOUNTER × S4`: LightGBM F2 MAE 20,66, Spearman 0,575 — também
generaliza bem para spec nunca vista dentro de um encounter fixo.

---

## 14. Encounter hold-out (S3)

`MODEL_GLOBAL` degrada minimamente (MAE +1,6%, Spearman ~igual). `MODEL_ENCOUNTER × S3` é
estruturalmente NOT_EVALUABLE. `MODEL_SPEC × S3`: LightGBM F2 MAE 21,10, Spearman 0,479 —
pior que Baseline 0 em MAE (24,62), consistente com o padrão de §8.

---

## 15. Coverage / insufficient_data

Cobertura geral: **78/120 células (65%)** avaliadas na matriz default (4 granularidades ×
5 splits × 2 famílias × 3 modelos).

| Split | Avaliadas / Total |
|---|---:|
| S1 | 18/24 |
| S2 | 18/24 |
| S3 | 12/24 |
| S4 | 12/24 |
| S5 | 18/24 |

Análise de sensibilidade do limiar `min_train_rows_per_group` (contagem apenas, sem
treinar modelo — sobre o fold de treino de S1):

| Granularidade | limiar=10 | limiar=20 (usado) | limiar=30 | limiar=50 |
|---|---:|---:|---:|---:|
| MODEL_TARGET (198 grupos) | 0 elegíveis | 0 elegíveis | 0 elegíveis | 0 elegíveis |
| MODEL_SPEC (25 grupos) | 21 elegíveis (426 obs) | 11 elegíveis (279 obs) | 2 elegíveis (66 obs) | 0 elegíveis |
| MODEL_ENCOUNTER (9 grupos) | 8 elegíveis (442 obs) | 8 elegíveis (442 obs) | 8 elegíveis (442 obs) | 5 elegíveis (326 obs) |

`min_train_rows_per_group=20` e `min_validation_rows_per_group=5` são constantes
explícitas em `experiment_evaluate.py`, reportadas em todo run, nunca um default
silencioso. A tabela acima mostra que **o limiar não é o problema de MODEL_TARGET**: nem o
piso mais permissivo testado (10) libera um único grupo — o maior grupo por target tem 8
observações no total, abaixo de qualquer limiar razoável.

---

## 16. Bootstrap uncertainty

1.000 reamostragens, seed `20260821`, percentil 2,5–97,5. Toda célula avaliada tinha
n ≥ 75 (bem acima do piso de mensurabilidade `MIN_ROWS_FOR_BOOTSTRAP=10`), então nenhuma
célula ficou marcada "não mensurável" nesta rodada. Exemplos com intervalo mais estreito
(mais confiável) e mais largo (menos confiável) já citados em §8/§10; o padrão geral é que
IC de MAE tem largura de ~15–25% do ponto central, e IC de Spearman tem largura de
~0,15–0,30 — suficiente para separar os modelos vencedores do Baseline 0 (interseção zero
em quase toda célula "vencedora"), mas não suficiente para separar Baseline 1 de LightGBM
com confiança em toda célula.

---

## 17. Resultados por spec

Da melhor célula (S5, MODEL_GLOBAL, F2, LightGBM), specs com mais observações de validação:

| Spec | n | MAE | Spearman |
|---|---:|---:|---:|
| Druid/Balance | 28 | 20.38 | 0.558 |
| DeathKnight/Unholy | 22 | 18.50 | 0.769 |
| Warlock/Destruction | 18 | 18.05 | 0.756 |
| Warrior/Arms | 18 | 17.81 | 0.564 |
| Priest/Shadow | 17 | 25.28 | 0.272 |
| Mage/Arcane | 16 | 11.30 | 0.826 |

Alta variância spec-a-spec (Spearman de 0,27 a 0,83) com n de 16-28 por spec — típico de
amostra pequena, não uma classificação estável de "quais specs o modelo entende melhor".

---

## 18. Resultados por encounter

Mesma célula, por encounter: Spearman entre 0,55 e 0,83 na maioria dos 9 encounters
(n=18–42), exceto os dois com n=5 (3182: 0,40; 3183: 0,82) — ruidosos demais para
confiança individual com este volume.

---

## 19. Resultados por rankPercent bucket

Mesma célula: MAE varia 12,3–24,3 entre buckets; **R² é fortemente negativo em todo
bucket** (-5 a -25). Isso é esperado, não um sinal de ausência de habilidade: dentro de uma
faixa de 20 pontos de rankPercent a variância do alvo já é pequena, então qualquer erro
residual infla o R² negativamente por construção (`ss_tot` pequeno no denominador).
Spearman intra-bucket (0,00–0,35) mede algo diferente — ordenação dentro de uma faixa
estreita, não a tarefa que a métrica global testa — e é fraco por natureza aqui, já que a
maior parte do sinal do modelo vem de separar buckets, não de ordenar dentro de um.

---

## 20. Implicações arquiteturais

**MEASURED RESULT**: `MODEL_TARGET` não tem nenhuma célula avaliável em nenhum split,
família ou modelo com este dataset parcial (0/198 grupos elegíveis mesmo no limiar mais
frouxo testado).

**MEASURED RESULT**: `MODEL_GLOBAL` + LightGBM + F2 bate Baseline 0 em MAE e Spearman em
todo split onde foi avaliável (S1, S3, S4, S5), com degradação mínima ou nula ao mover de
S1 para hold-outs de encounter (S3) e spec (S4), e seu melhor resultado é justamente S5
(as duas dimensões retidas ao mesmo tempo).

**MEASURED RESULT**: `MODEL_SPEC` e `MODEL_ENCOUNTER` não batem Baseline 0
simultaneamente em MAE e Spearman em S1 com nenhum modelo testado.

**INFERENCE**: os resultados são consistentes com H3 e H4 não sendo rejeitadas para a
arquitetura global — o sinal medido não parece depender de memorizar targets específicos.

**INFERENCE**: o colapso de Baseline 1 sob F2 em grupos pequenos é uma propriedade do
modelo linear sem regularização, não evidência de que covariáveis de contexto atrapalham
— LightGBM ganha com F2 em toda comparação equivalente.

**OPEN DECISION**: se a perda de B/C/D relativa a A (o critério central de §13 do
protocolo — MAE ≤ 1,25×, Spearman ≥ 0,8× do modelo por target) é pequena o suficiente para
justificar a granularidade mais geral **não pode ser respondida** — A não tem nenhum ponto
de comparação medido.

**OPEN DECISION**: se `MODEL_SPEC`/`MODEL_ENCOUNTER` merecem mais investimento apesar de
não baterem Baseline 0 em S1, ou se isso é artefato de amostra pequena (grupos de
~20-90 linhas), fica em aberto.

---

## 21. O que as 603 observações conseguem responder

- H0 pode ser considerada rejeitada para `MODEL_GLOBAL`: existe sinal preditivo medido,
  com IC de bootstrap que exclui o Baseline 0 em MAE e Spearman.
- H3 (generalização para encounter não visto) e H4 (generalização para spec não vista) não
  colapsam para `MODEL_GLOBAL` — degradação nula a favorável nos splits medidos.
- A ablation F1 vs F2 responde, para `MODEL_GLOBAL` com LightGBM: contexto não-controlável
  carrega ~15% de MAE de sinal incremental genuíno além do comportamento do jogador.

## 22. O que ainda exige as 597 restantes

- A comparação-âncora do protocolo (§13: A vs B/C/D) — `MODEL_TARGET` precisa de volume
  por target, não de amplitude (specs/encounters já bem cobertos: 25/9). O maior grupo por
  target tem 8 observações; a análise de sensibilidade (§15) mostra que nenhum limiar
  razoável ajuda sem mais linhas por target.
- `MODEL_SPEC × S4` e `MODEL_ENCOUNTER × S3` continuam estruturalmente NOT_EVALUABLE por
  definição de granularidade — mais dados não resolve isso, é uma limitação de desenho,
  não de volume.
- Um veredito estável por spec/encounter (§17/§18) — a variância spec-a-spec e a fragilidade
  dos encounters de n=5 pedem mais observações por grupo antes de qualquer ranking confiável.

## 23. Próxima decisão recomendada

**Classificação: A — SUFFICIENT_SIGNAL** (para a pergunta "existe sinal preditivo e ele
sobrevive a hold-outs de dimensão"). Justificativa quantitativa: 6 células de S1 batem
Baseline 0 simultaneamente em MAE e Spearman, com intervalo de bootstrap mensurável em
todas; 78/120 (65%) das células requisitadas foram avaliáveis.

Isso **não é** o mesmo que dizer que a pergunta de granularidade (A vs B/C/D) está
resolvida — ela continua bloqueada exclusivamente por `MODEL_TARGET` ter zero cobertura.
Recomendação: uma próxima rodada de coleta (autorização separada, fora de escopo aqui)
deveria priorizar **volume por target já coberto**, não largura nova — largura (25 specs,
9 encounters) já é suficiente para S3/S4/S5. Um alvo aproximado de 20-30 observações por
Phase4Target já presente destravaria a comparação de §13.

---

## Notas de implementação

- `min_train_rows_per_group=20`, `min_validation_rows_per_group=5` — explícitos,
  documentados, reportados e acompanhados de análise de sensibilidade (§15).
- Nenhuma observação da campanha foi tocada; nenhum ponto de API foi gasto; nenhum modelo
  desta rodada foi promovido a `phase4_model_registry` — persistência em
  `experiment_architecture_eval_runs`, tabela separada.
- `MODEL_HIERARCHICAL` (E) continua levantando `NotImplementedError` em todo caminho de
  código (`grouping_key`, `evaluate_cell`, `run_matrix`).
