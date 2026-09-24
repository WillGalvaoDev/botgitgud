# Revisão independente da M1 — REQUIRES_CHANGES

Autoridades: `docs/m1-specification.md` e `docs/m1-review-evidence.md`. Revisão da árvore de trabalho, incluindo alterações não commitadas; nenhuma confiança presumida na declaração de conclusão. Não foram alterados código, testes ou documentos do workspace. Os arquivos desta revisão estão somente na cópia temporária isolada. M2 não foi iniciada.

Árvore validada: 976 arquivos, SHA256 `e399a8c07d37b3a49c549dcfb582e2012582e2b6c0c97341a9b2b239f9b4435d`, igual ao declarado. A cópia isolada contém também os arquivos auxiliares versionados necessários à suíte (legacy/deploy/configuração de exemplo), fora do escopo desse fingerprint. Não contém credenciais reais nem Store de produção.

Reprodução dos achados: executar `independent_probes.py` com o Python do venv do workspace. O script importa a cópia revisada explicitamente, usa os construtores sintéticos existentes, cria seus bancos/Parquets em temporários e grava `independent-probes.json`. As asserções confirmam o comportamento defeituoso observado nesta árvore; não são correções nem testes adicionados ao projeto. A mutação de R10 existe somente em memória e é restaurada ao sair do contexto.

```powershell
& 'C:\Users\wsgon\OneDrive\Desktop\BotGITGUD\.venv\Scripts\python.exe' 'C:\Users\wsgon\AppData\Local\Temp\m1-independent-review-mp3r5oc5\independent_probes.py'
```

## Violações e correções exigidas

### R1 — Coaching aceita um cast inválido como prova de execução própria [P1]

Local: `analysis/dps_gap.py:218`, especialmente o teste de mera existência de `cast_timeline` em `_review_eligible`; consumidores `findings`, `remediation`, `materiality` e Discord.

Entrada: jogador com dano 100, 15 referências distintas com dano 200, T=300, identidade resolvida e origem PLAYER exclusiva. Substituir o único timestamp da timeline por -1, 301 ou NaN.

Resultado reproduzido em cada caso: `player_casts_per_minute:1` corretamente recebe INVALID, mas `review_eligible=True`, há um candidato material e o Discord recomenda “Revise a contribuição observada desta habilidade”. A recomendação sobrevive ao caminho real de construção de findings/remediações/candidatos.

Violação: §§4.2 e 7. Não há um cast válido do jogador no intervalo que prove o requisito de coaching. Dano válido não sana a evidência de cast inválida. A independência de streams não autoriza utilizar um valor inválido como prova positiva.

Correção exigida: validar a evidência de pelo menos um cast próprio no mesmo ID e intervalo antes de habilitar coaching, com abstenção propagada a todos os consumidores. Não bloquear automaticamente dano por ausência de cobertura de casts; bloquear a inferência que depende da evidência inválida. Acrescentar regressões até CLI/Discord para os três timestamps.

### R2 — Cursor ausente e eventos fora do intervalo viram COMPLETE [P1]

Locais: `ingest/performance_fetch.py:103`, `ingest/log_fetcher_aux.py:238` e `ingest/event_validation.py:14`.

Entrada mínima: `events={"data":[]}`, sem a chave `nextPageTimestamp`. Ambos os paginadores devolvem COMPLETE sem razão. O uso de `.get()` confunde campo ausente com cursor explicitamente nulo, embora não exista prova de término normal. Outro caso: solicitar [0,300000] ms e receber cast com timestamp 301000; `fetch_cast_timelines` devolve `{1:(301.0,)}` e COMPLETE. A validação de damage sequer exige timestamp.

Violação: §4.1/A14: estrutura válida e término normal precisam ser demonstrados; campo ausente não equivale a stream vazio completo. O intervalo solicitado é parte da proveniência.

Correção exigida: distinguir chave ausente de cursor nulo, validar os campos que sustentam o intervalo/stream e falhar com PARTIAL/UNKNOWN e razão quando não houver prova de completude. Preservar o caso positivo `data=[]` com cursor explicitamente nulo. Testar ambos os paginadores e o fetch integrado.

