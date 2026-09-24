# Metodologia de medidas e comparabilidade

Contratos vigentes de medida, contabilidade de dano e comparabilidade de referências. Eles
estão implementados e protegidos por testes permanentes; o que ainda está aberto está em
[`roadmap.md`](roadmap.md). O comportamento voltado ao jogador está em
[`product.md`](product.md).

Estado: **M0, M1 e M2 fechados** (M2 = M2.1, M2.2 e M2.3). M3–M6 não iniciados.

> Os módulos `analysis/reference_eligibility.py` e `analysis/metric_population.py` são
> byte-idênticos à entrega fechada (um teste de regressão fixa seus hashes), por isso suas
> docstrings ainda citam `docs/m2-1-specification.md` e `docs/m2-2-specification.md`. Esses
> documentos foram consolidados nas §3 e §4 abaixo; o texto original está no histórico Git.

## 1. Princípios normativos (C01–C10)

| ID | Contrato |
|---|---|
| C01 | Dano bruto, dano ajustado por suporte e DPS de ranking do WCL são medidas distintas; registrar fonte, escopo, versão, duração e ajustes, sem misturar denominadores. |
| C02 | Todo percentual declara denominador e escala; percentuais com denominadores diferentes não são somados. |
| C03 | Cast do jogador, tick, hit de pet e proc não são unidades intercambiáveis. Identidade canônica pode relacioná-los, mas não prova que todos são ações controláveis. |
| C04 | Participação no dano descreve composição; não prova frequência, eficiência, uptime nem execução correta. Cada afirmação usa a distribuição da própria medida. |
| C05 | Diferença observada não é ganho recuperável nem efeito causal. Recomendação precisa de evidência adicional. |
| C06 | Desconhecido, não aplicável, observação incompleta e zero confirmado são estados diferentes; zero exige coleta suficiente e aplicabilidade comprovada. |
| C07 | Comparabilidade é definida **por métrica**: versão, mecanismo, duração/exposição e condições relevantes precisam ser compatíveis ou declaradas. |
| C08 | Disponibilidade é condição necessária, não suficiente, de recomendação temporal. |
| C09 | Cada frase deve ser rastreável à medida e à população corretas; hipóteses e restrições não podem desaparecer na renderização. |
| C10 | Correção de semântica exige versão nova dos derivados afetados; brutos e resultados históricos são preservados. |

Afirmações e a prova mínima de cada uma:

| Estado / afirmação | Prova mínima |
|---|---|
| Zero observado | coleta completa da métrica + entidade aplicável (ainda não "oportunidade perdida") |
| Não aplicável | build/versão demonstra ausência do mecanismo |
| Desconhecido | falta fonte de build/cooldown/eventos → declarar indisponibilidade |
| Incompleto | paginação falhou ou cobertura parcial → só medida explicitamente parcial |
| "Menos usos" | medida de contagem/taxa apropriada e referência compatível |
| "Oportunidade perdida" | também disponibilidade e aplicabilidade |
| "Deveria usar aos 95 s" | contexto suficiente para recomendar aquele instante |
| "Ganhará X%" | estimativa de efeito de uma mudança — a decomposição contábil não fornece isso |

## 2. Medidas e contabilidade de dano (M1)

### 2.1 Estimando

A decomposição explica a **diferença entre o DPS líquido medido do jogador e a média
aritmética dos DPS líquidos medidos das referências aceitas**, um voto por log, sem
trimming/winsorização e sem escolher referências pelo efeito no resultado. A mediana de DPS do
WCL é contexto separado, nunca o total que a decomposição explica. A referência aspiracional,
quando existe, é uma comparação separada com a mesma fórmula e seu próprio conjunto.

### 2.2 Equações

Para o jogador `u`, cada referência `i ∈ R`, duração `T > 0` e habilidade `a`:

```text
D_j,a = dano bruto observado da habilidade a no escopo elegível
g_j,a = D_j,a / T_j            s_j = S_j / T_j (suporte agregado)
y_j   = Σ_a g_j,a − s_j        (DPS líquido medido)

reference_dps   = (1/N) Σ_i y_i
delta_a_dps     = (1/N) Σ_i (g_u,a − g_i,a)
support_delta   = (1/N) Σ_i (s_i − s_u)
total_delta_dps = y_u − reference_dps

Σ_a delta_a_dps + support_delta == total_delta_dps
```

