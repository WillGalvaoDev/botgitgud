# Relatório de Auditoria — BotGITGUD

**Data:** 2026-08-17
**Escopo analisado:** `bot.py` (761 linhas), `spells.json` (441 linhas), `.env`
**Objetivo declarado do projeto:** analisar o log de um personagem, compará-lo com os melhores logs daquela luta com tempo de kill parecido, e gerar um relatório completo do que o jogador deve focar para melhorar o parse.

> Nenhum código foi alterado. Este documento é apenas diagnóstico + plano.

---

## 1. Sumário executivo

**Veredito: o projeto está em ~25–30% do objetivo declarado.**

A ideia central está certa e é boa — *comparar contra uma coorte de kills com duração equivalente* é exatamente a decisão metodológica correta, e é algo que a maioria das ferramentas públicas (incluindo o próprio WCL) **não** faz. O tempo de kill é o principal confundidor em análise de DPS: kills mais curtos inflam parse por causa de burst inicial e menos janelas de downtime. Filtrar por isso demonstra intuição analítica real.

O problema é que, a partir dessa boa premissa, o pipeline atual:

1. **Mede a coisa errada.** Analisa apenas *timings de cast*, que explicam talvez 20–30% da variância de parse. Ignora active time, uptime de buff/debuff, overcap de recurso, dano por habilidade, mortes/downtime, talentos e item level — variáveis que tipicamente dominam a diferença entre um parse 60 e um parse 95.
2. **Compara de forma estatisticamente inválida.** O algoritmo de matching (`min(possible_refs, key=|x - u_time|)`) sempre escolhe a referência *mais próxima* de cada cast do jogador, o que enviesa sistematicamente o resultado para "verde". Um jogador que usou 3 cooldowns onde a referência usou 8 sai do relatório com nota perfeita.
3. **Não quantifica impacto.** O relatório diz "você atrasou 12s", mas nunca diz "isso custou ~X DPS / Y percentis". Sem isso, não há priorização, e sem priorização não é coaching — é um dump de dados.
4. **Não é reprodutível.** O `spells.json` é mutado a cada execução (inclusive de threads concorrentes, sem lock), e a classificação `non-trackable` é gravada **globalmente**, então a mesma análise rodada duas vezes produz resultados diferentes.

O que existe hoje é um **protótipo funcional de um subcomponente** (comparador de timing de CDs) empacotado como bot de Discord. A distância até "ferramenta profissional de análise de logs" é grande, mas o caminho é claro e está mapeado na Seção 6.

### Matriz de maturidade

| Dimensão | Situação atual | Nota |
|---|---|---|
| Seleção de coorte (peer group) | Só duração ±30s; sem talentos, ilvl, dificuldade, patch, comp | 4/10 |
| Extração de dados | Funciona; casts paginados corretamente | 6/10 |
| Método de comparação | Nearest-neighbor enviesado, slots desalinhados | 2/10 |
| Cobertura das métricas que determinam parse | Só casts (~20–30% da variância) | 2/10 |
| Quantificação de impacto (DPS/parse ganho) | Inexistente | 0/10 |
| Priorização / acionabilidade | Nenhuma; lista tudo em ordem de "presença" | 2/10 |
| Tratamento de incerteza | `stdev` é calculado e **descartado**; thresholds fixos | 1/10 |
| Reprodutibilidade | Estado mutável global, sem versionamento | 1/10 |
| Engenharia (testes, tipos, deps, CI) | Ausente por completo | 1/10 |
| Escalabilidade / custo de API | ~100+ requisições por comando, sem cache | 2/10 |
| Segurança / higiene de credenciais | `.env` em pasta sincronizada, sem `.gitignore` | 3/10 |
| Apresentação do relatório | Texto em code block, split cego em 1900 chars | 3/10 |

---

## 2. O que o projeto faz hoje (pipeline atual)

```
!analisar <char> <link WCL>
  │
  ├─ parse_report_input()          → extrai reportCode + fightID por regex
  ├─ get_wcl_token()               → OAuth client_credentials
  │
  ├─ fetch_player_timeline_data()  → 1 query meta (fights + Summary + Casts table)
  │                                  → identifica playerID, class, spec por NOME
  │                                  → loop paginado de events(dataType: Casts)
  │                                  → timeline_by_id: {spellID: [t0, t1, ...]}
  │
  ├─ fetch_top_logs_for_cds()      → characterRankings(metric: dps), até 5 páginas
  │                                  → filtra |duração_ref − duração_user| ≤ 30s
  │                                  → ThreadPoolExecutor(5) refaz fetch_player_timeline
  │                                    para até 100 logs de referência
  │
  ├─ build_cd_reference_profile()  → agrupa por "slot" (índice do N-ésimo cast)
  │                                  → mediana + stdev por slot
  │                                  → avg_cd_duration = média dos intervalos entre medianas
  │                                  → type = MAJOR se avg_cd ≥ 120s senão MINOR
  │
  ├─ discover_clean_major_cds()    → mantém spells com presença ≥ 70% e avg_cd ≥ 18s
  │                                  → blacklist por substring ("potion", "trinket", ...)
  │                                  → grava "non-trackable" de volta no spells.json
  │
  ├─ compare_major_cds_clean()     → para cada cast do user, acha a referência MAIS PRÓXIMA
  │                                  → delta; 🟢 ≤10s, 🟡 ≤25s, 🔴 >25s
  │
  └─ generate_coach_report_string() → texto ASCII, enviado em chunks de 1900 chars
```

