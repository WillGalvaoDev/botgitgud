# Statistical Architecture Decision Gate

**Data:** 2026-08-21
**Campanha:** `exp-840b1ef99d76c33c8a0b` (offline; zero chamadas WCL nesta rodada)
**Execução:** `botgitgud experiment-decide --campaign exp-840b1ef99d76c33c8a0b`
**dataset_fingerprint:** `ds-1920e6a79ac4d20ea560` (idêntico ao de `docs/fase4-statistical-
architecture-results.md` — nenhuma linha nova foi coletada entre as duas rodadas)
**dataset_status:** PARTIAL — 603 de 1.200 observações planejadas

Este gate decide **apenas** qual granularidade merece avançar para validação — não coloca
nada em produção, não registra `READY`, não gera recomendações, não executa SHAP. Toda
seção separa explicitamente **MEASURED** (número medido), **INFERENCE** (leitura razoável
do número) e **DECISION** (escolha humana a partir da evidência).

---

## 1. Executive summary

`MODEL_GLOBAL` generaliza de forma real para specs e encounters não vistos: o hold-out
leave-one-out tem `micro_MAE` praticamente igual ao `macro_MAE` em ambas as dimensões
(spec: 19,31 vs 19,82; encounter: 21,05 vs 21,19), sinal de que o resultado não está sendo
carregado por um único grupo fácil. Contra um baseline linear **numericamente estável**
(Ridge, diagnóstico), LightGBM ainda vence por ~17% de MAE (20,72 vs 24,22) — a vantagem
não depende da instabilidade do Baseline 1 original. A sensibilidade a seed é **zero**
(config determinística, sem `subsample`/`colsample_bytree` fracionários). O ganho medido
contra Baseline 1 especificamente é mais frágil do que os relatórios anteriores sugeriam:
o intervalo pareado de ΔMAE quase toca zero (`[-5,10; -0,06]`) e o de ΔSpearman **cruza**
zero — grande parte da vantagem agregada vem de duas falhas catastróficas isoladas do
Baseline 1 (Shaman/Elemental, encounter 3306), não de uma vantagem ampla e uniforme.
A cauda de erro é a maior fraqueza medida: P95 ≈ 50 pontos, erro máximo ≈ 61 pontos, com
viés sistemático de subestimar o topo (bucket 80-100: viés −30) e superestimar o fundo
(bucket 00-20: viés +22) — regressão à média clássica. `MODEL_PER_TARGET` permanece
inteiramente inviável mesmo assumindo as 1.200 observações completas: nenhum target
chegaria a 20 observações. **Decisão: `MODEL_GLOBAL` avança para validação, com as
ressalvas explícitas de §19.**

---

## 2. Dataset

| Campo | Valor |
|---|---|
| campaign_id | `exp-840b1ef99d76c33c8a0b` |
| dataset_fingerprint | `ds-1920e6a79ac4d20ea560` |
| rows | 603 |
| planned (frozen plan) | 1.200 |
| specs / encounters / Phase4Targets cobertos | 25 / 9 / 198 de 213 |
| seed | `20260821` (fixo, `SEED_SENSITIVITY_SEEDS[0]`) |

## 3. Dataset limitations

PARTIAL — 50,25% do plano congelado. Toda métrica abaixo é preliminar. Em particular,
`MODEL_PER_TARGET` é a granularidade mais sensível a esse corte, mas §15 mostra que
mesmo as 1.200 completas não a tornariam viável — a limitação é do plano, não só do
percentual coletado.

## 4. Previous SAE result

`docs/fase4-statistical-architecture-results.md` (SAE.8) mediu, na matriz A–D × S1–S5
pooled: `MODEL_TARGET` inteiramente not_evaluable; `MODEL_SPEC`/`MODEL_ENCOUNTER` sem
vitória simultânea de MAE+Spearman contra Baseline 0 em S1; `MODEL_GLOBAL` vencendo em
todo split avaliável, melhor célula S5 (MAE 17,95, Spearman 0,649). Classificação daquela
rodada: `SUFFICIENT_SIGNAL`. Este gate aprofunda especificamente `MODEL_GLOBAL` com
leave-one-out real (não pooled), macro/micro, bootstrap pareado, diagnóstico Ridge,
sensibilidade a seed e diagnósticos de calibração/cauda que SAE.8 não cobria.

