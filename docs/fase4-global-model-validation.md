# Phase 4 — Global Model Validation & Calibration Gate

**Data:** 2026-08-21
**Campanha:** `exp-840b1ef99d76c33c8a0b` (offline; zero chamadas WCL nesta rodada)
**Execução:** `botgitgud experiment-calibrate --campaign exp-840b1ef99d76c33c8a0b`
**run_id:** `calibrate-e310db9e1d394630`
**dataset_fingerprint:** `ds-1920e6a79ac4d20ea560` (idêntico às rodadas anteriores)
**Arquitetura candidata congelada** (não reaberta): `MODEL_GLOBAL`, F2, LightGBM
(`docs/fase4-architecture-decision.md` §17-18). Nenhum erro metodológico foi encontrado que
justificasse reabrir essa decisão.

Toda seção separa **MEASURED** (número medido), **INFERENCE** (leitura razoável do número) e
**DECISION** (escolha humana a partir da evidência).

---

## 1. Executive summary

O modelo carrega sinal real e consistente (Spearman 0,544 na validação temporal, 0,652 no
diagnóstico cross-fitted sobre as 603 linhas inteiras — corrobora as medições independentes de
`docs/fase4-architecture-decision.md`). Mas suas predições **não são bem calibradas**: viés de
+19,2 no bucket observado 00-20 e −30,7 no bucket 80-100 — a mesma direção e magnitude já vistas
em splits diferentes em tarefas anteriores, o que indica um efeito estrutural, não um artefato de
amostra pequena. Calibração linear e isotônica, ajustadas exclusivamente no fold de calibração
(93 linhas, zero vazamento — verificado por teste), **não melhoram o MAE agregado** (18,89 →
19,15 linear / 19,14 isotônica) e **pioram o viés exatamente nos buckets extremos** que motivaram
a investigação, embora reduzam o erro na faixa central (40-60) e o pior caso (P90, máximo) da
calibração linear. Nenhum dos dois métodos oferece um ganho líquido claro. Decisão: a previsão
calibrada não está pronta para o jogador (`INTERNAL_EXPLANATION_ONLY`), mas a arquitetura está
estável o suficiente para começar a investigar explicabilidade (`SHAP_READY`).

---

## 2. Candidate architecture

`MODEL_GLOBAL` + F2 (`CONTROLLABLE` + `NON_CONTROLLABLE`) + LightGBM — config congelada, idêntica
à de `docs/fase4-architecture-decision.md` (`n_estimators=200, max_depth=4, num_leaves=15,
learning_rate=0.05, min_child_samples=5`, determinística). Nenhum tuning foi feito nesta rodada.

## 3. Dataset limitations

603/1.200 (`dataset_status=PARTIAL`), mesmo dataset das rodadas anteriores. O corte temporal em
3 partes reduz ainda mais o fold de validação (95 linhas) frente ao já usado em splits de 2 vias
— os intervalos de confiança aqui são naturalmente mais largos que os de
`docs/fase4-architecture-decision.md`.

---

## 4. Raw model performance

Split temporal de 3 vias (earliest → train, middle → calibration, latest → validation),
`calibration_fraction=0.15`, `validation_fraction=0.15`:

| Fold | n | Período (ms) |
|---|---:|---|
| train | 415 | 1.786.633.609.878 – 1.786.712.470.002 |
| calibration | 93 | 1.786.716.699.385 – 1.786.831.370.506 |
| validation | 95 | 1.786.856.524.792 – 1.787.070.620.607 |

Nenhuma linha de calibração ou validação pôde influenciar o treino; nenhuma linha de validação
pôde influenciar o calibrador — verificado por teste de perturbação (`§5`).

**RAW (C0)**, validação, n=95:

| MAE | RMSE | Spearman | mediana \|erro\| | P75 | P90 | P95 | máx | viés |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 18.89 | 23.67 | 0.544 | 16.84 | 26.84 | 40.81 | 45.85 | 64.48 | +6.22 |

---

## 5. Calibration protocol

`observed ~ f(raw_prediction)`, `f` ajustado **somente** no fold de calibração (93 pares
raw/observado). Verificado com um teste de caixa-preta que perturba **apenas** os rótulos do
fold de validação e confirma que nenhuma predição raw ou calibrada muda — a prova mais forte de
ausência de vazamento que o protocolo permite sem instrumentar o código de produção.
`min_calibration_rows_for_isotonic=30` (fixo, documentado, nunca escolhido contra validação);
93 ≥ 30, então isotônica foi avaliável nesta rodada.

