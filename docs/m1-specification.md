# M1 — Medidas confiáveis e decomposição coerente

**Estado: M1 aberta — correções implementadas, aguardando nova revisão independente.**
Data: 2026-09-10. Autoridade: [M0 normativa](m0-methodology-contract.md), C01–C10.
Este documento fecha as decisões necessárias à M1. Não alterar a M0 para fazer os
testes concordarem com a implementação. Não executar M2–M6 dentro deste trabalho.

## 1. Objetivo e limite de escopo

Entregar medidas cuja identidade, unidade, denominador, disponibilidade e referência
permaneçam corretos da ingestão à apresentação. A decomposição deve explicar exatamente
uma diferença declarada de DPS observado, inclusive suporte e componentes não classificados.
Ela não estima dano recuperável nem demonstra erro de rotação.

Incluído: contabilização de dano; normalização de duração; decomposição por referência;
distribuições próprias de DPS por habilidade, casts/min e uptime; retirada do proxy de
alvos por cast; proveniência mínima de coleta; versões; adaptação dos consumidores que
de outra forma continuariam publicando a semântica errada; testes e relatório de revisão.

Não incluído: redesign do matching, hotfix/partição/kill-wipe (M2); framework completo
observação/hipótese/ação e inferência estatística (M3); regras de disponibilidade de
cooldowns, instâncias de cast e oportunidades (M4); redesign de entrega/priorização
global/setup (M5); campanhas, treinamento ou reconstrução de datasets históricos (M6).
As adaptações mínimas descritas na seção 8 são obrigatórias para integrar M1, não uma
autorização para implementar esses marcos por inteiro.

## 2. Evidências verificadas nesta análise

Base de código: HEAD `8aaf4a3e19326106d75a209b035c348b1121abcb`, mais os artefatos M0
existentes no worktree (`docs/README.md`, documento M0 e teste M0), preservados.

| Evidência | Observação | Implicação |
|---|---|---|
| `analysis/dps_gap.py` | Medianas independentes de n e p; counts não normalizados; S subtraído somente do denominador | B01 precisa trocar o estimando e corrigir a contabilidade, não só ajustar apresentação |
| `ingest/damage_aggregation.py` | `RawDamageEvent` preserva spell/source/target/amount; não preserva timestamp, tick ou identificador de cast | B02 não pode ser resolvido recuperando associações do agregado atual |
| `ingest/performance_fetch.py`, `log_fetcher_aux.py` | Paginação pode terminar por ApiError/cursor sem progresso e retornar agregados sem estado de completude | Totais parciais não podem parecer completos; necessário estado persistido |
| `ingest/parquet_codec.py` | Ausência de cobertura de coleta e de versão geral das medidas; defaults históricos | Não fabricar completude ao ler registros antigos |
| `domain/canonical_ability.py` | Associação derivada por igualdade de nome; não prova vínculo entre instâncias | Não usar família por nome como prova de dano por cast/consumo de proc |
| `phase4/experimental_dataset.py`, `experiment_features.py` | Proxy de alvos vira feature numérica; ausência pode virar zero | Retirar essa feature de novos datasets/modelos já na M1 |
| `phase4/experiment_evaluate.dataset_hash` | Hash atual inclui identidades/label/tempo/target, mas não os valores das features; versão vem da constante de campanha | Novo fingerprint deve incluir conteúdo e versão reais das features, sem renomear campanhas antigas |
| `report/dps_gap_text.py`, `top_actions_text.py` | Mediana no cabeçalho de decomposição e “ganho estimado” | Adaptação obrigatória: média medida, unidades e déficit observado |
| M0 + `test_damage_scope.py` | 9 passed, 6 xfailed; replay reconcilia 20 jogadores | Base reproduzida; não há correção nesta análise |

Censo somente-leitura de `data/raw`: 1.361 arquivos Parquet, não necessariamente
1.361 jogadores/pulls independentes. São 1.274 `legacy_unscoped`, 50
`wcl_target_scope_v1` e 37 `unreconciled`. Nenhum dos 1.361 tem coluna de cobertura
de coleta; 87 têm alguma informação `by_source`; 23 têm ajuste positivo de suporte.
Das 37.531 entradas de dano positivo, 29.659 têm casts=0; isso não é prova de ausência
de uso pelo jogador. Há proxy de alvos em 1.358 arquivos. Nenhuma duração é não positiva.

O primeiro caso de suporte reproduz a falha histórica: bruto 166.442.802,
S=4.559.867, soma bruta sobre líquido=102,81676817880773%.
Hash do snapshot: `2436bb890baaf2fdd91d72f9120b30494f1fed3015ac341e026122276400085f`.
Receita do hash: caminhos relativos POSIX ordenados sob `data/raw`; atualizar SHA256
com os bytes UTF-8 do caminho e em seguida o digest binário SHA256 do conteúdo, por arquivo.

Replay `tests/fixtures/gate1_scope/phase2_events_p*.json.gz`: dez páginas, 91.574
eventos de dano; todos têm timestamp/sourceID/targetID; 18.771 têm targetInstance.
Nenhum tem castID/castId/castGUID/castGuid. `tick=true` em 14.380, ausente em 77.194;
nenhum `tick=false` explícito. Ausência do campo não será convertida em prova de dano direto.
Isso demonstra falta de associação nesta evidência, não impossibilidade universal da API.

