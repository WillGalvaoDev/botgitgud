# M0 — Contratos de medidas e diagnóstico confirmado

Data: 2026-09-10. Escopo: análise, documentação e reprodução offline de defeitos.
Este documento especifica a base para M1–M6; não declara esses marcos implementados.
Nenhuma fórmula, schema persistido, política de coorte ou resposta de produção muda em M0.
As regras normativas abaixo são requisitos futuros; a coluna “atual” descreve o código.

## 1. Decisões normativas fechadas em M0

| ID | Contrato | Consequência para os próximos marcos |
|---|---|---|
| C01 | Dano bruto, dano ajustado por suporte e DPS de ranking são medidas distintas até reconciliação explícita. | Registrar fonte, escopo, versão, duração e ajustes; não misturar denominadores. |
| C02 | Todo percentual declara seu denominador e escala. | `gap_pct` atual é fração da referência; `delta_dps_pct` é percentual do dano medido do jogador. Não somar diretamente. |
| C03 | Cast do jogador, tick, hit de pet e proc não são unidades intercambiáveis. | Identidade canônica pode relacioná-los, mas não prova que todos são ações controláveis. |
| C04 | Participação no dano descreve composição. | Não prova frequência, eficiência, uptime ou execução correta. Cada afirmação usa a distribuição da própria medida. |
| C05 | Diferença observada não é ganho recuperável nem efeito causal. | A decomposição deve declarar qual diferença explica; recomendações precisam de evidência adicional. |
| C06 | Desconhecido, não aplicável, observação incompleta e zero confirmado são estados diferentes. | Zero exige coleta suficiente e aplicabilidade comprovada; ausência de cast sozinha não prova oportunidade perdida. |
| C07 | Comparabilidade é definida por métrica. | Identidade da luta/spec não basta: versão, mecanismo, duração/exposição e condições relevantes precisam ser compatíveis ou declaradas. |
| C08 | Disponibilidade é condição necessária, mas não suficiente, de recomendação temporal. | Um cooldown disponível não prova que usá-lo imediatamente era a melhor decisão. |
| C09 | Cada frase deve ser rastreável à medida e à população corretas. | Hipóteses e restrições não podem desaparecer na renderização. |
| C10 | Correção de semântica requer versão nova para os derivados afetados. | Preservar brutos e resultados históricos; não reinterpretar silenciosamente datasets experimentais. |

Definição provisória de vocabulário: `D_bruto` é a soma dos eventos elegíveis de
jogador + pets, incluindo absorção conforme a ingestão; `S` é a atribuição de suporte
a subtrair; `D_ajustado = D_bruto - S`; `T` é a duração integral da luta em segundos.
`D_ajustado / T` só pode ser chamado de DPS reconciliado quando houver autoridade e
escopo compatíveis. A escolha do resultado principal e da referência final é B01.
Não distribuir `S` entre habilidades sem uma fonte que sustente essa distribuição.

## 2. Inventário das medidas atuais

Os caminhos abaixo são relativos a `src/botgitgud/`. Toda distribuição se refere
à amostra efetivamente usada pela métrica, não à população global de jogadores.