### R3 — Proveniência contraditória é aceita e permanece elegível após Parquet [P1]

Locais: `domain/models.py:35` e `analysis/measurement.py:108`.

Entrada: `CollectionProvenance(COMPLETE, (), 300000, 0)`, luta de 300s, `damage_reconciliation_status='unreconciled'` e residual declarado 999, com total numérico coincidente. A construção é aceita. Gravar e ler o Parquet preserva esses metadados e `account_damage` devolve AVAILABLE. Intervalos ausentes ou de apenas 1s para a luta de 300s também foram aceitos nas sondagens iniciais.

Violação: §§4.1 e 6: COMPLETE exige intervalo válido; metadados novos contraditórios não podem habilitar o caminho quantitativo. O status de reconciliação persistido é ignorado pelo consumidor. As fixtures de M1 também usam COMPLETE sem intervalo, não cobrindo esse contrato.

Correção exigida: validar intervalo, cobertura aplicável e coerência dos metadados na fronteira de leitura/medição; retornar abstenção explícita ou rejeitar entrada inválida. Manter separada a compatibilidade legítima do V1 histórico sem proveniência. Criar round-trips negativos reais.

### R4 — Foi relaxada a igualdade com a autoridade WCL [P2]

Local: `analysis/measurement.py:146`.

Entrada: G=1e12, S=0, autoridade `damage_table_total=1e12-1`. Resultado: AVAILABLE, residual=1 unidade de dano. O guard aceita o desvio porque a tolerância cresce com G.

Violação literal do §5: “Não relaxar a regra de igualdade com a autoridade WCL por conveniência em M1”. A tolerância das equações derivadas não substitui o guard de reconciliação exata anterior, que o fetcher ainda aplica com `==`.

Correção exigida: separar tolerância algébrica de elegibilidade contra a autoridade e manter a regra normativa também ao consumir dados persistidos. Testar o mesmo log antes/depois de serialização.

### R5 — Uptime ausente ou nulo é imputado como zero e recebe grade [P1]

Local: `ingest/performance_parsing.py:41`, alcançado por `fetch_buffs_and_debuffs`.

Entradas: `totalTime=300000` e aura `{"guid":1,"name":"Example"}`, ou a mesma aura com `totalUptime:null`. `parse_aura_uptimes` usa `a.get('totalUptime') or 0.0` e fabrica entrada 0.0. Contra 15 referências com uptime .8, ambas chegam a `MetricObservation(AVAILABLE,0.0)` e grade red, exatamente como o zero explicitamente medido.

Violação: §4.2/A24 exige entrada explícita válida e proíbe imputar zero à ausência. Isto não exige redesenhar a cobertura dos streams de auras, reservada para M3; exige preservar ausência do valor que a M1 já consome.

Correção exigida: distinguir valor ausente/nulo de zero explícito na ingestão e conservar indisponibilidade até comparação e coaching. Adicionar teste começando no payload WCL, pois os atuais começam em `PlayerLog.uptimes` já materializado.

### R6 — O caminho de features/core ignora a abstenção tipada [P1]

Locais: `analysis/feature_availability.py:100` e `analysis/pipeline.py:218`.

Entrada 1: log V1 histórico com `measurement_provenance=None` e timeline não vazia. `_report_ability_sections` retorna CAST_COUNT, CAST_TIMELINE e CASTS_PER_MINUTE disponíveis, com 1 cast e .2 casts/min.

Entrada 2: proveniência nova, timestamp NaN e uptime 2. O mesmo consumidor retorna as features disponíveis, timeline NaN e uptime=2. A observação tipada separada rejeita os mesmos valores. A CLI anuncia essas features como observáveis na seção por habilidade.

Violação: §§4.2, 6 e 8/A15/A20: cobertura histórica não deve ser inventada e disponibilidade exige validade. Criar uma observação correta em um módulo não basta se outro consumidor ativo continua publicando disponibilidade contraditória.

Correção exigida: fazer a disponibilidade e o relatório por habilidade respeitarem a mesma validação de valores/cobertura e de unidades; testar entradas históricas, PARTIAL, NaN e uptime fora de [0,1] no consumidor integrado.

