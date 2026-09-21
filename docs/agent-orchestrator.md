# Agent orchestrator — M2–M6

Implementação operacional do [workflow](milestone-workflow.md), sem alteração das
decisões metodológicas. A construção usa adapters/fakes e repositórios temporários:
não produz SPEC de M2.1 e não inicia M2.

## START, estado e saída

Executar da raiz, com o ambiente de desenvolvimento já instalado:

```powershell
.venv/Scripts/python.exe -m botgitgud.orchestrator doctor
.venv/Scripts/python.exe -m botgitgud.orchestrator status
```

`doctor` verifica interfaces, autenticação e catálogo local sem gerar respostas de
modelos. `status` retorna `NOT_STARTED` antes do START; não cria uma execução.

Somente quando o usuário autorizar a execução autônoma:

```powershell
.venv/Scripts/python.exe -m botgitgud.orchestrator start --authorize START
```

Esse comando executa o ciclo completo, sem aprovação entre etapas. Não foi
executado durante a construção. A retomada de uma execução já autorizada é:

```powershell
.venv/Scripts/python.exe -m botgitgud.orchestrator resume
.venv/Scripts/python.exe -m botgitgud.orchestrator audit
```

O controlador para somente em `HUMAN_BLOCK`, `TECH_BLOCK`, `PROJECT_COMPLETE` ou
`OPERATIONAL_FAILURE`. `PROJECT_COMPLETE` exige M6.3 fechada e todos os IDs anteriores
fechados. A lista aprovada possui **16 IDs**, não 17: o erro aritmético da formalização
anterior foi corrigido sem modificar nenhuma unidade ou dependência.

O estado fica em `<git-common-dir>/milestone-orchestrator/state.sqlite3`. Há uma
lease de SO comum a todos os worktrees desse repositório; uma segunda etapa não
obtém acesso concorrente. Outro diretório de estado não pode iniciar uma execução
paralela para o mesmo repositório.

## Isolamento e publicação local

START captura os arquivos Git existentes, inclusive alterações não commitadas,
em um checkout privado. Não reverte, faz stash nem incorpora essas alterações
como evidência de implementação nova. Credenciais `.env`, configurações pessoais,
warehouse e `data/` não são copiados. Templates `.env.example` não são credenciais.

Cada tentativa recebe um novo checkout privado, sem remotos, a partir do último
estado aceito. O resultado cumulativo fica no caminho `workspace` informado pelo
comando, com commit e árvore locais identificados. O checkout original permanece
preservado; integração/publicação de volta nele não é automática. Não há comando
de push ou merge remoto no controlador.

Os modelos não escrevem diretamente: Opus devolve o contrato da SPEC; Sonnet
devolve conteúdos UTF-8 completos em `files`; o controlador valida caminhos e
escopo antes de aplicá-los. Opus e Sonnet têm apenas Read/Glob/Grep no CLI. Astra
usa sandbox read-only, shell desabilitado e não pode devolver arquivos de correção.
Alteração direta detectada na árvore da tentativa bloqueia seu aproveitamento.

O executor só pode escrever arquivos/prefixos declarados pela SPEC dentro de
`src/botgitgud/`, `tests/`, `docs/` ou `README.md`. Governança, SPECs, evidências
históricas de M1, configuração de gates e o próprio controlador são protegidos,
inclusive contra aliases de caixa no Windows. Links/reparse points, traversal,
caminhos absolutos e nomes ambíguos de plataforma são rejeitados.

O protocolo atual transporta arquivos de texto completos; não executa patches como
shell e não aceita comandos arbitrários dos agentes. Necessidade de operação não
suportada deve resultar em bloqueio, não em fallback para execução irrestrita.
Dados reais necessários a M6 não são substituídos por fixtures: se estiverem ausentes
do contexto isolado, a unidade dependente deve bloquear e registrar a fonte necessária.
O controlador não autoriza treinamento, promoção ou campanhas de ML.

## Protocolo e transições

O schema está em `src/botgitgud/orchestrator/protocol.py`. A resposta é exatamente
um objeto JSON, com campos obrigatórios e sem campos extras ou chaves duplicadas:

- comuns: `status`, `unit`, `spec_sha`, `reason`;
- SPEC_READY: `spec`, `criteria` (3–7 IDs/textos), `write_paths`;
- IMPLEMENTATION_READY: `files` (path/content), `evidence`;
- REQUIRES_CHANGES: `route` e `findings`;
- MILESTONE_CLOSED: apenas findings de dívida, se houver;
- campos não usados: string vazia ou lista vazia, conforme o schema.

Só os tokens permitidos ao papel são aceitos. Texto livre nunca determina uma
transição. Unidade ou hash de SPEC divergente, payload contraditório, campo faltante,
erro do CLI ou envelope incompleto falham fechados. `spec_sha` em uma resposta do
Opus identifica a versão anterior (vazio na primeira SPEC); o controlador calcula
o hash da nova versão a partir de markdown, critérios e escopo de escrita.

SPECs são imutáveis em `docs/submilestones/<ID>/spec-vNNN.md` e `.json` nos checkouts
privados. Correção normativa cria uma nova versão, invalida as evidências anteriores
para novo fechamento e retorna ao executor. Sonnet não altera uma SPEC vigente.

| Etapa/resultado | Próxima etapa |
|---|---|
| Opus → SPEC_READY | Sonnet, com SPEC versionada |
| Opus → HUMAN_BLOCK | Parada |
| Sonnet → IMPLEMENTATION_READY | Validação de delta/evidência + gates; Astra somente se aprovados |
| Sonnet → TECH_BLOCK | Parada |
| Sonnet → HUMAN_BLOCK | Parada; motivo original preservado para decisão fora da autoridade |
| Astra → REQUIRES_CHANGES / implementation | Sonnet, com review anterior |
| Astra → REQUIRES_CHANGES / specification | Opus, com cláusula/contraexemplo do review |
| Astra → HUMAN_BLOCK | Parada |
| Astra → MILESTONE_CLOSED | Persistir closure individual; selecionar próxima elegível |
| M6.3 → MILESTONE_CLOSED | PROJECT_COMPLETE |

Um finding bloqueante identifica critério contratado ou invariante necessária,
referência e contraexemplo. O controlador valida a estrutura e os IDs; a validade
metodológica do achado continua sendo responsabilidade do revisor. Dívida não
bloqueia e permanece no resultado auditado.

## Gates e evidências

O controlador exige um delta novo de implementação/teste na tentativa, mais a
cobertura de todos os critérios da SPEC por evidência contendo `criterion`, `test`
(node ID pytest exato), `inputs`, `expected`, `observed` e `artifact` não vazio.
Essas observações são alegações do executor a revisar; não substituem testes.

Os comandos mecânicos são fixos, definidos em `gates.py`, e executados no checkout
da tentativa com o interpretador de desenvolvimento:

1. Ruff check;
2. Ruff format --check;
3. Pyright com contexto explícito do checkout e ambiente instalado;
4. pytest offline (`not network`), com JUnit e plugins de teste necessários.

Não se executam comandos de shell sugeridos em respostas. Os gates recebem ambiente
sem credenciais de provedores/bot, DATA_DIR e diretório pessoal isolados. Não há
acesso intencional ao Store de produção. Testes continuam sendo código executável;
essa configuração não é uma sandbox de SO para código Python adversarial.

Todos os quatro exits precisam ser zero. Cada node citado na aceitação precisa
constar como aprovado no JUnit produzido pelo controlador; skip, ausência de teste
ou um arquivo JUnit entregue pelo agente não satisfazem isso. Artefatos recebem
hashes e são vinculados à árvore verificada. Antes do closure, os hashes e a árvore
são conferidos novamente.

Falhas normais de lint/tipos/testes, com resultados mecânicos íntegros, registram
`GATES_REJECTED` e retornam automaticamente ao Sonnet com os logs; não são
`IMPLEMENTATION_READY` aceito. Evidência ausente/corrompida, execução vazia ou
incapacidade de executar um gate gera `TECH_BLOCK`. O limite operacional é 24
tentativas por unidade; esgotamento gera `TECH_BLOCK`, nunca fechamento presumido.