| Medida / pergunta | Origem e fórmula atual; unidade/denominador | População e limites | Consumidores |
|---|---|---|---|
| `dps`, `percentile`: qual resultado no WCL? | `ingest/log_fetcher.py` e `fight_rankings.py`; resultado obtido do WCL; dano/s e percentil 0–100 | Não confundir percentil global com quantil da coorte; sem resultado pode ser `None` | `pipeline`, conclusão, header, benchmark reference; label experimental por dataset/campanha |
| `AbilityDamage.total`: quanto dano foi observado? | `damage_aggregation.py`: soma `amount + absorbed`, por spell/source e alvos elegíveis | Jogador + pets; guarda por `DamageScopeVersion`; não é habilidade necessariamente acionável | `dps_gap`, classificação, core abilities, relevância e apresentação |
| `support_subtracted_damage`: qual ajuste de suporte? | `support_subtracted_total`; soma eventos com atribuição ao jogador/dono do pet | Ajuste agregado, não uma habilidade; aplicado no log V1 | Reconciliação da ingestão e denominadores de `dps_gap` |
| `measured_dps`: qual dano medido ajustado por segundo? | `(soma de totais positivos - S) / T` | Referências com mesmo damage scope; `UNRECONCILED` bloqueia quantitativo; legacy não comprova reconciliação | Decomposição, shares e texto de dano |
| `gap_pct`: qual distância do resultado geral? | `(player_dps - median_dps) / median_dps`; fração | Mediana da coorte geral pode diferir da subamostra aceita pelo guarda de dano | Resultado de DPS e texto |
| `n_u/n_r`, `p_u/p_r`: volume e dano por unidade? | Cast se todos os portadores de dano têm casts; caso contrário hits. `p=total/n`; medianas separadas por habilidade | Ausência vira `(0,0)`; contagens brutas de durações distintas; incompatibilidade de build não resolvida aqui | Oaxaca, diagnóstico, findings e prioridades |
| `volume`, `efficiency`, `interaction`: componentes da diferença? | `(nu-nr)pr`, `(pu-pr)nr`, `(nu-nr)(pu-pr)`; dano | Identidade fecha contra `nr*pr`, não necessariamente contra mediana de DPS | `dps_gap`, diagnóstico e relatórios |
| `delta_dps_pct`, `other_pct`: diferença por habilidade? | `100*(du-nr*pr)/(T*measured_dps)`; pp do dano do jogador | Gate de 0,5 pp; não resolvidos/subgate vão a “other”; suporte não aparece como componente | Findings, `estimated_gain_pct`, priorização e CLI |
| `n_ref/p_ref`, `delta_dps_pct_ref`: referência aspiracional? | Mesma fórmula contra cauda superior selecionada em `benchmark_reference.py` | Referência distinta da mediana da coorte; fallback mínimo de oito/um terço | `DpsGapReport` e consumidores de relatório; não confundir com benchmark de setup |
| `cohort_share`: qual participação da habilidade? | `100*dano_habilidade/(T*DPS_medido)`; grade por shares individuais | Composicional: variar outra habilidade altera a proporção; suporte pode fazer shares excederem 100% | Materialidade, observação positiva, ranking verbalizado no coaching |
| casts/min e timing: quando e com que frequência houve cast? | `profile.py`: casts/T*60, reescala contagem para T do jogador; medianas de slots, por fase/ocorrência | Timing usa subconjunto ±12%; zero-padding não comprova disponibilidade de habilidade | Cadência, alinhamento, comparação, CLI; agregados de casts no ML |
| `active_time_pct`: qual atividade reportada? | `performance_parsing.py`: `DamageDone.activeTime / duração_ms`; fração | Proxy do WCL, não ocupação de GCD nem decisão voluntária; `None` quando ausente | Grade, candidato ACTIVE_TIME, coaching e `c_active_time_pct` |
| `deaths`, `downtime_s`: morreu/quando voltou a agir? | Contagem de mortes; soma até próximo cast ou fim; unidades e segundos | Próximo cast comprova atividade, não timestamp exato de ressurreição | Performance, remediação, coaching; `c_deaths`, `c_downtime_s` |
| `uptimes`: por quanto tempo a aura esteve presente? | `totalUptime/totalTime`; fração; referências presentes e presença mínima de 70% | Jogador ausente vira zero; aura/talento/buff pode não ser aplicável; IDs de cast/dano/aura podem diferir | Grade, relevância, proc analysis; `c_mean_uptime`, `c_n_tracked_auras` |
| `resource_waste`: quanto recurso foi desperdiçado? | Soma `waste` por tipo; unidades de cada recurso; por habilidade em mapa separado | Referência ausente vira zero; total cresce com duração; não somar recursos como se tivessem unidade universal | Performance/coaching; ML soma tipos em `c_resource_waste_total` e divide por minutos |
| `avg_targets_per_cast`: quantos alvos? | Alvos distintos na luta / casts da habilidade | Não é média real por cast; omitted quando casts=0; associação temporal não existe nesse agregado | Diagnóstico `poucos_alvos`; ML `c_mean_targets_per_cast` |
| Métricas de proc | `proc_analysis.py`: bandas, primeiro cast ofensivo após início, sobreposição, bandas sem cast | Qualquer cast ofensivo; não comprova consumo do proc específico | Core/proc analysis e relatório detalhado; não motor de oportunidades |
| Setup: escolhas/prevalências | `SetupProfile`, `benchmark_aggregate`, `setup_*`; contagens/n disponível por categoria e banda; stats mínimos observados | Benchmark independente; frequência não é qualidade; stats mínimos não são armory puro | Setup findings, resumo/CLI; parte do contexto experimental |
| Grade, quantil, CI e confiança | `grading.py`, `performance_features.py`, `findings.py`: mid-rank; mínimo 15; bootstrap 90% da mediana; bandas de confiança | Não probabilidade causal; p=2*min(q,1-q) pode ser zero; BH não cobre todos os caminhos | Materialidade, prioridade, texto; B06 define interpretação futura |

