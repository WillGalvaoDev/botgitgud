# Roadmap M3–M6

Trabalho metodológico planejado. Estado: **M2 fechado; M3.1 fechado (política
`stream-availability-v1`); M3.2 NOT_STARTED** (M3.3, M3.4 não iniciados); M4–M6 não
iniciados. Os contratos já vigentes estão em [`methodology.md`](methodology.md).

## Regras comuns

- Ordem macro: M3 → M4 → M5 → M6. Unidades locais podem ser implementadas e revisadas por
  interface, mas nenhum macro é antecipado.
- Cada unidade local entrega comportamento verificável por interface. A unidade de integração
  de cada macro consome essas interfaces sem redesenhar suas regras, e é onde o macro fecha.
- Decisões abertas (§ final) são pré-condição da unidade afetada: são resolvidas e registradas
  em especificação antes da implementação dependente, nunca escolhidas durante a implementação.
- Fechar uma unidade exige: versão revisada, matriz critério → teste → resultado, entradas e
  saídas verificáveis, comandos e limitações. Contagem de testes, snapshots ou ausência de
  exceção não substituem prova de comportamento.
- Um caso novo só bloqueia se violar um critério contratado ou uma invariante necessária ao
  comportamento suportado; os demais são dívida registrada.
- Nenhuma unidade autoriza nova campanha de coleta, treinamento ou promoção de ML.

| Macro | Sequência | Closure |
|---|---|---|
| M3 | M3.1 e M3.2 → M3.3 → M3.4 | M3.4 |
| M4 | M4.1 → M4.2 → M4.3 | M4.3 |
| M5 | M5.1 e M5.2 → M5.3 | M5.3 |
| M6 | M6.1 → M6.2 → M6.3 | M6.3 |

## M3 — Evidência, interpretação e elegibilidade de recomendação

### M3.1 — Disponibilidade dos streams restantes (local) — CLOSED

Fechada. Especificação em [`m3-1-specification.md`](m3-1-specification.md) (política
`stream-availability-v1`, interface em `analysis/stream_availability.py`). Unidade local: nenhum
consumidor existente foi religado a ela; a integração é M3.4.

**Depende de:** contratos de disponibilidade de M1. **Escopo:** cobertura e estados das auras e
recursos já consumidos. **Fora:** novos sinais, motor de oportunidades, features experimentais.

1. Situações contratadas distinguem zero observado, desconhecido, incompleto e não aplicável.
2. Falha ou cobertura parcial não produz medida integral silenciosamente.
3. Cada valor mantém cobertura e proveniência necessárias à interpretação.
4. Recursos de unidades diferentes não são somados como uma grandeza.
5. Disponibilidade de um stream não comprova a de outro.

Evidência: respostas gravadas de coleta completa, vazia, parcial e ausente; valores esperados
calculados dos dados; round-trip dos estados. Decisão prévia: prova mínima de cobertura por
stream, aplicabilidade e normalização, com lista finita de streams e consumidores.

### M3.2 — Semântica das grades (local)

**Depende de:** populações de M2; **B06**. **Escopo:** significado das grades e demais
indicadores estatísticos. **Fora:** materialidade, prioridade global, recomendações temporais.

1. Cada grade usa valores e N da própria métrica.
2. Quantis e empates coincidem com cálculo independente.
3. Se B06 escolher descrição, nenhuma saída afirma controle de FDR ou confiança causal.
4. Se B06 escolher inferência, hipóteses, família de testes e calibração atendem limites
   quantitativos previamente especificados.
5. Insuficiência produz o estado contratado, sem grade ou confiança fabricada.

### M3.3 — Observação, hipótese e ação (local)

**Depende de:** M3.1, M3.2, M2; **B09**. **Escopo:** materialidade e elegibilidade de
recomendação dos sinais existentes. **Fora:** disponibilidade temporal e ordenação global (M5).

1. Déficit absoluto material continua revisável quando as proporções de dano são iguais.
2. Toda observação referencia medida, unidade e população.
3. Hipótese não comprovada permanece identificada como hipótese.
4. Ação sem a evidência exigida é substituída pela observação ou abstenção prevista.
5. Diferença contábil não gera promessa de ganho nem causa.