### R7 — N e fontes ainda se misturam no cabeçalho público [P1]

Locais: `analysis/pipeline.py:661`, `:710`, `:715`; `report/text.py:141` e `:146`.

Reprodução integrada com o `run_analysis` real e I/O mockado: 15 referências V1 históricas, sem proveniência de casts, dão N quantitativo=15 e N posicional=0. O cabeçalho publica “Referência: 0 logs | DPS mediano: 1” e “Coorte: 0 logs”. A mediana veio dos 15 logs de dano. No mesmo caso, DPS WCL do jogador=999 e DPS medido=100/300; o cabeçalho apresenta 999 ao lado da mediana medida sem identificar essas fontes.

Outra sondagem integrada: 15 matched logs, 8 com damage PARTIAL, produz R=7 e `INSUFFICIENT_REFERENCES`, mas cabeçalho N=15 e mediana publicada. `Conclusion.sample.matched_n=15` enquanto seu scalar usa N=7.

Violação: §§4.2, 6 e 8/A18/A20. Não usar `num_positional` como N de DPS; fontes distintas devem ser explícitas; o bloqueio de publicação com N<8 não pode existir apenas na seção de decomposição.

Correção exigida: transportar N/população e fonte por estatística até header/conclusão/CLI/Discord, distinguir WCL de DPS medido e aplicar o piso público nos consumidores. Testar combinações N_damage=15/N_casts=0 e matched=15/R=7.

### R8 — Eventos de dano com amount zero perdem suas medidas válidas [P2]

Local: `analysis/metric_observations.py:74`.

Entrada: D=0, hits=5, T=300, coleta reconciliada COMPLETE, identidade resolvida. Tanto `damage_events_per_second` quanto `damage_per_event` viram NOT_APPLICABLE/None por causa do guard `ability.total<=0`, aplicado antes da seleção da métrica.

Pelo §4.2, os valores são respectivamente 5/300 eventos/s e 0 dano/evento. O denominador existe; não é o caso proibido de inventar p=0 quando hits=0. A exigência de dano positivo para grade de DPS por habilidade não deve apagar outras medidas válidas.

Correção exigida: aplicar elegibilidade de cada métrica separadamente, preservando os zeros explicitamente medidos e mantendo indisponível o grade de execução por DPS quando não houver seu mecanismo.

### R9 — AVAILABLE contém infinito produzido por normalização [P2]

Local: `analysis/measurement.py:114` e `:160`.

Entrada finita: dano=100, S=0, T=1e-308 e autoridade=100. Resultado: accounting AVAILABLE, gross_dps=net_dps=inf. A validação verifica os operandos, mas não os resultados derivados. O próximo produtor de `MetricObservation` lança ValueError em vez de devolver a abstenção coerente da análise.

Violação: §4.2/A16: AVAILABLE exige valor finito; duração positiva não garante uma divisão representável.

Correção exigida: validar finitude dos totais e quocientes derivados, bloquear com razão explícita sem clamping, divisão inválida ou crash de consumidor.

### R10 — Provas de fechamento usam tolerância relaxada; A07 aceita peso errado [P1, evidência]

Locais: `tests/unit/test_m1_algebra_properties.py:54`, `scripts/m1_review_evidence.py:200`, `tests/unit/test_m1_acceptance.py:86` e `analysis/dps_gap.py:366`.

O contrato exige `max(1e-9,1e-12*scale)`, com scale derivada dos termos comparados. Propriedade e gerador usam `max(1e-6,1e-9*...)`, isto é, tolerâncias ampliadas e outra escala. Não é suficiente que o guard global de produção use a fórmula correta: a prova independente deve verificar o oráculo e os fechamentos antes/depois do gate com a regra normativa.

Contraexemplo de não-vacuidade reproduzido: monkeypatch temporário multiplica cada termo de split válido por 15/8 no caso com 8 pares válidos e 7 inválidos. O teste `test_a07_split_keeps_original_denominator_and_closes` continua PASS. A produção calcula `unclassified=delta-classified`; a asserção de soma aceita a compensação, mesmo com pesos errados. A propriedade gera hits=0 em todos os casos, portanto não reforça o peso dos pares válidos.