Como análise de sensibilidade, sete grupos com >=15 arquivos, agrupados por
classe/spec/encontro/dificuldade/partição/scope, tiveram razão média/mediana de DPS
líquido entre 0,9982366084 e 1,0168244650 (mediana das razões=1,0060072886).
Esses grupos NÃO passaram por deduplicação/matching: os números não validam a coorte
nem equivalência universal entre média e mediana. A decisão abaixo deriva das identidades
e do objetivo contábil, não de selecionar a estatística que melhora resultados no corpus.

Revisão numérica independente das equações propostas, executada somente em memória:
1.000 casos (seed 20260910), quatro habilidades, cinco referências, durações independentes,
contagens incluindo zero e suporte até 20% do bruto; pares alternados sem split testam
unclassified e pesos fixos. Os 4.000 contrastes por habilidade e os 1.000 fechamentos
globais respeitaram a tolerância da seção 5; máximo residual global observado:
`5,820766091346741e-11 DPS`. Isso verifica as equações da especificação, não uma
implementação M1, calibração estatística ou benefício de coaching.

## 3. Decisões fechadas

### D-M1-01 — B01: estimando principal

Escolher **diferença entre DPS líquido medido do jogador e média aritmética dos DPS
líquidos medidos de cada referência aceita**, com um voto por log já selecionado pelo
matching. O módulo M1 não modifica pesos por habilidade, não faz trimming/winsorization
e não escolhe referências pelo efeito que produzem no resultado.

| Alternativa | Avaliação | Decisão |
|---|---|---|
| Produto de medianas de contagem e dano/unidade | Não representa mediana de dano nem fecha contra o resultado geral | Rejeitada |
| Log mediano ou dois logs centrais como representante | Pode fechar contra mediana de DPS, mas atribuição por habilidade depende de uma/duas execuções e desempates | Rejeitada como decomposição principal |
| Média das diferenças individuais, população e pesos fixos | Aditiva, auditável e usa todas as referências; sensível a extremos, sem pretensão de robustez da mediana | Escolhida |
| Forçar fechamento contra mediana com residual média–mediana | Matematicamente possível, mas o residual seria mudança de estimando, não mecanismo de dano | Não necessário; mediana fica em contexto separado |

A mediana de DPS WCL existente continua identificada como estatística WCL, e a
mediana do DPS medido pode aparecer como contexto explicitamente identificado.
Nenhuma delas é o denominador ou total que a decomposição M1 afirma explicar.
Referência aspiracional, se fornecida, recebe comparação separada com a MESMA fórmula,
seu próprio conjunto e média. Nunca reutilizar seus números no fechamento da coorte principal.

### D-M1-02 — Bruto, suporte, líquido e shares

Manter ledger por `spell_id` como hoje; não unir IDs por nome na contabilidade M1.
Cada evento elegível entra exatamente uma vez. Classificações e famílias podem organizar
exibição, mas não podem duplicar nem eliminar dano. Suporte é linha contábil separada.

Participação de habilidade passa a ser `100 * D_a / D_bruto`: soma 100% do bruto,
nunca do líquido. Nome/denominador novos e versão nova obrigatórios. Não é sinal de
qualidade nem variável para decidir se existe déficit absoluto. Dano de habilidade
dividido pelo líquido só é permitido como contribuição contábil explicitamente nomeada,
que pode exceder 100%; não chamar esse número de share.

### D-M1-03 — B02: retirar alvos por cast

M1 **não implementa média real de alvos por cast**. Não há evidência suficiente para
associar todos os eventos às respectivas instâncias. Não usar proximidade temporal,
mesmo nome, hits/casts ou alvos distintos/casts como substituto.

Novos cálculos de `avg_targets_per_cast` ficam indisponíveis, com razão
`CAST_INSTANCE_LINK_UNAVAILABLE`. O campo legado continua decodificável para leitura
histórica, mas não é consumido em diagnóstico, grade, materialidade, texto ou novo ML.
Retirar `poucos_alvos` do produtor ativo baseado nesse proxy; não basta esconder a frase.
Não preencher a feature aposentada com zero/NaN/constante. Retirá-la do vetor de entradas.
B02 está resolvido para M1 pela abstenção; associação real pertence à M4.

### D-M1-04 — Unidades e causalidade

Counts de casts vêm da timeline do jogador, não do campo casts=0 de um portador de dano.
Hits vêm da contagem de eventos de dano. Nunca converter um no outro por fallback.
Preservar origem player/pet/unknown e periodicidade true/false/unknown; misturas são
declaradas. Um evento sem `tick` tem periodicidade unknown, não false.

A decomposição mecânica M1 usa **eventos de dano** como unidade de volume; a comparação
de frequência de casts é uma medida independente. Não há atualmente prova geral que
associe dano à instância de cast. Portanto, a parcela por eventos não será chamada de
“usos perdidos”, “erro de rotação” ou “dano por cast”. Isso mantém a identidade algébrica
sem promover hits de pets ou ticks a ações do jogador. Uma futura decomposição por casts
exigirá contrato e evidência próprios; não faz parte desta implementação.

## 4. Contratos de dados a implementar

Nomes abaixo são os nomes de referência da especificação. Organização de arquivos e
helpers pode variar; identidades, significados, razões e invariantes não podem variar.
Usar dataclasses/enums conforme o padrão do projeto, sem criar framework genérico.

### 4.1 Proveniência mínima persistida