Decisão prévia: complemento de B09 e tabela finita de evidência por família de recomendação,
preservando a visibilidade de déficit já existente.

### M3.4 — Integração e closure da interpretação

**Depende de:** M3.1–M3.3. **Escopo:** propagar estados e bases de evidência até findings,
remediação e consumidores. **Fora:** redesign editorial (M5) e oportunidades (M4).

1. Nenhum consumidor transforma desconhecido ou parcial em zero confirmado.
2. Nenhum consumidor promove hipótese a causa ou déficit a ganho.
3. Observação material permanece acessível mesmo sem ação elegível.
4. Caminhos ativos usam a mesma semântica de grades e evidências.

## M4 — Disponibilidade e oportunidades

### M4.1 — Evidência de mecanismo e associação a casts (local)

**Depende de:** M1, M2; **B02**, **B05**. **Escopo:** aplicabilidade e associação para um
conjunto inicial fechado de mecanismos. **Fora:** suporte universal, recomendação temporal,
estimativa de ganho.

1. Cada mecanismo identifica fonte, versão e condições de aplicação.
2. Cada associação evento → cast é verificável nos eventos; ambiguidades ficam explícitas.
3. Pet, tick ou proc não vira ação própria por compartilhar nome ou família.
4. "Alvos por cast", quando suportado, corresponde aos alvos de cada instância; senão continua
   indisponível.

### M4.2 — Reconstrução de oportunidades candidatas (local)

**Depende de:** M4.1, M3.1; **B05**. **Escopo:** estado temporal e oportunidades candidatas.
**Fora:** otimização de rotação, garantia de melhor momento, ganho causal.

1. Estado de cooldown/cargas/reset coincide com timelines de resultado conhecido.
2. Toda oportunidade tem instante, mecanismo e evidência de disponibilidade/aplicabilidade.
3. Mediana de casts da referência não substitui prova de disponibilidade.
4. Condição necessária ausente impede a conclusão dependente.
5. Disponibilidade isolada não produz afirmação de que usar imediatamente seria melhor.

### M4.3 — Integração e closure das oportunidades

**Depende de:** M3 fechado; M4.1, M4.2. **Escopo:** conectar evidências temporais ao contrato de
observação/hipótese/ação. **Fora:** ampliar catálogo ou refazer priorização.

1. Cada achado temporal permite recuperar eventos e regras que o sustentam.
2. Mecanismos não suportados preservam abstenção e medidas independentes.
3. Associação efeito ↔ botão não duplica dano nem altera a contabilidade de M1.
4. O contrato distingue comparação de frequência, oportunidade candidata e recomendação temporal.

## M5 — Seleção e apresentação ao jogador

### M5.1 — Seleção das prioridades (local)

**Depende de:** M3/M4; política de seleção. **Fora:** novos sinais, renderização, score causal.

1. Conjuntos de candidatos contratados produzem seleção e ordem esperadas.
2. Seleção não compara números de unidades incompatíveis como score comum.
3. Ausência de achados de execução não elimina informação válida de setup.
4. Prevalência de setup não vira prova de superioridade.
5. Empates seguem regra explícita, independente da ordem de entrada.

### M5.2 — Renderização fiel ao contrato (local)

**Depende de:** M3/M4; **B08**. **Escopo:** mensagens Discord e texto CLI, qualificadores e
posições verbais.

1. Afirmações quantitativas correspondem à medida, população, unidade e denominador corretos.
2. "Mais baixo" só aparece quando a ordenação real da própria variável sustenta a frase,
   incluindo empates; a fronteira `q ≤ 1/n` não produz posição ordinal falsa.
3. Redução de conteúdo preserva os qualificadores obrigatórios de B08.
4. Setup permanece contexto independente da execução.

Inclui a dívida de apresentação registrada em [`methodology.md`](methodology.md) §6 (N do
ledger ao lado de grades de outra população).

### M5.3 — Integração e closure da resposta

**Depende de:** M4 fechado; M5.1, M5.2.

1. Itens apresentados correspondem à seleção aprovada.
2. CLI e Discord preservam os mesmos fatos e restrições.
3. A resposta respeita os limites do canal sem truncar qualificadores necessários.
4. Casos sem comparação ou sem ação continuam apresentando o conteúdo válido previsto.

