# Journal autônomo da release 1.0

Baseline: HEAD inicial `3f7667f`; `CLAUDE.md` ausente; única mudança inicial era o roadmap não
rastreado. Nenhum push, tag, reescrita, API WCL real ou Phase 4 foi executado.

## R0-01 — BLOCKED_ON_HUMAN

- Status anterior/gap: PENDING; cinco credenciais exigem consoles e comprovação externa.
- Decisão/RED/GREEN/refactor: nenhuma credencial tocada; checklist seguro criado; n/a; n/a; n/a.
- Arquivos/testes/validações: `docs/credential-rotation-checklist.md`; auditoria garante que valores
  não entram no Git.
- Limitações/status final: revogação e autenticação real dependem do humano; BLOCKED_ON_HUMAN.

## R0-02 — DONE

- Status anterior/gap: PENDING; faltava busca de conteúdo histórico.
- Decisão: varrer 91 revisões com padrões de token Discord, Bearer, client secret e OAuth sem
  imprimir correspondências. RED/GREEN/refactor: n/a; zero achados; n/a.
- Arquivos/testes/validações: `.env` em zero árvores; zero padrões; 2 cassetes com `access_token` e
  131 com Authorization, todos redigidos; registro em `docs/desvios.md`.
- Limitações/status final: regex não substitui rotação; DONE.

## R0-03 — DONE

- Status anterior/gap: PENDING; README ausente. Decisão: documentar apenas sistema/CLI reais.
- RED/GREEN/refactor: validação manual de comandos; README criado; n/a.
- Arquivos/testes/validações: `README.md`, `cli --help`; instalação, configuração, arquitetura,
  limites e ausência de ML/Phase 4.
- Limitações/status final: primeiro `serve` real depende R1-01; parte documental DONE.

## R0-04 — DONE

- Status anterior/gap: PENDING; 5/32 campos documentados.
- Decisão/RED/GREEN/refactor: teste de igualdade falharia; template completo; nenhuma mudança de
  default.
- Arquivos/testes/validações: `.env.example`, `test_config.py`; teste compara chaves com
  `Settings.model_fields`, sem valores reais.
- Limitações/status final: nenhuma; DONE.

## R1-01 — BLOCKED_ON_HUMAN

- Status anterior/gap: PENDING; serviço nunca autenticado em Discord/WCL.
- Decisão/RED/GREEN/refactor: não simular smoke; testes determinísticos preparados; n/a.
- Arquivos/testes/validações: checklist em roadmap e rotação; contratos locais cobertos.
- Limitações/status final: requer credenciais rotacionadas, Discord real e pontos autorizados;
  BLOCKED_ON_HUMAN.

## R1-02 — DONE

- Status anterior/gap: PENDING; handlers sem cobertura. Decisão: fakes sem rede/tokens.
- RED/GREEN/refactor: contratos parse/enqueue/notify/worker adicionados; testes verdes; entrega
  assíncrona simplificada para uma mensagem + arquivo.
- Arquivos/testes/validações: `test_discord_bot.py`, `test_worker.py`, `discord_bot.py`; suíte final.
- Limitações/status final: cola de conexão real fica no smoke por design; DONE.

## R2-01 — DONE

- Status anterior/gap: fila enviava relatório completo em chunks. Decisão: `JobOutcome` carrega
  resumo e HTML.
- RED/GREEN/refactor: regressão de contrato escrita; worker renderiza ambos; chunking removido do
  notify.
- Arquivos/testes/validações: `worker.py`, `discord_bot.py`, testes de equivalência com o mesmo
  `AnalysisResult`; HTML e resumo idênticos ao caminho quente.
- Limitações/status final: `render_report` preservado para CLI/golden; DONE.

## R2-02 — DONE

- Status anterior/gap: `backfill` público sempre falhava. Decisão: busca encontrou somente stub;
  planner/checkpoints pertencem a discovery.
- RED/GREEN/refactor: parser aceitava comando quebrado; comando/função removidos; nenhum código de
  discovery tocado.
