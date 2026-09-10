# Política do warehouse — v1.0

## Compatibilidade do schema aditivo

Sinais novos são adicionados em colunas JSON opcionais. Parquets anteriores continuam legíveis e
produzem detalhes vazios (dados incompletos, não semanticamente inválidos); não são migrados nem
reconstruídos. Parquets enriquecidos mantêm todas as colunas antigas, portanto leitores anteriores
ignoram as colunas extras com segurança. O rollback é feito por `git revert`, sem alterar dados.

O corpus pré-M6 contém 1.274 Parquets e deve ser validado integralmente, sem amostragem. Os sinais
aditivos são o dano por fonte dentro de cada habilidade, desperdício por tipo e habilidade, e os
usos/bandas de cada aura; sua ausência em registros históricos representa somente incompletude.

Baseline em 2026-08-24: `data/` tem 172.491.349 bytes, `warehouse.duckdb` 134.492.160
bytes e 741 Parquets somando 37.999.189 bytes (≈51 KiB/log). A solução proporcional é uma cópia
local consistente e verificada; destino permanente externo continua sendo escolha humana.

| Classe | Dados | Backup/retenção/rotação |
|---|---|---|
| Irreproduzível/auditoria | `experiment_campaigns`, `experiment_campaign_observations`, `experiment_observation_attempts`, `experiment_architecture_eval_runs`, `experiment_architecture_decision_runs`, `experiment_calibration_runs` | obrigatório antes de operação destrutiva; retenção indefinida; nunca purgar na v1.0 |
| Reproduzível a custo de API | `logs`, `data/raw/*.parquet`, `discovery_reports`, `discovery_fights`, `discovery_targets`, `cohort_candidates` | backup diário enquanto houver ingestão e antes de release; manter 7 diários + 4 semanais; arquivar Parquet antigo somente após backup verificado |
| Operacional/regenerável | `runs`, `jobs`, `backfill_checkpoints`, `spells`, `spells.json` | incluir na cópia por simplicidade; manter com o backup correspondente; regenerável, exceto que `runs` é auditoria útil de relatórios |
| Vazio/reservado | `phase4_model_registry` | deve permanecer vazio na v1.0; incluir para preservar schema |

Com 100 análises novas/mês, o Parquet cresce na ordem de 5 MiB/mês, além do DuckDB; revisar a
política ao ultrapassar 1 GiB. Backup: parar o bot, copiar `warehouse.duckdb`, `data/raw/` e
`spells.json`, registrar tamanho e verificar restore. Rotação nunca apaga tabelas experimentais;
qualquer purga futura precisa de tarefa e backup prévio. Recuperação restaura para diretório novo,
valida todas as tabelas/contagens e só então troca o `DATA_DIR` numa janela humana. Perde-se apenas
o que entrou depois do snapshot. Nunca restaurar diretamente sobre `data/`.