## 6. Linear calibration

`observed ~ a·raw + b`, OLS de uma variável. Preserva Spearman exatamente quando o coeficiente
angular é positivo (propriedade matemática, testada). Resultado: **MAE piora** (18,89 → 19,15),
RMSE melhora (23,67 → 22,82), viés agregado melhora (+6,22 → +5,30), P90 e máximo melhoram
bastante (40,81→35,31; 64,48→58,36) — mas a mediana do erro piora (16,84→18,35).

## 7. Isotonic calibration

Monotônica, `out_of_bounds="clip"` no domínio de entrada. Avaliável (93 ≥ 30). Resultado muito
parecido ao linear: MAE 19,14 (pior que raw), RMSE 23,24, Spearman 0,534 (levemente pior — a
não-decrescência estrita da isotônica pode empatar predições e mexer minimamente no ranking,
diferente da linear que preserva Spearman exatamente).

| Método | MAE | RMSE | Spearman | mediana | P75 | P90 | P95 | máx | viés |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 raw | 18.89 | 23.67 | 0.544 | 16.84 | 26.84 | 40.81 | 45.85 | 64.48 | +6.22 |
| C1 linear | 19.15 | 22.82 | 0.544 | 18.35 | 28.22 | 35.31 | 41.34 | 58.36 | +5.30 |
| C2 isotonic | 19.14 | 23.24 | 0.534 | 16.93 | 26.05 | 36.75 | 43.37 | 59.07 | +4.93 |

---

## 8. Temporal validation

Cross-check independente: um diagnóstico **cross-fitted** (5 folds, partição determinística por
chave natural, não temporal — nunca substitui o gate temporal acima) sobre as 603 linhas
inteiras deu MAE 17,47, Spearman 0,652 — mais forte que o único slice temporal de validação
(MAE 18,89, Spearman 0,544), consistente com o esperado (mais dados, sem o corte único no fim da
janela). **INFERENCE**: o sinal do modelo é real e não é um artefato do recorte temporal
específico usado no gate principal.

## 9. Raw vs calibrated metrics

Ver tabela de §7. **Nenhum método de calibração produziu um MAE melhor que raw.** RMSE e viés
agregado melhoram com ambos; a mediana do erro piora com ambos.

## 10. Calibration by observed percentile

Raw vs linear (isotônica segue o mesmo padrão, ver JSON persistido):

| Bucket | n | obs. médio | raw médio | viés raw | calib. médio | viés calib. | MAE raw | MAE calib. |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 00-20 | 36 | 12.03 | 31.18 | **+19.16** | 33.70 | **+21.67** ⬆pior | 20.33 | 21.82 ⬆pior |
| 20-40 | 20 | 28.35 | 43.48 | +15.13 | 42.49 | +14.14 ⬇melhor | 18.72 | 15.83 ⬇melhor |
| 40-60 | 15 | 49.13 | 53.99 | +4.86 | 50.01 | +0.88 ⬇⬇melhor | 13.63 | 9.58 ⬇⬇melhor |
| 60-80 | 17 | 68.88 | 53.62 | −15.26 | 49.75 | **−19.14** ⬆pior | 15.81 | 19.14 ⬆pior |
| 80-100 | 7 | 87.14 | 56.45 | **−30.69** | 51.77 | **−35.37** ⬆pior | 30.69 | 35.37 ⬆pior |

**MEASURED**: calibração ajuda claramente só na faixa central (20-60); piora os dois extremos e
a faixa 60-80. **INFERENCE**: um calibrador linear/isotônico de uma variável ajustado sobre
raw→observado, quando a correlação é moderada (aqui Spearman ~0,54), sofre *regression dilution*
clássica — em vez de expandir a saída comprimida do modelo de volta à amplitude real, ele
comprime ainda mais em direção à média do fold de calibração. Isso é exatamente o efeito
observado no intervalo de predição (§14): raw vai de 1,25 a 79,99; calibrado linear vai de 12,29
a 68,60 — **mais estreito**, não mais amplo.

## 11. Calibration by predicted percentile

Pergunta do brief: "quando o modelo prevê ~70, jogadores realmente têm performance próxima
disso?"