- Arquivos/testes/validações: `cli.py`, `test_cli.py`, `--help`, exit 2 para comando removido.
- Limitações/status final: nenhuma feature nova inventada; DONE.

## R2-03 — DONE

- Status anterior/gap: Top 3 vazio afirmava ausência de problema. Decisão: corrigir texto, não
  ranking.
- RED/GREEN/refactor: teste rejeita frase antiga; texto/HTML explicitam somente ganho
  quantificável; snapshot atualizado deliberadamente.
- Arquivos/testes/validações: `top_actions_text.py`, `html_report.py`, testes e golden.
- Limitações/status final: seis categorias continuam sem estimativa inventada; DONE.

## R2-04 — DONE

- Status anterior/gap: golden usa coorte truncada. Decisão: manter como guarda estrutural e marcar
  números do gap não autoritativos; não regravar nem consumir API.
- RED/GREEN/refactor: property tests já detectam erro matemático; golden verde; n/a.
- Arquivos/testes/validações: D-30/README; golden atualizado apenas pela mudança honesta de texto.
- Limitações/status final: realismo da seção exige execução ao vivo autorizada; DONE.

## R2-05 — DONE

- Status anterior/gap: D-26/27/28 não formalmente aceitas. Decisão: ACCEPTED_V1_DEBT.
- RED/GREEN/refactor: verificação mostra que fallback altera rótulo MAJOR/MINOR, enquanto o achado
  depende do alinhamento observado; testes existentes provam precedência/fallback; sem fonte
  inventada.
- Arquivos/testes/validações: `docs/desvios.md`, README, testes cadence/comparison/profile.
- Limitações/status final: nomes de nós, pool posicional e cooldown base permanecem limitações;
  DONE.

## R3-01 — DONE

- Status anterior/gap: runbook não conseguia inspecionar fila offline nem recuperar `running`.
- Decisão: somente `ops-status` read-only e `recover-jobs`.
- RED/GREEN/refactor: testes de ausência, contagens e transição; comandos implementados; lógica em
  módulo pequeno.
- Arquivos/testes/validações: `cli_ops.py`, `cli.py`, `test_cli_ops.py`, help/exit codes.
- Limitações/status final: saldo live não é consultado para evitar API implícita; DONE.

## R3-02 — DONE

- Status anterior/gap: 172 MB sem política. Decisão: três classes mais vazio/reservado, cópia
  local proporcional.
- RED/GREEN/refactor: n/a; inventário real documentado; n/a.
- Arquivos/testes/validações: `docs/warehouse-policy.md`, 15 tabelas e 741 Parquets classificados.
- Limitações/status final: destino permanente é humano; desenho DONE.

## R3-03 — DONE

- Status anterior/gap: backup não testado. Decisão: ensaio em backup e restore temporários
  distintos, bot parado.
- RED/GREEN/refactor: n/a; cópia/restore reais; n/a.
- Arquivos/testes/validações: 172.503.638 bytes; backup 1,066 s; restore 0,438 s; 15/15 tabelas e
  contagens iguais; seis irreproduzíveis presentes; 741 Parquets.
- Limitações/status final: destino permanente pendente do humano, ensaio automatizável DONE.

## R3-04 — DONE

- Status anterior/gap: runbook ausente. Decisão: duas passadas; lacunas originaram R3-01.
- RED/GREEN/refactor: comandos inexistentes marcados e então implementados; 10 procedimentos
  finais usam comandos reais; n/a.
- Arquivos/testes/validações: `docs/runbook.md`, `ops-status`, `recover-jobs`, `--help` executados.
- Limitações/status final: observação Discord/WCL pertence R1-01; parte automatizável DONE.

## R4-01 — DONE

- Status anterior/gap: dez arquivos >300 sem decisão. Decisão: aceitar todos com desvio; os oito
  experimentais estão fora da v1.0 e os dois de produção são classes coesas, já com auxiliares.
- RED/GREEN/refactor: suíte baseline; nenhuma divisão sem benefício; zero churn.
- Arquivos/testes/validações: triagem por arquivo em `docs/desvios.md`; suíte final.
- Limitações/status final: convenção volta a ser avaliada quando houver mudança funcional; DONE.

