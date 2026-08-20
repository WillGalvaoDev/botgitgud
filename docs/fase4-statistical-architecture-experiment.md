# Experimento de arquitetura estatística da Fase 4

**Data:** 2026-08-20
**Status:** protocolo aprovado, infraestrutura implementada, **nenhuma coleta executada, nenhum
modelo treinado**.
**Escopo:** etapa de validação prévia entre o censo (`docs/fase4-target-census.md`) e a Fase 4
definitiva. **Não altera os requisitos de T4.1–T4.4** nem o gate de 5.000
(`docs/fase4-data-acquisition-plan.md` §10.3), que permanece vigente para a Fase 4 final.

---

## 1. Motivação medida

O censo A+B real (7 dias, zona 46) mediu:

| Fato | Valor |
|---|---:|
| Phase4Targets suportados observados | 662 |
| Projetados para 5.000 observações em <90 dias | **0** |
| Melhor target (`Warlock/Demonology/3183/5/4`) | 102,9 dias · 99.768 pontos |
| 5 targets independentes | ~506.088 pontos |
| 10 targets independentes | ~1.037.057 pontos |
| 25 specs × 1 encounter | ~2.671.735 pontos |
| 25 specs × 9 encounters | ~23.736.403 pontos |

O desenho `1 Phase4Target = 1 dataset de ≥5.000 = 1 modelo` é aritmeticamente inviável como
estratégia geral de produto. Isso **não** invalida o gate de 5.000 para um target isolado; invalida
a suposição de que essa é a única granularidade possível.

---

## 2. Pergunta experimental

> **Qual granularidade de modelo generaliza bem o suficiente para cobrir muitas specs/encounters
> sem exigir um modelo independente por Phase4Target?**

Formulada de modo falseável: existe alguma granularidade mais geral que o Phase4Target cuja perda
de qualidade preditiva, medida em backtesting temporal com isolamento explícito, seja pequena o
bastante para justificar a redução de ordens de magnitude no custo de dados e manutenção?

**Esta rodada não escolhe vencedor.** Ela constrói o instrumento que permitirá responder.

---

## 3. Princípio inviolável: previsão ≠ causalidade

O objetivo da Fase 4 é **prever/explicar performance relativa e apoiar priorização** de
recomendações já produzidas pelas Fases 0–3.

- SHAP, feature importance e coeficientes **não são prova causal** e não serão tratados como tal.
- Nenhuma recomendação é considerada causalmente validada por reduzir MAE.
- Melhora de MAE é evidência de **poder preditivo**, não de que agir sobre a feature produz o ganho.
- A validação causal continua sendo escopo da T4.3 (backtesting, calibração, controle de regressão
  à média), não deste experimento.
- SHAP é deliberadamente **excluído desta etapa** (§10): primeiro é preciso saber se existe
  capacidade preditiva e generalização; atribuição vem depois.

---

## 4. Hipóteses

| # | Hipótese | Falseável por |
|---|---|---|
| H0 | Nenhuma granularidade prevê `rankPercent` melhor que a mediana do treino | Baseline 0 empata ou vence todo modelo em S1 |
| H1 | Existe sinal preditivo controlável dentro de um target | Modelo por target bate Baseline 0 em S1 |
| H2 | Um modelo por spec (agregando encounters) perde pouco vs. modelo por target | Δ MAE em S1 dentro da margem de §8 |
| H3 | Um modelo global generaliza para **encounter não visto** | Desempenho em S3 não colapsa vs. S1 |
| H4 | Um modelo global generaliza para **spec não vista** | Desempenho em S4 não colapsa vs. S1 |
| H5 | Features controláveis mantêm sinal ao cruzar contextos | Erro por spec/encounter não é dominado por contexto |

H3 e H4 são as hipóteses caras. Se ambas forem rejeitadas, a generalização cross-target está
descartada e o produto precisa de outra estratégia (menos targets, mais tempo, ou abandonar a
modelagem preditiva na Fase 4).

---

## 5. Alternativas de granularidade (A–E)

| Cód. | Granularidade | Chave de agrupamento | Nº de modelos (censo) | Dados por modelo |
|---|---|---|---:|---|
| A | `MODEL_TARGET` | `class/spec/encounter/difficulty/partition` | 662 | menor |
| B | `MODEL_SPEC` | `class/spec` | 25 | médio |
| C | `MODEL_ENCOUNTER` | `encounter/difficulty/partition` | ~27 | médio |
| D | `MODEL_GLOBAL` | único | 1 | maior |
| E | `MODEL_HIERARCHICAL` | global + efeitos por spec/encounter | 1 | maior |

**E é apenas ponto de extensão nesta rodada.** `ModelGranularity.MODEL_HIERARCHICAL` existe no
contrato de domínio e é explicitamente rejeitado pelo planner/treinador até que A–D tenham
resultados — implementar multi-task antes de saber se D sequer compete seria construir complexidade
sem evidência.

