# M2.2 — final independent review — 2026-09-22

**MILESTONE_CLOSED**. R3 resolvido; R1/R2 permanecem fechados. Encerramento exclusivamente de M2.2. M2.3 não iniciada.

Autoridades: workflow operacional, roadmap M2–M6 e SPEC M2.2 v001, preservando M0/M1/M2.1. Esta revisão revalida a pendência documental da revisão anterior; não redefine política nem implementa correções.

## Versão revisada

Workspace: `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`, entrega sobre `edba6e62d144811724efe3a2f9bdf0d6023c64ee`, ainda sem commit próprio de M2.2.

SHA-256 conferidos novamente:

- SPEC: `ea35eb66efe593076a1fe8efed9b337731f0a970097877ca72fe33ab19d84968`.
- Implementação: `8aff6e01430c2e6fa7b0406ca42a0869f941f4b1186574bb40e80b3287aa5e2a`.
- Testes permanentes: `daff74b6353ac37eb62d70aa64b883591b0667c18db0f30ac61a82dbee483e41`.

Implementação, testes e SPEC são byte-idênticos aos da rerevisão anterior. A entrega final complementa `docs/m2-2-review-evidence.md` com a tabela sintética faltante.

## Fechamento de R3 / SPEC §12.9 / AC6

A tabela sintética agora preserva os seis níveis, covariáveis relaxadas, bandas, N e IDs dos membros. A tabela real mantém os quatro níveis e respectivas populações vazias. Ambas foram confrontadas mecanicamente com execução independente de `covariate_sensitivity`:

1. Carregados os construtores do teste por `runpy.run_path`, com `tests/fixtures` no caminho de importação.
2. Recriado o cenário sintético: alvo com tier 4, ilvl 400 e duração 300; duas referências compatíveis (`fight_id_start=1`) e seis com tier 0, ilvl 1 e duração 360 (`fight_id_start=200`).
3. Recriado o cenário real com `_load_gate1_rankings_logs`, Braska como alvo e as outras 19 referências.
4. Executada `covariate_sensitivity` para `gross_ability_dps:1`, consumindo `evaluate_references` em ambos os cenários.
5. Lidas as dez linhas preservadas do Markdown; comparados todos os campos, convertendo `member_ids` por `ast.literal_eval`, sem normalizar ou omitir membros.

Resultado: **10/10 linhas idênticas à execução independente**, inclusive IDs e ordem. N sintético: `[2, 2, 2, 2, 8, 8]`; N real: `[0, 0, 0, 0]`. A tabela sintética demonstra deltas zero e admissão dos seis membros na banda de 20%; o nível de 35% permanece no artefato de sensibilidade, sem alterar a parada do seletor no piso.

O censo real das seis métricas, a matriz de isolamento covariável × métrica, os testes de não-escrita e a ausência declarada de corpus opcional já validados permanecem presentes. **SPEC §12.9 e AC6 satisfeitos.**

Observação documental não bloqueante: o comando pytest com `-s` citado na entrega executa o teste, mas o arquivo permanente não contém o `print(row)` mencionado no texto. A fixture está precisamente identificada e a tabela foi reproduzida e verificada diretamente nesta revisão; essa imprecisão sobre impressão não compromete os valores preservados nem reabre R3.

## R1/R2 e ausência de regressão observada

Executados novamente:

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q tests/unit/test_m2_2_metric_population.py tests/unit/test_m2_1_reference_eligibility.py
.venv/Scripts/python.exe -B docs/submilestones/M2.2/independent-rereview-probes.py.txt
```

Resultados: **119 passed, 2 skipped**, e **50 verificações independentes passaram**. Colisões divergentes continuam rejeitadas; cópias iguais continuam aceitas sem inflar N; exclusões e serialização permanecem determinísticas nas seis métricas e nos dois caminhos aspiracionais. R1/R2 continuam fechados, com regressões permanentes passando.

AC1–AC5 mantêm a validação da rerevisão anterior, corroborada pelos testes e sondagens acima. Nenhum novo contraexemplo bloqueante foi demonstrado.

Por se tratar de complemento exclusivamente documental com código/testes idênticos, não foi repetida artificialmente a suíte ampla nem os gates estáticos. Permanecem aplicáveis os resultados independentes anteriores: Ruff passou, 331 arquivos formatados; Pyright 0 erros/avisos; suíte ampla **2690 passed, 47 skipped, 1 deselected, 1 failed**, 337 warnings. A única falha é a dívida preexistente `test_no_raw_print_calls_anywhere_in_src` no orquestrador, documentada desde M2.1. Os logs estão preservados nesta pasta; não se declara a suíte ampla integralmente verde.

Nenhuma correção aplicada. Somente este registro de revisão final foi acrescentado. M2.2 fechada; macro M2 e integração M2.3 permanecem fora desta declaração.
