# M3.2 — Semântica das grades (SPEC v001)

Unidade local de M3 ([`roadmap.md`](roadmap.md) §M3.2). Política nova:
**`grade-semantics-v1`**. Depende das populações de M2 ([`methodology.md`](methodology.md) §4,
§5) e resolve a decisão aberta **B06**. Não altera nenhum contrato de M1, M2 ou M3.1.

Estado: SPEC v001, **DRAFT** (não implementada; decisões D-M32-06..09 aceitas pelo dono em
2026-10-05). M3.3+ fora.

## 1. Objetivo e fronteira

M3.2 fixa **o que uma grade significa**: uma posição descritiva do valor do jogador na
distribuição observada da própria métrica, com o N dessa distribuição. Não é teste de
hipótese, não controla taxa de erro e não afirma causa.

M3.2 entrega:

1. o registro de B06 (descrição) e a retirada de toda alegação de FDR, significância ou
   intervalo de confiança das **saídas**;
2. uma interface descritiva (`analysis/grade_semantics.py`) que expõe posição, empates, N,
   suficiência e banda, com quantis e empates verificáveis por cálculo independente.

M3.2 é local quanto à **seleção de coaching**: nenhum consumidor de findings, remediação,
materialidade ou resposta de coaching troca de população, de corte ou de regra de seleção. A
troca desses consumidores para a interface nova, incluindo a aposentadoria do filtro BH em
`findings`, é M3.4 (§7), condicionada à regra R-M32-01 (D-M32-09). As únicas mudanças fora da
interface são no relatório CLI (§3, D-M32-06 e D-M32-07), exigidas pelo critério 3 do
roadmap.

Fora: materialidade e elegibilidade de recomendação (M3.3), disponibilidade de streams (M3.1),
seleção e ordenação globais (M5.1), redação da resposta (M5.2), novas métricas, novos limiares.

## 2. Auditoria do estado atual

Referências ao código em `main` @ `5120489`.

### 2.1 Produtores e consumidores de grades

| Grade | Função | Cauda | População (N) | Consumidores |
|---|---|---|---|---|
| Passo MATCH de cooldown | `comparison._grade_match_steps` → `grading.grade_deviation` | bicaudal | tempos do slot em `R_log` | CLI (`grading_text`, `cd_sections_text`) |
| `gross_ability_dps:<sid>` e demais métricas de `compare_metrics` | `performance_features.grade_scalar` | unicaudal | DESCRIPTIVE M2.2 da própria `metric_id` | findings (ABILITY_GAP), materialidade, coaching, CLI |
| `aura_uptime_fraction:<sid>` | idem | unicaudal (`higher_better`) | DESCRIPTIVE M2.2 | findings (UPTIME, via BH), materialidade, coaching, CLI |
| Tempo ativo | idem | `higher_better` | `R_log` com `active_time_pct` não nulo | execução, materialidade, nota positiva, CLI |
| Mortes, downtime | idem | `lower_better` | `R_log` | execução, nota positiva, CLI |
| Desperdício de recurso | idem | `lower_better` | `R_log`, ausente → `0.0` | execução, CLI |
| Posição geral (`standing`) | `materiality.build_conclusion` → `grade_scalar` | `higher_better` | DPS medido de `R_log` | conclusão do coaching, nota positiva |

Bandas atuais (corretas e mantidas, §3 D-M32-03): bicaudal verde `0,25 ≤ q ≤ 0,75`, amarela
`0,10 ≤ q < 0,25` ou `0,75 < q ≤ 0,90`, vermelha fora; unicaudal com `b = q` (higher) ou
`b = 1 − q` (lower): vermelha `b < 0,10`, amarela `b < 0,25`, verde senão. `N < 15` →
`insufficient`.

### 2.2 Lacunas encontradas