---

## 5. Leave-one-spec-out (MODEL_GLOBAL, F2)

25 folds — 23 `ok`, 2 `insufficient_data` (Warlock/Affliction n=9, Warrior/Fury n=8,
abaixo do piso de 10 linhas de validação).

| Spec | n_val | MAE B0/B1/LGBM | Spearman B1/LGBM | ΔMAE LGBM−B0 | ΔMAE LGBM−B1 |
|---|---:|---|---|---:|---:|
| DeathKnight/Frost | 14 | 23.21 / 22.20 / 16.16 | 0.421 / 0.515 | −7.06 | −6.05 |
| DeathKnight/Unholy | 38 | 27.55 / 23.00 / 19.71 | 0.792 / 0.715 | −7.84 | −3.29 |
| DemonHunter/Havoc | 14 | 19.93 / 15.81 / 22.24 | 0.502 / 0.207 | +2.31 | +6.43 |
| Druid/Balance | 39 | 26.41 / 25.98 / 20.03 | 0.275 / 0.505 | −6.38 | −5.95 |
| Druid/Feral (pior) | 17 | 31.71 / 28.05 / 28.83 | 0.474 / 0.240 | −2.87 | +0.78 |
| Evoker/Devastation | 25 | 25.52 / 19.45 / 20.33 | 0.788 / 0.554 | −5.19 | +0.88 |
| Hunter/BeastMastery | 34 | 23.44 / 19.93 / 13.24 | 0.567 / 0.748 | −10.20 | −6.69 |
| Hunter/Marksmanship | 29 | 19.69 / 15.22 / 20.23 | 0.607 / 0.462 | +0.54 | +5.01 |
| Hunter/Survival | 19 | 31.11 / 26.15 / 18.80 | 0.774 / 0.834 | −12.31 | −7.35 |
| Mage/Arcane | 30 | 23.67 / 43.97 / 14.27 | 0.454 / 0.758 | −9.40 | −29.70 |
| Mage/Fire | 18 | 34.11 / 25.86 / 18.52 | 0.571 / 0.663 | −15.59 | −7.34 |
| Mage/Frost | 24 | 26.04 / 20.82 / 13.31 | 0.635 / 0.857 | −12.73 | −7.51 |
| Monk/Windwalker | 25 | 22.40 / 20.53 / 18.72 | 0.444 / 0.610 | −3.68 | −1.81 |
| Paladin/Retribution | 31 | 26.23 / 23.63 / 18.43 | 0.657 / 0.719 | −7.79 | −5.19 |
| Priest/Shadow | 29 | 24.45 / 23.81 / 24.66 | 0.433 / 0.220 | +0.21 | +0.84 |
| Rogue/Assassination | 15 | 30.67 / 30.76 / 25.38 | 0.074 / 0.702 | −5.29 | −5.38 |
| Rogue/Outlaw | 17 | 29.71 / 22.23 / 24.52 | 0.570 / 0.613 | −5.18 | +2.29 |
| Rogue/Subtlety | 21 | 22.33 / 22.24 / 21.29 | 0.587 / 0.481 | −1.05 | −0.95 |
| Shaman/Elemental | 33 | 26.82 / **90.03** / 17.53 | 0.841 / 0.735 | −9.28 | **−72.50** |
| Shaman/Enhancement | 19 | 29.84 / 26.50 / 25.74 | 0.518 / 0.608 | −4.11 | −0.77 |
| Warlock/Affliction | 9 | — | — | INSUFFICIENT_DATA | |
| Warlock/Demonology | 30 | 23.77 / 23.19 / 18.78 | 0.313 / 0.440 | −4.99 | −4.41 |
| Warlock/Destruction | 32 | 24.56 / 23.68 / 17.33 | 0.302 / 0.759 | −7.23 | −6.35 |
| Warrior/Arms | 33 | 24.06 / 19.86 / 17.88 | 0.560 / 0.690 | −6.18 | −1.98 |
| Warrior/Fury | 8 | — | — | INSUFFICIENT_DATA | |

Spearman de Baseline 0 é sempre indefinido (predição constante). **MEASURED**: micro_MAE
19,31, macro_mean_MAE 19,82, macro_worst_MAE 28,83 (Druid/Feral), macro_p90_MAE ≈ 28.

