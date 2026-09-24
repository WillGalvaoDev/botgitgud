# M2.3 — independent re-review da SPEC v003 — 2026-09-23

Registro concluído em 2026-09-24 após retomada; hashes dos arquivos revisados reconfirmados sem alterações.

**REQUIRES_CHANGES. M2.3 e macro M2 permanecem abertos.**

**Routing: Sonnet**, para completar o R2 residual de testes/evidências. **R3 resolvido.** Não foi encontrado novo contraexemplo bloqueante de comportamento de produto nas sondagens desta rodada, nem contradição normativa que exija retorno ao Opus. Não há decisão identificada que exija HUMAN_BLOCK. Nenhuma correção implementada; M3 não iniciado.

## Objeto, versão e preservação

Workspace de produto indicado pelo repositório: `C:/Users/wsgon/OneDrive/Desktop/BotGITGUD-M2.1-product`, HEAD `b6241df` (M2.2 fechada); entrega M2.3 não commitada. Autoridades: M0/M1, SPECs e fechamentos M2.1/M2.2, roadmap e workflow. Revisados SPEC v003, implementação, testes, novas evidências e findings da [re-revisão anterior](independent-rereview.md).

Os arquivos `docs/m2-3-specification.md` e `docs/submilestones/M2.3/spec-v003.md` são byte-idênticos: SHA-256 `b39f576234efc2dd7747200c15953fbda4b055e84e7f4926b57a79ecc8a6f372`. Ambiente e hashes dos arquivos revisados: [independent-v003-artifacts.json](independent-v003-artifacts.json). Comparação com o manifesto independente de v002 confirmou alterações somente em SPEC atual, evidência atual, `cohort_match.py` e teste de integração M2.3; os demais arquivos previamente revisados permanecem iguais.

## R3 — resolvido

A SPEC v003 define sem ambiguidade o agrupamento por observação, sem percentil na chave, e a exclusão fechada pelos IDs detectados, somente dentro do domínio (§4.0). Isso garante a invariante de §7.1, preservando o comportamento de `match_cohort` e a contagem separada de self/não-kill. A implementação corresponde à regra.

Confirmado pela regressão permanente das seis permutações de A/B/C: três logs removidos; ID ausente de elegibilidade, ledger e populações DESCRIPTIVE/ASPIRATIONAL, inclusive após contrato, manifest e leitura de `runs`. R1 original e duplicata idêntica continuam cobertos e passam.

Sondagem independente adicional combinou A/B/C com um homônimo de outro servidor (mesmo ID) e um não-kill do mesmo ID. Nas seis permutações: quatro logs em quarentena, um removido pela higiene como não-kill, identidade dos contadores preservada, nenhuma reaparição do ID e JSON idêntico; contrato/Store fizeram round-trip exato. Também foram verificados nomes que diferem por caixa/espaços: a mesma identidade normalizada com representações divergentes remove ambos os IDs brutos. Não houve novo contraexemplo bloqueante nesses casos compostos.

Reprodução e saídas: [independent-v003-probes.py.txt](independent-v003-probes.py.txt), [independent-v003-probes.log.txt](independent-v003-probes.log.txt).

## R2 residual — parcialmente resolvido; ainda bloqueia AC6

Correções aceitas nesta rodada:

- Os testes agora afirmam ausência dos campos dependentes de ledger insuficiente no resultado e no contrato.
- O replay misto exige `accounting_status == "AVAILABLE"` e `total_delta_dps is not None` antes de calcular a identidade. Reexecução independente: N contábil aceito 16, delta total `-333.3333333333333 DPS`, soma por habilidade + suporte + residual fecha.
- O documento agora contém locais concretos da dívida editorial (§7) e a explicação da mudança do golden (§9). A causa por partição é consistente com os testes/diff; a perda de cobertura de conteúdo real permanece declarada, sem exigir nova coleta.

Restam dois itens já contratados e já apontados em R2:

### R2.1 — prova de renderização ainda não verifica o conteúdo Discord exigido