| # | Onde | Lacuna | Critério |
|---|---|---|---|
| G1 | `analysis/grading.py` (`FDR`, `benjamini_hochberg`, `two_tailed_p_value`) | `p = 2·min(q, 1−q)` é o quantil empírico de **uma** observação reescrito como p-valor. Não há hipótese nula declarada, a família muda por análise e não houve calibração. Não é controle de FDR. | 3 |
| G2 | `findings.build_findings` | BH filtra candidatos UPTIME antes da remediação e do coaching. Como `p ≤ 0,10` é sempre exigido, **nenhum uptime amarelo chega ao coaching** (q ∈ [0,10; 0,25) → p ≥ 0,20), ao contrário do que a materialidade (vermelho/amarelo) declara. Um vermelho com q = 0,05 sobrevive sozinho, mas não sobrevive se houver outro candidato amarelo: a sobrevivência depende dos outros candidatos, não do próprio valor. | 3 (seleção: M3.4) |
| G3 | `report/grading_text.py` | Seção CLI "Desvios menores **(não significativos)**" afirma significância a partir de G1; todo passo amarelo cai nela pelo mesmo motivo de G2. | 3 |
| G4 | `grading.bootstrap_median_ci`, `grading_text.render_match_step_line` | "IC90" do bootstrap da mediana de referência é afirmação inferencial, sem validação de cobertura. É impresso **mesmo quando a grade é `insufficient`**, inclusive com `n = 1` (intervalo de largura zero). | 3, 5 |
| G5 | `grading.empirical_quantile` | Referência não finita entra no denominador e não conta como menor nem igual: `q(3; [NaN,1,2,4,5]) = 0,4`, contra `0,5` sobre os finitos. Populações M2.2 já são finitas; as de `R_log` (tempo ativo, downtime, desperdício) não são filtradas. | 1, 2 |
| G6 | `performance_features._build_waste_findings` | Referência sem o tipo de recurso entra como `0.0`: N e valores não são os da métrica (já registrado como F6 de M3.1; correção é M3.4). | 1 |
| G7 | `report/performance_text.py` | Mediana de coorte de mortes, downtime, tempo ativo e desperdício é impressa para qualquer N ≥ 1; uptimes já exigem N ≥ 8. | 5 |
| G8 | `coaching_answer._cohort_position_clause`, `_conclusion` | "o mais baixo dos N" usa `q ≤ 1/n`. Com uma referência abaixo do jogador e nenhuma igual, `q = 1/n` e a frase é falsa (ex.: `n = 15`, uma referência menor → `q = 0,0667`). O quantil médio não carrega contagens. Correção da frase é M5.2; M3.2 fornece as contagens. | 2 |
| G9 | `findings.compute_confidence` | Parâmetro `survives_bh` (nunca passado como `False` por nenhum chamador) liga "confiança" a G1. As faixas `baixa/média/alta` são de tamanho de amostra e relaxamento, não de confiança estatística. | 3 |

Verificado sem lacuna: `compute_quantile_stats` coincide com `numpy.percentile` (interpolação
linear, Hyndman–Fan tipo 7) para P10–P90; empates no quantil médio seguem a convenção de
mid-rank; `MIN_N_FOR_GRADING = 15` e a escada de suficiência de M2.2 usam o mesmo limiar.

## 3. Decisões

### D-M32-01 — B06: grades são descrição

Registro de B06. Toda grade é **descrição** da posição do jogador na distribuição observada.
Consequências obrigatórias:

- nenhuma saída usa "significativo", "não significativo", "FDR", "p-valor", "intervalo de
  confiança", "IC" ou equivalente;
- nenhuma saída apresenta grade como prova de que o jogador "deveria" ter outro valor, nem como
  causa de diferença de dano;
- os limiares 15 (grade) e 8 (comparação) continuam como política de amostra, sem calibração
  estatística (já declarado em `methodology.md` §6).

Alternativa rejeitada: inferência. Exigiria hipótese nula por métrica, família de testes
estável e calibração de p-valores discretos com N entre 15 e 60. Não há dado nem consumidor
que justifique esse custo agora, e G2 mostra que a aproximação atual produz o oposto do
contrato de materialidade.

### D-M32-02 — Objeto descritivo

Uma posição descritiva carrega, e só carrega:

| Campo | Definição |
|---|---|
| `n` | número de valores de referência finitos usados |
| `n_below`, `n_tied`, `n_above` | contagens exatas contra o valor do jogador; somam `n` |
| `mid_rank_quantile` | `(n_below + 0,5·n_tied) / n`; `None` se `n = 0` |
| `direction` | `TWO_TAILED`, `HIGHER_BETTER` ou `LOWER_BETTER` |
| `sufficiency` | `INSUFFICIENT` (n < 8), `SUFFICIENT_FOR_COMPARISON` (8 ≤ n < 15), `SUFFICIENT_FOR_GRADING` (n ≥ 15) |
| `band` | `green`/`yellow`/`red` só com `SUFFICIENT_FOR_GRADING`; senão `None` |
| `summary` | P10, P25, P50, P75, P90 (tipo 7) só com `n ≥ 8`; senão `None` |