Raw, por banda prevista (nenhuma predição raw caiu na banda 80-100 — o máximo observado foi
79,99):

| Banda prevista | n | predito médio | observado médio | viés |
|---|---:|---:|---:|---:|
| 00-20 | 13 | 12.90 | 17.00 | −4.10 |
| 20-40 | 27 | 30.60 | 24.89 | +5.71 |
| 40-60 | 35 | 50.00 | 45.14 | +4.86 |
| 60-80 | 20 | 68.25 | 52.25 | **+16.00** |

**MEASURED**: **não** — quando o modelo prevê ~68, o desempenho real médio é 52,25, 16 pontos
abaixo. A banda 60-80 é a mais superconfiante das quatro que o modelo efetivamente usa.

## 12. Calibration by spec

25 specs com dados na validação; amostras pequenas (n=1 a 8) tornam qualquer leitura individual
frágil — reportado por completo no artifact persistido (`experiment_calibration_runs`).
Vieses raw mais extremos: Rogue/Subtlety (+36,88, n=3), Druid/Feral (+41,06, n=2),
Evoker/Devastation (−26,47, n=5) — todos com n pequeno demais para conclusão isolada. Vieses raw
mais próximos de zero com n razoável: Paladin/Retribution (+1,72, n=7), Hunter/BeastMastery
(−1,98, n=6), Warlock/Demonology (+1,33, n=5). Nenhum calibrador por spec foi ajustado — o
candidato continua global, conforme instruído.

## 13. Calibration by encounter

9 encounters, amostras maiores (n=5 a 22) que specs:

| Encounter | n | viés raw | viés calibrado |
|---|---:|---:|---:|
| 3176 | 18 | +15.94 | +11.87 |
| 3183 (melhor, mais confiável) | 22 | −1.49 | −1.81 |
| 3177 | 10 | −0.50 | −1.52 |
| 3180 | 11 | +12.12 | +10.36 |
| 3178 | 6 | +10.87 | +7.64 |

Nenhum encounter mostra viés catastrófico isolado — a maior magnitude (3176, +15,94) é da mesma
ordem do viés agregado do bucket 20-40 (§10), não um outlier estrutural.

## 14. Error tails

P90=40,81, P95=45,85, máximo=64,48 (raw) — cauda pesada, coerente com
`docs/fase4-architecture-decision.md` (P95≈50 lá). Calibração linear reduz a cauda
(P90 35,31, máx 58,36) às custas da mediana. **Out-of-range**: zero predições <0 ou >100 em
ambos raw e calibrado — LightGBM nunca extrapola, e o calibrador linear, apesar de poder
extrapolar em teoria, não o fez nesta amostra (intervalo calibrado 12,29–68,60, dentro de
[0,100]). Nenhum clipping foi aplicado a nenhuma métrica principal; um diagnóstico de clipping
seria redundante aqui já que não há valores fora do intervalo.

## 15. Extreme percentile limitations

00-20 observado: n=36 (maior bucket da validação — este slice temporal específico está
enviesado para baixo, um artefato do período, não da amostra geral). 80-100 observado: n=7 —
pequeno, mas maior que o n=3 do relatório anterior; o viés (−30,69) é consistente em direção e
magnitude com aquele achado (−30,13 lá), o que pesa a favor de ser um efeito real, não ruído de
amostra mínima. Ainda assim, **n=7 não sustenta conclusão definitiva isolada** — a corroboração
vem de comparar com a medição independente anterior, não deste n sozinho.

## 16. Confidence framework

Categorias determinísticas, não expostas ao bot (código em `experiment_calibration.py`,
`confidence_level`):

- **HIGH**: banda prevista não-extrema **e** cobertura de spec ≥30 **e** cobertura de encounter
  ≥50 (linhas de treino+calibração). Limiares fixados acima da mediana e abaixo do máximo
  medidos nesta campanha (specs: 6-35; encounters: 12-79) — nunca o padrão.
- **MEDIUM**: cobertura de spec ≥15 **ou** de encounter ≥30.
- **LOW**: qualquer outro caso, e **sempre** para bandas extremas quando a cobertura não atinge
  o piso de MEDIUM.

Nenhuma banda extrema pode receber HIGH, por construção — não importa quão bem coberta a
spec/encounter esteja.