Percentuais, cada um com seu nome e denominador; `None` quando o denominador é zero:

```text
gap_vs_reference_pct    = 100 · total_delta_dps / reference_dps
ability_delta_player_pp = 100 · delta_a_dps / y_u
support_delta_player_pp = 100 · support_delta / y_u
total_delta_player_pp   = 100 · total_delta_dps / y_u
```

Split mecânico por par (não causal), com `h = hits` de dano:
`volume + per_event + interaction == g_u,a − g_i,a`, calculado apenas quando os dois lados têm
hits > 0, contagem completa e um único bucket compatível de origem (PLAYER/PET) e
periodicidade (TRUE/FALSE). Os demais pares entram como `unclassified`, com peso original
`1/N`: `volume + per_event + interaction + unclassified == delta_a_dps`. `unclassified` é
diferença contábil sem classificação, não ruído nem ganho.

Tolerância das identidades: `|residual| ≤ max(1e-9, 1e-12 · scale)` com `math.fsum`; acima
disso a comparação é bloqueada, nunca rateada.

### 2.3 Regras de contabilidade

- Ledger por `spell_id`; cada evento elegível entra uma vez; suporte é linha separada.
- Participação de habilidade = `100 · D_a / D_bruto` (soma 100% do bruto). Dano dividido pelo
  líquido é "contribuição contábil", nunca chamado de share.
- Sem referências: accounting individual disponível e comparação `NO_REFERENCES`; nunca uma
  coorte simulada de DPS zero.
- Líquido negativo bloqueia; `S` desconhecido não é zero confirmado.
- **Alvos por cast** não são calculados (`CAST_INSTANCE_LINK_UNAVAILABLE`): não há prova de
  associação evento → instância de cast. O campo histórico é só leitura e não é consumido.
- Contagem de casts vem da timeline do jogador; hits, dos eventos de dano; um nunca substitui o
  outro. A parcela por eventos não é chamada de "usos perdidos" nem "dano por cast".

### 2.4 Proveniência de medida e elegibilidade de dados

`PlayerLog.measurement_provenance` (`measurement-input-v1`, Parquet aditivo
`measurement_provenance_json`; ausente = legado, nunca COMPLETE): status de coleta de dano e
casts (`COMPLETE`/`PARTIAL`/`UNKNOWN`, com razões e intervalo), `damage_table_total`, atores
jogador/pets e `damage_event_mix_by_spell` por origem e periodicidade.

| Entrada | Accounting | Comparação quantitativa |
|---|---|---|
| Nova, reconciliada, dano COMPLETE | disponível | disponível se `R` elegível |
| Nova, PARTIAL/INVALID/UNRECONCILED | só diagnóstico parcial | bloqueada |
| Histórica reconciliada sem metadados | `LEGACY_RECONCILED_TOTAL` | disponível para dano reconciliado, sem afirmar completude de casts |
| Histórica `LEGACY_UNSCOPED` | subtotal observado | bloqueada |
| Histórica `UNRECONCILED` | indisponível | bloqueada |

`R` é o subconjunto do ledger (§4) com accounting líquido elegível e escopo compatível. Piso
público de **8** referências para publicar a comparação (abaixo disso: `INSUFFICIENT_REFERENCES`);
grades só com **N ≥ 15** observações da própria métrica. São limiares de política atual, não
calibração de confiança (M3/B06). Versões `measurement-input-v1` e `damage-comparison-v2` são
persistidas no `RunManifest`; manifestos antigos são lidos como versão desconhecida.

### 2.5 Métricas por habilidade

`MetricObservation` (`AVAILABLE`/`PARTIAL`/`UNKNOWN`/`NOT_APPLICABLE`/`INVALID`, com razões;
só `AVAILABLE` participa de grade). Cada métrica tem seu próprio N.