Componentes auxiliares: resolver híbrido de nomes de spell (cache em memória → `spells.json` → API da Blizzard), com escrita de volta no JSON.

---

## 3. Falhas metodológicas (as que invalidam as conclusões)

Ordenadas por gravidade. Estas são as que fazem o relatório **dar conselhos errados**, não apenas incompletos.

### 3.1 🔴 Nearest-neighbor matching enviesa tudo para "verde" — `bot.py:570-576`

```python
ref_time = min(possible_refs, key=lambda x: abs(x - u_time))
delta = round(u_time - ref_time, 1)
```

Para cada cast do jogador, o código varre **toda** a lista de tempos de referência e escolhe o mais próximo. Consequências:

- **Viés otimista sistemático.** O delta é, por construção, o menor delta possível. Nunca mede "você perdeu um uso" — mede "você esteve perto de *algum* uso".
- **Mapeamento não-injetivo.** Vários casts do jogador podem casar com a mesma referência, e referências inteiras podem ficar órfãs. Um jogador que usou Metamorphosis 2 vezes onde o top-100 usa 4 vezes recebe 🟢🟢 e **zero menção às 2 utilizações perdidas** — que é exatamente o erro mais caro que existe.
- **Casts em excesso são invisíveis.** O ramo `excedente` (`bot.py:594-606`) marca com ⚪ e `delta = 0.0`, ou seja, um uso a mais nunca é avaliado. Mas o loop só entra ali quando `idx >= len(possible_refs)`, e como `possible_refs` é a lista de *medianas de slot*, o critério é frágil.

**O problema correto** é um *alinhamento monotônico de sequências*, não vizinho mais próximo. Ferramentas: DTW com restrição de monotonicidade, ou atribuição ótima 1-D (Hungarian / transporte ótimo na reta) com custo de "não-atribuição" explícito — assim casts faltantes e sobrando aparecem como custo, que é o que você quer reportar.

### 3.2 🔴 A agregação por "slot" cria uma rotação-fantasma — `bot.py:426-434`

```python
for idx, t in enumerate(times):
    spell_accumulator[s_id]["slots_timings"][idx].append(t)
```

O N-ésimo cast do jogador A é comparado com o N-ésimo cast do jogador B. Se A tiver **um** cast extra no início (um pre-pot, um uso em add spawn), todos os slots subsequentes de A ficam deslocados em 1 e "contaminam" as medianas de todos os slots seguintes.

Pior: a mediana por slot é calculada **independentemente por slot**. O vetor resultante `[mediana_slot0, mediana_slot1, ...]` é um perfil que **nenhum jogador real executou**. É um artefato de agregação — pode inclusive ser fisicamente impossível (intervalos menores que o cooldown real da habilidade).

**Correto:** ancorar o tempo em eventos do encontro (transições de fase, spawn de add, casts do boss — o WCL expõe tudo isso), não no índice do cast. Depois normalizar por progresso da luta ou alinhar via DTW nos marcadores de fase.

### 3.3 🔴 `avg_cd_duration` não é o cooldown da habilidade — `bot.py:465-474`

```python
if len(all_slot_medians) > 1:
    intervals = [...]
    avg_cd_duration = statistics.mean(intervals)
else:
    avg_cd_duration = all_slot_medians[0] if all_slot_medians else 0
```

Dois problemas:

1. Para uma habilidade usada **uma única vez**, `avg_cd_duration` recebe o *instante do primeiro cast*. Uma poção usada aos 200s é classificada como cooldown de 200s → `MAJOR`. Uma habilidade real de 3 minutos usada uma vez aos 10s é classificada como `MINOR` e cai fora do filtro `avg_cd >= 18.0` (`bot.py:529`). Isso é um bug direto, não uma aproximação.
2. Mesmo com múltiplos usos, a média de intervalos entre medianas mede *cadência observada agregada*, não cooldown. Cooldown real varia com haste, redução por talento e reset por proc.

**Correto:** o cooldown base é um dado, não uma inferência — vem da API de spell da Blizzard, do SimulationCraft, ou de uma tabela mantida por spec. A cadência observada é uma métrica separada e útil (mede drift/atraso acumulado), mas não deve ser usada para *classificar* a habilidade.

### 3.4 🔴 Coorte de referência mal especificada

`fetch_top_logs_for_cds` (`bot.py:318-325`) consulta:

