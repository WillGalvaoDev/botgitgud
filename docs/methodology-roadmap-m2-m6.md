# Roadmap metodológico M2–M6 — decomposição aprovada

Aprovado pelo usuário e formalizado em 2026-09-13. São **16 submilestones**.
Correção aritmética na construção do orquestrador: a proposta e sua formalização
anterior diziam 17, mas enumeravam 16 IDs (3+4+3+3+3). Os IDs, escopos e dependências
aprovados permanecem exatamente os mesmos; nenhuma unidade foi criada ou retirada.
Status 2026-09-22: **M2.1 MILESTONE_CLOSED** ([independent review and evidence](m2-1-closure.md)). **M2.2 MILESTONE_CLOSED** ([closure](m2-2-closure.md)). Status 2026-09-24: **M2.3 MILESTONE_CLOSED; macro M2 MILESTONE_CLOSED** ([closure](m2-3-closure.md)). M3.1-M6.3: **NOT_STARTED**. Product delivery workspace: `../BotGITGUD-M2.1-product`. Orchestrator suspended; historical run unchanged. This roadmap is not a SPEC.
M1 está [MILESTONE_CLOSED](m1-closure.md).

Base: [contrato M0](m0-methodology-contract.md) e [SPEC M1](m1-specification.md).
Esta decomposição preserva as fronteiras documentadas, fórmulas, limiares e decisões
de produto aprovadas. Não autoriza novas campanhas, treinamento ou promoção de ML.
O [workflow operacional](milestone-workflow.md) rege todas as unidades.

As pendências de especificação identificadas na proposta aprovada como “Astra”
estão atribuídas abaixo ao **Opus**, conforme o workflow aprovado posteriormente.
Astra é o revisor independente. Essa atribuição não resolve ou altera B03–B09.

## Regras comuns

- Cada unidade local entrega comportamento verificável por interface, normalmente
  em uma implementação e uma revisão. Integração consome essas interfaces sem
  redesenhar suas regras.
- Decisões pendentes do Opus são pré-condições da unidade afetada, não escolhas
  metodológicas delegadas ao executor.
- Cada fechamento entrega versão revisada, matriz critério → teste → resultado,
  entradas e saídas verificáveis, comandos e limitações. Quantidade de testes,
  snapshots ou ausência de exceção não substituem prova do comportamento.
- Revisões locais verificam os critérios da unidade. Integração verifica propagação
  e invariantes entre componentes, sem repetir integralmente as revisões locais.
- Novos casos só bloqueiam se violarem um critério contratado ou uma invariante
  necessária ao comportamento suportado do produto. Os demais são dívida e não
  ampliam automaticamente mecanismos, cobertura ou escopo.
- Cada submilestone fecha individualmente; o macro fecha exclusivamente na unidade
  de integration/closure. No modo autônomo autorizado por START, o fechamento inicia
  a próxima unidade elegível conforme o workflow; no modo manual, não.

## M2 — Comparabilidade por métrica

Responsabilidades agrupadas: identidade e compatibilidade temporal, kill/wipe,
seleção e relaxamento de covariáveis, populações descritiva e aspiracional e
propagação da população efetivamente usada.

### M2.1 — Elegibilidade básica das referências — local

**Dependências:** M1; SPEC Opus de B03.
**Escopo:** decidir compatibilidade básica usando identidade, versão/partição,
hotfix e estado da tentativa.
**Fora de escopo:** relaxamento de covariáveis, grades, oportunidades e apresentação editorial.

**Critérios de aceite:**

1. Cada referência recebe decisão e motivo reproduzíveis a partir dos metadados e da versão da política.
2. Partição desconhecida, log antigo, hotfix e kill/wipe seguem exatamente a tabela aprovada, incluindo abstenções.
3. Incompatibilidade obrigatória não é superada pelo aumento do número de referências.
4. A decisão preserva fonte e estado desconhecido; não fabrica equivalência temporal.

**Testes e evidências:** matriz positiva, negativa e desconhecida com resultado
esperado definido antes da implementação; replay de metadados reais disponíveis;
saída individual de elegibilidade.

**Pendências do Opus:** B03, fontes autoritativas de compatibilidade e tratamento
das informações ausentes, preservando restrições de produto vigentes.

### M2.2 — Seleção da população por métrica — local

**Dependências:** interface de M2.1; SPEC Opus de B04.
**Escopo:** matching por métrica, covariáveis admitidas, relaxamentos e distinção
entre referência descritiva e aspiracional.
**Fora de escopo:** causalidade, calibração de grades e novo desenho de aquisição de dados.