## 3. Mapa de propagação e riscos adicionais

1. `ingest/*` → `PlayerLog` → Parquet/Store → `analysis/pipeline.py`.
2. Pipeline → `dps_gap`/`performance_features` → `findings` → `remediation`
   → `materiality` → `prioritization` → `ReportContract` → `coaching_answer` (Discord).
3. O mesmo contrato → `report/render.py` → relatório textual do CLI, com conteúdo diferente.
4. `profile`/`comparison` → comparações por fase; o filtro `actionable_ids` do pipeline
   é aplicado a essas comparações, não a todos os déficits de dano que geram coaching.
5. Parquet/Store → `phase4/experimental_dataset.build_features` → registro de features
   → splits/modelos/avaliação. A métrica defeituosa de alvos chega ao ML, coberta por teste M0.

Na exportação experimental, `item_level`, tier, raid size e outras ausências podem virar
zero; uptime e alvos são médias entre habilidades, desperdício é soma entre tipos.
Essas transformações exigem revisão de unidades, disponibilidade e comparabilidade em M6.
`y_rank_percent` é o label atual; não é recomendação correta nem ganho por intervenção.
Não reclassificar esses agregados como controláveis apenas porque o registro atual os nomeia `c_*`.

O caminho de produção resolve capacidade no registry de Fase 4 quando configurado,
mas não usa essa resolução para gerar predição no coaching. A documentação antiga que
afirma que o registry nunca é consultado precisa ser reconciliada em M6.

## 4. Defeitos reproduzidos e comportamento exigido

Suite: [`test_m0_methodology_contract.py`](../tests/unit/test_m0_methodology_contract.py).
Os casos são sintéticos, usam funções reais e começam após seleção da coorte.
Não medem prevalência do problema em jogadores reais nem consomem API.

| ID / teste (`test_m0_…`) | Reprodução / saída atual | Comportamento exigido | Impacto / marco |
|---|---|---|---|
| D01 `duration_does_not_create_frequency_deficit` | 100 casts/300s versus 110/330s, dano por cast=1000; mesmo DPS, discrepância -10 pp | Duração sozinha não gerar déficit de frequência; não confundir taxa com oportunidade | Gap/findings; M1 |
| D02 `one_target_per_cast_is_one` (10 e 20 casts) | Mesmo alvo em todos os casts → 0,1 e 0,05 | Média real=1 quando observável; senão retirar esse significado da métrica | “Poucos alvos” e feature ML; M1/M6 |
| D03 `uniform_output_deficit_remains_visible_by_ability` | Metade do dano em duas habilidades, mesmas proporções → nenhum candidato de habilidade | Déficit absoluto deve continuar revisável; não obriga recomendar ação nem prometer ganho | Materialidade; M1/M3 |
| D04 `share_rank_must_not_be_rendered_as_use_rank` | 50 usos; duas referências têm 40 e 45, outras 100; share inferior a todas → “uso … o mais baixo” | Não afirmar que foi o menor uso; nenhuma posição pode vir de outra variável | Discord; M3/M5 |
| D05 `separate_medians_do_not_explain_overall_gap` | Pares (1,100), (2,2), (100,1), cinco de cada; mediana do dano=100, produto das medianas=4; gap geral zero e por habilidade +96 pp | Referência da decomposição e resultado devem ser coerentes, ou residual/diferença de estimando explícitos | B01 aberto; M1 |
| D06 `support_adjusted_breakdown_closes` | Dano bruto=100000, suporte=10000; sem referências, soma por habilidade=111,111…% | Fechar contabilidade do jogador com ajuste explícito ou indisponibilidade, sem inventar dano por habilidade | M1 |