---

## 6. Leave-one-encounter-out (MODEL_GLOBAL, F2)

9 folds, todos `ok`.

| Encounter | n_val | MAE B0/B1/LGBM | Spearman B1/LGBM | ΔMAE LGBM−B0 | ΔMAE LGBM−B1 |
|---|---:|---|---|---:|---:|
| 3176 | 90 | 28.53 / 24.93 / 20.54 | 0.420 / 0.610 | −7.99 | −4.39 |
| 3177 | 75 | 27.59 / 23.12 / 16.94 | 0.634 / 0.695 | −10.64 | −6.18 |
| 3178 | 80 | 25.27 / 19.92 / 18.72 | 0.559 / 0.609 | −6.55 | −1.20 |
| 3179 | 73 | 24.78 / 21.68 / 17.68 | 0.397 / 0.615 | −7.10 | −4.00 |
| 3180 | 77 | 22.38 / 19.68 / 19.61 | 0.484 / 0.523 | −2.77 | −0.07 |
| 3181 (pior) | 79 | 27.11 / 28.65 / 30.11 | 0.498 / 0.411 | +2.99 | +1.45 |
| 3182 | 36 | 18.83 / 19.43 / 17.96 | 0.280 / 0.406 | −0.87 | −1.47 |
| 3183 | 34 | 26.85 / 28.82 / 26.03 | 0.419 / 0.386 | −0.82 | −2.79 |
| 3306 | 59 | 27.10 / **152.50** / 23.10 | 0.629 / 0.658 | −4.00 | **−129.40** |

**MEASURED**: micro_MAE 21,05 (idêntico ao S3 pooled de SAE.8 — cross-check consistente),
macro_mean_MAE 21,19, macro_worst_MAE 30,11 (encounter 3181, o único onde LightGBM perde
para ambos os baselines). **INFERENCE**: os +1,6% de degradação relatados em SAE.8 (S1→S3)
são representativos do conjunto, não de um encounter isolado fácil — micro e macro
praticamente coincidem nas duas dimensões (§5, §6).

---

## 7. Macro vs micro

| Dimensão | micro_MAE | macro_mean_MAE | macro_worst_MAE | pior grupo |
|---|---:|---:|---:|---|
| spec | 19.31 | 19.82 | 28.83 | Druid/Feral |
| encounter | 21.05 | 21.19 | 30.11 | encounter 3181 |

**INFERENCE**: micro ≈ macro em ambas as dimensões — nenhum grupo grande está escondendo
grupos ruins pequenos por diluição. O pior grupo em cada dimensão ainda fica abaixo do
MAE de Baseline 0 (spec: 28,83 < ~25-30 nos folds correspondentes; encounter: 30,11 é o
único caso onde LightGBM perde).

---

## 8. Paired model comparison (S1, F2, n=152)

Bootstrap pareado determinístico, 1.000 reamostras, seed `20260821`, delta = a − b.

| Comparação | ΔMAE (IC 95%) | ΔSpearman (IC 95%) | Conclusão |
|---|---|---|---|
| LightGBM vs Baseline 0 | −6.47 [−9.20, −3.58] | não mensurável (B0 constante) | **robusto**: IC inteiramente negativo |
| LightGBM vs Baseline 1 | −2.62 [−5.10, **−0.06**] | +0.119 [**−0.037**, 0.259] | **marginal**: MAE quase toca zero, Spearman cruza zero |
| F2 vs F1 (LightGBM) | −3.54 [−5.81, −1.26] | +0.212 [0.079, 0.346] | **robusto**: os dois IC inteiramente do lado favorável |

**MEASURED**: por instrução explícita do protocolo, um ganho não é declarado consistente
quando o intervalo cruza zero de forma relevante — isso se aplica ao Spearman de LightGBM
vs Baseline 1 aqui. **INFERENCE**: a vantagem de LightGBM sobre o *Baseline 1 oficial*
especificamente é real mas frágil nesta amostra parcial; a vantagem sobre Baseline 0 e a
vantagem de F2 sobre F1 são, ao contrário, robustas.

---

## 9. Linear baseline instability