Adicionar a `PlayerLog` um objeto opcional `measurement_provenance` e serialização
Parquet aditiva `measurement_provenance_json`. Ausente significa legado, nunca COMPLETE.

| Campo | Contrato |
|---|---|
| `schema_version` | `measurement-input-v1` |
| `damage_collection`, `casts_collection` | `COMPLETE`, `PARTIAL`, `UNKNOWN`, com razões e intervalo solicitado |
| `damage_table_total` | Autoridade do total, opcional; unidade dano; não usar ranking arredondado como substituto |
| `player_actor_id`, `pet_actor_ids` | Identidades da coleta; ausência não permite reconstruir origem por suposição |
| `damage_event_mix_by_spell` | Contagem e dano por origem PLAYER/PET/UNKNOWN e periodicidade TRUE/FALSE/UNKNOWN; buckets exclusivos, somas iguais ao ledger |

Preservar as colunas e dados anteriores. Nos novos fetches, o mesmo conjunto de eventos
que gera `AbilityDamage` gera a proveniência, sem novas queries só para repetir cálculos.
Auxiliares de paginação devem devolver status tipado além dos eventos, ou levantar
erro específico que o fetcher traduza em PARTIAL. É proibido devolver sucesso implícito.

COMPLETE exige resposta com estrutura válida, intervalo válido e término normal por
cursor nulo ou limite superior atingido. Cursor <= início corrente enquanto há sequência,
erro de API, formato inválido ou interrupção deixa PARTIAL/UNKNOWN, com razão. Budget
global continua propagado; não convertê-lo em aparente sucesso de um stream vazio.
Um stream vazio válido e completo é diferente de campo de resposta ausente.

Não unificar a paginação por uma refatoração ampla se interfaces pequenas resolverem.
Não mudar os outros fetchers (recursos/auras) além da adaptação necessária a interfaces
compartilhadas; os seus problemas de cobertura ficam explicitamente registrados para M3/M6.

### 4.2 Observação e comparação de métricas

`MetricObservation` contém `metric_id`, `value: float|None`, `unit`, `status`,
`reasons`, `denominator_kind` e identidade da origem. Estados:
`AVAILABLE`, `PARTIAL`, `UNKNOWN`, `NOT_APPLICABLE`, `INVALID`.
Somente AVAILABLE participa de grade/comparação completa. Não representar desconhecido por 0.
AVAILABLE exige valor finito e nenhuma razão bloqueante; os demais estados exigem razão.
PARTIAL pode preservar subtotal observado para inspeção, mas não participa de grade.
UNKNOWN/NOT_APPLICABLE/INVALID usam `value=None`. Estado de dado e suficiência de amostra
são independentes: métrica disponível com N pequeno não vira dado ausente.

`MetricComparison` contém observação do jogador, IDs das referências usadas, valores
de referência, exclusões por razão, `ScalarFinding` opcional e `metric_id` idêntico em
ambos os lados. Cada métrica tem seu próprio N; não usar `num_positional` como N de DPS.

Métricas mínimas por habilidade:

| `metric_id` | Fórmula e população |
|---|---|
| `gross_ability_dps` | `D_a/T`; observação absoluta. Ledger contabiliza zero de dano no intervalo reconciliado, mas comparação por habilidade exige mecanismo observado nos dois lados, conforme abaixo |
| `player_casts_per_minute` | `60 * len(cast_timeline[a]) / T`; coleta COMPLETE; sem prova de aplicabilidade, ausência de cast não vira referência zero de execução |
| `damage_events_per_second` | `hits_a/T`; coleta completa para essa finalidade; sem implicação de controlabilidade |
| `damage_per_event` | `D_a/hits_a` se hits>0; n=0 não produz p=0 artificial |
| `aura_uptime_fraction` | Fração já observada; jogador e referências precisam de entrada explícita válida; não imputar zero à ausência |
| `gross_damage_share_pct` | `100*D_a/D_bruto`; composição apenas, não materialidade |

Para a comparação de DPS por habilidade em M1, “mecanismo observado” exige entrada
de dano positiva da habilidade no jogador e na referência, identidade resolvida e
reconciliação elegível. Isso é subgrupo de portadores observados, não prova de build ótima.
Referências sem habilidade ainda contam com dano zero no ledger global reconciliado,
mas NÃO no grade de execução daquela habilidade. Essa separação é obrigatória.

M1 não presume disponibilidade por build a partir de ausência. Quando só o jogador ou
só referências possuem a habilidade, o delta contábil existe, mas o grade específico
fica indisponível. M2 poderá ampliar a comparação mediante prova de aplicabilidade.
Mesmo-ID não resolve hotfixes, talentos ou todos os modificadores: registrar que a
compatibilidade semântica refinada continua sendo limite conhecido até M2.

Frações de uptime devem ser finitas em [0,1]; não limitar automaticamente valores
inválidos para fazê-los parecer válidos. Contagens/dano devem ser finitos e não negativos;
duração deve ser finita e >0. Valores inválidos bloqueiam a medida, não viram zero.
Não alterar a medida de waste/deaths/downtime nesta M1 nem fazer o fallback de segundos
de downtime se apresentar como percentual ativo; esse último caso deve ser suprimido
na apresentação com unidade incompatível e registrado para M3/M5.

### 4.3 Contabilidade individual e comparação global

`DamageAccounting` contém:

- `measurement_version = damage-accounting-v2`;
- `status`, `reasons`, `damage_scope`, `duration_s`, qualidade da reconciliação;
- `gross_damage_by_ability`, `gross_damage_total`, `support_subtracted_damage`, `net_damage`;
- `gross_dps`, `support_dps`, `net_dps`, todos divididos pelo MESMO T;
- `wcl_reported_dps` separado e opcional; não corrigir esse valor em memória para obter igualdade;
- residual da reconciliação com `damage_table_total`, quando a autoridade está disponível.

`DamageComparison` contém versão `damage-comparison-v2`, método `paired_mean`,
accounting do jogador, IDs ordenados de R e razões de exclusão, N, média líquida das
referências, delta em DPS, percentuais definidos na seção 5, linhas por habilidade,
linha de suporte e residual numérico. Referência aspiracional é outra instância.

Não manter campos antigos ambíguos como autoridade paralela. Campos como `n_r*p_r`,
`delta_d`, `estimated_gain_pct` e `cohort_share` usados com a semântica antiga devem
ser removidos/renomeados nos consumidores ativos. Se um adapter temporário for indispensável
para leitura histórica, ele deve declarar versão legada, ser só de leitura e não entrar
em cálculo/grade/novo relatório. Não armazenar DPS em campo documentado como dano.

## 5. Equações normativas e casos de borda

Para jogador u e cada referência i, duração T>0, habilidade a:

```text
D_j,a = dano bruto observado da habilidade a no escopo elegível
G_j   = soma_a D_j,a
S_j   = ajuste agregado de suporte a subtrair
g_j,a = D_j,a / T_j
s_j   = S_j / T_j
y_j   = soma_a g_j,a - s_j

N               = len(R)
reference_dps   = (1/N) * soma_i y_i
delta_a_dps     = (1/N) * soma_i (g_u,a - g_i,a)
support_delta   = (1/N) * soma_i (s_i - s_u)
total_delta_dps = y_u - reference_dps

soma_a delta_a_dps + support_delta == total_delta_dps
```

Usar união de IDs de dano em u e R. Ausência em um ledger elegível significa **nenhum
dano registrado desse ID nesse intervalo**, não prova de habilidade disponível. Não
eliminar IDs desconhecidos da contabilidade. Distribuição de grade usa subgrupo da
seção 4.2; o ledger não altera R por habilidade nem renormaliza seus pesos.

Percentuais, sem reutilizar nomes com denominadores diferentes:

```text
gap_vs_reference_pct       = 100 * total_delta_dps / reference_dps  [se reference_dps>0]
ability_delta_player_pp    = 100 * delta_a_dps / y_u                [se y_u>0]
support_delta_player_pp    = 100 * support_delta / y_u              [se y_u>0]
total_delta_player_pp      = 100 * total_delta_dps / y_u            [se y_u>0]
```

Valores percentuais ficam `None` se o denominador for zero, nunca 0 ou infinito. Deltas
em DPS continuam definidos quando possível. Não há floor de DPS inventado. Valor líquido
negativo bloqueia a contabilidade; líquido zero é válido, mas não habilita percentual.
`S=0` só quando medido/definido pelo contrato; `S` desconhecido não é zero líquido confirmado.

Decomposição mecânica por par (não causal), com `h=hits` de dano observado:

```text
q_j,a = h_j,a / T_j
p_j,a = D_j,a / h_j,a             [definido apenas se h_j,a>0]
volume_i      = (q_u - q_i) * p_i
per_event_i   = (p_u - p_i) * q_i
interaction_i = (q_u - q_i) * (p_u - p_i)
volume_i + per_event_i + interaction_i == g_u,a - g_i,a
```

Calcular esse split somente se ambos os p forem definidos, contagens completas e
origens/tipos declarados compatíveis. Regra fechada de compatibilidade M1: em cada lado,
todos os eventos desse ID pertencem a um único bucket não vazio de `damage_event_mix_by_spell`;
o bucket é o mesmo par (origem, periodicidade) nos dois lados; origem é PLAYER ou PET
e periodicidade é TRUE ou FALSE explícita. Contagens são inteiras, hits>0 e soma dos
buckets confere com hits/dano do ID. Qualquer unknown, mistura, divergência, contagem
parcial ou metadado ausente torna o par sem split. Não comparar IDs diferentes nem
inferir periodicidade pelo nome. O delta de DPS não depende da disponibilidade do split.

Para pares sem split: `unclassified_i = g_u,a - g_i,a`. Nos demais, unclassified=0.
Agregar cada componente com o peso ORIGINAL 1/N; não tirar a média só dos pares bons.
Registrar `split_pair_count`, N e razões dos pares sem split. Então:

```text
volume_dps + per_event_dps + interaction_dps + unclassified_dps == delta_a_dps
```

`unclassified` é diferença contábil sem classificação, não ruído nem ganho recuperável.
O suporte não recebe split de volume/eficiência por habilidade.

Apresentação pode manter gate de 0,5 pp do líquido do jogador para linhas individuais,
desde que todos os componentes omitidos sejam somados em `other_delta_dps/pp`. Não
sumir com interações ou valores favoráveis. Se y_u=0, não aplicar gate percentual:
reter linhas não nulas no contrato; o renderizador pode agrupá-las em “outras”.