**Critérios de aceite:**

1. Para cada métrica contratada, a população selecionada coincide com a esperada nas fixtures.
2. Cada relaxamento aplicado identifica covariável, regra e efeito sobre a seleção.
3. Referências incompatíveis em M2.1 permanecem excluídas em todos os níveis de relaxamento.
4. Populações descritiva e aspiracional mantêm identidades e finalidades separadas.
5. N, exclusões e insuficiência pertencem à população da própria métrica, sem empréstimo de outra distribuição.

**Testes e evidências:** fixtures com covariáveis variando isoladamente; comparação
dos membros selecionados; análise de sensibilidade de B04; permutação da entrada.

**Pendências do Opus:** B04, matriz métrica × covariável, relaxamentos permitidos e
política de suficiência. N elevado não é prova de ajuste estatístico.

### M2.3 — Integração e closure da comparabilidade

**Dependências:** M2.1 e M2.2 revisadas.
**Escopo:** conectar seleção, análise, contrato de relatório e proveniência persistida.
**Fora de escopo:** redesign de texto, fórmulas contábeis e novos seletores.

**Critérios de aceite:**

1. IDs usados no cálculo coincidem com os da população declarada para a métrica.
2. N, exclusões, relaxamentos e versão da política sobrevivem às fronteiras e à persistência pertinente.
3. Insuficiência ou incompatibilidade impedem a comparação dependente, mantendo observações independentes válidas.
4. A mudança de população preserva as identidades contábeis de M1 sobre a população efetivamente aceita.

**Testes e evidências:** replay integrado com populações diferentes entre métricas;
round-trip de proveniência; cálculo independente dos resultados esperados;
regressões contábeis de M1.

**Pendências do Opus:** formato mínimo de proveniência na SPEC de M2; nenhuma nova
decisão metodológica deve ficar para o closure.

## M3 — Evidência, interpretação e elegibilidade de recomendação

Responsabilidades agrupadas: cobertura dos streams restantes, interpretação
estatística, materialidade, separação entre observação/hipótese/ação e integração
dos consumidores.

### M3.1 — Disponibilidade dos streams restantes — local

**Dependências:** contratos de disponibilidade de M1.
**Escopo:** cobertura e estados das auras e recursos já consumidos pelo produto.
**Fora de escopo:** novos sinais, motor de oportunidades e revisão das features experimentais.

**Critérios de aceite:**

1. Situações contratadas distinguem zero observado, desconhecido, incompleto e não aplicável.
2. Falha ou cobertura parcial não produz medida integral silenciosamente.
3. Cada valor mantém cobertura e proveniência necessárias à sua interpretação.
4. Recursos de unidades diferentes não são somados como uma grandeza única.
5. Disponibilidade de um stream não comprova disponibilidade de outro.

**Testes e evidências:** respostas gravadas e fixtures de coleta completa, vazia,
parcial e ausente; valores esperados calculados diretamente dos dados;
round-trip dos estados.

**Pendências do Opus:** prova mínima de cobertura por stream, aplicabilidade e
normalização das medidas existentes. Lista finita de streams e consumidores.

### M3.2 — Semântica das grades — local

**Dependências:** contrato de população de M2; decisão Opus B06.
**Escopo:** atribuir às grades e demais indicadores estatísticos o significado aprovado.
**Fora de escopo:** materialidade, prioridade global e recomendações temporais.

**Critérios de aceite:**

1. Cada grade usa valores e N da própria métrica.
2. Quantis e empates coincidem com resultados calculados independentemente.
3. Se B06 escolher descrição, nenhuma saída desse contrato afirma controle de FDR ou confiança causal.
4. Se B06 escolher inferência, hipóteses, família de testes e procedimento de calibração atendem aos limites quantitativos previamente especificados.
5. Insuficiência produz o estado contratado sem fabricar grade ou confiança.

**Testes e evidências:** distribuições pequenas com resultado exato; fronteiras e
empates; se aplicável, simulações com desenho e tolerâncias congelados pelo Opus.

**Pendências do Opus:** B06. Alternativas excludentes; não há obrigação de implementar ambas.

### M3.3 — Observação, hipótese e ação — local

**Dependências:** interfaces de M3.1/M3.2 e M2; SPEC Opus de B09.
**Escopo:** materialidade e elegibilidade de recomendação para sinais existentes,
com bases de evidência explícitas.
**Fora de escopo:** reconstrução de disponibilidade temporal e ordenação global de M5.

**Critérios de aceite:**