```graphql
characterRankings(className:, specName:, metric: dps, page:)
```

Faltam parâmetros que **mudam completamente** a validade da comparação:

- `difficulty` — não especificado, usa o default. Comparar um kill Heroic com referências Mythic é comparar mecânicas diferentes.
- `partition` — não especificado. Rankings de partitions antigas (patches anteriores) têm tier sets e balanceamento diferentes.
- `bracket` (item level) — ausente. A diferença de dano entre ilvl 620 e 660 é enorme e não é corrigível por rotação.
- **Talentos** — ausente. Builds diferentes têm rotações estruturalmente diferentes. Comparar timing de CD entre builds distintas gera conselhos ativamente errados.
- **Composição de raid** — buffs externos (Power Infusion, Innervate, lust timing) deslocam os CDs ótimos.
- `metric: dps` é **hardcoded**, mas `fetch_player_timeline_data` (`bot.py:221`) aceita jogadores do grupo `healers` e `tanks`. Analisar um healer produz uma coorte de referência sem sentido.

### 3.5 🔴 Viés de sobrevivência: o top-100 é a cauda extrema

O grupo de referência é, por construção, o percentil 99+. Esses logs tiveram RNG favorável: streaks de crit, posicionamento do boss, spawn de add conveniente, lust no momento certo. **Copiar os timings deles é, em parte, ajustar a ruído.**

A pergunta estatisticamente honesta não é "o que os melhores fizeram", e sim "**qual parte da diferença é controlável pelo jogador**". Isso exige separar variância controlável (rotação, uptime, active time) de variância não-controlável (RNG, comp, comportamento do boss). Um alvo mais defensável é a mediana do **decil superior** com incerteza reportada, não o top-100 absoluto.

### 3.6 🟠 Thresholds absolutos e hardcoded — `bot.py:579-584`

```python
if abs_d <= 10.0:   status = "🟢"
elif abs_d <= 25.0: status = "🟡"
else:               status = "🔴"
```

10 segundos de atraso em um cooldown de 30s é catastrófico (33% do CD perdido). 10 segundos em um cooldown de 3 minutos é irrelevante. O mesmo threshold para os dois casos garante falsos positivos em CDs longos e falsos negativos em CDs curtos.

Agravante: o código **calcula** `s_std` (`bot.py:455`) e o armazena no perfil (`bot.py:458`) — e **nunca o usa**. A infraestrutura para uma nota normalizada (z-score, ou melhor, quantil empírico não-paramétrico da distribuição de referência) já existe e está sendo descartada.

### 3.7 🟠 Comparações múltiplas sem controle

Um relatório típico avalia ~20 habilidades × ~6 usos = ~120 comparações. Com threshold de 10s e ruído natural, **dezenas de bandeiras 🟡/🔴 aparecem por puro acaso**. O relatório vira ruído e o jogador aprende a ignorá-lo.

Solução: controle de FDR (Benjamini-Hochberg) ou, mais pragmático para o domínio, **gating por tamanho de efeito** — só reportar desvios cujo impacto estimado em DPS ultrapasse um piso (ex.: 0,5% de DPS total).

### 3.8 🟠 Estado global mutável destrói reprodutibilidade — `bot.py:497-541`

`discover_clean_major_cds` grava `category: "non-trackable"` de volta no `spells.json` **de forma global e permanente**. Mas a decisão foi tomada no contexto de *uma spec, um boss, uma coorte*. Consequências:

- Uma habilidade que é lixo para Havoc DH mas core para Frost Mage é permanentemente descartada para ambos.
- A mesma análise rodada duas vezes produz saídas diferentes (a segunda tem menos habilidades).
- Não há como auditar por que uma habilidade sumiu do relatório.

Isso é o oposto de um pipeline analítico reprodutível. A categorização é uma propriedade de **(spell, spec, contexto)**, não de spell.

### 3.9 🟠 Filtro de duração absoluto, não relativo — `bot.py:358`

`±30s` em um kill de 4 minutos é ±12,5%; em um kill de 10 minutos é ±5%. O critério de "kill comparável" muda de significado conforme o encontro. Deveria ser relativo (ex.: ±7%) ou, melhor ainda, o tempo deveria ser normalizado por progresso da luta para permitir comparação entre durações diferentes.

### 3.10 🟠 Dados fabricados como default — `bot.py:361`

```python
p_val = r.get("percentile", 99.0)
```

Quando o percentil está ausente, inventa-se 99. Isso contamina `avg_p`, `min_p`, `max_p`, que aparecem no cabeçalho do relatório como se fossem medidos. Valores ausentes devem ser `None` e propagados como ausentes, nunca substituídos por uma constante otimista.

### 3.11 🟠 O relatório nunca mostra o parse do jogador