A comparação relevante não é "quem tem o menor MAE absoluto", e sim **quanto D/B/C perdem em
relação a A**, contra quanto economizam.

---

## 6. Dataset experimental (largura, não profundidade)

O gate de 5.000 é sobre **profundidade em um target**. Esta pergunta é sobre **largura entre
targets**. São objetivos opostos, e o experimento prioriza largura.

**Os 5.000 NÃO são pré-requisito deste experimento.** Não é necessário coletar 5.000 observações
para descobrir que a arquitetura estatística está errada.

Diversidade exigida:

| Dimensão | Objetivo |
|---|---|
| spec | máximo possível (até 25) |
| encounter | múltiplos (≥5) — sem isso S3 é impossível |
| difficulty | fixa por campanha (evita confundir dificuldade com contexto) |
| partition | **exatamente uma** (§1.5: nunca misturar partitions) |
| rankPercent | cobertura equilibrada das 5 faixas |
| duração / ilvl / build | variação natural herdada da amostra |
| tempo | espalhada por toda a janela do censo |

### Frame recomendado

`difficulty=5`, `partition=4` — medido no warehouse do censo: **213 grupos, 7.333 observações
candidatas, 25 specs, 9 encounters**. É o maior recorte que mantém difficulty e partition
constantes enquanto varia as duas dimensões cuja generalização está sob teste.

---

## 7. Amostragem e estratificação

O planner é **read-only e determinístico**: não chama a API, não usa RNG.

Estratos: `(spec, encounter, faixa de rankPercent)`.

Faixas de rankPercent (fixas):

```
[0,20)  [20,40)  [40,60)  [60,80)  [80,100]
```

Distribuição real medida no censo (kills, rankPercent presente): 4.581 / 4.894 / 5.027 / 4.914 /
5.278 — o pool bruto já é quase uniforme, então estratificar custa pouca perda de tamanho.

**Alocação:** round-robin determinístico sobre estratos ordenados canonicamente, limitado pela
disponibilidade de cada estrato, até esgotar o número de observações ou o orçamento. Nenhum
estrato é servido duas vezes antes que todos tenham sido servidos uma — isso impede que specs
populares dominem.

**Seleção dentro do estrato:** os candidatos são ordenados por
`(start_time_ms, report_code, fight_id, player_name)` e escolhidos com **espaçamento uniforme** ao
longo dessa lista, não os primeiros N. Sem isso, toda a amostra se concentraria no início da janela
e S1 (split temporal) ficaria degenerado.

**Anti-viés explícito:** a amostra nunca é "os melhores parses". Um estrato `[80,100]` recebe a
mesma cota-base que `[0,20)`.

---

## 8. Protocolos de split

Random split simples **nunca** é a validação principal. Todos respeitam ordem temporal quando
aplicável.

| Cód. | Protocolo | Treino | Validação | Testa |
|---|---|---|---|---|
| S1 | temporal within-target | passado dos mesmos targets | futuro dos mesmos targets | sinal preditivo básico |
| S2 | held-out players/logs temporal | passado, sem os reports/players de validação | futuro, reports e players disjuntos | memorização de indivíduo/report |
| S3 | held-out encounter | encounters vistos | encounter **nunca visto** | generalização por encounter (H3) |
| S4 | held-out spec | specs vistas | spec **nunca vista** | generalização por spec (H4) |
| S5 | held-out spec+encounter | spec e encounter vistos **separadamente** | aquela **combinação** não vista | interação spec×encounter |

Invariantes garantidos por teste:

- A mesma observação (chave natural `report_code/fight_id/player_name`) nunca aparece nos dois lados.
- S1/S2: todo timestamp de validação ≥ todo timestamp de treino.
- S2: nenhum `report_code` e nenhum `player_name` compartilhado entre os lados.
- S3: o encounter de validação tem **zero** linhas no treino.
- S4: a spec de validação tem **zero** linhas no treino.
- S5: a combinação está ausente do treino, mas spec e encounter aparecem isoladamente.

---

## 9. Riscos de leakage

| Risco | Natureza | Tratamento |
|---|---|---|
| **DPS bruto (`dps`, `amount`)** | `rankPercent` É o percentil do DPS no pool daquele spec/encounter/difficulty/bracket — usar DPS prevê o alvo por construção | **EXCLUDED_LEAKAGE**, jamais entra como feature |
| `rank_percent` | é o alvo | TARGET |
| `total_parses` | histórico do personagem, proxy de identidade/habilidade prévia | **EXCLUDED_LEAKAGE** nesta rodada |
| `bracketPercent` | percentil dentro do bracket — variante do alvo | **EXCLUDED_LEAKAGE** |
| Identidade (`player_name`, `report_code`) | permite memorizar indivíduos | IDENTITY, nunca feature; S2 existe para detectar |
| Estatística de coorte que inclui o próprio jogador | o alvo entra no denominador | não usada; features T3.1 são do próprio log |
| Ordem temporal | treinar no futuro e validar no passado | todo split ordena por tempo |
| Mesmo pull em ambos os lados | 2 jogadores do mesmo fight compartilham contexto | S2 separa por report |
| Nomes localizados | `resource_waste` usa rótulos PT-BR; boss name é display | só identificadores canônicos viram feature |