D05 é caracterização que passa: prova a divergência sem impor uma escolha de estimando
antes de B01. Na correção, substituir suas expectativas atuais pelo contrato escolhido.
D01–D04 e D06 usam `xfail(strict=True, raises=AssertionError)`: representam requisitos
ainda não atendidos, não exceções aceitas para sempre. XPASS falha para exigir revisão e
remoção do marcador. Erros de execução não viram xfail por esses marcadores.

Controles adicionais: saída idêntica em luta de mesma duração; bloqueio de dano não
reconciliado; quantil de share distinto de quantil de usos; propagação da métrica de
alvos para a feature experimental. O teste antigo
`test_dps_gap.py::test_real_corpus_gated_and_other_sum_is_closed` permanece intacto:
a análise anterior observou 102,8168% no corpus local; M0 fornece D06 independente
da presença desse corpus. Não mascarar essa falha antiga com novos marcadores.

Na revisão de D04 foram usadas duas referências com menos casts, para isolar a troca
de métrica do problema adicional de fronteira `q <= 1/n` usado pelo renderizador.
Esse limite também pode chamar de “mais baixo” um valor com uma referência inferior;
registrado para M5, sem alterar a produção em M0.

## 5. Disponibilidade e comparabilidade: contratos para implementação futura

| Estado | Prova mínima | Afirmação permitida |
|---|---|---|
| Zero observado | Coleta completa para a métrica + entidade aplicável | “Não houve cast observado”; ainda não “perdeu oportunidade” |
| Não aplicável | Build/versão demonstra ausência do mecanismo | Não comparar com zero de quem poderia usá-lo |
| Desconhecido | Falta de fonte de build/cooldown/eventos | Declarar indisponibilidade |
| Incompleto | Paginação falhou ou cobertura parcial detectada | Somente medida explicitamente parcial; sem comparação total silenciosa |
| Oportunidade candidata | Disponibilidade, estado e instante observáveis | Revisão de oportunidade, com condições não observadas declaradas |

Identidade básica: classe/spec, encontro, dificuldade, partição/versionamento e damage
scope pertinentes. Kill/wipe requer política própria; a pipeline hoje não estabelece
equivalência entre o estado do jogador e as referências, que são kills. Talento,
equipamento, buffs, duração e disponibilidade de alvos devem ser avaliados por métrica.
Não transformar uma lista de covariáveis “relaxadas” em prova de ajuste estatístico.
Aumentar N não corrige incompatibilidade. A origem leaderboard precisa continuar visível.

Afirmação “menos usos” exige medida de contagem/taxa apropriada e referência compatível.
“Oportunidade perdida” exige também disponibilidade e aplicabilidade. “Deveria usar aos
95s” exige contexto suficiente para recomendar aquele momento. “Ganhará X%” exige
estimativa de efeito de uma mudança; a identidade Oaxaca atual não fornece essa prova.

## 6. Decisões abertas e bloqueios de avanço

Estes são bloqueios metodológicos para etapas específicas, não pedidos de autorização
nem impedimentos à conclusão documental de M0. Devem ser resolvidos na análise do marco
indicado, com decisão registrada antes da mudança dependente.