Contrato: SPEC §9.7/§6.4 e AC6, renderização CLI **e Discord** sem números de comparação dependente não computada.

Em `tests/unit/test_m2_3_comparability_integration.py:1315–1316`, o teste de ledger insuficiente com métrica suficiente ainda verifica somente `"Traceback" not in discord_text`. O teste GraphQL e o replay misto mantêm a mesma verificação para Discord. Asserções sobre o contrato comprovam o conteúdo de entrada, mas não o que o renderer publica.

Contraexemplo reproduzível à prova: na sonda independente, substituí **apenas em memória** o retorno de `render_coaching_answer` usado pelo teste por `Comparacao da coorte: mediana 123456.7 DPS; gap 99999.9 DPS.`. Executei o teste permanente completo, sem modificar fonte nem fixture; **ele passou**, embora esse texto publique uma mediana e um gap de ledger inexistentes. A chamada substituída foi confirmada. Isso demonstra a lacuna específica de §9.7, não uma exigência de cobertura universal.

Limitação adicional das asserções CLI: as ausências de `ANALISE POR HABILIDADE`, `SELF BUFFS & PROCS` e `CONTEXTO DE DPS EXTERNO` são verificadas numa chamada que omite `core_abilities`, `proc_analysis` e `external_dps_context`; a instrumentação confirmou seis argumentos posicionais e nenhum keyword. Esses campos ficam nos defaults do renderer, em vez de provar sua passagem pelo caminho renderizado. As asserções diretas de ausência no resultado são válidas e foram reconhecidas acima.

**Correção requerida:** manter as asserções de resultado/contrato e acrescentar verificação semântica da saída Discord nos cenários contratados, rejeitando mediana/delta/valores de comparações indisponíveis; passar os campos reais ao verificar sua renderização CLI. Não proibir números legítimos da métrica suficiente ou da observação do jogador. Não é solicitado redesign, texto novo ou correção de produto sem defeito observado.

A renderização atual foi executada separadamente com todos os campos reais: CLI informa `NO_REFERENCES`/comparação indisponível; Discord informa que não há prioridade de coaching sustentada. Saída preservada em [independent-v003-render.json](independent-v003-render.json). **O bloqueio é a prova permanente contratada, não uma alegação de que o renderer atual fabricou os números da mutação.**

### R2.3 — tabela antes/depois continua incompleta e contém contagem incorreta

Contrato: último parágrafo de SPEC §9, linhas 502–505, e AC6: **“tabela por consumidor com N antes/depois no replay (2)”**.

`docs/m2-3-review-evidence.md:255–256` informa `— (população M2.2 não existia antes de M2.3)` / `— (não existia)` para os seis consumidores métricos. A população estruturada nova não era usada em produção antes, mas **os consumidores e suas distribuições de comparação existiam**. O N anterior é calculável; não é N/A. A tabela ainda não mostra o antes/depois desses cálculos, que é o objetivo da exigência.

Carreguei o código real de `cohort_match.py`, `metric_observations.py` e `measurement.py` do commit de fechamento M2.2, `b6241df`, e executei sobre a mesma fixture, com o piso/política de produção e o mesmo catálogo. Depois, executei `run_analysis` atual. Resultados:

| Consumidor/distribuição | Antes M2.3 | Depois v003 |
|---|---:|---:|
| Entrada do ledger | 31 | 30 |
| Referências quantitativas aceitas pelo ledger | 17 | 16 |
| `gross_ability_dps:1` | 17 | 16 |
| `damage_events_per_second:1` | 17 | 16 |
| `damage_per_event:1` | 17 | 16 |
| `gross_damage_share_pct:1` | 17 | 16 |
| `aura_uptime_fraction:1` | 31 | 34 |
| `player_casts_per_minute:1` | 31 | 34 |

A mesma linha 255 diz “excluídas as 16 referências sem coleta de dano completa”. A fixture contém **14**, não 16: `range(30)` com `complete_damage_collection=i < 16`. As exclusões observadas de `gross_ability_dps:1` são **14 `UNKNOWN_DAMAGE_COLLECTION` + 4 `TIER_PIECES_BAND_MISMATCH` + 1 `BASIC_ELIGIBILITY_INELIGIBLE`**: 35 entradas − 19 = 16 membros.