Tolerância para equações derivadas: `abs(residual) <= max(1e-9, 1e-12 * scale)`,
onde `scale=max(1, soma dos valores absolutos dos termos comparados)`, na unidade
da equação. Usar `math.fsum`/ordem estável. Tolerância não autoriza ratear residual
entre habilidades. Residual acima do limite é erro/inconsistência, bloqueia comparação.
Não relaxar a regra de igualdade com a autoridade WCL por conveniência em M1: o guard
de escopo existente permanece, acrescido da identificação de coleta parcial.

Sem R: calcular accounting individual, devolver comparação indisponível `NO_REFERENCES`.
Não simular coorte de DPS zero. O teste D06 passa a verificar `G-S=net` e percentuais
contábeis individuais com linha de suporte, não um falso “gap” sem referências.

## 6. Elegibilidade de dados e B07

Decisão: versionar de forma aditiva, não limpar caches, não regravar Parquets antigos
e não acionar coleta/backfill em massa. Não trocar `DamageScopeVersion` por versão de
fórmula: população de dano e algoritmo são eixos independentes.

| Entrada | Accounting | Comparação quantitativa M1 | Cast/event split |
|---|---|---|---|
| Nova, V1 reconciliada, coleta damage COMPLETE | Disponível | Disponível se R elegível | Conforme cobertura e tipo dos eventos; casts têm status independente |
| Nova, PARTIAL/INVALID/UNRECONCILED | Apenas diagnóstico parcial/razões | Bloqueada | Bloqueado |
| Histórica V1, sem metadados novos | Reconciliação herdada do contrato V1, marcada `LEGACY_RECONCILED_TOTAL` | Disponível para diferenças de dano reconciliado; não afirmar completude de casts | Sem split/grade de casts sem prova de cobertura; manter unclassified |
| Histórica LEGACY_UNSCOPED | Subtotal observado, S não confirmado; não rotular líquido reconciliado | Bloqueada para o novo resultado líquido M1 | Bloqueado para inferência de execução |
| Histórica UNRECONCILED | Indisponível quantitativamente | Bloqueada | Bloqueado |

Para uma linha nova com proveniência, V1 exige também autoridade `damage_table_total`
finita, estado de damage COMPLETE e reconciliação válida. Metadados novos PARTIAL ou
contraditórios não podem se beneficiar da compatibilidade reservada a linhas históricas
sem proveniência. Fonte nova inválida não pode ser convertida em LEGACY para passar no guard.

A compatibilidade histórica V1 usa o significado já persistido do scope (total
reconciliado), não inventa uma nova verificação da API. Registrar essa proveniência
separada. Não promover unknown para COMPLETE em memória nem no disco.
O censo mostra que a cobertura inicial do novo caminho quantitativo pode cair muito;
isso é consequência explícita do contrato, não algo a compensar admitindo legacy
silenciosamente. Outras partes válidas do relatório continuam disponíveis.

R é o subconjunto dos matched logs com accounting líquido elegível e scope compatível,
calculado uma vez. Exclusões e tamanho entram no contrato. Aplicar o piso existente de
oito para publicar a comparação; abaixo disso, disponibilizar accounting e estatísticas
internas, mas estado público `INSUFFICIENT_REFERENCES`. Grades só com >=15 observações
da métrica, sem reutilizar N de outro subconjunto. Esses números preservam a política
atual; não constituem calibração de confiança estatística (M3).

Persistir versões `measurement-input-v1` e `damage-comparison-v2` no manifesto da análise
e no contrato; ampliar `RunManifest`/Store por migração aditiva idempotente se necessário.
Manifestos antigos recebem versão desconhecida/legada na leitura, não a versão corrente.
Identidade do pool de candidatos não muda: critério de seleção continua pertencendo a M2.
Recalcular comparações a partir de logs em memória é permitido; não regravar esses logs.
Cache de log antigo não deve gerar fetch extra automático apenas por faltar proveniência.

Para ML, fechar B07 apenas no necessário à M1:

- Remover `c_mean_targets_per_cast` de `build_features`, do registro modelável e de qualquer
  fallback de feature space. Incluir em registro de features aposentadas com razão.
- Definir `feature_schema_version=experimental-features-v2` em novos datasets e incluir
  a versão no hash e no registro de novas avaliações. Resultados antigos mantêm a versão
  persistida (hoje `sae3-v1`), sem reatribuição.
- Não substituir globalmente `experiment_store.FEATURE_SCHEMA_VERSION`: ela também entra
  na identidade de campanha. Criar constante distinta para o schema das medidas/features;
  dataset a carrega e `MatrixResult.feature_schema_version` deve vir do dataset utilizado.
  Uma campanha histórica pode fornecer logs para nova materialização v2, mas o registro
  da campanha e seus resultados históricos não mudam; o novo artefato é identificado como v2.
- Fingerprint novo usa serialização canônica com versão do algoritmo de hash, versão
  de features, identidades/target/tempo/label e **todos os nomes e valores das features**
  efetivamente materializadas, com chaves ordenadas e float finito. Alterar feature mantendo
  identidade deve mudar o hash; reordenar linhas/chaves não. Não depender de locale ou repr
  de dict, e não permitir NaN/inf na serialização. Hashes antigos continuam legíveis.
- Leitura histórica não é nova avaliação. Novos experimentos não podem consumir colunas
  aposentadas nem misturar schemas. Não treinar modelo, regenerar campanha ou alterar
  os rótulos históricos como parte desta tarefa.