| `metric_id` | Fórmula |
|---|---|
| `gross_ability_dps` | `D_a/T`; exige dano positivo da habilidade nos dois lados, identidade resolvida e reconciliação |
| `player_casts_per_minute` | `60 · casts_a / T`; coleta de casts COMPLETE |
| `damage_events_per_second` | `hits_a / T` |
| `damage_per_event` | `D_a / hits_a` se `hits_a > 0` |
| `aura_uptime_fraction` | fração observada em [0,1], entrada explícita nos dois lados |
| `gross_damage_share_pct` | `100 · D_a / D_bruto`; composição, não materialidade |

### 2.6 Materialidade mínima

`gross_ability_dps` é o escalar de visibilidade de déficit absoluto: delta negativo, magnitude
≥ 0,5 pp quando definida e grade vermelha/amarela na distribuição própria (N ≥ 15). O convite
genérico de revisão (base `OBSERVED_OUTPUT_DEFICIT`, condição `CAUSE_NOT_IDENTIFIED`) só vale
para entidades com identidade resolvida, papel ofensivo próprio, cast do jogador no mesmo ID,
dano exclusivamente de origem PLAYER e escalar material. Ordem entre candidatos do mesmo tipo
usa déficit observado, não retorno estimado.

### 2.7 Dados experimentais (B07, recorte M1)

A feature de alvos por cast foi aposentada do vetor experimental; novos datasets carregam
`feature_schema_version = experimental-features-v2` com fingerprint canônico de todas as
features materializadas. Resultados históricos mantêm sua versão. O restante de B07 é M6.

## 3. Elegibilidade básica das referências (M2.1)

`analysis/reference_eligibility.py`, política `reference-eligibility-v1`. Função pura de dois
logs (alvo, referência); avalia sempre os cinco eixos. Agregação: algum `INELIGIBLE` →
`INELIGIBLE`; senão algum `INDETERMINATE` → `INDETERMINATE`; senão `ELIGIBLE`.
`INDETERMINATE` nunca é promovido.

| Eixo | Regra | Motivos |
|---|---|---|
| IDENTITY | mesmo encontro, dificuldade, classe e spec (comparação `strip`+`casefold`); não pode ser o próprio alvo | `SELF_REFERENCE`, `ENCOUNTER_MISMATCH`, `DIFFICULTY_MISMATCH`, `CLASS_MISMATCH`, `SPEC_MISMATCH`, `IDENTITY_UNKNOWN` |
| ATTEMPT_STATE | referência precisa ser kill; duração finita e > 0 nos dois lados | `ATTEMPT_STATE_NOT_KILL`, `INVALID_DURATION` |
| PARTITION | iguais → elegível; diferentes → inelegível; ausente → indeterminado | `PARTITION_MISMATCH`, `PARTITION_UNKNOWN` |
| DAMAGE_SCOPE | `UNRECONCILED` ou escopos diferentes → inelegível | `SCOPE_UNRECONCILED`, `SCOPE_MISMATCH` |
| HOTFIX | não observável; nunca decide | `HOTFIX_NOT_OBSERVABLE` |

- "Log antigo" é exatamente partição diferente ou desconhecida; data de calendário não é
  versão de mecanismo.
- Alvo que não é kill não exclui referências kill, mas a população declara
  `TARGET_ATTEMPT_NOT_KILL`.
- Com ao menos uma referência elegível, a população declara `HOTFIX_COMPATIBILITY_UNVERIFIED`.
- Fonte autoritativa nova (ex.: versão de jogo) exige `reference-eligibility-v2`.

## 4. População por métrica (M2.2)

`analysis/metric_population.py`, política `metric-population-v1`. A população é definida por
`metric_id` (`f"{metric}:{spell_id}"`), em estágios: A (elegibilidade M2.1), B (covariáveis),
C (disponibilidade da métrica), D (aspiracional).

Covariáveis admitidas como inclusão: `duration`, `item_level`, `tier_pieces` e
`external_buffs` (interseção com `EXTERNAL_OFFENSIVE_IDS`). Talentos/setup,
`has_augmentation` (covariável declarada de ajuste), `dps`/`percentile`, datas, `role`,
`server` e nome **nunca** filtram.

| `metric` | `duration` | `external_buffs` | `item_level` | `tier_pieces` |
|---|---|---|---|---|
| `gross_ability_dps`, `damage_per_event`, `damage_events_per_second`, `gross_damage_share_pct` | REQ (ladder amplo) | REQ, não relaxável | REL | REL |
| `player_casts_per_minute` | REQ (ladder posicional) | REQ, não relaxável | NA | NA |
| `aura_uptime_fraction` | REQ (ladder amplo) | REQ, não relaxável | NA | NA |

