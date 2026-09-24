# Workflow operacional das submilestones

Status: **WORKFLOW_READY**. Aprovado pelo usuário em 2026-09-13.

Aplica-se a todas as novas submilestones, incluindo as 16 unidades do
[roadmap M2–M6](methodology-roadmap-m2-m6.md). Este documento governa papéis,
handoffs e fechamento; não altera decisões metodológicas ou de produto.

## Papéis e saídas

| Agente | Responsabilidade | Limites | Saídas permitidas |
|---|---|---|---|
| CLAUDE OPUS — ANALYST / ARCHITECT | Resolve decisões metodológicas pendentes da unidade; produz a SPEC normativa; define escopo, fora de escopo e critérios de aceite | Não implementa código; preserva decisões já aprovadas | `SPEC_READY`, `HUMAN_BLOCK` |
| CLAUDE SONNET — EXECUTOR | Implementa a SPEC vigente; produz testes e evidências | Não redefine decisões metodológicas; não expande o escopo | `IMPLEMENTATION_READY`, `TECH_BLOCK` |
| CODEX ASTRA — INDEPENDENT REVIEWER | Revisa SPEC × implementação × testes × evidências; procura contraexemplos aos contratos | Não implementa correções durante a revisão | `MILESTONE_CLOSED`, `REQUIRES_CHANGES`, `HUMAN_BLOCK` |

As pendências chamadas de “decisões do Astra” na proposta original passam a ser
responsabilidade de especificação do Opus. Astra mantém a revisão independente.
As decisões B03–B09 não são resolvidas por essa mudança de responsabilidade.

## Protocolo e routing

1. Opus resolve as pendências dentro de sua autoridade e entrega a SPEC versionada
   com `SPEC_READY`. Decisão fora da autoridade dos agentes resulta em `HUMAN_BLOCK`.
2. Sonnet implementa a SPEC vigente e entrega testes e evidências com
   `IMPLEMENTATION_READY`. Impedimento técnico é registrado como `TECH_BLOCK`,
   com causa, evidência e trabalho dependente impedido; não autoriza mudar a SPEC.
3. Astra revisa a versão identificada da SPEC, implementação, testes e evidências.
   Entrega `MILESTONE_CLOSED`, `REQUIRES_CHANGES` ou `HUMAN_BLOCK`.
4. `REQUIRES_CHANGES` causado por implementação volta ao Sonnet.
5. Ambiguidade ou contradição normativa da SPEC volta ao Opus. A revisão identifica
   a cláusula conflitante; Sonnet não escolhe uma interpretação metodológica.
6. Decisão fora da autoridade dos agentes resulta em `HUMAN_BLOCK`, independentemente
   da etapa em que foi identificada.

`SPEC_READY` não declara implementação pronta; `IMPLEMENTATION_READY` não declara
fechamento. Um bloqueio registra a causa e o destinatário, sem ampliar autoridade.
No modo manual não há início automático da próxima unidade. No modo autônomo,
autorizado por `START`, o [orquestrador](agent-orchestrator.md) avança automaticamente
após cada fechamento para a próxima unidade elegível de M2–M6. Essa autorização
persiste durante a execução e suas retomadas; não exige aprovação entre etapas.
O modo autônomo para em `HUMAN_BLOCK`, `TECH_BLOCK`, `PROJECT_COMPLETE` ou falha
operacional irrecuperável. Papéis, critérios de closure e routing permanecem iguais.

## Regra de closure

Um novo achado só bloqueia a submilestone quando:

1. viola diretamente um critério de aceite contratado; ou
2. viola uma invariante necessária ao comportamento suportado do produto.

O achado bloqueante identifica o critério ou a invariante, o comportamento suportado
afetado e o contraexemplo reproduzível. Demais achados são registrados como dívida
nas evidências da unidade e não expandem automaticamente o escopo. Não exigir
“robustez completa”, cobertura universal de mecanismos ou melhorias sem contrato.

Cada submilestone é fechada individualmente. O milestone macro fecha somente na
sua unidade explícita de integration/closure, após as unidades locais revisadas:
M2.3, M3.4, M4.3, M5.3 e M6.3, respectivamente. Aprovação documental do roadmap
não fecha nenhuma dessas unidades.

## Unidade de trabalho e evidências

- Cada unidade normalmente cabe em uma rodada de implementação e uma de revisão.
  Correções exigidas seguem o routing; essa expectativa não dispensa critérios.
- Contratos locais são verificáveis por interface. Integração verifica propagação
  e invariantes entre componentes sem redesenhar regras locais ou repetir
  integralmente suas revisões.
- Decisões pendentes são pré-condições da unidade afetada e devem constar da SPEC
  antes da implementação dependente. Limiares e decisões aprovados continuam vigentes.
- O pacote de revisão identifica versão da SPEC e da implementação, matriz
  critério → teste → resultado, entradas e saídas verificáveis, comandos, ambiente,
  falhas/skips e limitações. Critérios verificam fatos; número de testes, snapshots
  e ausência de exceção não substituem prova do comportamento.
- Revisão de alterações de código inclui as verificações pertinentes ao contrato
  e às regras do repositório. Alteração somente documental recebe verificação
  documental; não exige execução artificial de testes de produto.

## Estado inicial

M1: **MILESTONE_CLOSED**, conforme declaração humana de revisão final independente.
O [registro de fechamento](m1-closure.md) distingue essa decisão das revisões anteriores.

M2–M6: decomposição aprovada, unidades não iniciadas, nenhuma declarada `SPEC_READY`.
Correção de contagem na automação: a lista aprovada contém 16 IDs (3+4+3+3+3),
não 17. Nenhum ID, escopo ou dependência foi acrescentado ou removido.
Esta formalização não produz SPEC de M2.1, não inicia M2 e não autoriza treinamento,
campanha ou promoção de ML. Parar após a entrega `WORKFLOW_READY`.