Confirmado por §5/§6: Baseline 1 colapsa numericamente em pelo menos 2 grupos —
Shaman/Elemental (MAE 90,03) e encounter 3306 (MAE 152,50) — dominando o ΔMAE agregado
vs Baseline 1 (§8). Sem esses dois grupos, a vantagem de LightGBM sobre Baseline 1 fica
bem mais modesta e às vezes negativa (DemonHunter/Havoc +6,43, Hunter/Marksmanship
+5,01, encounter 3181 +1,45). **INFERENCE**: parte real da "vitória agregada" contra
Baseline 1 relatada em SAE.8 vinha de instabilidade numérica concentrada, não de uma
vantagem ampla — daí a necessidade do diagnóstico Ridge (§10).

---

## 10. Ridge diagnostic

`DIAGNOSTIC_BASELINE_RIDGE` (alpha=10,0, fixo, nunca escolhido contra validação),
S1, F2, n=152:

| Modelo | MAE |
|---|---:|
| LightGBM | 20.72 |
| Ridge (diagnóstico) | 24.22 |

**MEASURED**: LightGBM vence Ridge por ~17% de MAE. **INFERENCE**: diferente da
comparação com Baseline 1 (§8-9), esta não está contaminada por instabilidade numérica —
Ridge é um baseline linear estável e ainda perde. **A vantagem de LightGBM sobre uma
baseline linear justa e estável é real, mensurável, e não depende de colapsos
numéricos de outro modelo.**

---

## 11. Seed sensitivity

Seeds testadas: 20260821–20260825 (5), LightGBM, S1, F2.

| Métrica | mean | std | min | max |
|---|---:|---:|---:|---:|
| MAE | 20.7185 | **0.0** | 20.7185 | 20.7185 |
| Spearman | 0.5099 | **0.0** | 0.5099 | 0.5099 |

**MEASURED**: desvio padrão exatamente zero — cada seed produziu o resultado
byte-idêntico. **INFERENCE**: a config atual do LightGBM (`subsample=1.0,
colsample_bytree=1.0`, sem amostragem estocástica de linhas/colunas) não deixa nada para
a seed randomizar; `random_state` só afetaria a ordem interna de resolução de empates,
que aqui não muda o resultado. Documentado como fato medido — nenhuma aleatoriedade
artificial foi introduzida para forçar uma medição de variância.

---

## 12. Error distribution (S1, MODEL_GLOBAL, F2, LightGBM, n=152)

| P50 | P75 | P90 | P95 | max |
|---:|---:|---:|---:|---:|
| 18.23 | 30.03 | 43.96 | 49.91 | 61.02 |

Viés (predito − observado) por faixa de rankPercent **verdadeira**:

| Bucket | Viés |
|---|---:|
| 00-20 | +21.91 |
| 20-40 | +18.34 |
| 40-60 | +4.82 |
| 60-80 | −13.67 |
| 80-100 | −30.13 |

**MEASURED**: erro mediano razoável (18,23), mas a cauda é pesada — P95 quase 50 pontos,
máximo 61. O viés por bucket verdadeiro é monotônico e simétrico ao redor do centro:
superestima o fundo, subestima o topo. **INFERENCE**: regressão à média clássica — o
modelo comprime previsões em direção ao centro da distribuição, uma fraqueza real para
qualquer uso que dependa de identificar corretamente os extremos (percentis muito baixos
ou muito altos).

---

## 13. Calibration (predicted bucket, S1, MODEL_GLOBAL, F2, LightGBM)

| Bucket predito | n | média predita | média observada | viés |
|---|---:|---:|---:|---:|
| 00-20 | 14 | 8.75 | 14.93 | −6.18 |
| 20-40 | 43 | 31.13 | 26.14 | +4.99 |
| 40-60 | 50 | 49.38 | 45.26 | +4.12 |
| 60-80 | 42 | 68.31 | 52.83 | +15.48 |
| 80-100 | 3 | 81.39 | 53.33 | +28.06 |

**MEASURED**: quando o modelo prevê 60-80 (n=42, amostra razoável), o valor real médio é
só 52,83 — o modelo está sistematicamente confiante demais nessa faixa. O bucket 80-100
tem apenas 3 observações — viés grande, mas não confiável isoladamente. **INFERENCE**:
consistente com §12 — o problema de calibração é mais sério na metade superior da
distribuição, exatamente onde o produto mais precisa de confiança (identificar
desempenho excepcional).

---

## 14. Percentile range behavior