Não há p-valor, intervalo de confiança, score ou peso.

### D-M32-03 — Quantis, empates e bandas

- Quantis de resumo: interpolação linear sobre os valores ordenados (Hyndman–Fan tipo 7,
  `statistics.quantiles(method="inclusive")`), como hoje.
- Posição: quantil médio (mid-rank) acima. Empates contam meio para cada lado.
- Bandas: exatamente as de §2.1, sem mudança de corte. A banda da interface nova é idêntica a
  `grade_deviation`/`grade_scalar` para toda entrada finita com `n ≥ 15` (equivalência
  testada), de modo que M3.4 possa religar consumidores sem mudar grade nenhuma.
- Afirmações ordinais saem das contagens, nunca do quantil: "abaixo de todas" só com
  `n_below = 0` e `n_tied = 0` (orientado pela direção).

### D-M32-04 — Valores não finitos

Valor do jogador não finito → `INVALID` com razão `NONFINITE_TARGET_VALUE`, sem posição.
Referências não finitas são **excluídas** e contadas em `excluded_nonfinite`; `n` é só dos
finitos. Nunca entram no denominador (corrige G5 na interface nova).

### D-M32-05 — N da própria métrica

A interface recebe os valores da população já selecionada e não escolhe população. A regra
"cada grade usa valores e N da própria métrica" é verificada por consumidor em M3.4, a partir
da tabela de §2.1. Preenchimento com zero (G6) é proibido na entrada da interface: o chamador
passa apenas valores observados.

### D-M32-06 — Seção CLI de desvios menores: retirada (aceita)

A seção "Desvios menores (não significativos)" é **retirada já em M3.2**. Todo passo MATCH
graduado volta a ser listado inline na sua seção de cooldown, com a própria banda; nenhum
passo é escondido ou rebaixado por BH. `compute_minor_deviation_keys` e
`render_minor_deviations_section` são removidos, e o relatório CLI deixa de chamar
`benjamini_hochberg`. O BH continua só em `findings` (seleção de coaching) até M3.4.

### D-M32-07 — "IC90": retirado (aceita)

O "IC90" é **apenas retirado** das saídas, sem substituto. `render_match_step_line` deixa de
imprimir intervalo. O campo `ci90` de `StepGrade`/`ScalarFinding` e `bootstrap_median_ci`
ficam sem consumidor de saída e são removidos em M3.4, junto com a religação dos produtores.

### D-M32-08 — Rótulo de "confiança" (aceita)

As faixas de `compute_confidence` passam a ser definidas na metodologia como **suporte da
comparação** (tamanho de amostra e relaxamento), não confiança estatística nem causal. O
parâmetro `survives_bh` é removido (nenhum chamador o usa). A troca da palavra "confiança" nas
saídas fica para M5.2 (B08), porque muda redação, não semântica.

### D-M32-09 — Regra R-M32-01: uptime amarelo e retirada do BH (aceita)

Hoje o BH impede que qualquer uptime amarelo chegue ao coaching (G2). Retirar o BH muda isso
silenciosamente. Regra vinculante:

> **R-M32-01.** O filtro BH em `findings.build_findings` só pode ser removido (M3.4) depois que
> M3.3 registrar, na tabela de evidência por família de recomendação (B09), se um uptime com
> banda amarela é elegível para recomendação, observação ou abstenção. Até esse registro, o
> comportamento atual de seleção é preservado e testado (AC7). A remoção do BH nunca pode ser
> o mecanismo que decide essa elegibilidade.

A regra é registrada também em [`roadmap.md`](roadmap.md) §M3.3 e §M3.4.

## 4. Interface

`src/botgitgud/analysis/grade_semantics.py`, versão `grade-semantics-v1`:

```python
class GradeDirection(StrEnum): TWO_TAILED; HIGHER_BETTER; LOWER_BETTER

@dataclass(frozen=True, slots=True)
class DescriptivePosition:
    version: str                     # "grade-semantics-v1"
    status: MetricStatus             # AVAILABLE ou INVALID
    reasons: tuple[str, ...]
    direction: GradeDirection
    n: int
    n_below: int
    n_tied: int
    n_above: int
    excluded_nonfinite: int
    mid_rank_quantile: float | None
    sufficiency: SufficiencyState    # reutiliza M2.2
    band: Literal["green", "yellow", "red"] | None
    summary: QuantileStats | None    # reutiliza grading.QuantileStats

def describe_position(
    value: float, reference_values: Sequence[float], direction: GradeDirection
) -> DescriptivePosition: ...
```