1. Déficit absoluto material continua revisável quando as proporções de dano são iguais.
2. Toda observação referencia sua medida, unidade e população.
3. Hipótese não comprovada permanece identificada como hipótese.
4. Ação sem a evidência exigida pelo contrato é substituída pela observação ou abstenção prevista.
5. Diferença contábil não gera promessa de ganho recuperável nem causa por si só.

**Testes e evidências:** D03 e D04; pares com mesmo déficit e diferentes evidências
de ação; contratos estruturados com observação, hipótese, ação e restrição separadas.

**Pendências do Opus:** complemento de B09 e tabela finita de evidência necessária
por família de recomendação. Preservar a visibilidade já resolvida em M1.

### M3.4 — Integração e closure da interpretação

**Dependências:** M2 fechada; M3.1–M3.3 revisadas.
**Escopo:** propagar estados e bases de evidência até findings, remediação e consumidores existentes.
**Fora de escopo:** redesign editorial de M5 e oportunidades de M4.

**Critérios de aceite:**

1. Nenhum consumidor transforma desconhecido ou parcial em zero confirmado.
2. Nenhum consumidor promove hipótese a causa ou déficit a ganho.
3. Observação material permanece acessível mesmo sem ação elegível.
4. Caminhos ativos usam a mesma semântica aprovada para grades e evidências.

**Testes e evidências:** replays completos até contrato e textos existentes;
inspeção das afirmações contra fatos de origem; regressões de ausência e elegibilidade de M1.

**Pendências do Opus:** nenhuma decisão nova; divergências entre contratos resolvidas antes desta unidade.

## M4 — Disponibilidade e oportunidades

Responsabilidades agrupadas: fontes de regras, aplicabilidade de build/versão,
associação a instâncias de cast, reconstrução temporal e identificação de oportunidades.

### M4.1 — Evidência de mecanismo e associação a casts — local

**Dependências:** M1; compatibilidade de M2; SPEC Opus B02/B05.
**Escopo:** produzir evidência de aplicabilidade e associação para um conjunto inicial fechado de mecanismos.
**Fora de escopo:** suporte universal a classes/mecanismos, recomendação temporal e estimativa de ganho.

**Critérios de aceite:**

1. Cada mecanismo suportado identifica fonte, versão e condições de aplicação.
2. Cada associação evento → cast é verificável nos eventos; ambiguidades permanecem explícitas.
3. Pet, tick ou proc não se torna ação própria apenas por compartilhar nome ou família.
4. “Alvos por cast”, quando suportado, corresponde aos alvos efetivamente associados a cada instância; nos demais casos permanece indisponível.

**Testes e evidências:** matriz B02 com direto/AoE/periódico/pet classificados como
suportados ou não; timelines anotadas; contagem manual por instância; casos ambíguos negativos.

**Pendências do Opus:** conjunto inicial e regras de associação; fontes aprovadas
para cooldown, cargas, resets e build. A matriz não obriga suporte a todas as categorias.

### M4.2 — Reconstrução de oportunidades candidatas — local

**Dependências:** M4.1; disponibilidade pertinente de M3.1; SPEC Opus B05.
**Escopo:** calcular estado temporal e oportunidades candidatas dos mecanismos contratados.
**Fora de escopo:** otimização de rotação, garantia de melhor momento e ganho causal.

**Critérios de aceite:**

1. Estado de cooldown/cargas/reset coincide com timelines de resultado conhecido.
2. Toda oportunidade candidata possui instante, mecanismo e evidência de disponibilidade/aplicabilidade.
3. Mediana de casts da referência não substitui prova de disponibilidade.
4. Condição necessária ausente impede a conclusão dependente.
5. Disponibilidade isolada não produz afirmação de que usar imediatamente seria melhor.

**Testes e evidências:** timelines com cargas, resets e limites de janela aplicáveis
ao conjunto inicial; cálculo manual de estados e oportunidades; casos com contexto ausente.

**Pendências do Opus:** regras temporais, condições necessárias e afirmações
permitidas para oportunidade candidata, perdida e recomendação de instante.

### M4.3 — Integração e closure das oportunidades

**Dependências:** M3 fechada; M4.1 e M4.2 revisadas.
**Escopo:** conectar evidências temporais ao contrato de observação/hipótese/ação.
**Fora de escopo:** ampliar catálogo ou refazer priorização global.

**Critérios de aceite:**