**Correção requerida:** completar a tabela por consumidor com os Ns realmente usados antes/depois e corrigir os motivos/contagens, preservando comando/script e resultados verificáveis. A sonda independente fornece os resultados acima, mas não transforma em correta a afirmação incompatível que permanece na entrega. Não é necessário alterar a SPEC nem a seleção de produção.

## Acceptance criteria e fechamento

| Critério | Resultado da revalidação |
|---|---|
| AC1 | Roteamento correto nos replays, sem novo contraexemplo bloqueante; populações efetivamente consumidas preservadas. |
| AC2 | R3 resolvido; determinismo, exclusão por ID, contrato/persistência e leitura legada verificados. |
| AC3 | Ledger insuficiente preserva métrica suficiente e observações independentes; campos dependentes ausentes. Lacuna de prova da publicação está em R2.1/AC6. |
| AC4 | Comparação não trivial agora obrigatória no teste; identidade conferida independentemente e regressões executadas. |
| AC5 | Escopo preservado, módulos M2.1/M2.2 inalterados conforme hashes/normalização já documentada; matching anterior equivalente nos testes; sem mudança de query/fórmula/texto identificada. |
| AC6 | **Não atendido: R2.1 e R2.3 residuais acima.** |

M2.1 e M2.2 continuam fechadas; seus registros/commits satisfazem as dependências locais. O workflow exige o fechamento independente de M2.3 para fechar o macro M2. Enquanto AC6 não estiver atendido, **nenhum dos dois fecha**. O trabalho restante vai ao Sonnet, dentro do escopo de testes/evidências; não demanda nova decisão metodológica. M3 permanece não iniciado por esta revisão.

## Verificações independentes

- Foco M2.3, matching, pipeline, regressões/persistência M1, M2.2 e golden: **219 passed, 4 skipped; 1 snapshot passed**.
- Ruff check passou; Ruff format: **333 files already formatted**.
- Pyright: **0 errors, 0 warnings, 0 informations**.
- Sondas R3 e round-trips adicionais passaram; tabela antes/depois reproduzida; mutação isolada demonstrou a lacuna de teste Discord.
- Suíte ampla: **2720 passed, 47 skipped, 1 deselected, 1 failed**, 337 warnings, 319,81 s; **2 snapshots passed**. Única falha: `tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`, no orquestrador, a mesma dívida preexistente registrada desde M2.1. Não foi convertida em PASS nem usada como novo bloqueio. Contagens confirmam a evidência v003: quatro testes aprovados a mais que v002 e trinta a mais que a baseline M2.2.

Comandos, da raiz do workspace de produto:

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q tests/unit/test_m2_3_comparability_integration.py tests/unit/test_cohort_match.py tests/unit/test_pipeline.py tests/unit/test_m1_persistence_contract.py tests/unit/test_m1_required_changes.py tests/unit/test_m2_2_metric_population.py tests/golden/test_new_pipeline_output.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pyright
.venv/Scripts/python.exe -B docs/submilestones/M2.3/independent-v003-probes.py.txt
.venv/Scripts/python.exe -B -m pytest -o addopts='' -p no:cacheprovider -m 'not network' -q
```

Suíte ampla com os cinco placeholders públicos `ci-placeholder-not-a-secret` em `DISCORD_TOKEN`, `WCL_CLIENT_ID`, `WCL_CLIENT_SECRET`, `BLIZZARD_CLIENT_ID`, `BLIZZARD_CLIENT_SECRET`. Log: [independent-v003-suite.log.txt](independent-v003-suite.log.txt). Corpus opcional `data/raw` ausente no workspace de produto; a fixture `gate1_scope` foi exercitada com preservação verificada pelos testes. Banco e dados históricos do workspace principal não foram usados.

Somente artefatos de revisão independente foram acrescentados. Nenhuma SPEC, implementação ou teste permanente foi corrigido. Revisão encerrada sem avançar para M3.