Função pura, sem RNG, resultado independente da ordem de `reference_values`.

## 5. Critérios de aceite

| AC | Roadmap | Critério | Evidência |
|---|---|---|---|
| AC1 | 1 | `n`, contagens e quantis usam só os valores recebidos e finitos; `n_below + n_tied + n_above = n`. | Propriedade (hypothesis) com listas aleatórias, inclusive NaN/±inf. |
| AC2 | 2 | `summary` coincide com `numpy.percentile(..., method="linear")`; `mid_rank_quantile` coincide com cálculo independente por contagem; resultado invariante a permutação. | Oráculo independente; empates massivos (mortes = 0 em 80%); `n` em {1, 2, 7, 8, 14, 15, 60}. |
| AC3 | 3 | Nenhuma saída CLI ou Discord contém "significativ", "FDR", "p-valor", "IC90" ou "intervalo de confiança". | Varredura das saídas golden e de um replay do corpus real; teste de regressão das strings. |
| AC4 | 3 | Banda da interface = grade atual para toda entrada finita com `n ≥ 15`, nas três direções. | Propriedade contra `grade_deviation`/`grade_scalar`; fronteiras 0,10/0,25/0,75/0,90 exatas. |
| AC5 | 5 | `n < 15` → `band = None`; `n < 8` → `summary = None`; nenhuma mediana é exibida abaixo do piso contratado; nenhum intervalo é exibido em caso algum. | Casos `n` em {0, 1, 7, 8, 14}; renderização CLI de G4 e G7. |
| AC6 | — | Posição ordinal correta: "abaixo de todas" ⇔ `n_below = 0 ∧ n_tied = 0`. | Caso de G8 (`n = 15`, uma referência menor). |
| AC7 | — | Seleção de coaching inalterada (R-M32-01): findings, remediação, candidatos materiais e resposta Discord idênticos antes e depois em todo o corpus. | Replay do corpus real comparando `ReportContract` serializado e a resposta de coaching. |
| AC8 | 3 | CLI sem seção de desvios menores e sem chamada a BH: todo passo MATCH graduado aparece inline com sua banda; o conjunto de passos listados é o mesmo de antes (só muda a seção). | Golden do CLI; teste que conta passos MATCH graduados antes/depois. |

## 6. Limitações

- O filtro BH continua decidindo quais uptimes chegam ao coaching até M3.4 (G2), sob
  R-M32-01. M3.2 o retira do CLI e retira a alegação estatística das saídas.
- Bandas 10/25/75/90 são convenção de produto, não calibração.
- Mid-rank em distribuições muito discretas (mortes) produz poucas posições possíveis; a
  interface expõe as contagens para que consumidores não leiam precisão inexistente.
- A grade de desperdício continua com zeros fabricados (G6) até M3.4 religar a M3.1.

## 7. Consumidores e lacunas para M3.4

| Consumidor | Ação em M3.4 |
|---|---|
| `findings.build_findings` (BH sobre UPTIME) | Remover BH, só depois do registro exigido por R-M32-01 em M3.3. |
| `comparison._grade_match_steps`, `performance_features.grade_scalar` | Derivar grade de `describe_position`; remover `ci90`. |
| `performance_features._build_waste_findings` | Valores observados por tipo via `stream_availability` (M3.1 F6). |
| `coaching_answer` (posição ordinal) | Contagens em vez de `q ≤ 1/n` (texto em M5.2). |
| `grading.bootstrap_median_ci`, `FDR`, `benjamini_hochberg`, `two_tailed_p_value` | Remover quando sem consumidor. |

## 8. Registro de decisões do dono (2026-10-05)

| Pergunta | Decisão |
|---|---|
| Seção "Desvios menores" | Retirar já em M3.2 (D-M32-06) |
| "IC90" | Apenas retirar (D-M32-07) |
| Palavra "confiança" | Definir como suporte da comparação; troca da palavra em M5.2 (D-M32-08) |
| Uptime amarelo após retirada do BH | Regra vinculante R-M32-01 (D-M32-09) |