- Revisão geral de imputação, unidades de recurso, target, controlabilidade e splits
  permanece em M6; não declarar o dataset todo validado por retirar uma feature.

## 7. Materialidade mínima necessária à M1

D03 exige separar composição e déficit, não resolver toda a priorização de coaching.
Usar `gross_ability_dps` como scalar autoritativo para visibilidade de déficit absoluto:
delta principal negativo, magnitude >=0,5 pp quando definida, e grade red/yellow
na distribuição própria elegível (N>=15). Em y_u=0, o filtro percentual é indisponível;
não inventar ganho. Dano observado zero sem prova de mecanismo gera observação contábil,
não candidato de execução. Manter os atuais limiares de grade como descrição.

`is_material_ability`, contagem de materialidade e observações positivas deixam de usar
share como proxy de execução. Observação favorável deve nomear DPS observado da habilidade,
não “habilidade bem usada”. As distribuições de casts e uptime também são independentes.

O candidato de habilidade na M1 é **ponto de revisão**, sem causa medida. Para integrá-lo
ao fluxo atual, adicionar uma base explícita `OBSERVED_OUTPUT_DEFICIT` e condição
`CAUSE_NOT_IDENTIFIED` ao contrato existente de remediação. Ação condicional permitida:
“Revise a contribuição desta habilidade; a causa da diferença não foi identificada.”
Não usar as parcelas de eventos para escolher USE_COUNT, DAMAGE_PER_USE, poucos alvos,
janela ou buffs próprios. O framework completo e critérios causais ficam em M3/M4.

Somente entidades que atendam TODOS os requisitos abaixo podem receber o convite genérico:

1. Identidade resolvida e role em CORE_DAMAGE, SECONDARY_DAMAGE, OFFENSIVE_COOLDOWN,
   RESOURCE_GENERATOR ou RESOURCE_SPENDER, conforme o classificador existente.
2. Pelo menos um cast do próprio jogador observado no MESMO ID; família por nome não basta.
3. Proveniência do dano desse ID demonstra origem exclusivamente PLAYER (periodicidade
   pode ser mista/unknown para revisão do DPS agregado, mas não para o split acima).
4. Scalar próprio elegível/material conforme esta seção.

Não usar `ACTIONABLE_ROLES` sozinho: hoje esse conjunto também contém PET_DAMAGE,
DOT_TICK e procs. Efeitos de equipamento, consumíveis, raciais, externos, pets isolados,
IDs não resolvidos e sem prova dos requisitos ficam no ledger/contexto sem recomendação.
O role CORE/SECONDARY pode continuar usando share como taxonomia do kit, junto com o
cast próprio observado; isso não autoriza usar share como grade de execução. Não descartar
dano por falta de elegibilidade. Associação de pet/tick a outro botão pertence a M4.

Não refazer ordenação entre morte/atividade/uptime/waste. Onde a ordenação atual compara
`estimated_gain_pct`, substituir por `observed_deficit_player_pp` e declarar que é ordem
de déficit observado dentro do mesmo tipo, não retorno estimado por ação. Não introduzir
novo score de benefício/esforço nem converter ausência de denominador em ganho zero.

## 8. Integração obrigatória e arquivos afetados

| Área | Mudança obrigatória | Limite |
|---|---|---|
| `domain/models.py`, `ingest/parquet_codec.py` | Proveniência aditiva, estados, origem de eventos, versões, leitura histórica | Sem reescrever `data/raw` |
| `ingest/damage_aggregation.py`, `performance_fetch.py`, `log_fetcher_aux.py`, `log_fetcher.py` | Retirar proxy ativo, capturar cobertura de damage/casts e composição de eventos; PARTIAL não vira legado elegível | Sem novas queries de regra/guia, sem custo adicional para duplicar dados |
| `analysis/dps_gap.py`, guarda de scope | Accounting, R único, média por pares, suporte, unclassified e tolerâncias | Helpers novos focados são preferíveis a ampliar o monólito |
| `analysis/performance_features.py`, `feature_availability.py` | Comparações próprias e ausência não imputada para uptime/casts novos | Não inferir disponibilidade de talento |
| `analysis/pipeline.py` | Orquestrar novas medidas e classificar elegibilidade antes da seleção; propagar versões/N/razões | Não mudar política de matching |
| `findings`, `materiality`, `remediation`, `prioritization` | Déficit observado, não share/ganho; remediação genérica e rastreável | Não implementar seleção causal/de oportunidades |
| `report/contract.py`, `render.py`, `dps_gap_text.py`, `top_actions_text.py`, `coaching_answer.py` | Consumir campos novos; média identificada, suporte e unidades; não verbalizar share como uso | Sem redesign de canal/formato/setup |
| `runmanifest.py`, `bot/analysis_runs.py`, Store | Persistir versão do cálculo e N quantitativo; migração aditiva se usada | Não atribuir versão atual a registro antigo |
| `phase4/experimental_dataset.py`, `experiment_features.py` e persistência/hash dependentes | Aposentar feature defeituosa e versionar novos derivados | Não rodar treino/campanha |
| Testes e documentos dos consumidores alterados | Atualizar expectativas pelo contrato, com justificativa por grupo | Não regenerar golden cegamente |