Os prompts usam a unidade do roadmap, governança, contratos M0/M1, SPEC vigente,
arquivos do repositório e review anterior quando necessário. Astra inicia sessão
nova, recebe fontes, testes do escopo e logs dos gates, sem histórico de conversa
ou sessão dos outros agentes. Contexto excessivo bloqueia; não há truncamento
silencioso nem resumo inventado pelo controlador.

## Interrupções e auditoria

SQLite com transações e synchronous FULL mantém estado e evento juntos. Eventos
formam uma cadeia de hashes e incluem papel, tentativa, resultado, SPEC, commit,
árvore, prompt e resposta. Os diretórios `attempt-NNNNN` preservam transportes e
gates; os resultados de revisão guardam dívidas e contraexemplos.

Uma retomada entre etapas continua do último checkpoint, sem repetir Opus ou
Sonnet já aceitos. Uma interrupção com tentativa em voo resulta em `TECH_BLOCK`:
nenhum resultado parcial é promovido nem a chamada é repetida cegamente. Não há
garantia de uma única cobrança em falha de transporte de um provedor; não se
infere a conclusão remota a partir do encerramento do processo local.

Após resolver um bloqueio e verificar/encerrar eventual processo interrompido,
a recuperação humana explícita registra a resolução e volta ao último checkpoint
aceito, preservando tentativas rejeitadas na auditoria:

```powershell
.venv/Scripts/python.exe -m botgitgud.orchestrator retry --resolution "Causa resolvida e processos anteriores verificados"
```

`resume` sozinho não remove bloqueios. `retry` não pode fechar unidade ou pular
dependência. Alterações metodológicas continuam passando pelo Opus e pelo workflow.

## Interfaces efetivamente descobertas

Verificação local de 2026-09-13, sem geração:

- Claude Code `2.1.270`, autenticado; aliases `opus` e `sonnet` documentados pelo
  `--help`; print, safe-mode, read tools, json-schema e structured_output disponíveis.
- Codex CLI `0.154.0`, autenticado; `gpt-6-astra` presente no catálogo local;
  exec, output-schema, output-last-message, JSONL, read-only, ephemeral,
  ignore-user-config, ignore-rules e controles de shell disponíveis.
- Launchers nativos foram resolvidos dos wrappers npm no Windows; prompts entram
  por stdin. Não há interpolação de prompt/schema em PowerShell ou cmd.exe.

Descoberta e autenticação não provam disponibilidade futura do modelo ou quota.
Mudança de interface, negativa do provedor ou saída fora do contrato resulta em
`TECH_BLOCK`, com stdout/stderr da tentativa quando disponíveis. Não há fallback
automático para outro modelo.

Fontes de transporte: [Claude Code programático](https://code.claude.com/docs/en/headless)
e [Codex não interativo](https://developers.openai.com/codex/noninteractive/),
confrontadas com o help instalado. O adapter mantém os formatos específicos fora
da máquina de estados.

## Verificação offline

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_orchestrator.py
.venv/Scripts/python.exe -m ruff check src/botgitgud/orchestrator tests/unit/test_orchestrator.py
.venv/Scripts/python.exe -m ruff format --check src/botgitgud/orchestrator tests/unit/test_orchestrator.py
.venv/Scripts/python.exe -m pyright src/botgitgud/orchestrator tests/unit/test_orchestrator.py
```

O arquivo de testes cobre caminho feliz completo, loops Sonnet/Opus, bloqueios,
protocolo inválido/contraditório, retomada, interrupção em voo, dependências,
ausência de delta/evidência/gates, integridade, escrita proibida, lease, envelopes
reais simulados dos CLIs e M6.3 → PROJECT_COMPLETE. Também executa os quatro gates
reais em um repositório mínimo temporário. Nenhum desses testes chama modelos.

Resultado da construção: **ORCHESTRATOR_READY**. Verificação final: 48 testes
aprovados, zero falhas/erros/skips; Ruff (lint/formato) e Pyright aprovados.
Os [resultados e hashes](orchestrator-validation.json) e o
[JUnit](orchestrator-validation.xml) registram essa rodada. A suíte completa do
produto não foi reexecutada; os testes desta entrega são do controlador isolado.
O projeto permanece `NOT_STARTED` no orquestrador; M2 não foi iniciada.