1. Cada achado temporal permite recuperar os eventos e regras que o sustentam.
2. Mecanismos não suportados preservam abstenção e medidas independentes válidas.
3. Associação entre efeito e botão não duplica dano nem altera a contabilidade de M1.
4. Contrato final distingue comparação de frequência, oportunidade candidata e recomendação temporal.

**Testes e evidências:** replay integrado de mecanismo suportado e não suportado;
rastreio evento → estado → achado; reconciliação contábil; saída estruturada e texto correspondente.

**Pendências do Opus:** nenhuma ampliação de mecanismo no fechamento; lacunas seguem a abstenção contratada.

## M5 — Seleção e apresentação ao jogador

Responsabilidades agrupadas: prioridade entre tipos de achado, separação de setup
e execução, conteúdo mínimo, linguagem quantitativa e consistência entre canais.

### M5.1 — Seleção das prioridades — local

**Dependências:** contratos de M3/M4; SPEC Opus da política de seleção.
**Escopo:** ordenar e selecionar achados existentes, preservando a independência de setup.
**Fora de escopo:** novos sinais, renderização e score causal de benefício.

**Critérios de aceite:**

1. Conjuntos de candidatos contratados produzem seleção e ordem esperadas.
2. Seleção não usa ganho aposentado nem compara números de unidades incompatíveis como um score comum.
3. Ausência de achados de execução não elimina informação válida de setup.
4. Prevalência de setup não é convertida em prova de superioridade.
5. Empates seguem regra explícita e produzem resultado independente da ordem de entrada.

**Testes e evidências:** casos mistos de morte, atividade, uptime, recursos, déficit
e oportunidade; setup isolado; empates; candidatos selecionados e omitidos com motivo.

**Pendências do Opus:** precedência entre famílias e política de redução de itens,
sem introduzir benefício/esforço não aprovado.

### M5.2 — Renderização fiel ao contrato — local

**Dependências:** contratos de M3/M4; SPEC Opus B08.
**Escopo:** mensagens Discord e texto CLI, qualificadores e posições verbais.
**Fora de escopo:** seleção de candidatos, novos canais e mudanças no transporte Discord.

**Critérios de aceite:**

1. Afirmações quantitativas correspondem à medida, população, unidade e denominador corretos.
2. “Mais baixo” só aparece quando a ordenação real da própria variável sustenta a frase, incluindo empates.
3. D04 e a fronteira `q <= 1/n` não produzem posição ordinal falsa.
4. Redução de conteúdo preserva qualificadores obrigatórios definidos em B08.
5. Setup permanece apresentado como contexto independente de execução.

**Testes e evidências:** contratos sintéticos com texto esperado semanticamente
revisado; casos ordinais com referência inferior e empate; mensagens nos limites
de tamanho; correspondência frase → campo de origem.

**Pendências do Opus:** B08, conteúdo mínimo por tipo de item, empates e regra de
redução. Não retirar ressalvas apenas para caber mais recomendações.

### M5.3 — Integração e closure da resposta

**Dependências:** M4 fechada; M5.1 e M5.2 revisadas.
**Escopo:** conectar seleção e renderização aos caminhos ativos de resposta.
**Fora de escopo:** infraestrutura de entrega, novos critérios analíticos e expansão de formato.

**Critérios de aceite:**

1. Itens apresentados correspondem à seleção aprovada.
2. CLI e Discord preservam os mesmos fatos e restrições, mesmo com comprimentos diferentes.
3. Resposta final respeita limites vigentes do canal sem truncar qualificadores necessários.
4. Casos sem comparação ou sem ação continuam apresentando conteúdo válido previsto no contrato.

**Testes e evidências:** replays até payloads finais, sem envio externo necessário;
exemplos completos, insuficientes e apenas com setup; verificação dos limites e
revisão semântica dos textos.

**Pendências do Opus:** nenhuma decisão nova; aplicar B08 e política de seleção já especificadas.

## M6 — Validação dos dados derivados e prontidão experimental

Responsabilidades agrupadas: semântica de features, ausências e controlabilidade,
versionamento, validação histórica, materialização e integridade das entradas experimentais.

### M6.1 — Contrato das features remanescentes — local

**Dependências:** contratos finais de medidas de M1–M4; SPEC Opus da parte restante de B07.
**Escopo:** revisar features existentes e implementar sua representação aprovada.
**Fora de escopo:** novas famílias de features, treinamento, campanha e reconstrução histórica.

**Critérios de aceite:**