| ID / responsável | Pergunta aberta | Evidência/decisão necessária para liberar |
|---|---|---|
| B01 — M1 | Qual resultado, referência e operação de agregação a decomposição explica? | Comparar decomposição por pares agregada, referencial representativo e residual explícito; congelar equação, denominador e tolerância. Não impor média/mediana por conveniência. |
| B02 — M1/M4 | Quais eventos permitem atribuir alvos a instâncias de cast? | Matriz por mecanismo (direto, AoE, periódico, pet), cobertura e ambiguidade. Sem prova, bloquear “alvos por cast”. |
| B03 — M2 | Qual compatibilidade temporal e kill/wipe é exigida? | Política para partição desconhecida, hotfixes, log antigo e tentativa incompleta; fixtures positivos/negativos. |
| B04 — M2 | Quais covariáveis podem ser relaxadas para cada métrica? | Análise de sensibilidade e seleção; explicitar população descritiva versus aspiracional, sem limiar universal de N. |
| B05 — M4 | Qual fonte prova cooldown/cargas/reset/build e disponibilidade? | Proveniência, versão, regras de aplicabilidade e conjunto inicial de mecanismos suportados. Não inferir disponibilidade de mediana de casts. |
| B06 — M3 | Grades são descrição ou inferência com controle de erro? | Se inferência, definir hipótese, calibração de p-valores finitos e família de testes; se descrição, retirar alegação de FDR/confiança causal. |
| B07 — M1/M6 | Como versionar/recalcular dados derivados e ausências? | Dependências por feature, preservação de brutos, estados explícitos, política de backfill; sem reescrever histórico durante M0. |
| B08 — M5 | Qual conteúdo mínimo cabe na mensagem? | Priorizar evidência, comparabilidade, ação e ressalva; decidir redução de itens antes de eliminar qualificadores. Setup independente de execução. |
| B09 — M3 | Qual regra de materialidade preserva déficit absoluto sem inventar ação? | Separar sinal para revisão de elegibilidade de recomendação; resolver D03 sem reintroduzir causalidade pelo tamanho do gap. |

## 7. Revisão e execução

Comandos offline (Windows; em Unix adaptar o executável do venv):

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts="" -p no:cacheprovider tests/unit/test_m0_methodology_contract.py -v
.venv/Scripts/python.exe -B -m pytest -o addopts="" -p no:cacheprovider tests/unit/test_m0_methodology_contract.py --runxfail -v
```

O segundo comando deve falhar nos seis casos de contratos não atendidos (D02 tem duas
parametrizações). Ele existe para inspecionar os erros reais, não como gate verde.
O primeiro deve separar cinco controles/caracterizações aprovados de seis xfails.
Revisão estática deve cobrir o novo arquivo com Ruff e Pyright e verificar o diff:
somente documentação e testes podem mudar em M0. Registrar o resultado observado abaixo.

Saída de M0: C01–C10 definidos; métricas/consumidores inventariados; D01–D06 reproduzidos;
comportamentos esperados registrados; B01–B09 localizam decisões pendentes. Isso libera
a análise de M1, não declara produção corrigida ou ML pronto.

### Registro de revisão de 2026-09-10

- M0 isolada: 5 passed, 6 xfailed (11 casos; D02 parametrizado).
- Com `--runxfail`: 5 passed, 6 failed, nas asserções esperadas de D01–D04/D06.
  Valores: -10 pp, 0,1/0,05 alvos, conjunto vazio de candidatos, frase falsa de menor
  uso e 111,111…% de dano. D05 confirma separadamente 4 de referência e +96 pp.
- Seleção com testes existentes de agregação, materialidade, coaching, remediação e
  priorização: 79 passed, 6 xfailed. A suíte completa não foi executada em M0.
- Ruff (lint e formato) e Pyright do novo teste: aprovados.
- Nenhuma chamada externa, alteração de `src/`, migração ou reconstrução de dataset.
- Critério de M0 atendido. Os xfails continuam sendo defeitos a corrigir nos marcos
  indicados; não significam aprovação metodológica do runtime.