## R4-02 — PENDING_DEPENDENCY

- Status anterior/gap: PENDING; depende R0-01 e R1-01. Parte mecânica executada na validação final.
- Decisão/RED/GREEN/refactor: não promover RC sem gates humanos; suíte/linters coletados; n/a.
- Arquivos/testes/validações: registrados abaixo e em `docs/progresso.md`.
- Limitações/status final: credenciais/smoke; PENDING_DEPENDENCY.

## R5-01 — PENDING_DEPENDENCY

- Status anterior/gap: PENDING; soak real ≥24h. Decisão: não simular. Testes/arquivos: n/a.
- Limitações/status final: depende RC e observação humana; PENDING_DEPENDENCY.

## R5-02 — PENDING_DEPENDENCY

- Status anterior/gap: PENDING; três análises reais. Decisão: zero WCL sem autorização.
- Testes/arquivos/limitações/status final: depende RC, Discord e inspeção humana;
  PENDING_DEPENDENCY.

## R5-03 — PENDING_DEPENDENCY

- Status anterior/gap: PENDING; bump/commit só após soak e análises. Nenhuma mudança de versão.
- Testes/arquivos/limitações/status final: depende R5-01/02 e aprovação; PENDING_DEPENDENCY.

## R5-04 — PENDING_DEPENDENCY

- Status anterior/gap: PENDING; tag explicitamente humana. Nenhuma tag criada.
- Testes/arquivos/limitações/status final: depende commit aprovado; PENDING_DEPENDENCY.

## RC-PYRIGHT — tornar o gate Pyright reproduzível (DONE)

- Status anterior/gap: lacuna de Release Candidate descoberta na validação do checkpoint
  `eccad6a`. O gate do roadmap está escrito como `pyright src tests`, mas esse comando retornava
  **416 erros**; só passava com a flag manual `--pythonpath .venv/Scripts/python.exe`. Registrar
  R4-02 como mecanicamente verde nessas condições seria falso: o comando documentado não
  reproduzia o resultado documentado.
- Causa raiz: `[tool.pyright]` não declarava o ambiente virtual, então o Pyright caía no
  interpretador do PATH — sem as dependências e sem o install editável. Não era dívida de tipagem:
  os 416 diagnósticos eram 399 `reportMissingImports` (inclusive `httpx`, importado desde a T0.1)
  mais 17 achados em arquivos intocados por aquele diff.
- Decisão/RED/GREEN/refactor: RED = `pyright src tests` com 416 erros; GREEN = `venvPath = "."` e
  `venv = ".venv"` em `[tool.pyright]`; refactor = nenhum. Nenhum código de produção, dependência
  ou versão foi tocado.
- Arquivos/testes/validações: `pyproject.toml`. Verificado em três invocações — `pyright src tests`,
  `pyright` sem argumentos e `pyright.exe src tests` — todas 0 erros / 0 warnings, sem flag.
- Limitações/status final: não foi adicionado teste automatizado. Um teste que apenas afirmasse a
  presença das duas chaves no TOML seria tautológico, e reproduzir a falha real exigiria invocar o
  Pyright de dentro do pytest — caro e redundante com o próprio gate. DONE.

## Validação final automatizada

- Coleta: 931 testes.
- `pytest -q`: 931 passaram; 2 snapshots passaram; apenas depreciações conhecidas do discord.py
  sob Python 3.14.
- `ruff check .`: verde.
- `ruff format --check .`: 174 arquivos formatados.
- `pyright src tests`: 0 erros, 0 warnings (executado com `.venv` ativada).
- CLI: `--help` e `ops-status` exit 0; `backfill` removido retorna exit 2 pelo argparse.
- `git diff --check`: sem erros (somente avisos informativos LF→CRLF do Git no Windows).
- HEAD permaneceu `3f7667f`; versão permaneceu `0.1.0`; nenhum commit, push, tag ou remote criado.