Correção exigida: oráculo independente por par, asserções numéricas sobre cada componente e sobre o unclassified dos pares realmente sem split, pesos originais explícitos e tolerância normativa. Gerar misturas de pares com/sem split nas propriedades. Não atribuir residual excedente a unclassified para obter fechamento. Reexecutar e reconstruir as evidências após as correções.

### R11 — Adaptadores antigos continuam no contrato e na renderização ativa [P2]

Locais: `analysis/dps_gap.py:58`, `:403` e `report/dps_gap_text.py`.

Uma análise nova continua construindo `n_r`, `p_r`, `d_r`, `delta_d`, `volume`, `efficiency`, `interaction` e `cohort_share`. `volume`/`interaction` são multiplicados pela duração para preencher campos antigos e divididos novamente pela CLI. Não são objetos legados apenas decodificados de histórico; fazem parte do produtor e consumidor M1 atuais.

Violação: §4.3 proíbe autoridade paralela ambígua e exige que adapter indispensável seja declarado legado, somente leitura histórica e fora de novo cálculo/relatório. Corrigir a unidade desses campos resolveu apenas uma parte desse requisito. Não foi encontrado produtor ativo do diagnóstico por proxy de alvos; esse ponto é distinto da manutenção dos campos antigos.

Correção exigida: remover/renomear os campos ambíguos do contrato ativo, transportar componentes diretamente em DPS com denominadores explícitos e isolar eventual decodificação histórica por versão e somente leitura.

## Matriz de revisão A01–A25

“Cenário confirmado” significa que a entrada especificada foi verificada por teste/censo e inspeção; não é certificação universal contra os achados acima. Todos os 78 testes dos sete módulos M0/M1 foram reexecutados: 78 passaram. Os achados mostram por que essa contagem não encerra o marco.

| Critério | Resultado da revisão direta |
|---|---|
| A01 | Cenário confirmado: R=15, DPS igual, termos do par zero; a prova M0 verifica o helper explicitamente, além do gate vazio. |
| A02 | Cenário confirmado: média 68/300, delta 32/300, 32pp e 100*32/68. |
| A03 | Cenário confirmado: G-S=90000, DPS=300 e NO_REFERENCES, sem coorte zero. |
| A04 | Cenário confirmado: delta apenas na linha de suporte, com sinal correto. |
| A05 | Permutação de referências e conflito de identidades verificados; prova numérica geral precisa da tolerância normativa, R10. |
| A06 | Ausência de mecanismo e hits=0 não inventam ação/p; a generalização a eventos medidos com D=0 falha em R8. |
| A07 | Implementação do exemplo usa N original; prova de aceitação não detecta renormalização incorreta, R10. |
| A08 | Cenário pet sem cast preserva ledger e não recomenda apertá-lo; o gate completo de prova de cast próprio falha em R1 e o caminho de uptime contorna a exclus?o em R12. |
| A09 | Cenário de queda uniforme confirmado: scalars de DPS próprios, candidatos e texto sem promessa de recuperação. |
| A10 | Independência dos scalars de DPS/casts/uptime da habilidade intacta confirmada; share muda separadamente. |
| A11 | D04 preservado: casts q=2/15, sem ordinal derivado de share. |
| A12 | Proxy retirado do agregado/diagnóstico ativo; razão CAST_INSTANCE_LINK_UNAVAILABLE, sem valor inventado. |
| A13 | Feature aposentada ausente no builder/registro/ambas famílias do feature space; legado não vira coluna imputada. |
| A14 | REPROVADO: cursor ausente/evento fora do intervalo vira COMPLETE, R2; proveniência contraditória aceita, R3. |
| A15 | Compatibilidade de accounting histórico e leitura sem promoção testadas; disponibilidade ativa de casts ainda promove ausência de proveniência, R6. |
| A16 | Zeros, suporte, T inválido/NaN/inf cobertos nos testes existentes; overflow de operandos finitos falha em R9 e coerência de autoridade em R4. |
| A17 | Ledger/união de IDs/outras/suporte/sinais nos cenários executados conferem; tolerância da prova de fechamento precisa correção, R10. |
| A18 | Distribuições próprias N7 casts/N9 uptime/N15 DPS confirmadas no novo contrato; N do cabeçalho/conclusão diverge, R7. |
| A19 | Migração repetida, NULL histórico, versões e round-trips reais confirmados; round-trip de proveniência inválida revela falta de validação semântica, R3. |
| A20 | REPROVADO nos consumidores: coaching inválido, features contraditórias, cabeçalho/populações e adaptadores ativos, R1/R6/R7/R11/R12. |
| A21 | Replay dos 20 jogadores e reconciliação preservados nos testes/censo; pets e suporte sem dupla contagem nos cenários fornecidos. |
| A22 | Média aritmética responde à cauda extrema; nenhuma troca silenciosa por mediana encontrada no comparador. |
| A23 | Objetos principal/aspiracional têm IDs, N, exclusões, média e fechamento separados; cenário de aspiracional inválido coberto. |
| A24 | REPROVADO da ingestão ao scalar: ausência/nulo vira zero red, R5; zero explícito permanece corretamente zero. |
| A25 | Mudança de valor/schema altera hash; ordem normal de linhas/chaves não; não finitos rejeitados; avaliação usa v2, campanha permanece sae3-v1. |