## M6 — Validação dos dados derivados e prontidão experimental

Relacionado à trilha experimental descrita em [`phase4.md`](phase4.md).

### M6.1 — Contrato das features remanescentes (local)

**Depende de:** contratos finais de M1–M4; restante de **B07**. **Fora:** novas famílias de
features, treinamento, campanha, reconstrução histórica.

1. Cada feature ativa tem fonte, unidade, agregação, disponibilidade e controlabilidade
   explícitas.
2. Ausências (item level, tier, raid size etc.) seguem política especificada, sem zero
   silencioso.
3. Agregados de uptime e recursos correspondem às unidades e populações contratadas.
4. Features aposentadas não entram em nova matriz por nenhum fallback.
5. Mudança semântica produz nova versão e identidade de derivado, preservando históricos.

Label de ranking (`y_rank_percent`) não representa qualidade de recomendação nem efeito de
intervenção; agregados nomeados `c_*` não são controláveis só pelo nome.

### M6.2 — Validação integral do corpus e derivados (dados)

**Depende de:** M6.1; política de elegibilidade e eventual recálculo. **Fora:** nova coleta,
reescrita de brutos, sobrescrita de campanhas, treinamento.

1. Todo arquivo do manifesto inicial (inclusive os 1.274 Parquets pré-M6) recebe resultado;
   totais por estado reconciliam com o inventário.
2. Cada exclusão ou impossibilidade de recálculo identifica a dependência ausente.
3. Dados brutos e resultados históricos permanecem preservados.
4. Cada derivado novo identifica origem, versão e fingerprint.
5. Nenhum registro é semanticamente válido só porque o Parquet é legível.

### M6.3 — Integração experimental e closure de prontidão

**Depende de:** M5 fechado; M6.1, M6.2. **Fora:** treinamento, nova campanha, promoção `READY`,
ML no coaching, revisão das decisões de arquitetura experimental.

1. A matriz experimental contém apenas linhas, colunas e versões elegíveis.
2. Splits respeitam separações de identidade, grupo e tempo, verificadas pelos membros.
3. Label, origem e limitações permanecem identificados, sem interpretação causal.
4. Carregar resultados históricos preserva suas versões.
5. O caminho de produção distingue consulta de capacidade no registry de uso de predição e
   confirma a fronteira vigente (hoje o resolver é consultado, mas nenhuma predição é usada).

Prontidão dos dados não equivale a aprovação de modelo.

## Decisões abertas

| ID | Unidade | Pergunta | Evidência necessária |
|---|---|---|---|
| B02 | M4.1 | Quais eventos permitem atribuir alvos a instâncias de cast? | Matriz por mecanismo (direto, AoE, periódico, pet), cobertura e ambiguidade; sem prova, alvos por cast continua bloqueado. |
| B05 | M4.1/M4.2 | Qual fonte prova cooldown, cargas, reset, build e disponibilidade? | Proveniência, versão, regras de aplicabilidade e conjunto inicial de mecanismos; nunca inferir disponibilidade da mediana de casts. |
| B06 | M3.2 | Grades são descrição ou inferência com controle de erro? | Se inferência: hipótese, calibração de p-valores finitos e família de testes; se descrição: retirar alegações de FDR/confiança causal. |
| B07 (resto) | M6.1/M6.2 | Como versionar/recalcular derivados e ausências? | Dependências por feature, preservação de brutos, estados explícitos; recalcular, excluir ou manter indisponível por categoria, sem backfill irrestrito. |
| B08 | M5.2 | Qual conteúdo mínimo cabe na mensagem? | Priorizar evidência, comparabilidade, ação e ressalva; decidir redução de itens antes de eliminar qualificadores. |
| B09 (resto) | M3.3 | Qual regra de materialidade preserva déficit absoluto sem inventar ação? | Separar sinal para revisão de elegibilidade de recomendação, sem reintroduzir causalidade pelo tamanho do gap. |

B01, B03 e B04 estão resolvidos (estimando M1, elegibilidade M2.1, matriz M2.2).