O item mais importante é o primeiro. Um modelo com DPS bruto atingiria MAE quase zero e não
significaria nada.

---

## 10. Modelos e baselines

| Papel | Modelo |
|---|---|
| Baseline 0 | prever a mediana apropriada do **treino** (por granularidade) |
| Baseline 1 | regressão linear sobre subset simples de features |
| Candidato | LightGBM |

Baseline 0 é o teto de rejeição de H0: um modelo que não o supera não tem poder preditivo.

**SHAP não entra nesta etapa.** LightGBM só será instalado quando o experimento for efetivamente
executado — a infraestrutura desta rodada não depende dele.

---

## 11. Métricas

Obrigatórias por (granularidade × protocolo de split):

- MAE, RMSE, R², correlação de Spearman
- erro por faixa de percentil
- erro por spec
- erro por encounter

Para modelos compartilhados (B/C/D/E), adicionalmente:

- desempenho em targets **vistos** no treino
- desempenho em targets **não vistos**
- **degradação relativa** ao modelo específico (A) no mesmo split

Spearman importa mais que MAE para o uso real: o produto prioriza recomendações, ou seja, precisa
**ordenar** corretamente, não acertar o percentil absoluto.

---

## 12. Matriz de decisão (humana, posterior)

Nenhum modelo é selecionado automaticamente. A matriz a preencher:

| Critério | A | B | C | D | E |
|---|---|---|---|---|---|
| accuracy (MAE/RMSE/R²/Spearman) | | | | | |
| generalization (S3/S4/S5) | | | | | |
| data requirement (obs/modelo) | | | | | |
| API acquisition cost | | | | | |
| training complexity | | | | | |
| maintenance cost (nº de modelos) | | | | | |
| coverage (targets atendidos) | | | | | |

**Uma arquitetura mais geral não precisa vencer em MAE absoluto.** Perder pouco e reduzir
drasticamente custo de dados/modelos pode ser preferível. A decisão é humana, após os resultados.

---

## 13. Critérios de aprovação e rejeição

Avaliados após a execução, não agora:

- **H0 rejeitada** (há sinal): o melhor modelo supera Baseline 0 em MAE **e** Spearman em S1.
- **Generalização aceita** para uma granularidade: em S3/S4, MAE ≤ **1,25×** o MAE do modelo por
  target no mesmo período **e** Spearman ≥ **0,8×** o do modelo por target.
- **Generalização rejeitada**: MAE > 1,5× ou Spearman < 0,5× do modelo por target.
- **Zona cinzenta** entre os dois: decisão humana com a matriz de §12.
- **Experimento inconclusivo**: se nem o modelo por target supera Baseline 0, a questão de
  granularidade não se coloca — o problema é de features ou de tamanho de amostra, não de
  arquitetura.

Estes limiares são **do experimento**, não do produto, e não substituem T4.2/T4.3.

---

## 14. Orçamento e critérios de parada

| Limite | Valor |
|---|---:|
| Teto de API para **todo** o Stage C experimental | **25.000 pontos** |
| Custo estimado por observação (fight solo) | 17 pontos |
| Custo por fight compartilhado | 15 + 2/jogador |
| Observações máximas sob o teto | ~1.470 |
| Faixa alvo | 500–1.500 |

O teto é configurável (`--max-points`) mas **nunca implícito**: o planner recusa produzir uma
campanha sem orçamento declarado.

**Interromper o experimento se:**

1. o custo real de Stage C exceder o teto aprovado — para imediatamente, mantém checkpoint;
2. a amostra coletada não atingir cobertura mínima (≥5 encounters e ≥8 specs) — S3/S4 ficam
   impossíveis e o experimento perde o objeto;
3. nem o modelo por target superar Baseline 0 (§13, inconclusivo);
4. surgir qualquer evidência de leakage não tratado — invalida todos os resultados até a correção;
5. o bot interativo for degradado pela coleta — prioridade absoluta das Fases 0–3.

---

## 15. O que esta rodada NÃO faz

- Não executa Stage C nem baixa observações.
- Não treina modelo nem instala LightGBM/SHAP.
- Não escolhe granularidade vencedora.
- Não altera T4.1–T4.4 nem o gate de 5.000.
- Não registra nenhum modelo como `READY`.
- Não implementa arquitetura hierárquica.