## Integridade e consistência das evidências

Os cinco hashes registrados na auditoria final conferem byte a byte: especificação, JUnit, log, exit JSON e gzip de evidências. Raw atual: 1361 Parquets, hash `983b0a3ed9294925e70c60da62c871da87d629261d3d9c4568e67c25cd426926`. Catálogo operacional: `9c9fb070beb3b4919f624615e4ec6ef37ac25742411190f63fe50ae69a294d5f`.

O gerador foi reexecutado na cópia. Todos os campos estruturados coincidem com o artefato entregue, exceto o timestamp de geração: censo, exemplos D01–D06, comparações, N/exclusões, elegibilidade, fingerprints e resíduos. Confirmados 50 comparações internas, 47 publicáveis, 672 pulls e 869 identidades; isso não torna os 1361 arquivos independentes. Os resíduos reproduzidos são os documentados; a crítica R10 é à tolerância/prova utilizada, não à invenção desses números.

Entretanto, “PASS em A01–A25” e a alegação de consumidores integralmente migrados não são sustentáveis diante de R1–R11. O fingerprint comprova a identidade da árvore testada, não a conformidade semântica. As correções devem atualizar a matriz e regenerar os artefatos, sem manter o resultado global PASS por contagem de testes.

A divergência do snapshot histórico `2436bb...` é declarada honestamente e permanece sem prova de equivalência. Não se infere mutação histórica por esta revisão; também não se transforma preservação no intervalo atual em prova de preservação histórica. A exigência de evidência histórica do §11.5 continua com esse limite explicitamente aberto.

## Validação executada pelo revisor

- Ruff: `ruff check src tests scripts/m1_review_evidence.py` passou.
- Pyright: zero erros e zero warnings de análise; apenas aviso da ferramenta sobre versão mais recente.
- Seleção M0/M1: 78 passed, em 12,29s; `docs/independent-focus.xml`.
- Onze grupos de reprodução: asserções confirmadas pelo script externo; `independent-probes.json`.
- Gerador de evidências/censo: executado, conteúdo reproduzido conforme comparação acima.
- Suíte ampla: resultado final será registrado em `independent-validation.md`. A primeira tentativa não concluiu: faltavam arquivos auxiliares na cópia inicial (legacy/deploy, corrigido somente na cópia), e houve access violation nativa em PyArrow durante `test_parquet_codec`. Não se atribui essa falha nativa à M1 nem se a descarta como preexistente sem baseline. A repetição usa a cópia completa dos arquivos auxiliares.

Nenhum teste network foi solicitado. Nenhum backfill, treino de campanha real, regravação de raw do workspace ou migração do Store de produção foi executado. A suíte ampla contém os testes sintéticos de modelos já existentes no projeto.

REQUIRES_CHANGES. Corrigir os contratos e consumidores apontados, reforçar as provas, atualizar as evidências e submeter novamente à revisão independente. M1 não está fechada. M2 não foi iniciada.