| | n | <0 | >100 | min | max |
|---|---:|---:|---:|---:|---:|
| LightGBM (S1, F2) | 152 | 0 | 0 | 0.66 | 82.13 |

**MEASURED**: zero predições fora de [0,100]; `raw_metrics.mae == clipped_metrics.mae`
exatamente — clipping não teria efeito algum aqui. **INFERENCE**: árvores de decisão
(LightGBM) nunca extrapolam além do intervalo dos alvos de treino, ao contrário de
regressão linear (Baseline 1/Ridge frequentemente produziram valores negativos ou acima
de 100 em SAE.8 e neste dataset) — uma propriedade estrutural do modelo, não um
resultado de pós-processamento.

---

## 15. Phase4Target density

| | n_targets | min | median | mean | P75 | P90 | max | ≥10 | ≥20 | ≥30 | ≥50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Atual (603 completed) | 198 | 1 | 3 | 3.05 | 4 | 5 | 8 | 0 | 0 | 0 | 0 |
| Plano completo (1.200) | 213 | 1 | 6 | 5.63 | 7 | 8 | **10** | **4** | **0** | **0** | **0** |

**MEASURED**: mesmo assumindo as 1.200 observações do plano congelado completas, nenhum
Phase4Target chegaria a 20 observações — o máximo teórico é 10, alcançado por apenas 4
dos 213 targets. **INFERENCE**: `MODEL_PER_TARGET` não é inviável por causa do percentual
coletado (50%) — é inviável pela profundidade que o *próprio plano congelado* atribui a
cada target. Terminar a coleta atual não resolve isso; só um plano novo com alocação por
target deliberadamente mais profunda resolveria.

---

## 16. What the remaining 597 rows would change

Comparando cobertura atual (603) contra o plano completo (1.200):

| | specs | encounters | targets |
|---|---:|---:|---:|
| Atual | 25 / 25 | 9 / 9 | 198 / 213 |
| Plano completo | 25 / 25 | 9 / 9 | 213 / 213 |

**MEASURED**: largura (specs/encounters) já está 100% coberta agora — as 597 restantes
não adicionam nenhuma spec ou encounter novo. Adicionariam 15 targets hoje com zero
observações, e elevariam a densidade mediana por target de 3 para 6 (§15) — ainda muito
abaixo de qualquer piso razoável para `MODEL_PER_TARGET`.

**DECISION** sobre a pergunta A/B/C/D: as 597 restantes **(A) apenas reduzem incerteza do
modelo global** (mais linhas por spec/encounter, folds de leave-one-out menos dependentes
de poucos grupos pequenos, especialmente os dois hoje `insufficient_data`) — **não (B)**
tornam `MODEL_PER_TARGET` viável, dado §15. Não é performance futura inventada: é
contagem direta do plano congelado.

---

## 17. Architecture-by-architecture decision

| Arquitetura | Decisão | Evidência-chave |
|---|---|---|
| `MODEL_PER_TARGET` (A) | **REJECT_FOR_CURRENT_PHASE4** | 0/198 grupos avaliáveis em qualquer limiar (10-50); mesmo com as 1.200 completas, máximo 10 obs/target, 0 targets ≥20 (§15) |
| `MODEL_PER_SPEC` (B) | **KEEP_AS_SECONDARY_CANDIDATE** | SAE.8: não bate Baseline 0 em MAE simultaneamente com Spearman em S1; sinal de ranking existe mas é mais fraco que D |
| `MODEL_PER_ENCOUNTER` (C) | **KEEP_AS_SECONDARY_CANDIDATE** | SAE.8: bate Baseline 0 em MAE e Spearman em S1 (25,04/0,320 F1; 21,98/0,520 F2) — sinal real, mas `MODEL_ENCOUNTER × S3` é not_evaluable por construção, sua própria generalização entre encounters nunca pôde ser testada |
| `MODEL_GLOBAL` (D) | **ADVANCE_TO_VALIDATION** | §5-§10: generaliza para spec/encounter não vistos (micro≈macro), vence Baseline 0 robustamente, vence um baseline linear estável (Ridge) por ~17%, seed-estável; ressalvas em §19 |
| `MODEL_HIERARCHICAL` (E) | **NOT_EVALUATED** | ponto de extensão apenas — `NotImplementedError` preservado em todo o código |