O cabeçalho exibe "Parse méd" **da coorte de referência** (`bot.py:636`), mas nunca o parse/DPS do próprio jogador. O objetivo declarado é "ter um parse melhor" e a ferramenta não informa nem o ponto de partida nem o gap. Além disso, `parses` é retornado por `fetch_top_logs_for_cds`, propagado por três funções, e **nunca usado**.

### 3.12 🟡 Heurísticas por substring em inglês — `bot.py:523`

```python
if any(x in name_lower for x in ["potion", "healthstone", "gladiator", "ring", "trinket"]):
```

`"ring"` casa com *Ring of Peace*, *Ringing Clarity*, *Bringer* (substring!), etc. Filtro léxico não substitui metadado. E como o resultado é persistido (3.8), um falso positivo é permanente.

### 3.13 🟡 Identificação de jogador por nome — `bot.py:223`

`p.get("name","").lower() == char_name.lower()` — nomes se repetem entre realms. Os rankings já retornam identificadores canônicos de personagem; usá-los elimina a ambiguidade.

---

## 4. Falhas de engenharia

### 4.1 🔴 Race condition corrompe o `spells.json`

`fetch_player_timeline_data` chama `save_spell_to_local_db` (`bot.py:241`), e essa função roda dentro de um `ThreadPoolExecutor(max_workers=5)` (`bot.py:390-391`). Ela:

- muta o dicionário global `LOCAL_SPELL_DB` sem lock,
- e escreve o arquivo inteiro com `open(w)` + `json.dump` — **não atômico**.

Cinco threads escrevendo o mesmo arquivo simultaneamente pode produzir JSON truncado/inválido. Na próxima inicialização, o `except` em `bot.py:45-48` engole o erro e o banco de spells volta a **vazio**, silenciosamente.

Mitigação mínima: `threading.Lock` + escrita atômica (arquivo temporário + `os.replace`).

### 4.2 🔴 Custo de API insustentável, sem cache

Cada `!analisar` executa, no pior caso:
- 1–5 queries de rankings,
- **até 100 × (1 query meta + N queries de eventos paginadas)**.

Isso são facilmente 300–600 requisições GraphQL por comando, sem nenhum cache. A API do Warcraft Logs opera com cota de pontos por hora — dois ou três comandos consecutivos esgotam a cota. E o usuário espera minutos por uma resposta.

**Esta é a maior falha arquitetural.** O perfil de referência de uma coorte `(encounter, spec, difficulty, bucket de duração, partition)` é *idêntico* para todos os usuários e muda lentamente. Ele deve ser **pré-computado em batch** e armazenado. O caminho interativo deveria ser: 1 fetch do log do usuário + 1 lookup de perfil = resposta em segundos.

### 4.3 🔴 Sem tratamento de rate limit, retry ou timeout

- Nenhuma das chamadas ao WCL (`bot.py:203`, `260`, `336`) tem `timeout`. Uma conexão pendurada trava um worker do pool indefinidamente.
- Nenhum tratamento de HTTP 429 / `Retry-After`.
- Nenhum backoff exponencial. Falhas transitórias viram falha total da análise.
- `except Exception` largo com `print` (`bot.py:304`, `407`) engole a causa raiz.

### 4.4 🟠 Tokens sem controle de expiração

`BLIZZARD_TOKEN_CACHE` (`bot.py:26`) é cacheado para sempre. Tokens da Blizzard expiram em 24h. Um bot rodando continuamente começa a receber 401 e nunca reautentica — e o `fetch_spell_from_blizzard` retorna `None` silenciosamente, degradando a resolução de nomes sem nenhum alerta.

### 4.5 🟠 Sem manifest de dependências, sem git, sem testes

- Não há `requirements.txt` nem `pyproject.toml`. O projeto não é instalável nem reproduzível em outra máquina.
- **Não é um repositório git.** Não há histórico, nem branches, nem possibilidade de reverter.
- Zero testes. Nenhuma das funções estatísticas (`build_cd_reference_profile`, `compare_major_cds_clean`) tem verificação de comportamento — e são exatamente as que contêm os bugs da Seção 3.
- Sem type hints, sem `mypy`/`pyright`, sem linter.

### 4.6 🟠 Higiene de credenciais

O `.env` contém segredos de três serviços (WCL, Blizzard, Discord) e está numa pasta **sincronizada com OneDrive**, sem `.gitignore`. No momento em que o projeto virar um repositório, o risco de commit acidental é imediato. Recomendo: criar `.gitignore` com `.env` **antes** do `git init`, e rotacionar as três chaves se a pasta já foi compartilhada em algum momento.

### 4.7 🟠 Arquitetura monolítica

761 linhas em um arquivo, sem separação entre cliente de API, modelo de domínio, análise e apresentação. Assinaturas como:

```python
return reference_players, len(reference_players), min_d, max_d, avg_p, parses, min_p, max_p
```

Tuplas de 8–9 posições (`bot.py:406`, `718`, `734`) são impossíveis de manter e já contêm inconsistência: o caminho de erro retorna `0` onde o de sucesso retorna `None` (`bot.py:719-727`).

