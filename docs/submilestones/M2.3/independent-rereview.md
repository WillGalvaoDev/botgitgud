# M2.3 — independent re-review — 2026-09-23

**REQUIRES_CHANGES. M2.3 e macro M2 permanecem abertos.**

Routing: **Opus primeiro**, para reconciliar R3 (§4.0 × §7.1 da SPEC v002); **Sonnet**, para implementar a decisão normativa e completar R2. Não há decisão identificada fora da autoridade desses papéis que justifique HUMAN_BLOCK. Nenhuma correção implementada, nenhum trabalho M3 iniciado.

## Objeto e versões

Workspace de produto indicado pelo repositório: `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`; HEAD `b6241df` (M2.2 fechada), entrega M2.3 não commitada. Revisados workflow, roadmap, fechamentos M2.1/M2.2, SPEC M2.3 v002, diff integral de produção, testes e evidência atual, e R1/R2 da revisão anterior.

`docs/m2-3-specification.md` e `docs/submilestones/M2.3/spec-v002.md` são byte-idênticos: SHA-256 dos bytes `1154a7dc1518aa3ec441d8b5476dbb7288c15fb1be12995f20a5472be474d03d`. Este registro identifica os bytes Markdown revisados separadamente do `spec_sha` citado pelo executor. Ambiente e hashes dos arquivos revisados estão em [independent-rereview-artifacts.json](independent-rereview-artifacts.json).

## R1 anterior — resolvido nos contraexemplos originais

A v002 separa explicitamente quarentena de higiene, sem alterar `match_cohort`. A implementação respeita essa separação. As regressões executadas exercitam `run_analysis` nas duas ordens para uptime ausente/observado e classe compatível/incompatível: removem os dois logs conflitantes e preservam proveniência/JSON. Duplicatas iguais continuam colapsadas pela higiene. A equivalência usa agora o código anterior real de Git; os testes passam. Portanto o conflito original de determinismo versus preservação de `match_cohort` foi resolvido.

Isso não resolve a nova contradição de identidade abaixo; não se trata de repetir o achado antigo sem considerar a correção.

## R3 — bloqueante: quarentena por chave de empate não garante exclusão por ID

Contrato: SPEC v002 §4.0.2–5 determina agrupar pela chave que inclui `dedup_priority`, excluir todos os logs dos grupos conflitantes e conservar os demais. §7.1, linhas 327–328, exige: **“Nenhum id de `conflicting_duplicate_ids` aparece em `eligibility`, `ledger` ou `metrics`.”** AC2 exige que essa proveniência de exclusão sobreviva às fronteiras.

Contraexemplo: três representações de `REPORT:501:Duplicate`, mesmo jogador/pull:

1. A: uptime ausente, percentile original da fixture.
2. B: uptime 0.5, mesmo percentile de A.
3. C: igual a B, mas percentile 99 (chave de empate diferente).

A/B formam um grupo divergente e são removidos; C não pertence ao grupo e deve sobreviver pela regra fechada de §4.0. `cohort_match.py:159–165` implementa precisamente isso, removendo instâncias do grupo, não todas as representações do ID. A higiene escolhe C; M2.1 o aceita e o ID chega ao ledger e às seis populações métricas.

Reproduzido pelo **run_analysis real**, substituindo apenas aquisição, com Store temporário, nas **seis permutações**. Em todas:

- `excluded_conflicting_duplicates == 2`;
- `conflicting_duplicate_ids == ("REPORT:501:Duplicate",)`;
- o mesmo ID está em `eligibility.eligible_ids`, `ledger.member_ids` e em todas as seis populações DESCRIPTIVE;
- contrato, manifest e JSON lido de `runs` preservam exatamente esse resultado;
- proveniência/JSON são determinísticos — o defeito aqui é a contradição da exclusão por ID, não permutação.

Reprodução e saídas: [independent-rereview-probes.py.txt](independent-rereview-probes.py.txt), [independent-rereview-probes.log.txt](independent-rereview-probes.log.txt).

**Resolução requerida:** Opus deve reconciliar a unidade de exclusão e sua representação na proveniência: §4.0 prescreve exclusão por grupo de empate, enquanto §7.1 proíbe qualquer reaparição do ID. Sonnet não pode ampliar unilateralmente a quarentena para outras chaves, nem enfraquecer a invariante. Depois da decisão, acrescentar regressão permanente do caso composto e verificar contrato/persistência. É violação direta de uma invariante contratada em entrada suportada; não é pedido de robustez universal.

## R2 anterior — parcialmente resolvido; AC6 ainda não atendido

Confirmadas as correções principais:

- Replay real com ledger N=30, uptime N=34 e gross_ability_dps N=16, com exclusão da classe incompatível.
- Fixture GraphQL agora fornece `report_rankings` e afirma elegibilidade positiva; replay adicional mantém métrica suficiente com ledger vazio.
- Fixture de classe compatível corrigida e com asserções positivas.
- Guarda aspiracional exercitada no pipeline real, abaixo/acima do piso, incluindo `dps=None`.
- Equivalência contra implementação anterior real e permutação de proveniência completa; cálculo da identidade contábil acrescentado.

Persistem lacunas concretas dos contratos de evidência:

1. **§9.3/§9.7:** `test_run_analysis_ledger_insufficient_with_sufficient_metric` (linha 1037) não afirma ausência de `performance`, `comparisons`, `core_abilities`, `proc_analysis`, `external_dps_context` ou da comparação aspiracional, embora §9.3 exija os campos da coluna direita de §6.2 ausentes. As únicas asserções sobre ambas as renderizações continuam sendo `"Traceback" not in ...` (linhas 1087/1089; também 731/733 no replay misto). Não verificam ausência de números de comparações não computadas, exatamente a lacuna já apontada em R2.2. O código atual contém as guardas, mas removê-las ou exibir valores indevidos não é o comportamento verificado por essas asserções.
2. **§9.6:** a identidade contábil do replay misto é condicional a `total_delta_dps is not None` (linha 710), sem afirmar que a fixture produziu uma comparação quantitativa não trivial. A implementação atual produz essa comparação, mas o teste permite perder o objeto quantitativo esperado e saltar toda a prova. Exigir explicitamente o resultado esperado desta fixture antes da identidade; não é necessária uma nova fórmula ou suíte genérica.
3. **§9, pacote/AC6:** o documento atual substitui integralmente a evidência anterior e declara que ela não vale para fechamento. Porém §8 apenas menciona genericamente frases herdadas: não inventaria frases/locais afetados. Também falta a tabela **por consumidor com N antes/depois** exigida no final de §9. A tabela de três Ns atuais no replay não substitui a comparação antes/depois. As explicações das diferenças do golden em relação à baseline M2.2 também não estão preservadas no pacote atual: dizer que ele não mudou nesta correção não explica o diff ainda pendente de M2.3. Os comentários do teste ajudam a identificar a causa (partição), mas §9 pede a justificativa nas evidências.

**Resolução requerida ao Sonnet:** completar as asserções e evidências expressamente contratadas; preservar resultados/saídas verificáveis. A dívida editorial continua fora do escopo de correção de produto, mas seu inventário está dentro do escopo documental. Não é solicitado redesign ou nova coleta. Uma contagem de testes aprovados não substitui essas provas (workflow, regra de evidências).

## Critérios e fechamento do macro

| Critério | Reavaliação |
|---|---|
| AC1 | Replays e inspeção sustentam roteamento por população; correções principais de R2 confirmadas. |
| AC2 | Round-trips e determinismo originais passam; **bloqueado por R3**, invariante de exclusão por ID incompatível com regra de quarentena. |
| AC3 | Implementação mantém métrica suficiente quando ledger insuficiente; prova permanente de ausência/publicação ainda incompleta em R2. |
| AC4 | Identidades mantidas nos cenários atuais; guarda condicional no teste integrado deve deixar de permitir prova vazia. |
| AC5 | Módulos M2.1/M2.2 preservados (normalização CRLF/LF conforme entrega); matching anterior preservado nos casos executados; sem novo seletor, query ou alteração de fórmula identificados. |
| AC6 | **Não atendido: R2 residual.** |

M2.1 e M2.2 continuam fechadas, com registros e commits próprios; nenhuma dessas unidades é reaberta. A pré-condição de fechamento local está satisfeita. O macro M2 só fecha com M2.3, sua unidade explícita de integração/closure. R3 e R2 residual impedem ambos os fechamentos. Não há avanço para M3.

## Verificação independente

- Foco M2.3, matching, pipeline, persistência/regressões M1 e M2.2: **208 passed, 4 skipped**.
- Ruff check: passou. Ruff format: **333 files already formatted**.
- Pyright: **0 errors, 0 warnings, 0 informations**.
- Sonda R3: seis permutações, resultado confirmado inclusive em contrato e leitura do Store.
- Suíte offline ampla: **2716 passed, 47 skipped, 1 deselected, 1 failed**, 337 warnings, 180,69 s; **2 snapshots passed**. Única falha: `tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`, offender `src/botgitgud/orchestrator/__main__.py`. É a mesma dívida preexistente registrada em M2.1/M2.2 e v001; não é um novo bloqueio nem foi convertida em PASS. A contagem corresponde à evidência v002 e acrescenta 26 testes aprovados à baseline M2.2 (2690).
- Corpus opcional `data/raw` ausente neste workspace de produto; fixture `gate1_scope` executada pelos testes, com hash antes/depois. Não foi usado o banco histórico do workspace principal.

Comandos (raiz do workspace de produto):

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q tests/unit/test_m2_3_comparability_integration.py tests/unit/test_cohort_match.py tests/unit/test_pipeline.py tests/unit/test_m1_persistence_contract.py tests/unit/test_m1_required_changes.py tests/unit/test_m2_2_metric_population.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pyright
.venv/Scripts/python.exe -B docs/submilestones/M2.3/independent-rereview-probes.py.txt
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q
```

Na suíte ampla, `DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`, `BLIZZARD_CLIENT_ID` e `BLIZZARD_CLIENT_SECRET` receberam `ci-placeholder-not-a-secret`, conforme CI/evidências. Log: [independent-rereview-suite.log.txt](independent-rereview-suite.log.txt).

Somente artefatos independentes de revisão foram acrescentados. SPEC, implementação e testes permanentes não foram corrigidos.