---

## 18. Selected candidate architecture

**`MODEL_GLOBAL`**, features F2 (`CONTROLLABLE` + `NON_CONTROLLABLE`), LightGBM como
modelo candidato.

Critérios de §13 do brief, avaliados:

| # | Critério | Status |
|---|---|---|
| 1 | Supera Baseline 0 consistentemente | ✅ robusto (§8) |
| 2 | Supera Baseline 1 consistentemente | ⚠️ parcial — robusto contra Ridge (§10), marginal/contaminado contra o Baseline 1 oficial (§8-9) |
| 3 | Ganho não depende de 1-2 specs | ⚠️ parcial — vs Baseline 0 sim; vs Baseline 1 não (§9) |
| 4 | Ganho não depende de 1 encounter | ⚠️ parcial — mesma ressalva de #3 (§9) |
| 5 | Hold-out de specs razoável | ✅ (§5, §7) |
| 6 | Hold-out de encounters razoável | ✅ (§6, §7) |
| 7 | Seed sensitivity não material | ✅ zero (§11) |
| 8 | Error tails aceitáveis | ⚠️ cauda pesada e viés de calibração no topo (§12-13) |

5 de 8 critérios totalmente atendidos, 3 parcialmente — nenhum falha de forma completa.
**DECISION**: o conjunto de evidência justifica avançar `MODEL_GLOBAL` para validação,
não uma promoção incondicional — as ressalvas parciais (#2-4, #8) tornam-se os riscos e
o próximo gate abaixo.

---

## 19. Remaining risks

1. **Cauda de erro e calibração no topo** (§12-13): viés de −13,67 a −30,13 nos buckets
   60-80/80-100 — o modelo tende a subestimar desempenho excepcional. Antes de qualquer
   uso voltado a "identificar os melhores", isso precisa de investigação — mais dados no
   topo da distribuição, ou uma camada de calibração pós-hoc (fora de escopo aqui).
2. **Vantagem contra Baseline 1 concentrada em outliers** (§9): dois grupos
   (Shaman/Elemental, encounter 3306) carregam a maior parte do ΔMAE agregado contra o
   Baseline 1 oficial. A vantagem contra um baseline linear estável (Ridge, §10) é sólida
   e não sofre desse problema — mas é ~17%, não a diferença dramática que a comparação
   com Baseline 1 sugeria.
3. **`ctx_class`/`ctx_spec` ausentes do dataset materializado** (já registrado em
   `docs/fase4-statistical-architecture-results.md` §4): `MODEL_GLOBAL` não tem nenhuma
   representação direta de identidade de spec: sua generalização vem inteiramente de
   covariáveis correlacionadas.
4. **Dataset ainda PARTIAL** (§3, §16): a densidade por spec/encounter deve aumentar com
   mais coleta, reduzindo os grupos hoje `insufficient_data` (2 specs) — mas a arquitetura
   escolhida não deve mudar por isso (§16).

## 20. Next validation gate

Antes de qualquer promoção a `phase4_model_registry` ou uso em `!analisar`:

1. Investigar e, se possível, corrigir o viés de calibração no topo/fundo da distribuição
   (§12-13) — não faz parte deste gate.
2. Reavaliar `MODEL_GLOBAL` contra Baseline 1 (ou Ridge) quando a densidade por spec dos
   dois grupos hoje `insufficient_data` permitir incluí-los no leave-one-out.
3. T4.3 (backtesting, calibração, controle de regressão à média) continua sendo o escopo
   apropriado para tratar os achados de §12-13 — este gate não os corrige, só os mede.
4. Nenhuma promoção a `READY` deve ocorrer sem uma rodada de validação subsequente que
   trate explicitamente o risco #1 de §19.

---

## Notas de implementação

- Nenhuma observação da campanha foi tocada; nenhum ponto de API foi gasto; nenhum modelo
  foi promovido a `phase4_model_registry` (confirmado por teste dedicado e por leitura
  read-only do warehouse real). Persistência em `experiment_architecture_decision_runs`,
  tabela isolada tanto do registry quanto da tabela de runs do SAE.8.
- Ridge é estritamente diagnóstico — nunca substituiu Baseline 1 no protocolo oficial.
- `MODEL_HIERARCHICAL` continua levantando `NotImplementedError` em todo caminho de código.