### 4.8 🟡 Entrega do relatório

- Split cego a cada 1900 caracteres (`bot.py:751`) pode cortar no meio de uma linha e quebrar o bloco markdown.
- Texto ASCII em code block não escala: um relatório completo (com uptimes, dano por habilidade, atribuição) seria ilegível nesse formato.
- Não há feedback de progresso além de uma mensagem inicial, apesar de a análise levar minutos.
- Um único `ThreadPoolExecutor` default serializa usuários concorrentes.

### 4.9 🟡 `DEBUG = True` hardcoded com dumps massivos

`bot.py:449` e `463` imprimem **as amostras brutas de cada slot de cada habilidade** de 100 logs. São milhares de linhas por análise no stdout. Logging estruturado com níveis substituiria isso.

---

## 5. O que o projeto acerta

Vale registrar, porque são decisões que eu manteria:

1. **Matching por duração de kill.** A premissa central é correta e diferenciada. É a base de uma metodologia de coorte válida — só precisa de mais covariáveis.
2. **Mediana em vez de média** (`bot.py:453-454`). O comentário no código mostra que a decisão foi consciente, pelo motivo certo (robustez a outliers).
3. **Resolver híbrido de spells com cache em camadas** (memória → JSON local → API). Pragmático e reduz chamadas externas.
4. **Paginação correta** de eventos via `nextPageTimestamp`, com guarda contra loop infinito (`bot.py:287`).
5. **Concorrência** no fetch das referências — a intuição de que I/O é o gargalo está certa.
6. **Filtro de presença ≥ 70%** para separar habilidades de rotação de ruído — heurística simples e razoável para descobrir automaticamente o que importa por spec, sem hardcode por classe.
7. **Descoberta data-driven das habilidades relevantes**, em vez de listas manuais por spec. É a abordagem certa e escala para novas classes sem manutenção.

---

## 6. O que eu mudaria para transformar isso em ferramenta profissional

Organizado nas mesmas camadas que times de analytics de e-sports (e de esportes tradicionais) usam hoje.

### 6.1 Arquitetura alvo

Separar em quatro camadas com fronteiras explícitas, substituindo o monólito:

```
┌─────────────────────────────────────────────────────────────┐
│ INGESTÃO      wcl_client/  (GraphQL tipado, retry, backoff, │
│               orçamento de pontos, cache HTTP)              │
├─────────────────────────────────────────────────────────────┤
│ ARMAZENAMENTO Parquet particionado + DuckDB                 │
│               eventos brutos · logs normalizados · coortes  │
├─────────────────────────────────────────────────────────────┤
│ FEATURES      feature store: uptimes, active time, damage/  │
│               ability, alinhamento de CD, waste de recurso  │
├─────────────────────────────────────────────────────────────┤
│ ANÁLISE       matching de coorte · alinhamento de sequência │
│               · decomposição de gap · modelo + SHAP         │
├─────────────────────────────────────────────────────────────┤
│ SERVING       relatório web + Discord (resumo + link)       │
└─────────────────────────────────────────────────────────────┘
```

**Batch vs. interativo é a divisão mais importante.** Perfis de coorte são pré-computados por um job agendado; o comando do usuário faz 1 fetch + 1 lookup. Isso transforma minutos em segundos e reduz o consumo de API em duas ordens de grandeza.

### 6.2 Camada de dados

**Recomendação concreta: DuckDB + Parquet.** Para um projeto pessoal, é a escolha certa — zero infraestrutura, um arquivo, mas consulta dezenas de milhões de eventos em SQL com performance de warehouse. É literalmente o padrão atual para analytics local.

Esquema mínimo:

```
raw_events/     partition=(encounter_id, difficulty, patch)/report_code.parquet
                → timestamp, source_id, type, ability_id, amount, target_id, ...
logs/           → report_code, fight_id, player, spec, ilvl, talent_hash,
                  duration_ms, dps, percentile, difficulty, partition, comp_hash
cohorts/        → cohort_id (hash das covariáveis), membros, data de construção
profiles/       → cohort_id, feature, quantis (p10/p25/p50/p75/p90), n
```

Princípios:
- **Imutabilidade dos dados brutos.** O `spells.json` mutável é substituído por uma tabela de dimensão versionada. Nada de reescrever dados de entrada durante a análise.
- **Content-addressing / hash de coorte.** Cada relatório carrega o hash da coorte usada, permitindo reproduzir a análise exata meses depois.
- **Manifest de execução.** Cada relatório gravado com: versão do código, patch do jogo, partition, filtros, n da coorte, timestamp. Sem isso não há auditoria.

### 6.3 Metodologia estatística

Esta é a mudança de maior impacto sobre a qualidade das conclusões.

**a) Construção de coorte por matching multivariado.**
Substituir o filtro `|Δduração| ≤ 30s` por matching em covariáveis, exatamente como se faz em inferência causal observacional:

| Covariável | Por quê |
|---|---|
| Duração do kill (relativa, ±5–8%) | Confundidor principal |
| Item level | Não é controlável pelo jogador — precisa ser neutralizado |
| Hash de talentos / cluster de build | Builds diferentes = rotações diferentes |
| Dificuldade + partition | Mecânicas e balanceamento diferentes |
| Peças de tier set | Muda a rotação inteira em alguns casos |
| Buffs externos recebidos | Power Infusion etc. deslocam CDs ótimos |
| Timing do Bloodlust | Ancora a janela de burst |

Implementação: propensity score ou nearest-neighbor multivariado com caliper. Reportar sempre o **n da coorte** e recusar-se a gerar relatório abaixo de um mínimo (ex.: n ≥ 30) — hoje o código aceita `n = 1` sem avisar.

**b) Clustering de build como achado próprio.**
Antes de comparar timings, clusterizar as referências por hash de talentos. Se o jogador está numa build minoritária entre os top parses, **isso sozinho costuma ser o maior ganho de parse disponível** e deve ser o primeiro item do relatório — muito antes de qualquer delta de 8 segundos.

**c) Normalização temporal por fase.**
Substituir tempo absoluto por: (i) fração de progresso da luta, ou (ii) tempo relativo à transição de fase mais próxima. O WCL expõe fases e casts do boss. Isso resolve 3.2 e permite ampliar a coorte para durações mais distantes.

**d) Alinhamento de sequência monotônico.**
Substituir o nearest-neighbor por DTW restrito ou atribuição ótima com custo de não-atribuição. A saída passa a distinguir três categorias que hoje são indistinguíveis:
- cast atrasado/adiantado (com magnitude),
- **cast perdido** (referência sem correspondente) ← hoje invisível, e é o erro mais caro,
- cast extra (jogador sem correspondente).

**e) Grading por quantil empírico, não por threshold fixo.**
Em vez de "±10s = verde", usar a posição do jogador na distribuição de referência daquela habilidade específica: "seu 3º uso aos 96s está no percentil 12 da coorte (IQR 78–86s)". Isso adapta automaticamente a escala por habilidade e resolve 3.6.

**f) Intervalos de confiança por bootstrap.**
Toda mediana de referência deve vir com IC. Com n=8 logs, a mediana de um slot tem incerteza enorme e não justifica um 🔴. Reportar incerteza é o que separa análise de opinião.

**g) Gating por tamanho de efeito + controle de FDR.**
Só reportar um achado se o impacto estimado exceder um piso material. Resolve 3.7 e transforma um dump de 120 linhas em 3–5 recomendações.

### 6.4 Quantificação de impacto — a peça que falta inteira

Sem isso, a ferramenta não cumpre o objetivo. Três níveis, em ordem de esforço:

**Nível 1 — Decomposição do gap de dano (barato, alto retorno).**
Puxar a tabela `DamageDone` do jogador e da coorte, e decompor a diferença de DPS total por habilidade, no estilo Oaxaca-Blinder:

```
Δdano_habilidade = (Δnº_casts × dano_médio_por_cast_ref)      ← erro de rotação/uso
                 + (Δdano_por_cast × nº_casts_jogador)         ← erro de janela/buff/gear
                 + termo de interação
```

Isso responde diretamente "**onde foi meu DPS**" e é implementável em dias, não meses. Saída típica: *"Você está 8,4% abaixo da mediana da coorte. 5,1pp vêm de Chaos Strike (−0,9% dano/cast → você não está sob buff durante a janela) e 2,2pp de 2 usos perdidos de Eye Beam."*

**Nível 2 — Modelo preditivo + SHAP.**
Treinar um gradient boosting (LightGBM) prevendo `percentil ~ features` sobre milhares de logs da mesma spec/encontro. Features: active time, uptimes de buff/debuff, contagem de casts por CD, score de alinhamento, waste de recurso, ilvl, tier, mortes.

Usar **SHAP para atribuição por log**: para *este* log específico, quais features empurraram o parse para baixo e em quantos percentis. Isso é hoje o padrão da indústria em analytics esportivo para explicar performance individual — é exatamente a mesma classe de problema.

Cuidado necessário: SHAP explica o *modelo*, não causalidade. Feature com alta correlação ≠ alavanca acionável. Separar explicitamente features controláveis (rotação, uptime) de não-controláveis (ilvl, comp) e só recomendar as primeiras.

**Nível 3 — Contrafactual por simulação (ground truth do domínio).**
Integrar SimulationCraft: importar gear/talentos do jogador, simular com a APL ótima, comparar com o dano real. O gap "sim vs. actual" é a métrica que a comunidade competitiva de WoW já reconhece como referência. Ir além: re-simular com a APL do jogador reconstruída do log, para isolar quanto do gap é rotação vs. gear vs. RNG.

### 6.5 Ampliar o que é medido