Distribuição medida sobre as 95 linhas de validação: **LOW=4, MEDIUM=90, HIGH=1**. Apenas 2/25
specs e 6/9 encounters atingem o piso de HIGH; a interseção com uma banda prevista não-extrema é
rara — o resultado é o esperado, não meramente conservador por acidente.

## 17. Player-facing semantics

**rankPercent observado (WCL)** é um fato: já vem do log real do jogador, o bot já tem acesso a
ele. **Percentil previsto (este modelo)** é uma estimativa de "que performance seria esperada
dadas as características observadas" — não substitui, não compete com, e não deveria aparecer
como uma segunda nota ao lado do rankPercent real. Sua utilidade pretendida é servir de base para
uma futura camada explicativa ("por que o desempenho ficou X pontos acima/abaixo do esperado"),
não como um número exibido isoladamente. Esta tarefa não implementa essa camada nem qualquer
integração com o bot — `!analisar` não consome nenhum artifact desta rodada.

## 18. Calibration decision

**INTERNAL_EXPLANATION_ONLY.**

Não `PLAYER_FACING_READY`: nenhum dos dois calibradores reduziu o MAE agregado, e ambos pioraram
o viés exatamente nas duas faixas mais extremas (§10) — o problema que motivou a investigação
continua sem solução simples. Não `NOT_RELIABLE_ENOUGH`: existe sinal real e reprodutível
(Spearman 0,544 na validação, 0,652 no cross-fit, consistente com todas as medições anteriores
desta campanha) — útil para análise interna e para uma futura camada explicativa, apenas não
preciso o bastante para virar um número mostrado ao jogador.

## 19. Remaining risks

1. Calibração simples de 1 variável não resolve a compressão do modelo — pode até piorá-la nos
   extremos (§10). Uma correção real provavelmente exige mais dados nos extremos, não um
   calibrador mais sofisticado sobre a mesma amostra pequena.
2. Amostra de calibração (93 linhas) e validação (95 linhas) são pequenas; specs individuais têm
   n=1-8 na validação — qualquer leitura por spec isolada é frágil (§12).
3. A banda prevista 60-80 é sistematicamente superconfiante (§11) — um risco concreto se qualquer
   consumidor futuro tratar essa banda como confiável sem o contexto deste relatório.
4. Dataset ainda PARTIAL — mais dados podem mudar os números específicos de calibração sem
   necessariamente mudar a conclusão estrutural (viés nos extremos).

## 20. SHAP readiness decision

**SHAP_READY** — no sentido estrito definido pelo brief: a arquitetura está estável o
suficiente para começar a estudar explicabilidade. Não significa que SHAP provará causalidade,
nem que recomendações estarão prontas.

Justificativa quantitativa: seed sensitivity zero (`docs/fase4-architecture-decision.md`),
Spearman consistente entre 0,51 e 0,65 em pelo menos quatro medições independentes desta sessão
(S1 do gate de arquitetura, cross-fit desta rodada, validação temporal desta rodada, S5 de
SAE.8), e o viés por bucket replicado em duas amostras de validação diferentes com a mesma
direção e ordem de grandeza. Isso é o suficiente para uma arquitetura "estável", não para uma
predição "calibrada" — as duas perguntas são independentes, e SHAP responde à primeira.

## 21. Next gate

1. Explicabilidade (SHAP) sobre o modelo RAW (não o calibrado) — fora de escopo aqui, mas
   desbloqueada por este relatório.
2. Investigar a causa raiz da compressão antes de tentar calibrar de novo — provavelmente requer
   mais dados nos buckets extremos (00-20 e 80-100), não um calibrador diferente sobre a mesma
   amostra.
3. Nenhuma promoção a `phase4_model_registry` ou a `!analisar` deve ocorrer antes de resolver o
   risco #1 de §19.

---

## Notas de implementação

- Nenhuma observação da campanha foi tocada; nenhum ponto de API foi gasto; nenhum modelo ou
  calibrador foi promovido a `phase4_model_registry` (confirmado por teste dedicado e leitura
  read-only do warehouse real). Persistência em `experiment_calibration_runs`, tabela isolada de
  `phase4_model_registry`, `experiment_architecture_eval_runs` (SAE.8) e
  `experiment_architecture_decision_runs` (SAD.1).
- SHAP não foi instalado nem executado.
- `MODEL_HIERARCHICAL` não foi implementado; nenhuma recomendação foi gerada.
