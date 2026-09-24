# Contrato de produto

Autoridade de escopo do BotGITGUD: o que o produto entrega ao jogador e os princípios que
decidem conflitos. Como isso é implementado está em [`architecture.md`](architecture.md);
as regras de medida e comparabilidade, em [`methodology.md`](methodology.md).

## 1. O que o produto é

Uma ferramenta de **coaching de DPS** para World of Warcraft. Recebe

```
!analisar <Player> <WarcraftLogs URL>
```

e responde, no canal do Discord, com uma análise **curta, compreensível e acionável** sobre
como aquele jogador pode melhorar a **execução** naquela luta, comparando-o com logs
comparáveis (a coorte) do Warcraft Logs.

Não é um clone do Warcraft Logs, um simulador, um parser universal das mecânicas de WoW nem
uma reprodução da contabilidade interna do WCL.

## 2. A resposta de coaching

Exatamente **uma** resposta no Discord, sem anexo e sem link para relatório:

1. conclusão em linguagem natural;
2. **0 a 3** prioridades;
3. se houver, uma observação positiva curta.

Ela responde, nesta ordem: por que o dano não foi melhor, qual foi o maior erro, o que fazer
diferente e o que importa mais. Setup só aparece se muda materialmente o conselho; confiança
e amostra só aparecem quando qualificam a interpretação; número só entra quando ajuda a
entender a recomendação. Mais curto é melhor quando basta. O CLI (`analyze`) renderiza o
mesmo contrato em texto detalhado para depuração.

### 2.1 Zero prioridades é resultado válido

`0..3`, nunca "exatamente 3". Materialidade tem de ser demonstrada; um desvio imaterial não
vira problema e nenhuma vaga é preenchida só porque existe uma comparação. Um jogador forte
recebe uma conclusão positiva honesta.

### 2.2 Ordenação por impacto material

Prioridades são ordenadas por impacto material no fight do jogador, não por um score
aritmético inventado. Não se fabrica equivalência entre achados heterogêneos. Preferência:
impacto medido diretamente comparável, depois camadas semânticas conservadoras de
materialidade, e confiança apenas como qualificação/desempate.

### 2.3 Remediação limitada pela evidência

Cada prioridade diz **o que aconteceu, por que importa e o que fazer**, e o "o que fazer" é
limitado pela evidência:

- se a evidência prova morte e tempo ativo perdido, explica a consequência observada;
- só cita mecanismo ou timing quando ele foi de fato identificado;
- não inventa por que um downtime ocorreu;
- preserva a incerteza de diagnósticos incertos;
- um diagnóstico que se declara possivelmente não controlável não ocupa vaga de prioridade.

## 3. Escopo de gameplay

Specs DPS do `SUPPORTED_SPEC_SET` (`src/botgitgud/domain/specs.py`); o scope gate rejeita as
demais antes de qualquer consulta à API.

**Cobertura ≠ destaque.** A execução ofensiva relevante do jogador é analisada por inteiro;
até 10 habilidades canônicas por spec são o recorte **destacado**. Dano ofensivo próprio e
material que desapareça da análise (identidade não resolvida, classificação ausente, perda
estrutural) é defeito de produto, não degradação aceitável.

Priorizar: dano rotacional central, spenders/generators, cooldowns ofensivos, summons e
buffs/procs próprios relevantes. Fora da análise principal: defensivas, utility, raciais,
consumíveis, efeitos de equipamento, buffs/efeitos externos, habilidades de outros jogadores e
sinais sem observabilidade suficiente. Conhecimento curado (inclusive a fonte de rotação da
Wowhead, via `!update`) pode informar quais habilidades importam.

## 4. Fontes de autoridade

Usar a fonte mais direta disponível; não reconstruir por eventos um valor que o WCL fornece
diretamente, salvo quando um consumidor concreto precisar da reconstrução.

| Pergunta | Fonte |
|---|---|
| DPS exibido, percentil | WCL Rankings (`amount`, `rankPercent`) |
| Dano agregado e escopo do encontro | tabela `DamageDone` do WCL |
| Execução: casts, timing, sequência, cooldowns, auras/procs | eventos do WCL |
| Identidade e semântica das habilidades | `CanonicalAbility` / `AbilityRole` |
| Importância e interpretação da spec | conhecimento curado |

Fatos verificados contra a API real estão em [`schema_confirmado.md`](schema_confirmado.md).

## 5. Princípios

- **Fail-closed local.** Feature sem evidência suficiente falha fechada na própria feature,
  não no relatório: omitir uma comparação não implica omitir casts, timing, uptime ou outra
  feature independente. O limite: fail-closed não autoriza tornar invisível uma parcela
  material da execução — nesse caso a lacuna é declarada ao usuário.
- **Não fabricar.** Nenhum ganho de DPS, causa, zero confirmado ou equivalência é inventado;
  ausência de evidência é um estado explícito.
- **Efeitos externos não são execução própria.**
- **Histórico preservado.** Evolução de contrato versiona a semântica e aplica o contrato novo
  às análises novas; não exige reprocessar o corpus nem reescrever dados brutos.
- **API é recurso limitado.** Antes de qualquer coleta: esgotar o offline, formular pergunta
  falsificável, definir alvo, orçamento e condição de parada.

Prioridade em caso de conflito:

```
coaching correto para o usuário
  > fail-closed local
    > implementação simples e manutenível
      > fidelidade de reconstrução interna
        > elegância arquitetural
```

## 6. Definição de pronto

Para uma análise nova e suportada, o produto:

- identifica corretamente jogador, spec e luta;
- mostra DPS e percentil do WCL corretos;
- avalia o setup, independentemente da execução;
- cobre a execução ofensiva do log inteira e destaca até 10 habilidades;
- mede somente features observáveis;
- compara a execução com a coorte apenas quando há evidência comparável;
- produz 0 a 3 prioridades, cada uma com o que aconteceu, por que importa e o que fazer, dentro
  do que a evidência sustenta;
- não atribui efeitos externos como execução própria nem fabrica ganho ou causa;
- entrega a resposta compacta no Discord;
- falha fechado localmente quando uma feature não é confiável.

Não é requisito reproduzir a matemática interna do WCL, explicar 100% dos eventos, suportar
todas as specs ou eliminar discrepância interna sem impacto no usuário.

## 7. Fora do produto atual

Modelos de ML no caminho servido ao jogador (a trilha experimental está em
[`phase4.md`](phase4.md)), coaching gerado por LLM, inferência de mecânica de encontro,
inferência causal especulativa e uma página/relatório paralelo ao coaching.
