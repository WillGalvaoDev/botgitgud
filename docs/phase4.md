# Fase 4 — trilha experimental de ML

Estado: **experimental, nunca promovida para produção.** O coaching servido ao jogador é
inteiramente estatístico. A trilha existe para investigar se um modelo preditivo pode
complementar a análise; a prontidão dos dados para essa investigação é o objeto de M6
([`roadmap.md`](roadmap.md)).

## Fronteira com a produção

- `run_analysis` resolve a capacidade de modelo por `Phase4ModelResolver` (registry
  `phase4_model_registry`, resolução exata por `Phase4Target`), mas **nenhuma predição é usada**
  no coaching. A tabela do registry deve permanecer vazia; promoção a `READY` exige uma rodada
  de validação que ainda não existe.
- O extra `ml` do `pyproject.toml` é obrigatório para carregar o CLI (`botgitgud.cli` importa a
  trilha no topo do módulo), mesmo sem usá-la.

## Pipeline e comandos

Todos offline, exceto `discover`/`triage`/`experiment-collect`, que consomem orçamento da API e
respeitam o mesmo rate limiter e piso protegido do bot.

| Etapa | Comando | O que faz |
|---|---|---|
| Descoberta (Estágio A) | `discover` | lista reports de uma zona via `reportData.reports`, com checkpoint por janela |
| Triagem (Estágio B) | `triage` | `report.rankings` por report: partição, dificuldade, spec e `rankPercent` de cada jogador |
| Status do data gate | `dataset-status` | inspeção objetiva do que foi descoberto/triado; mostra apenas specs `SUPPORTED` |
| Plano | `experiment-plan` | amostragem multi-target determinística, congelada em `experiment_campaign_observations` |
| Coleta (Estágio C) | `experiment-collect` | coleta resumível das observações do plano congelado |
| Status | `experiment-status` | estado da campanha e custo real de API |
| Avaliação | `experiment-evaluate` | matriz de granularidades A–D × splits S1–S5 contra baselines |
| Decisão | `experiment-decide` | gate de arquitetura (hold-outs, pareamento, sensibilidade) |
| Calibração | `experiment-calibrate` | validação e calibração do modelo candidato |

### Regras da coleta

- **Identidade da campanha**: `exp-<sha256[0:20]>` sobre dificuldade, partição, versões do
  planner e do schema de features, máximo de observações e o universo ordenado de chaves
  naturais. Orçamento de API não faz parte da identidade.
- **Plano congelado**: retomar por `--campaign` nunca replaneja, reestratifica nem cria
  substitutos.
- **Estados** por observação: `pending` → `collecting` → `completed`/`failed`/`rejected`, com
  tentativas, pontos e motivo persistidos; um `collecting` interrompido volta a `pending`;
  terminais não são recoletados (salvo reabertura administrativa explícita de motivo
  autorizado).
- **Label**: o `rank_percent` congelado de `report.rankings` é a fonte autoritativa; o collector
  não redescobre percentil por `character.encounterRankings` (que não representa a partição
  histórica). O payload coletado precisa coincidir em report, fight, jogador, spec, encontro,
  dificuldade e partição, senão é rejeição.
- Esgotar o orçamento para a coleta normalmente, preservando o checkpoint.

## Resultado atual

Campanha `exp-840b1ef99d76c33c8a0b`, dataset `ds-1920e6a79ac4d20ea560`, **PARTIAL**: 603 de
1.200 observações planejadas (25 specs, 9 encontros, 198 de 213 targets).

- **Arquitetura candidata**: `MODEL_GLOBAL`, features F2 (`CONTROLLABLE` + `NON_CONTROLLABLE`),
  LightGBM determinístico (`n_estimators=200, max_depth=4, num_leaves=15,
  learning_rate=0.05, min_child_samples=5`).
- Há sinal real: melhor célula MAE 17,95 e Spearman 0,649; generaliza em hold-out de spec e de
  encontro; vence um baseline linear estável (Ridge) por ~17% de MAE; sensibilidade a seed zero.
- `MODEL_PER_TARGET` é inviável mesmo com o plano completo (nenhum target chegaria a 20
  observações).
- **Calibração ruim nos extremos**: viés ≈ +19 no bucket 00–20 e ≈ −31 no 80–100
  (regressão à média); calibração linear e isotônica não melhoram o MAE agregado.
- Veredito: `INTERNAL_EXPLANATION_ONLY` — a previsão não está pronta para o jogador.

## Riscos e condições antes de qualquer uso

1. Corrigir o viés de calibração no topo/fundo da distribuição.
2. Reavaliar contra Baseline 1/Ridge quando os grupos hoje `insufficient_data` tiverem densidade.
3. O dataset materializado não tem representação direta de classe/spec.
4. Label de ranking não representa qualidade de recomendação nem efeito de intervenção;
   features `c_*` não são controláveis só pelo nome (revisão em M6.1).
5. Nenhuma promoção a `READY` nem uso em `!analisar` sem validação subsequente.