O resultado geral WCL pode permanecer no header, com fonte explícita. Conclusão sobre
coorte medida usa jogador/R medidos, não mistura WCL de um lado e eventos do outro.
Quando medição M1 estiver indisponível, uma posição WCL só pode ser exibida com essa
identificação; não afirmar que a decomposição explica seu gap. Evitar `or 0.0` ao
construir conclusão quantitativa ausente.

Frase para um déficit M1 deve dizer “O DPS observado de X ficou abaixo da referência”
e, se houver números, apresentar unidade e referência. Não usar “uso”, “dano por cast”,
“perdeu X usos” ou “ganhará X%” a partir desse scalar. D04 deve passar por essa adaptação.
Não implementar posições ordinais novas em M1; remover a leitura de share já resolve
o caminho defeituoso. Revisão abrangente da fronteira `q<=1/n` continua em M5.

CLI precisa renderizar interação e unclassified quando presentes, além de suporte;
subtotal de habilidades + outras + suporte deve corresponder ao total em DPS.
Não apresentar o conjunto como causas demonstradas. `gain` vira `observed deficit`
nos nomes e frases ativos tocados. Mensagem Discord continua limitada e determinística.

## 9. Plano de implementação para o Luna

1. Introduzir contratos/versões e fixtures com proveniência explícita, sem alterar brutos.
2. Implementar coleta de qualidade e codec aditivo, incluindo round-trip e fallback legado.
3. Implementar accounting individual puro e suas provas de reconciliação.
4. Implementar comparação por pares e split por eventos com denominador fixo, sem coaching.
5. Expor scalars por métrica, resolver D03 e integrar adapters de relatório/achados.
6. Retirar proxy e feature experimental; incluir versionamento e testes de consumidor.
7. Revisar testes M0 e existentes, rodar a seleção e suíte offline apropriadas, produzir
   o pacote da seção 11. Parar ao entregar M1 para revisão; não iniciar M2.

Não delegar decisão B01/B02 ao implementador: estão fechadas acima. Luna pode escolher
organização de helpers e detalhes de migração equivalentes. Divergência de contrato ou
evidência nova que contradiga esta especificação exige relato ao analista, não ajuste
silencioso de fórmulas/aceite e não consulta humana sobre escolha já resolvida aqui.

## 10. Critérios de aceite executáveis

| ID | Entrada/prova | Resultado exigido |
|---|---|---|
| A01 | D01: 100 eventos/300s vs 110/330s, dano/evento igual; >=15 refs elegíveis | Mesmo DPS, delta global/habilidade zero; nenhuma parcela de frequência causada por T |
| A02 | D05: 15 refs, cinco de cada dano 100,4,100 em T=300; jogador dano=100 | Média do dano=68, média DPS=68/300; delta=32/300 DPS; delta_player_pp=32; gap_vs_ref=100*32/68. Mediana=100/300 somente como contexto |
| A03 | G=100000, S=10000, T=300, sem R | Accounting net=90000, DPS=300; bruto/líquido=111,111…%, suporte=-11,111…%, soma=100%. Comparação NO_REFERENCES, não gap contra zero |
| A04 | u: G=100000,S=10000; refs: G=100000,S=0; T=300 | Delta por habilidade=0, suporte=-10000/300, delta total igual ao suporte |
| A05 | Mesmo input/same T; ordem de R e IDs permutada | Resultado e seleção determinísticos; totais dentro da tolerância |
| A06 | Dano só em u, só em R, hits=0, ausência de mecanismo | Ledger fecha; p não definido não vira 0; split sem prova vai para unclassified; nenhuma ação inventada |
| A07 | Apenas parte dos pares tem split válido | Soma componentes+unclassified=delta; peso permanece 1/N; N e split_pair_count corretos |
| A08 | Pet/tick/externo e casts=0 com dano positivo | Dano preservado; nunca unidade CAST por fallback nem conselho de apertar o portador de dano |
| A09 | Metade do DPS em todas as habilidades com proporções constantes; entidades acionáveis e métricas elegíveis | Déficits revisáveis por scalar de DPS, sem share esconder; nenhuma promessa de ganho |
| A10 | Dobrar apenas dano de outra habilidade | Scalar de DPS/casts/uptime da habilidade intacta permanece idêntico; share pode mudar sem gerar piora de execução |
| A11 | D04: 50 usos vs 40,45 e treze refs com 100; share mínimo | Nenhuma afirmação “uso mais baixo” com base em share; saída não depende de share para ordinal |
| A12 | Um alvo em 10/20 casts, sem prova de associação | `targets_per_cast` indisponível com razão, não 0,1/0,05 nem média inventada=1; nenhum consumidor ativo do proxy |
| A13 | Feature dict legado com `c_mean_targets_per_cast` | Nova construção/feature space não inclui nem imputa a coluna; versão v2 identificada; histórico preservado |
| A14 | Interrupção damage/casts, cursor sem progresso, resposta malformada, stream vazio válido | Estados distintos; PARTIAL não classificado como legado quantitativamente válido; zero só em stream válido completo e aplicação permitida |
| A15 | V1 histórico, legacy histórico, unreconciled, schema ausente | Tratamento exato da seção 6; nenhum metadado de completude inventado; arquivos inalterados |
| A16 | y_u=0, reference=0, T=0/NaN/inf, S>G, danos negativos/NaN | Sem divisões inválidas/clamping; deltas válidos mantidos quando possível; percentuais None; razões explícitas |
| A17 | IDs desconhecidos, linhas subgate e interação favorável | Contabilidade não perde dano; soma exibida+outras+suporte fecha, inclusive sinais |
| A18 | R diferente do matched pool e N por métrica diferente | Média, scalar, amostra e exclusões referem-se ao conjunto correto; n=8 permite descrição, n<15 não grade |
| A19 | Round-trip novo/legado e repetição de migração | Valores/versões/ausências preservados, migração idempotente, nenhuma alteração de Parquet histórico |
| A20 | Discord/CLI/worker/hot path/cold path em testes offline | Novos contratos chegam à entrega, sem campos legados como autoridade, sem vazamento de erro de unidade/ganho |
| A21 | Replay gate1_scope dos 20 jogadores | Reconciliação WCL preservada; contas de suporte por jogador corretas, nenhuma dupla contagem de pets |
| A22 | Contraste extremo: 14 refs iguais e uma muito alta | Média muda conforme fórmula; mediana não substitui média silenciosamente; saída nunca alega robustez à cauda |
| A23 | Referência aspiracional distinta de R | Comparações separadas e fechadas individualmente, sem troca de população de percentuais/grades |
| A24 | Entrada de uptime ausente no jogador/ref; entrada explícita zero | Ausência indisponível/excluída, zero explícito preservado; grade de uptime nunca vem de share |
| A25 | Mesma identidade/label/tempo, uma feature alterada; depois mera reordenação | Alteração de valor/schema muda fingerprint; reordenação não; nova avaliação usa versão do dataset, campanha histórica inalterada |

