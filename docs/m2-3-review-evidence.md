# M2.3 — evidências de integração e closure da comparabilidade

Status: **IMPLEMENTATION_READY**. Autoridade: [`m2-3-specification.md`](m2-3-specification.md),
spec_sha `0bc1b444c5831319495de8ba318d19426c6da0e2fdaa37aca8e1e3b2000aeee9`
(`docs/submilestones/M2.3/spec-v003.md`/`.json`, **v003** — substitui v002, `spec_sha`
`7a0f78f5f116729d631818d6b9695e18f83cf7da43c89d01141f9a9ee17d4137`, que substituiu
v001, `spec_sha` `392f6d5bbff6dedb9eb1299919c253c5c17d41ac51d2cce79d202fdd35b6f354`).
Escopo: somente M2.3. Linha de produto: workspace `BotGITGUD-M2.1-product`, sobre
o commit de fechamento de M2.2 `b6241df` (macro M2 ainda aberto antes desta unidade).

**Esta é a correção v003.** Resolve os achados de
`docs/submilestones/M2.3/independent-rereview.md` (2026-09-23):

- **R3** (Opus, metodológico): a quarentena de v002 agrupava pela chave de
  empate da higiene (que inclui `dedup_priority`), então uma terceira
  representação com `dedup_priority` diferente escapava do grupo divergente e
  levava seu id a `eligibility`/`ledger`/`metrics` — violando a invariante que
  a própria SPEC v002 §7.1 contratava. D-M23-07 é refinada (SPEC v003 §4.0):
  a unidade de conflito passa a ser a observação `(report_code, fight_id,
  player_identity)`, e a exclusão é fechada por `damage_reference_id`.
- **R2 residual** (Sonnet, evidência/teste): três lacunas que sobreviveram à
  correção v002 — ausência explícita dos campos "não computados" de §6.2 e de
  seus números no texto renderizado; a identidade contábil do replay misto
  condicional a um resultado que o teste não garantia; e um pacote de
  evidências sem inventário de dívida de apresentação nem tabela por
  consumidor com N antes/depois. Corrigidas nesta rodada (§3 abaixo).

**Rodada seguinte à revisão da v003**
(`docs/submilestones/M2.3/independent-v003-review.md`, R3 resolvido): restavam dois
itens de R2/AC6, fechados aqui sem tocar SPEC, produção nem R3 — **R2.1** (prova de
conteúdo da renderização Discord/CLI, §3.1) e **R2.3** (tabela antes/depois medida e
corrigida, §8).

**As evidências produzidas contra v001 e v002 são inválidas para o fechamento
desta versão**, conforme a própria v003 declara. Este documento substitui o
anterior por inteiro; nada dele é reaproveitado sem nova verificação.

## 0. Arquivos alterados nesta correção

Exatamente o previsto pela SPEC v003 para resolver R3/R2 residual, dentro do
`write_paths` já aprovado (inalterado desde v001):

**Produção:**

- `src/botgitgud/analysis/cohort_match.py` — `quarantine_conflicting_duplicates`
  reescrita (SPEC v003 §4.0, D-M23-07): agrupa por
  `observation_key = (report_code, fight_id, player_identity)`, não mais pela
  chave de empate da higiene; a exclusão passa a ser fechada por
  `damage_reference_id` (`conflicting_ids`), removendo todo log do domínio com
  um id em conflito, não só as instâncias do grupo detectado. `match_cohort`
  continua byte-idêntico para qualquer entrada — a quarentena nunca é chamada
  por ele (invariante 2 da SPEC), e nenhum outro arquivo de produção precisou
  mudar: `DuplicateConflictReport`, `comparability_provenance.py` e a
  chamada em `pipeline.py` já tinham a forma certa desde v002.

**Testes:**

- `tests/unit/test_m2_3_comparability_integration.py` — 32 testes (era 26 em
  v002; 30 na rodada da v003, +2 nesta): 4 novos (dois unitários de quarentena para o gap de R3, um unitário
  para o contraexemplo composto A/B/C e um para o fecho por id entre
  servidores; um `run_analysis` de seis permutações do contraexemplo
  composto), mais, nesta rodada, o verificador de números não computados e a tabela
  N antes/depois medida (§3.1, §8), e as três lacunas de R2 residual fechadas dentro de testes
  já existentes (§3). Nenhum teste foi removido.

Nenhum outro arquivo (`test_pipeline.py`, `test_cohort_match.py`,
`test_m1_persistence_contract.py`, `test_m1_required_changes.py`,
`tests/golden/`) precisou de mudança nesta correção — confirmado rodando cada
um isoladamente após a mudança de produção (§6). `reference_eligibility.py` e
`metric_population.py` continuam **não** tocados — byte-idênticos, verificados
em §5.