- Ladder amplo de duração: `(0.07, 0.12, 0.20, 0.35)`; posicional: `(0.07, 0.12)`.
- Ordem de relaxamento: `tier_pieces` → `item_level` → alargamento de duração, um passo por vez,
  **somente até o piso de 8**; nunca para alcançar 15. Cada passo registra covariável, regra,
  N antes/depois e IDs admitidos.
- Covariável desconhecida na referência a exclui no nível estrito; desconhecida no alvo torna a
  covariável inadmissível (declarada `TARGET_*_UNKNOWN`).
- **DESCRIPTIVE**: todos os admitidos com observação `AVAILABLE`; é a única distribuição de
  quantil/grade da métrica.
- **ASPIRATIONAL**: subconjunto da DESCRIPTIVE via `select_benchmark_reference`, ordenado pelo
  desfecho (`dps` do WCL), excluindo antes quem não tem `dps` finito; indisponível abaixo de 8
  ordenáveis. Nunca fornece quantil, grade ou N à descritiva.
- Suficiência: `n < 8` → `INSUFFICIENT`; `8 ≤ n < 15` → `SUFFICIENT_FOR_COMPARISON`;
  `n ≥ 15` → `SUFFICIENT_FOR_GRADING`. N elevado não é prova de ajuste.
- Limitações sempre declaradas quando aplicáveis: `RELAXATION_APPLIED` e
  `COVARIATE_ADJUSTMENT_UNVERIFIED` (relaxar não é ajustar), `SETUP_NOT_MATCHED`,
  `AUGMENTATION_NOT_MATCHED`, `ASPIRATIONAL_SELECTED_ON_OUTCOME`,
  `ASPIRATIONAL_ORDERED_BY_WCL_DPS`, `ASPIRATIONAL_UNAVAILABLE`, além das herdadas de M2.1.
- Códigos de exclusão e limitação são listas fechadas; ampliar exige `metric-population-v2`.

## 5. Integração no caminho de análise (M2.3)

### 5.1 Ordem de estágios em `run_analysis`

0. **Quarentena** de representações divergentes sobre a lista buscada inteira.
1. **Higiene** (`hygienic_candidates`): exclui o próprio jogador e não-kills; uma referência por
   pull e uma por jogador (`dedup_priority`).
2. **M2.1** sobre o conjunto higiênico.
3. **Ledger** (`R_log`): `match_covariates` sobre os elegíveis, `min_n = 15`, política de
   matching v2.
4. **M2.2** sobre o conjunto higiênico inteiro (não sobre `R_log`).

`match_cohort` continua sendo exatamente higiene + `match_covariates`, com saída idêntica à
anterior para qualquer entrada; a quarentena não é chamada por ele.

### 5.2 Quarentena (`quarantine_conflicting_duplicates`)

- Domínio: candidatos que não são o próprio jogador e são kills.
- Unidade de conflito: a **observação** `(report_code, fight_id, player_identity)`; um grupo está
  em conflito quando contém dois logs diferentes por `!=` (inclusive só no percentil).
- `conflicting_ids` = `damage_reference_id` de todos os logs dos grupos em conflito; são
  removidos **todos** os logs do domínio com esses ids (inclusive um homônimo de outro servidor
  no mesmo pull). Nenhuma representação é escolhida, mesclada ou reparada.
- Grupos de logs iguais passam intactos (a higiene os colapsa).
- Resultado é função do multiconjunto de candidatos: a proveniência é invariante à ordem da
  lista buscada.

### 5.3 Roteamento de população por consumidor