1. Cada feature ativa possui fonte, unidade, agregação, disponibilidade e classificação de controlabilidade explícitas.
2. Ausências de item level, tier, raid size e demais campos seguem a política especificada, sem zero silencioso.
3. Agregados de uptime e recursos correspondem às unidades e populações contratadas.
4. Features aposentadas não entram em nova matriz experimental por nenhum fallback.
5. Mudança semântica produz versão e identidade de derivado exigidas, preservando resultados históricos.

**Testes e evidências:** matriz feature → contrato → consumidor; vetores calculados
manualmente; casos ausentes/não aplicáveis; fingerprint e rejeição de schemas incompatíveis.

**Pendências do Opus:** imputação, agregação, controlabilidade e dependências por
feature. Label de ranking não representa qualidade de recomendação ou efeito de intervenção.

### M6.2 — Validação integral do corpus e derivados — dados

**Dependências:** M6.1; política Opus de elegibilidade e eventual recálculo.
**Escopo:** censar e validar integralmente o corpus delimitado; produzir novos
derivados somente nos casos previstos pelo contrato aprovado.
**Fora de escopo:** nova coleta, reescrita de brutos, sobrescrita de campanhas e treinamento.

**Critérios de aceite:**

1. Todos os arquivos do manifesto inicial recebem resultado; totais por estado reconciliam com o inventário.
2. Cada exclusão ou impossibilidade de recalcular identifica a dependência ausente.
3. Dados brutos e resultados históricos permanecem preservados.
4. Cada novo derivado, quando previsto, identifica origem, versão e fingerprint.
5. Nenhum registro é semanticamente válido apenas porque o Parquet é legível.

**Testes e evidências:** execução integral sobre o manifesto, incluindo os 1.274
Parquets citados na [política do warehouse](warehouse-policy.md), com diferenças
de inventário explicitadas; relatório por arquivo; hashes antes/depois; verificação
independente dos cálculos em fixtures conhecidas.

**Pendências do Opus:** restante de B07: recalcular, excluir ou manter indisponível
por categoria. Não presumir campanha de backfill irrestrita.

### M6.3 — Integração experimental e closure de prontidão

**Dependências:** M5 fechada; M6.1 e M6.2 revisadas.
**Escopo:** validar entrada do pipeline experimental, splits e identidade dos
artefatos; reconciliar documentação e comportamento do registry.
**Fora de escopo:** treinamento, nova campanha, promoção `READY`, ML no coaching e
revisão das decisões de arquitetura aprovadas.

**Critérios de aceite:**

1. Matriz experimental contém apenas linhas, colunas e versões elegíveis.
2. Splits respeitam separações de identidade, grupo e tempo especificadas, verificadas pelos membros efetivos.
3. Label, origem e limitações permanecem identificados sem interpretação causal.
4. Carregar resultados históricos preserva suas versões; nova materialização não reatribui versões antigas.
5. Verificação do caminho de produção distingue consulta de capacidade no registry de uso de predição e confirma a fronteira de produto vigente.

**Testes e evidências:** construção e validação da matriz sem treinamento;
manifestos dos splits e interseções proibidas; round-trip de artefatos; inspeção e
teste do caminho servido ao jogador.

**Pendências do Opus:** regras de leakage e admissibilidade dos splits, significado
do veredito de prontidão e fronteira de eventual etapa futura de experimentação.
Prontidão dos dados não equivale a aprovação de modelo.

## Dependências, estado e fechamento

| Macro | Sequência | Trabalho local separável | Closure macro |
|---|---|---|---|
| M2 | M2.1 → M2.2 → M2.3 | Seleção testada pela interface de elegibilidade | M2.3 |
| M3 | M3.1 e M3.2 → M3.3 → M3.4 | Cobertura de streams e grades têm revisões próprias | M3.4 |
| M4 | M4.1 → M4.2 → M4.3 | Associação a casts fecha antes da reconstrução temporal | M4.3 |
| M5 | M5.1 e M5.2 → M5.3 | Seleção e renderização testadas com contratos estruturados | M5.3 |
| M6 | M6.1 → M6.2 → M6.3 | Código das features separado da execução sobre o corpus | M6.3 |

A ordem macro M2 → M3 → M4 → M5 → M6 permanece. A independência é de implementação
e revisão por interface; não elimina dependências metodológicas nem autoriza
antecipar um macro. Todas as 16 unidades permanecem não iniciadas; nenhuma SPEC
é declarada pronta por este roadmap.

As seções “Pendências do Opus” são insumos para especificação futura, não decisões
resolvidas. O fechamento local exige revisão independente pelo Astra conforme o
workflow. O fechamento do macro é registrado somente na sua unidade explícita
de integration/closure, após o fechamento individual das unidades anteriores.