Só casts é insuficiente. Adicionar, em ordem aproximada de poder explicativo:

| Métrica | Fonte WCL | Por que importa |
|---|---|---|
| **Active time / GCD efficiency** | tabela Summary | Tipicamente o preditor #1 de parse baixo |
| **Uptime de buff/debuff** | Buffs / Debuffs tables | DoTs caídos e buffs dropados custam mais que timing de CD |
| **Dano por habilidade** | DamageDone table | Base da decomposição de gap (6.4) |
| **Overcap / waste de recurso** | `resourcechange` events | Fury/Energy/Mana capado = dano perdido direto |
| **Mortes e downtime** | Deaths / damage taken | Morrer é o maior destruidor de parse |
| **Damage taken evitável** | tabela DamageTaken | Correlaciona com downtime |
| **Contagem de alvos / cleave** | eventos de dano | Explica variância enorme em encontros com adds |
| **Interrupts / dispels** | Casts | Custo de oportunidade em DPS |
| **Timing relativo ao Bloodlust** | Buffs | O alinhamento que mais importa na prática |

Um relatório profissional abre com **active time e uptimes**, não com deltas de cooldown.

### 6.6 Produto e apresentação

- **Priorizar impiedosamente.** O relatório deve abrir com "Top 3 ações, ganho estimado" e esconder o resto atrás de detalhamento. Um dump de 20 habilidades não é coaching.
- **Relatório web em vez de texto no Discord.** Gerar uma página HTML (gráficos de timeline com bandas de referência, waterfall de decomposição de DPS) e o Discord posta o resumo + link. Elimina o problema do split em 1900 chars e permite visualização de séries temporais, que é a forma natural de mostrar timing.
- **Sempre mostrar o baseline do jogador** (DPS, parse, percentil) e o gap. Hoje ausente.
- **Sempre mostrar o n e a incerteza da coorte.** "Baseado em 47 logs comparáveis" constrói confiança; um número inventado destrói.
- **Progresso e cancelamento.** Fila de jobs com estados, não uma chamada bloqueante de minutos.

### 6.7 Engenharia e operação

- `pyproject.toml` com dependências pinadas; `uv` ou `poetry`.
- `git init` **depois** de criar `.gitignore` com `.env`.
- Type hints completos + `pyright`/`mypy` em modo estrito na camada de análise; `ruff` para lint/format.
- **Testes:** golden-file sobre um log fixture (garante que refactors não mudam a saída), testes unitários das funções estatísticas, e property-based (Hypothesis) sobre o alinhamento de sequência — ex.: alinhar uma sequência consigo mesma deve dar custo zero, propriedade que o algoritmo atual **não** satisfaz de forma útil.
- **Data quality gates** no estilo Great Expectations: timestamps dentro dos limites da luta, sem valores negativos, n mínimo por coorte, cobertura mínima de eventos. Falhar alto em vez de produzir relatório silenciosamente errado.
- Logging estruturado (`structlog`) com correlation id por análise; métricas de pontos de API consumidos, hit rate de cache, latência.
- Configuração via arquivo tipado (Pydantic Settings) em vez de constantes espalhadas.
- Docker + job agendado para o batch de construção de coortes.

### 6.8 Validação — o que separa ferramenta séria de gerador de opinião

Nenhuma das recomendações acima vale nada sem verificar que **o conselho funciona**:

- **Backtesting:** separar um conjunto de validação de logs. Verificar que o score de alinhamento/uptime que a ferramenta produz correlaciona com percentil real. Se não correlacionar, a métrica não deve ser reportada.
- **Métrica de sucesso longitudinal:** para jogadores que usam a ferramenta repetidamente, medir se o parse melhora nos pulls seguintes nas dimensões apontadas. Cuidado com **regressão à média** — comparar contra um grupo de controle, não contra o próprio pull ruim que motivou a análise.
- **Calibração:** quando a ferramenta diz "+3% de DPS", esse número precisa ser calibrado contra ganho realizado.

---

## 7. Roadmap priorizado (impacto × esforço)

### Fase 0 — Correções críticas (dias)
Ganho imediato de correção, sem mudar arquitetura.

1. Corrigir `avg_cd_duration` para habilidades de uso único (3.3) — bug direto.
2. Trocar nearest-neighbor por alinhamento monotônico e **reportar casts perdidos** (3.1).
3. Lock + escrita atômica no `spells.json` (4.1).
4. `timeout` + retry com backoff + tratamento de 429 em todas as chamadas HTTP (4.3).
5. `.gitignore` com `.env`, depois `git init`; `requirements.txt`. Rotacionar chaves.
6. Parar de gravar `non-trackable` globalmente (3.8) — mover para cache por (spec, encounter).
7. Mostrar parse/DPS do jogador e o `n` da coorte no cabeçalho (3.11).