## 1. R3 — unidade de conflito e fecho por id (D-M23-07 refinada)

### 1.1 O defeito em v002 e a correção

Em v002, `quarantine_conflicting_duplicates` agrupava candidatos por
`(report_code, fight_id, player_identity, dedup_priority)` — a chave exata em
que os `min()` da higiene empatam. Uma terceira representação com a MESMA
observação (mesmo jogador, mesmo pull) mas um `dedup_priority` diferente
(por exemplo, um percentil diferente) formava seu PRÓPRIO grupo de tamanho 1,
nunca detectado como conflito, e sobrevivia — levando um id que deveria estar
em quarentena para `eligibility`, `ledger` e todas as populações M2.2,
violando a invariante da própria SPEC v002 (§7.1: "nenhum id de
`conflicting_duplicate_ids` aparece em `eligibility`, `ledger` ou `metrics`").

Em v003, o agrupamento usa `observation_key = (report_code, fight_id,
player_identity)` — sem `dedup_priority`. Toda representação da mesma
observação cai no mesmo grupo, então uma terceira representação divergente
não tem mais como escapar. A exclusão também passa a ser fechada por
`damage_reference_id`: todo log do domínio cujo id está em `conflicting_ids`
é removido, não só as instâncias fisicamente agrupadas — isso cobre o caso em
que um homônimo (mesmo nome, servidor diferente) no mesmo pull tem
`player_identity` diferente (logo cairia num grupo próprio, não-conflitante
por si só) mas o MESMO `damage_reference_id` (que não inclui servidor).

### 1.2 Testes unitários da quarentena (oráculo escrito à mão)

Os 6 testes de v002 continuam válidos e passam sem alteração (a mudança de
agrupamento não altera nenhum deles — nenhuma fixture existente tinha uma
terceira representação com `dedup_priority` divergente). Quatro novos,
exigidos por SPEC v003 §9.8 para fechar o gap de R3:

| Cenário | Esperado | Teste |
|---|---|---|
| Mesmo jogador, mesmo pull, SÓ o percentil difere (demais campos iguais) | agora É conflito — ambos removidos (v002 não detectava isso: dedup_priority diferente escapava do grupo) | `test_quarantine_same_player_same_pull_different_percentile_is_a_conflict` |
| Contraexemplo composto de R3: A/B (mesmo percentil, campo divergente) + C (percentil diferente, igual a B) | as 3 representações formam UMA observação em conflito; todas removidas, em toda permutação | `test_quarantine_removes_the_r3_composite_counterexample_entirely` |
| Fecho por id: 2 representações divergentes do mesmo jogador/pull + 1 homônimo de outro servidor no mesmo pull, mesmo id | as 3 removidas — o homônimo tem sua PRÓPRIA observação (não é conflito por si só), mas compartilha o id, então é removido pelo fecho | `test_quarantine_closes_exclusion_by_id_across_different_servers` |

(A tabela de v002, com os 6 testes originais — grupo divergente completo,
duplicatas idênticas intactas, jogadores diferentes/percentis diferentes
intactos, self/non-kill fora do domínio, ids ordenados/deduplicados,
invariância de permutação — permanece válida; ver `test_quarantine_*` no
arquivo de testes.)

### 1.3 O contraexemplo composto de R3, via `run_analysis` real, nas seis permutações

`test_run_analysis_r3_composite_counterexample_quarantined_in_every_permutation`
reproduz literalmente o contraexemplo da re-revisão — três representações de
`REPORT:501:Duplicate` (A: sem uptime; B: uptime 0.5, mesmo percentil de A; C:
igual a B, percentil diferente) — com 16 referências limpas adicionais, nas
SEIS permutações de `[A, B, C]`. Em cada uma:

- `hygiene.conflicting_duplicate_ids == ("REPORT:501:Duplicate",)`;
- `hygiene.excluded_conflicting_duplicates == 3`;
- o id está ausente de `eligibility.eligible_ids`, `.indeterminate_ids` e
  `.ineligible_ids`;
- o id está ausente de `ledger.member_ids` e de TODAS as populações
  DESCRIPTIVE e ASPIRATIONAL das seis métricas;
- `ledger.state == "SUFFICIENT"` (as 16 referências limpas não são afetadas);
- `contract.confianca.comparability is provenance` (mesmo objeto);
- `decode_comparability_provenance(manifest.comparability_provenance_json) == provenance`;
- o JSON lido de volta da linha `runs` do Store decodifica para o mesmo
  `provenance`;
- a proveniência codificada é byte-idêntica entre as seis permutações.

Isso fecha R3: nenhuma ordem de chegada das três representações produz um
resultado diferente, e o id nunca aparece onde a invariante de §7.1 proíbe.

## 2. R1 (v002, preservado) — quarentena de representações divergentes

Continua válido, sem mudança de comportamento além do refinamento de R3: os
3 testes de `run_analysis` nas duas ordens (`uptime` ausente/observado,
classe divergente, duplicata idêntica como contraste) de v002 passam sem
alteração — ver `test_run_analysis_r1_*`/`test_run_analysis_identical_duplicate_*`
no arquivo de testes.

## 3. R2 residual — as três lacunas fechadas

| # | Achado da re-revisão | Correção nesta versão |
|---|---|---|
| 1 | `test_run_analysis_ledger_insufficient_with_sufficient_metric` não afirmava ausência de `performance`, `comparisons`, `core_abilities`, `proc_analysis`, `external_dps_context` nem da comparação aspiracional; as únicas asserções de renderização eram `"Traceback" not in ...`, em ambos os testes de ledger insuficiente e no replay misto — não verificavam ausência de números de comparações não computadas | Adicionadas asserções diretas de campo (`result.performance is None`, `result.comparisons == ()`, `result.core_abilities == ()`, `result.proc_analysis is None`, `result.external_dps_context == ()`, `aspirational_comparison.reference_ids == ()`) e as mesmas espelhadas no `contract`, nos dois testes de ledger insuficiente. No texto renderizado, verificada a AUSÊNCIA dos cabeçalhos de seção que só aparecem quando o campo correspondente é não vazio (`"ANALISE POR HABILIDADE"`, `"SELF BUFFS & PROCS"`, `"TIMELINE OFENSIVA"`, `"CONTEXTO DE DPS EXTERNO"` — `report/text.py::render_report`), e a PRESENÇA explícita de `"Comparação indisponível"`/`"NO_REFERENCES"` para a comparação do ledger. No replay misto (ledger SUFICIENTE), o espelho: `"Comparação indisponível"` AUSENTE e `"Gap observado"` PRESENTE, provando que o texto reflete o número real quando ele existe |
| 2 | A identidade contábil do replay misto (`ability_delta_dps` + suporte + residual == `total_delta_dps`) era condicional a `total_delta_dps is not None`, permitindo que o teste passasse mesmo se a fixture parasse de produzir uma comparação quantitativa, pulando toda a prova silenciosamente | `test_run_analysis_population_routing_larger_and_smaller_than_ledger_in_same_replay` agora afirma explicitamente `result.dps_gap.accounting_status == "AVAILABLE"` e `ledger_comparison.total_delta_dps is not None` ANTES de calcular a identidade — a fixture é obrigada a produzir o resultado quantitativo esperado, não apenas tolerada se produzir |
| 3 | O pacote de evidências não inventariava as frases/locais concretos da dívida de apresentação de §6.4, nem trazia uma tabela por consumidor com N antes/depois do replay (2) (só os três Ns atuais), nem preservava a explicação do diff do golden pendente de M2.3 | Este documento: §7 inventaria os dois locais concretos com trecho de código; §8 traz a tabela antes/depois com números computados de verdade (não os três Ns finais); §9 preserva a explicação completa do diff do golden (causa raiz na cassette, não um artefato de teste) |

### 3.1 R2.1 — prova de conteúdo da renderização (revisão da v003)

A revisão substituiu, só em memória, o retorno de `render_coaching_answer` por
`Comparacao da coorte: mediana 123456.7 DPS; gap 99999.9 DPS.` e o teste permanente
passou (só verificava `"Traceback" not in discord_text`). Correção, sem alterar
produção nem texto:

- `_assert_no_uncomputed_comparison_numbers` rejeita afirmações de mediana/média/gap/
  delta/coorte/referência/comparáveis com número, e qualquer quantidade `DPS`/`%`.
  `test_uncomputed_comparison_number_check_rejects_the_reviews_mutation` fixa que o
  verificador rejeita exatamente o texto da mutação e aceita a resposta real.
- Nos dois cenários de ledger insuficiente (GraphQL e métrica suficiente), o Discord
  agora é verificado por **conteúdo**: sem números de comparação não computada **e**
  igual à resposta própria do renderer ("A análise não encontrou uma prioridade de
  coaching sustentada pelos dados."). Números legítimos da métrica suficiente ou do
  jogador não são proibidos em nenhum outro ponto — o cenário insuficiente não publica
  nenhum.
- No replay misto (ledger suficiente, comparação computada), a verificação é positiva:
  o Discord publica `"dos 16 comparáveis"`, o N contábil realmente aceito
  (`DamageComparison.reference_n`), e **não** o N de entrada do ledger (30) nem o da
  população de uptime (34).
- CLI: `_render_all` passa `core_abilities`, `proc_analysis`, `external_dps_context`,
  `setup` e `confidence` reais ao `render_report` (antes ficavam nos defaults, então as
  ausências de `ANALISE POR HABILIDADE`/`SELF BUFFS & PROCS`/`CONTEXTO DE DPS EXTERNO`
  não provavam nada sobre a passagem dos campos). As asserções diretas de ausência no
  resultado e no contrato foram mantidas.
- **Mutação reexecutada** (patch de `render_coaching_answer` com o texto fabricado) nos
  testes de ledger insuficiente e no replay misto: ambos falham agora (antes: passavam).

Saída real preservada pela revisão em `independent-v003-render.json` (CLI informa
`NO_REFERENCES`/"Comparação indisponível (N=0; mínimo=8)"; Discord, a resposta acima).

## 4. Matriz completa de casos (32 testes, SPEC §4-§9)

| Comportamento | Situação | Resultado esperado | Teste |
|---|---|---|---|
| Equivalência contra o pré-M2.3 real | fixtures de `test_cohort_match.py` + casos sintéticos, v1 e v2, cada um nas duas ordens | `match_cohort` novo == antigo (git history) | `test_match_cohort_equals_the_actual_pre_m23_implementation` |
| Idem, com empate divergente | par com `tier_pieces` diferente | novo == antigo em CADA ordem | `test_match_cohort_equals_the_actual_pre_m23_implementation_with_divergent_duplicates` |
| Quarentena — unidade (v002, preservados) | ver v002 | ver v002 | 6 testes `test_quarantine_*` originais |
| Quarentena — unidade, gap de R3 | ver §1.2 | ver §1.2 | 3 testes novos, ver §1.2 |
| Quarentena — R1 via `run_analysis`, duas ordens | ver §2 | ver §2 | 3 testes |
| Quarentena — R3 composto via `run_analysis`, seis permutações | ver §1.3 | ver §1.3 | `test_run_analysis_r3_composite_counterexample_quarantined_in_every_permutation` |
| Referência que M2.1 exclui, compatíveis genuinamente compatíveis | classe divergente + 9 compatíveis reais | `CLASS_MISMATCH`, fora do ledger/populações; compatíveis DENTRO de ambos | `test_class_mismatched_reference_stays_out_of_ledger_and_every_metric_population` |
| População M2.2 maior E menor que o ledger, mesmo replay | 35 referências (30 limpas + 4 `tier_pieces` + 1 classe divergente) | `aura_uptime_fraction` (34) > ledger (30) > `gross_ability_dps` (16); identidade contábil M1 calculada e verificada; ausência de comparação não computada N/A aqui (tudo SUFICIENTE) — ver §3.1/3.2 | `test_run_analysis_population_routing_larger_and_smaller_than_ledger_in_same_replay` |
| Verificador de números não computados rejeita a mutação da revisão | texto fabricado com mediana/gap | `AssertionError`; resposta real aceita | `test_uncomputed_comparison_number_check_rejects_the_reviews_mutation` |
| N antes/depois por consumidor, medido | mesmo replay; pré-M2.3 real (git `b6241df`) vs. `run_analysis` atual | tabela do §8 (ledger 31→30; aceitas 17→16; 4 métricas de dano 17→16; uptime/casts 31→34); exclusões 14+4+1 | `test_run_analysis_consumer_n_before_and_after_m23_on_the_mixed_replay` |
| Roteamento invariante à ordem de busca | mesma fixture, ordem revertida | proveniência/JSON idênticos por métrica | `test_run_analysis_population_routing_is_invariant_to_reference_order` |
| Guarda aspiracional do ledger, produção real | abaixo/acima do piso de 8 ordenáveis | `[]`/`ASPIRATIONAL_UNAVAILABLE` abaixo; não vazio e só dps finitos acima | `test_run_analysis_aspirational_ledger_guard_never_passes_non_finite_dps_and_respects_the_floor` |
| Round-trip de proveniência | `ComparabilityProvenance` real | `decode(encode(x)) == x`; chaves ordenadas; só ASCII | `test_comparability_provenance_round_trips_through_encode_decode` |
| Versão desconhecida no decode | payload inválido | `ValueError` | `test_decode_rejects_an_unknown_provenance_version` |
| Round-trip via Store | `write_run` → `SELECT` → decode | idêntico ao original | `test_comparability_provenance_round_trips_through_store_write_and_read` |
| Migração aditiva/linha legada | banco pré-M2.3 | colunas novas `NULL` | `test_legacy_runs_row_decodes_with_unknown_comparability_version` |
| Ledger insuficiente, GraphQL completo | 8 referências fora de banda, `report_rankings` corrigido | eligibilidade 100%; ledger `INSUFFICIENT_REFERENCES`; ausência de conteúdo verificada no texto (§3.1) | `test_run_analysis_ledger_insufficient_via_graphql_partition_fix_still_renders_safe` |
| Ledger insuficiente COM métrica suficiente | 16 referências 30% fora da banda máxima do ledger | `gross_ability_dps` `SUFFICIENT_FOR_GRADING`; todos os campos §6.2 ausentes checados; texto sem os cabeçalhos correspondentes | `test_run_analysis_ledger_insufficient_with_sufficient_metric` |
| Ledger suficiente, proveniência completa | fixture feliz | `ledger.state=SUFFICIENT`; manifest/Store com o mesmo JSON | `test_run_analysis_sufficient_ledger_populates_full_comparability_provenance` |
| Determinismo entre execuções | mesma fixture, dois `Deps` independentes | `comparability` e JSON idênticos | `test_run_analysis_comparability_is_deterministic_across_repeated_runs` |
| M2.1/M2.2 intocados | SHA-256 dos dois módulos | iguais aos fechados | `test_m2_1_and_m2_2_modules_remain_byte_identical_to_the_closed_delivery` |
| Caminho de produção só usa `compare_metrics` em modo de população | inspeção de `dps_gap.py` | `populations=metric_populations` sempre no caminho real | `test_production_path_calls_compare_metrics_only_in_population_mode` |
| Replay somente-leitura | `tests/fixtures/gate1_scope/` | populações `n=0`; hash antes/depois igual | `test_replay_gate1_scope_end_to_end_comparability_matches_manual_expectation` |

## 5. Critério → teste → resultado (AC1–AC6, v003)

| Critério | Teste representativo | Entrada | Esperado | Observado |
|---|---|---|---|---|
| AC1 | `test_run_analysis_population_routing_larger_and_smaller_than_ledger_in_same_replay` | 35 referências reais | IDs de `MetricComparison` == população declarada; nenhum ID rejeitado por M2.1 | idêntico |
| AC1 | `test_class_mismatched_reference_stays_out_of_ledger_and_every_metric_population` | classe divergente + 9 compatíveis reais | fora vs. dentro corretamente | idêntico |
| AC2 | `test_comparability_provenance_round_trips_through_encode_decode` + `..._through_store_write_and_read` | proveniência real | round-trip exato | idêntico |
| AC2 | `test_run_analysis_r3_composite_counterexample_quarantined_in_every_permutation` | contraexemplo composto de R3, 6 permutações | id ausente de `eligibility`/`ledger`/`metrics`/contrato/manifest/Store em TODAS; proveniência byte-idêntica | idêntico — **R3 resolvido** |
| AC2 | `test_run_analysis_r1_uptime_divergent_duplicate_quarantined_and_order_invariant` + `..._class_divergent...` | contraexemplos de R1, duas ordens | invariante à ordem | idêntico |
| AC2 | `test_legacy_runs_row_decodes_with_unknown_comparability_version` | linha `runs` pré-M2.3 | colunas novas `NULL` | idêntico |
| AC3 | `test_run_analysis_ledger_insufficient_with_sufficient_metric` | ledger vazio | TODOS os campos §6.2 ausentes checados (não só "sem Traceback"); métrica suficiente mantém comparação | idêntico — R2 residual #1 fechado |
| AC4 | `test_run_analysis_population_routing_larger_and_smaller_than_ledger_in_same_replay` + suíte M0/M1 | replay real com resultado quantitativo GARANTIDO (não condicional) | identidade contábil calculada sobre um resultado que o teste exige que exista | idêntico — R2 residual #2 fechado |
| AC5 | `test_m2_1_and_m2_2_modules_remain_byte_identical_to_the_closed_delivery` + equivalência `match_cohort` | SHA-256; git history | iguais/idênticos | idêntico |
| AC6 | Este documento + `docs/m2-3-specification.md` v003 + §6/§7/§8/§9 | pacote completo | replay integrado, round-trip, quarentena R1+R3, inventário §7, tabela §8, diff do golden §9, Ruff, Pyright, suíte ampla | idêntico — R2 residual #3 fechado |

## 6. Regressões contábeis de M1 e módulos M2.1/M2.2 intocados

- `test_m2_1_and_m2_2_modules_remain_byte_identical_to_the_closed_delivery`:
  SHA-256 do blob git (normalizado LF) — `reference_eligibility.py` =
  `17496bd431e1177dcc2bc9bf6430f506519c196416dd5c5df652deba406863ad`,
  `metric_population.py` = `8aff6e01430c2e6fa7b0406ca42a0869f941f4b1186574bb40e80b3287aa5e2a`,
  ambos idênticos aos das entregas fechadas — nenhuma mudança nesta correção.
- Suítes M0/M1 completas passam dentro da suíte ampla (§6/§10), sem nenhuma
  alteração nesta rodada.
- `test_cohort_match.py` (inalterado) continua passando: prova independente de
  que a reescrita de `quarantine_conflicting_duplicates` — chamada só pelo
  caminho de produção em `pipeline.py` — não muda nada em `match_cohort`.
- A identidade contábil de M1 é calculada sobre um resultado GARANTIDO pela
  fixture (`accounting_status == "AVAILABLE"`, `total_delta_dps is not None`
  afirmados antes do cálculo — R2 residual #2), não apenas sobre um resultado
  que aconteceu de existir.
- `tests/unit/test_pipeline.py`, `tests/unit/test_m1_persistence_contract.py`,
  `tests/unit/test_m1_required_changes.py` — inalterados, todos passam.
- O golden real (`tests/golden/`) permanece inalterado por esta correção —
  rodado isoladamente, 1 snapshot passou, sem diff. A explicação completa do
  diff pendente de M2.3 (herdado de v001, nunca corrigido nem reaberto por
  R3/R2 residual) está preservada em §9.

## 7. Dívida de apresentação declarada (SPEC §6.4) — inventário concreto

A SPEC autoriza, mas não exige corrigir nesta unidade, frases que associem o N
do ledger a uma grade cuja população é outra (dívida de M5.2, C09). Dois
locais concretos, identificados por inspeção do código de apresentação
(nenhum tocado nesta correção):

1. **`src/botgitgud/report/text.py`, `_render_covariates_line`
   (linha ~114) e a seção "COORTE & CONFIANÇA" (linha ~278):** ambas
   renderizam `"Coorte pareada: {n} logs"`, onde `n` é
   `header.matched_reference_n`/`confidence.matched_cohort_members` — SEMPRE
   o N do **ledger** (`R_log`), nunca o N de uma população M2.2 específica.
   Essa linha aparece uma vez por relatório, tipicamente antes das seções
   por habilidade/uptime.
2. **`src/botgitgud/report/performance_text.py::render_uptimes_section`
   (linha ~52):** renderiza a grade de `aura_uptime_fraction:<sid>` — desde
   M2.3, alimentada pela população DESCRIPTIVE própria dessa métrica em
   M2.2, tipicamente DIFERENTE do ledger (ver §8: 34 vs. 30 no replay
   misto) — mas a linha nunca imprime o N dessa população
   (`uf.finding.stats.n` só é usado como limiar interno, `>= 8`, nunca
   exibido). Um leitor não tem, nesta seção, nenhum número próprio para
   contrastar com o "Coorte pareada" já mostrado no cabeçalho, e pode
   presumir (incorretamente, quando as populações divergem) que é o mesmo N.

Nenhuma dessas duas linhas foi criada ou alterada por M2.3 — ambas já
existiam antes desta unidade; M2.3 apenas fez a grade de uptime passar a vir
de uma população diferente (M2.2) sem tocar no texto que a envolve. Corrigir
isso (mostrar o N próprio de cada grade) é trabalho de M5.2, fora do escopo
desta unidade — a SPEC exige o inventário, não a correção.

## 8. Roteamento por consumidor — tabela N antes/depois (replay do §4/AC1)

Medida por `test_run_analysis_consumer_n_before_and_after_m23_on_the_mixed_replay`
(35 referências: 30 limpas — 14 sem coleta de dano completa, pois
`complete_damage_collection=i < 16`: 16 completas e 14 incompletas — + 4 com `tier_pieces` divergente + 1 de classe incompatível).
**Antes** = os consumidores reais pré-M2.3 (`match_cohort`,
`metric_observations.compare_metrics`, `measurement.compare_damage`) carregados de
`git show b6241df:<arquivo>` (fechamento de M2.2) e executados sobre as mesmas
referências, com `min_n=COHORT_TARGET_N`, política de matching de produção e o mesmo
catálogo; **depois** = as referências que o `run_analysis` atual efetivamente usou.
Reprodução: `pytest -o addopts="" -p no:cacheprovider -q
tests/unit/test_m2_3_comparability_integration.py -k consumer_n_before_and_after` (o
oráculo de `git show` roda dentro do teste; a revisão independente reproduziu os mesmos
números com script próprio, `independent-v003-probes.py.txt`).

| Consumidor / distribuição | N antes (M2.2, sem M2.1) | N depois (v003) | Por quê |
|---|---:|---:|---|
| Entrada do ledger (`match_cohort` → `match_covariates` sobre elegíveis) | 31 | 30 | a de classe incompatível entrava (`match_cohort` nunca checou classe); M2.1 a exclui (`CLASS_MISMATCH`); as 4 de `tier_pieces` divergente já saíam no nível estrito |
| Referências quantitativas aceitas pelo ledger (`compare_damage`) | 17 | 16 | 31 e 30 menos as 14 sem coleta de dano completa = 17 e 16 |
| `gross_ability_dps:1` | 17 | 16 | idem (antes: distribuição sobre as aceitas do ledger) |
| `damage_events_per_second:1` | 17 | 16 | idem |
| `damage_per_event:1` | 17 | 16 | idem |
| `gross_damage_share_pct:1` | 17 | 16 | idem |
| `aura_uptime_fraction:1` | 31 | 34 | antes: as 31 do ledger; agora população própria — as 4 de `tier_pieces` ENTRAM (`tier_pieces` é NOT_ADMITTED, M2.2 §5.3) e a de classe sai |
| `player_casts_per_minute:1` | 31 | 34 | idem |

Exclusões de `gross_ability_dps:1` (35 entradas − 19 = 16 membros), de
`descriptive.exclusion_counts`, afirmadas no teste: **14**
`UNKNOWN_DAMAGE_COLLECTION` + **4** `TIER_PIECES_BAND_MISMATCH` + **1**
`BASIC_ELIGIBILITY_INELIGIBLE`. (Uma versão anterior deste documento dizia "16
referências sem coleta de dano completa" e marcava a coluna "antes" das seis
métricas como "não existia": ambos incorretos — os consumidores e suas
distribuições existiam, e a fixture tem 14, não 16.)

A linha do ledger reproduz num fixture controlado o mecanismo que o golden real
mostra no extremo (§9). As linhas de uptime/casts mostram por que AC1 existe: a
MESMA referência é excluída do ledger e admitida em duas métricas, cada consumidor
usando sua população declarada (SPEC §5), nunca uma emprestada.

## 9. Diferença do snapshot golden — explicada por completo (herdada, não reaberta)

`tests/golden/__snapshots__/test_new_pipeline_output.ambr` mudou desde a
baseline de M2.2 (108 linhas removidas, 2 adicionadas) — inalterado por esta
correção v003 e por v002; a mudança já existia desde a entrega original de
M2.3 e nenhuma versão da SPEC pediu revertê-la. Causa raiz confirmada
diretamente na cassette (`tests/fixtures/cassettes/dbd43e50441ac69f.json`,
resposta `GetReportRankings` do próprio jogador): `"partition": 2`. A
cassette `17c5cc4b6272308f.json` (`GetZonePartitions`) tem `default: true` na
partição 3. As duas respostas foram gravadas na mesma sessão de captura — não
é um artefato de teste, é o dado real mostrando um log de uma partição
anterior à corrente.

Antes de M2.3, `match_cohort` nunca verificava partição (T2.1 nunca teve esse
eixo); a comparação de execução (10 logs pareados, `core_abilities` com 8
entradas) rodava mesmo assim, contra uma coorte parcialmente incompatível.
M2.1 §5.3 ("log antigo... partição diferente") agora corretamente marca as 24
referências candidatas como `PARTITION_MISMATCH`, o ledger fica vazio, e
`core_abilities`/`performance`/`comparisons` ficam ausentes (§6.2, exatamente
os campos que §3/§7 desta rodada passaram a checar explicitamente). O
relatório passa a mostrar `NO_REFERENCES`/amostra insuficiente em vez de
comparações fabricadas contra uma coorte de partição incompatível.

Isso reduz a única evidência real end-to-end deste repositório que exercia
profundamente o conteúdo do relatório a um caso de população insuficiente —
uma consequência real, correta e prevista pela SPEC, registrada explicitamente
aqui porque nenhuma fixture real alternativa mais recente está disponível
neste workspace (sem acesso de rede para regravar cassettes, fora do escopo
desta unidade). Os testes que dependiam do conteúdo agora ausente foram
reescritos, na entrega original de M2.3, para verificar o novo comportamento
honesto — `test_zarad_fixture_exercises_eight_fundamental_abilities` e
`test_new_pipeline_differs_from_legacy_and_shows_missed_usage` em
`tests/golden/test_new_pipeline_output.py`, cada um com a causa documentada
no próprio docstring, confirmados passando nesta rodada sem qualquer edição.

## 10. Comandos, ambiente, Ruff, Pyright, suíte completa

Ambiente: Windows 11, `.venv` do repositório, Python conforme `pyproject.toml`,
os cinco placeholders públicos de CI para a suíte ampla:

```powershell
$env:DISCORD_TOKEN = "ci-placeholder-not-a-secret"
$env:WCL_CLIENT_ID = "ci-placeholder-not-a-secret"
$env:WCL_CLIENT_SECRET = "ci-placeholder-not-a-secret"
$env:BLIZZARD_CLIENT_ID = "ci-placeholder-not-a-secret"
$env:BLIZZARD_CLIENT_SECRET = "ci-placeholder-not-a-secret"
```

```powershell
.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q tests\unit\test_m2_3_comparability_integration.py
# 32 passed

.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q tests\unit\test_m2_3_comparability_integration.py tests\unit\test_cohort_match.py tests\unit\test_pipeline.py tests\unit\test_m1_persistence_contract.py tests\unit\test_m1_required_changes.py tests\unit\test_m2_2_metric_population.py tests\golden\test_new_pipeline_output.py
# 221 passed, 4 skipped

ruff check .
ruff format --check .
.venv\Scripts\python.exe -m pyright
# All checks passed / all files already formatted / 0 errors, 0 warnings, 0 informations

.venv\Scripts\python.exe -B -m pytest -o addopts="" -p no:cacheprovider -m "not network" -q
# 1 failed, 2722 passed, 47 skipped, 1 deselected, 337 warnings in 283.68s
```

A suíte ampla soma exatamente as 6 novas provas aos `passed` em relação à
baseline de v002 (2722 = 2716 + 6; 32 testes nesta unidade vs. 26 em v002;
nenhum teste existente fora deste arquivo mudou de resultado — §6). A única
falha é a mesma de baseline preexistente,
`tests/unit/test_logging_setup.py::test_no_raw_print_calls_anywhere_in_src`
(offender `src/botgitgud/orchestrator/__main__.py`), documentada desde M2.1,
fora do `write_paths` desta unidade e sem relação com D-M23-07.

## 11. Limitações residuais declaradas

- Uma observação (mesmo jogador no mesmo pull) com representações
  divergentes, inclusive só no percentil, é excluída por inteiro, junto com
  todo log do domínio que compartilhe um de seus `reference_id` (§4.0/v003).
  Isso pode reduzir N, e o fecho por id pode excluir um homônimo de outro
  servidor no mesmo pull, porque `reference_id` não distingue servidor —
  demonstrado em `test_quarantine_closes_exclusion_by_id_across_different_servers`.
  A causa da divergência na aquisição e a composição de `reference_id` não
  foram investigadas nem alteradas aqui (sem query, fetcher ou identidade
  nova). Chamadores diretos de `match_cohort` mantêm o comportamento
  anterior, dependente de ordem nesse caso.
- O ledger continua na política de matching v2 existente (incluindo o
  crescimento acima do piso); as seis métricas contratadas não — divergência
  declarada e resolvida por escopo (SPEC §6.1/D-M23-03).
- O gate de relevância de uptime (`presence`, T3.1) permanece medido sobre o
  ledger, não sobre a população M2.2 de `aura_uptime_fraction`.
- Populações ASPIRATIONAL por métrica (M2.2) não têm consumidor de produto até
  decisão posterior; ficam somente na proveniência persistida.
- Hotfix segue não verificado (herdado de M2.1); relaxar covariável não é
  ajustar por ela (herdado de M2.2); setup não é casado com a execução.
- As duas linhas de apresentação inventariadas em §7 são dívida de M5.2, não
  corrigidas aqui.
- O diff do golden (§9) é herdado da entrega original de M2.3 e permanece sem
  fixture real alternativa neste workspace.
- Interpretação de grades (B06), streams restantes (M3.1), oportunidades (M4)
  e ML (M6) permanecem inalterados.

Com o fechamento local desta unidade (v003), **M2.3 fecha o macro M2** (regra
de closure do workflow — M2.3 é a unidade explícita de integration/closure do
roadmap M2–M6), sujeito à revisão independente.