| Consumidor | População |
|---|---|
| As seis métricas em `compare_metrics` (grades por habilidade, materialidade de `gross_ability_dps`) | DESCRIPTIVE de M2.2 do próprio `metric_id`, exatamente seus membros |
| Grade de uptime em `performance_features` | DESCRIPTIVE de `aura_uptime_fraction:<sid>`; o gate de relevância `presence` continua sobre `R_log` |
| `compare_damage` (ledger, suporte, residual), mediana de DPS medido, conclusão | `R_log` e seus aceitos M1 |
| Aspiracional do ledger | `select_benchmark_reference` sobre `R_log`, excluindo `dps` não finito; indisponível abaixo de 8 ordenáveis (`ASPIRATIONAL_UNAVAILABLE`) |
| Demais grades de performance, perfil de cooldowns, comparações por spell, seções por habilidade, duração do header | `R_log` |
| Populações ASPIRATIONAL por métrica | somente proveniência |

Nenhum consumidor recebe referência `INELIGIBLE` ou `INDETERMINATE`. Uma grade só existe com
`SUFFICIENT_FOR_GRADING`.

### 5.4 Insuficiência

A análise nunca aborta por tamanho de população. `ledger_state = SUFFICIENT` se
`len(R_log) ≥ 8`, senão `INSUFFICIENT_REFERENCES` (o `accounting_status` de M1 continua com
suas próprias regras, incluindo `NO_REFERENCES`). Com ledger insuficiente, são sempre
computados o accounting do jogador, as seis métricas pela própria população e suficiência,
setup e demais campos independentes; ficam ausentes `performance`, comparações por spell,
`core_abilities`, `proc_analysis`, `external_dps_context` e o aspiracional do ledger. Uma
métrica suficiente não é bloqueada pela insuficiência do ledger. CLI e Discord não exibem
número de comparação não computada.

### 5.5 Proveniência persistida — `comparability-provenance-v1`

`analysis/comparability_provenance.py`. `ComparabilityProvenance` registra versões das
políticas (M2.1, M2.2, matching do ledger, proveniência), `target_id`, higiene (incluindo
`excluded_conflicting_duplicates` e `conflicting_duplicate_ids`), elegibilidade (listas
ordenadas e motivos verbatim), ledger (estado, membros, covariáveis casadas/relaxadas/de
ajuste, nível da coorte, aceitos e exclusões contábeis, aspiracional) e, por métrica, as
populações descritiva e aspiracional (N, suficiência, membros, banda final, passos de
relaxamento, contagem de exclusões por código, limitações).

Invariantes: IDs ordenados; `n_input == n_output + quarentena + self + não-kill + dedup por
pull + dedup por jogador`; nenhum id em quarentena aparece em elegibilidade, ledger ou métricas;
`accepted_ids ⊆ member_ids ⊆ eligible_ids`; aspiracional ⊆ descritiva.

Propagação: `AnalysisResult.comparability` e `ReportContract.confianca.comparability`
(cópia sem transformação). Persistência aditiva em `RunManifest`/tabela `runs`:
`reference_eligibility_policy_version`, `metric_population_policy_version`,
`ledger_matching_policy_version`, `comparability_provenance_version` e
`comparability_provenance_json` (JSON canônico: chaves ordenadas, separadores compactos,
ASCII, sem NaN). Linhas antigas ficam `NULL` e são lidas como versão desconhecida; versão
desconhecida no decode levanta `ValueError`.

## 6. Limitações declaradas

- O ledger usa a política de matching v2, inclusive o crescimento acima do piso até 30
  (estágio "low bias": só `tier_pieces`/`item_level` cedem); as seis métricas não crescem além
  do piso. Populações distintas por finalidade são o contrato de C07.
- Compatibilidade de hotfix não é verificável; relaxar covariável não é ajustar por ela; setup e
  talentos nunca são casados com a execução.
- A quarentena pode reduzir N e excluir um homônimo de outro servidor no mesmo pull, porque
  `reference_id` não distingue servidor.
- Populações aspiracionais por métrica não têm consumidor de produto.
- Limiares 8/15 são política de amostra, não calibração estatística (B06, M3).
- A única fixture real end-to-end (Zarad) é um log de partição antiga: todas as referências são
  `PARTITION_MISMATCH`, então o golden cobre o caminho de ledger vazio, não conteúdo completo.
- Apresentação: o texto cita o N do ledger (`Coorte pareada: N logs`, em
  `report/text.py`) ao lado de grades de uptime cuja população é outra, e a seção de uptimes
  (`report/performance_text.py`) não mostra o N próprio. Dívida de M5.2.