Testes algébricos property-based devem gerar durações independentes, suporte, zero e
IDs ausentes; verificar identidades antes e depois do gate de apresentação. Não usar
apenas exemplos espelhando a implementação nem aceitar mudança de snapshot como prova.

### Tratamento explícito dos testes M0

- D01: remover xfail, adaptar fixture para qualidade/escopo elegíveis e manter invariante.
- D02: remover os dois xfails e substituir a asserção numérica pela abstenção tipada de
  A12. Isso implementa a alternativa normativa da M0 “senão retirar esse significado”.
- D03: remover xfail; construir prova sintética de kit/qualidade para revisão de déficit,
  verificar scalar de DPS e texto não causal, não só existência de candidato.
- D04: remover xfail; manter contraexemplo e testar a métrica efetivamente publicada.
- D05: atualizar caracterização para números de A02; não manter expectativa defeituosa=4.
- D06 e o teste antigo de fechamento real: atualizar soma para incluir suporte/accounting
  e a ausência de comparação sem R; preservar o teste do caso histórico original.
- Propagação do proxy: tornar teste de retirada no consumidor, não apagar a cobertura.
- Bloqueio UNRECONCILED e contraste entre quantis permanecem.

Não remover xfail apagando o teste, fazendo assert em coleção vazia sem estado ou
relaxando tolerância. Toda adaptação de contrato exige evidência do comportamento positivo
e do negativo. Após M1 não deve restar xfail referente a D01–D06 nessa suite adaptada.

## 11. Evidências obrigatórias para revisão do analista

Entregar `docs/m1-review-evidence.md` com:

1. Matriz A01–A25 → teste executado → resultado, e lista de adaptações dos testes M0.
2. Exemplos estruturados antes/depois para D01–D06, mais mensagens CLI/Discord dos mesmos
   contratos quando aplicável. Identificar dado sintético versus replay/corpus.
3. Censo do corpus com versão, status de accounting, elegibilidade e motivos de abstenção,
   número de comparações possíveis e N por métrica. Arquivo/pull/jogador não são sinônimos.
4. Distribuição e máximo de resíduos algébricos em casos elegíveis, com população e
   tolerância; não preencher resultados ausentes com passe vazio.
5. Hash de snapshot de `data/raw` antes/depois e prova de preservação de tabelas/artefatos
   históricos utilizados; testes usam temporários, não abrem Store de produção para migrar.
6. Diff de schemas/versões e lista dos consumidores que deixaram de ler campos aposentados.
7. Resultados de testes unitários/golden/integrados offline, Ruff e Pyright, com comandos,
   ambiente e skips/falhas explícitos. Falha preexistente deve ter baseline comprovado;
   falha afetada por M1 não pode ser descartada como “legada”. Não rodar testes network.
8. Limitações residuais por marco: M2 comparabilidade, M3 inferência/ação e cobertura dos
   demais streams, M4 oportunidade, M5 apresentação completa, M6 validação do dataset.

Critério de revisão: equações e semântica corretas, consumidores coerentes, cobertura
real declarada, dados históricos preservados. Não exigir falsa completude de features
para aprovar M1; exigir abstenção verificável onde a medida não pode ser sustentada.

## 12. Encerramento da análise

B01 fechado por média de contrastes; B02 fechado para M1 pela retirada do proxy;
B07 fechado para proveniência/versões/aposentadoria da feature, sem backfill.
B09 parcialmente operacionalizado apenas para visibilidade de déficit absoluto,
preservando a separação de ação para M3. B03–B06/B08 continuam nos marcos responsáveis.
Não há decisão humana pendente para iniciar esta implementação.

Esta análise executou somente leituras/censos/testes offline e escreveu esta especificação
com sua entrada no índice de documentação.
O código de produção e os testes existentes não foram alterados. Parar aqui e entregar
ao Luna; implementação e posterior revisão são etapas distintas.