### Fase 1 — Fundação de dados (semanas)
8. Persistência em Parquet + DuckDB; cache de logs já baixados.
9. Pré-computação em batch dos perfis de coorte; caminho interativo vira lookup.
10. Refatoração em camadas com dataclasses (elimina as tuplas de 9 posições).
11. Testes golden-file + unitários sobre as funções estatísticas.

### Fase 2 — Metodologia (semanas)
12. Matching multivariado de coorte (ilvl, talentos, dificuldade, partition) (6.3a).
13. Clustering de build e reporte de divergência de build (6.3b).
14. Grading por quantil empírico + bootstrap CI (6.3e/f).
15. Normalização temporal por fase (6.3c).

### Fase 3 — O que falta medir (semanas)
16. Active time, uptimes de buff/debuff, dano por habilidade, waste de recurso, mortes (6.5).
17. **Decomposição do gap de DPS** (6.4 nível 1) — melhor relação retorno/esforço do roadmap inteiro.
18. Priorização por tamanho de efeito; relatório reduzido a top-N ações.

### Fase 4 — Modelagem e produto (meses)
19. LightGBM + SHAP para atribuição por log (6.4 nível 2).
20. Relatório web com timeline visual e waterfall de DPS (6.6).
21. Integração SimulationCraft (6.4 nível 3).
22. Backtesting e calibração (6.8).

---

## 8. Anexo — índice de achados por linha

| Linha(s) | Severidade | Achado |
|---|---|---|
| `bot.py:26`, `58-75` | 🟠 | Token Blizzard cacheado sem expiração |
| `bot.py:45-48` | 🟠 | JSON corrompido → banco de spells zerado silenciosamente |
| `bot.py:203`, `260`, `336` | 🔴 | Chamadas HTTP sem `timeout` |
| `bot.py:221` vs `324` | 🔴 | Healers/tanks analisados contra ranking `metric: dps` |
| `bot.py:223` | 🟡 | Identificação de jogador por nome (ambíguo cross-realm) |
| `bot.py:241` + `390-391` | 🔴 | Escrita concorrente não-atômica em `spells.json` |
| `bot.py:318-325` | 🔴 | Rankings sem `difficulty`, `partition`, `bracket`, talentos |
| `bot.py:358` | 🟠 | Filtro de duração absoluto (±30s) em vez de relativo |
| `bot.py:361` | 🟠 | Percentil ausente substituído por 99.0 fabricado |
| `bot.py:390-391` | 🔴 | Até 100 logs baixados por comando, sem cache |
| `bot.py:406`, `718`, `734` | 🟠 | Tuplas de 8–9 posições; contrato inconsistente entre sucesso/erro |
| `bot.py:426-434` | 🔴 | Agregação por slot gera rotação-fantasma |
| `bot.py:449`, `463` | 🟡 | DEBUG imprime amostras brutas de 100 logs |
| `bot.py:455`, `458` | 🟠 | `stdev` calculado e nunca utilizado |
| `bot.py:465-474` | 🔴 | `avg_cd_duration` = instante do 1º cast quando há uso único |
| `bot.py:497-541` | 🟠 | `non-trackable` gravado globalmente; análise não-reprodutível |
| `bot.py:523` | 🟡 | Blacklist por substring em inglês (`"ring"` gera falso positivo) |
| `bot.py:570-576` | 🔴 | Nearest-neighbor: viés otimista, casts perdidos invisíveis |
| `bot.py:579-584` | 🟠 | Thresholds 10s/25s absolutos para todos os cooldowns |
| `bot.py:594-606` | 🟠 | Casts excedentes com `delta = 0.0`, nunca avaliados |
| `bot.py:636` | 🟠 | Cabeçalho mostra parse da coorte, nunca o do jogador |
| `bot.py:718-732` | 🟠 | `parses` propagado por 3 funções e nunca usado |
| `bot.py:751` | 🟡 | Split cego em 1900 chars quebra linhas e markdown |
| `.env` | 🟠 | Segredos em pasta OneDrive, sem `.gitignore`, projeto sem git |

---

## 9. Conclusão

O projeto tem **uma ideia central correta e diferenciada** (coorte por tempo de kill) implementada sobre um pipeline que ainda não sustenta conclusões confiáveis. A distância até uma ferramenta profissional não está na quantidade de código — está em três mudanças conceituais:

1. **Medir o que determina parse**, não só timing de cast. Active time, uptimes e dano por habilidade vêm antes de cooldowns.
2. **Quantificar impacto em DPS**, não em segundos. Um relatório que não diz "isso custou X" não pode priorizar, e sem priorização não há coaching.
3. **Separar batch de interativo** e tornar o pipeline reprodutível. Perfis de coorte pré-computados, dados imutáveis, execuções auditáveis.

Se eu tivesse que escolher **uma única mudança** com melhor retorno: a **decomposição do gap de DPS por habilidade** (6.4, nível 1). É implementável em poucos dias sobre dados que a API já expõe, responde diretamente à pergunta do usuário, e transforma o produto de "comparador de timings" em "explicador de performance".
