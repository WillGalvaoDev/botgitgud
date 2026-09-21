# B6 — supervisão externa ao processo

## O problema

`bot/discord_bot.py::WorkerSupervisor` (RC.5/RC.6) responde "existe um consumidor vivo da fila
*dentro deste processo*?" — mas se o **processo Python inteiro** morrer (crash não capturado,
`taskkill`, queda de energia seguida de boot), nada dentro do processo pode ajudar. O soak de 24h
exige que o bot volte sozinho.

## Solução escolhida: script PowerShell + Task Scheduler como lançador, política em Python

Avaliadas as três opções:

| Opção | Veredito |
|---|---|
| **A — Task Scheduler puro** | Reinício nativo existe, mas as opções de recuperação são fixas (até 3 tentativas, intervalo fixo) — sem backoff exponencial, sem storm-breaker, sem eventos customizados. "Não iniciar se já estiver rodando" é nativo e bom, mas isso sozinho não cobre o resto do gate. |
| **B — Windows Service** | Exige NSSM (dependência externa) ou `pywin32` (dependência nova) para um service de verdade. Contra a preferência explícita por "menor complexidade operacional". |
| **C — wrapper PowerShell supervisionado** | Escolhida. |

**Decisão real, mais precisa que "C sozinha":** a política de restart (backoff, storm-breaker,
duplicação, stop limpo) está inteira em **Python**
(`src/botgitgud/ops/supervisor.py`, `python -m botgitgud.cli supervise`), testável com pytest da
mesma forma que todo o resto do projeto. O PowerShell (`scripts/bot-supervisor.ps1`) é uma casca
fina que só garante o interpretador certo e o diretório certo, e o Task Scheduler só lança essa
casca uma vez no logon/boot — ele não decide nada sobre reinício do bot. Isso evita reimplementar
backoff/storm-detection em PowerShell (frágil, difícil de testar) e evita depender só das opções
fixas do Task Scheduler (insuficientes para os requisitos deste gate).

### Critérios atendidos

| Critério | Como |
|---|---|
| restart automático | `run_supervisor_loop` — qualquer saída sem `stop.request` reinicia |
| start no boot/login | Task Scheduler, disparo `Logon` (padrão) ou `Startup` |
| working directory correto | `spawn_bot_child(cwd=repo_root)`, `repo_root` resolvido de `Path(__file__)`, não do cwd herdado |
| venv correta | `sys.executable` — o supervisor só roda pela venv, então o filho herda o mesmo interpretador |
| stdout/stderr dispensável | tudo relevante já vai para `supervisor.jsonl` / `botgitgud.jsonl` (B5) |
| stop limpo | arquivo de controle, nunca sinal de SO — ver abaixo |
| nenhuma instância duplicada | lock `msvcrt` + PID liveness real, ver abaixo |
| compatível com DuckDB single-owner | o supervisor nunca abre o warehouse; só o filho o abre |
| fácil instalar/remover | 5 scripts idempotentes em `scripts/` |

## Nunca dois processos `serve`

Três camadas independentes:

1. **Lock de instância única do supervisor** — `control/supervisor.lock`, travado com
   `msvcrt.locking()` (verificado empiricamente: uma segunda tentativa de trava, mesmo reabrindo o
   arquivo no mesmo processo, levanta `PermissionError`). Um segundo `supervise` nunca chega a
   tentar spawnar nada — loga `supervisor.duplicate_detected` e sai.
2. **Um único filho rastreado por vez** — o loop guarda exatamente uma variável de child; nunca
   spawna de novo enquanto a anterior não reportou não-viva (`poll()` != None).
3. **PID liveness real na recuperação de boot** — `recover_stale_pid()` limpa `control/bot.pid` na
   subida se o PID referenciado já não existe, para que um restart não confunda um PID reciclado
   com um processo vivo. **Importante, verificado empiricamente:** `os.kill(pid, 0)` **não**
   detecta morte no Windows (não levanta para um PID morto) — a checagem real usa
   `ctypes`/`OpenProcess`+`GetExitCodeProcess` (`pid_is_alive()`).
4. Adicionalmente, o Task Scheduler tem `-MultipleInstances IgnoreNew` como camada extra.

## Comando do filho

```
sys.executable -m botgitgud.cli serve
```

Nunca um caminho fixo, nunca depende de `PATH`: `sys.executable` É a venv, porque o próprio
supervisor só é iniciado por `.venv\Scripts\python.exe` (garantido por `bot-supervisor.ps1`).
`cwd` é explicitamente `repo_root`, calculado por `Path(__file__).resolve().parents[3]` —
independente de onde o supervisor foi lançado.

## Ambiente

O supervisor **não chama `_build_deps()`** — não fala com WCL nem Discord, e portanto não precisa
de nenhuma credencial. O filho (`serve`) carrega `.env`/`DATA_DIR`/credenciais exatamente como
sempre carregou, via `Settings()`. Nada muda aí; o supervisor só decide *quando* relançar o mesmo
comando que já funcionava.

## Restart policy

Qualquer saída do filho **sem** `control/stop.request` presente é tratada como inesperada —
inclusive `exit(0)`: `serve` roda para sempre por desenho, então mesmo uma saída "limpa" que
ninguém pediu é uma anomalia para um serviço de longa duração. A distinção entre "falha" e
"parada pedida" é feita **apenas** pela presença do arquivo de controle, nunca pelo código de
saída.

**Backoff exponencial**, com teto e reset:

```python
delay = min(backoff_base_s * 2 ** (attempt - 1), backoff_max_s)
```

Defaults: base 2s, teto 60s (2, 4, 8, 16, 32, 60, 60, ...). Se o filho ficou de pé por mais que
`backoff_reset_after_s` (padrão 300s) antes de cair de novo, o próximo restart volta ao `attempt 1`
— um crash raro não deve herdar o backoff acumulado de crashes antigos.

**Restart storm**: `storm_threshold` restarts (padrão 5) dentro de `storm_window_s` (padrão 600s)
para o loop inteiro — loga `restart_storm_detected` e `supervisor.stopped(reason=restart_storm)`,
sem mais tentativas. Não consome CPU em loop e exige intervenção humana. O Task Scheduler ainda tem
seu próprio `RestartCount`/`RestartInterval` fixo (3 tentativas, 1 min), mas isso cobre o
**supervisor script crashar**, um caso raro e diferente do bot crashar — o bot crashando é tratado
pela política acima, não pela recuperação do Task Scheduler.

## Clean shutdown — canal de arquivo, nunca sinal de SO

Decisão deliberada: **nenhum `CTRL_BREAK`/`SIGTERM`/sinal de console é enviado no caminho normal.**
Sinalização de console no Windows (grupos de processo, `GenerateConsoleCtrlEvent`) é notoriamente
frágil entre versões e não foi possível verificar contra o bot real nesta tarefa (proibido subir o
bot). Em vez disso:

1. `stop-bot-service.ps1` escreve `control/stop.request` (ver `src/botgitgud/ops/control.py`);
2. o **bot** (dentro do próprio processo `serve`, em `discord_bot.py::_stop_request_watcher`) faz
   polling desse arquivo a cada `bot_stop_poll_interval_s` (padrão 1s) e, ao vê-lo, chama
   `await bot.close()` — de dentro do próprio loop de eventos, onde o websocket e (via o `finally`
   já existente de `_cmd_serve`, de B5) o DuckDB são efetivamente liberados;
3. `bot.run()` retorna normalmente, o `finally` de `_cmd_serve` roda: `process.stopping` →
   `deps.store.close()` / `deps.client.close()` → `process.stopped`;
4. o **supervisor** só espera o PID desaparecer (`_stop_child_gracefully`), sem enviar nada — só
   escalando para `Popen.terminate()` (`TerminateProcess` no Windows) se `stop_grace_s` (padrão
   30s) expirar sem o processo sumir sozinho.

`ChildHandle.terminate()` existe **apenas** como escalada — nunca o caminho normal, documentado
explicitamente no código.

**Contrato do arquivo de controle** (`ops/control.py`): quem lê `stop.request` **nunca** o apaga —
nem o bot, nem o supervisor. Só `start-bot-service.ps1` limpa, e só no início de um `start`
deliberado. Isso evita uma corrida onde o bot já viu o pedido e o apagou antes do supervisor
verificar, ou vice-versa.

### Limitação honesta

O mecanismo de polling do lado do bot (`_stop_request_watcher`) foi testado com um bot **falso**
(`_FakeCloseBot`, `close()` como `AsyncMock` equivalente) — prova que o watcher detecta o arquivo e
chama `bot.close()`. **Não foi verificado contra o `discord.commands.Bot` real** (proibido nesta
tarefa: "não suba o bot real"). O comportamento downstream (`bot.close()` realmente fechando o
websocket e retornando de `bot.run()` de forma limpa) é comportamento documentado do discord.py, não
algo que este projeto reimplementa — mas a cadeia completa `stop.request` → `process.stopped` no
JSONL real só deve ser considerada comprovada após um ensaio real de stop, **antes** do soak de
24h.

## Reboot

Com disparo `Logon` (padrão): o supervisor sobe quando o usuário atual faz logon — **não**
sobrevive a um reboot sem login, e é a opção que **não exige nenhuma credencial armazenada**.

Com disparo `Startup` (`install-bot-service.ps1 -Trigger Startup`): sobe no boot, **sem** exigir
sessão interativa — genuinamente 24/7. Exige uma credencial do Windows para o Task Scheduler poder
rodar "sem logon" (`-LogonType Password`), pedida interativamente via `Get-Credential` no momento
da instalação — **nunca hardcoded, nunca gravada em arquivo por este script.**

## Health check

Sem sistema novo — reusa o que já existe:

```
.venv\Scripts\python.exe -m botgitgud.cli ops-status
```

Confirma `worker_alive`, idade do snapshot (`snapshot_stale`) e estado da fila. `start-bot-service.ps1`
já sugere esse comando ao final.

## PID / session_id / restart — como correlacionar

- **`control/bot.pid`** — PID do filho *atualmente* supervisionado, escrito a cada spawn/restart,
  removido quando esse PID é confirmado morto;
- **`session_id`** (B5) — cada `process.started` em `botgitgud.jsonl` tem um `session_id` novo,
  mintado pelo próprio filho no boot. Um restart produz um `session_id` **diferente** — isso é
  esperado e desejado: cada tentativa é uma sessão auditável distinta;
- **`supervisor.jsonl`** — `child.started`/`child.restarted` trazem `child_pid`, que corresponde ao
  `pid` da primeira linha (`process.started`) daquele mesmo `session_id` em `botgitgud.jsonl`. É
  assim que se reconstrói, depois do fato, "esta sessão específica foi o restart #3, causado por um
  crash às 03:14".

## Log do supervisor — arquivo separado

`data_dir/logs/supervisor.jsonl`, **nunca** `botgitgud.jsonl`. O supervisor roda como processo
Python separado do bot (não há dois writers concorrentes no mesmo arquivo em processo algum), mas
mesmo assim usa um nome diferente — `botgitgud.jsonl` é o sink do `serve`, por contrato. Mesmo
formato JSONL, mesma rotação por tamanho (`enable_file_logging(..., filename=SUPERVISOR_LOG_FILENAME)`,
generalização não-invasiva do sink de B5), mesma redação de segredos por nome de chave.

### Eventos do supervisor

`supervisor.started`, `supervisor.duplicate_detected`, `supervisor.stopped(reason=...)`,
`child.started`, `child.restarted`, `child.exited(exit_code, uptime_s)`,
`child.restart_scheduled(attempt, delay_s)`, `restart_storm_detected(count, window_s)`,
`child.stop_grace_period_exceeded`, `supervisor.stale_pid_cleared`. Todos trazem `pid` (do
supervisor, via o mesmo processador de B5) e `child_pid`/`attempt`/`delay_s`/`exit_code` quando
fazem sentido.

## Segurança

Nenhum script lê o **conteúdo** de `.env` — só verificam que o arquivo existe (`Test-Path`). Os
únicos usos de `Get-Content` são sobre `control/bot.pid` (um número). Nenhuma credencial é
impressa, gravada em log ou embutida em literal nos scripts (verificado por teste — ver abaixo). A
senha pedida por `-Trigger Startup` só existe na memória do processo de instalação, passada
diretamente para `Register-ScheduledTask`.

## Instalação

```powershell
# Instalar (disparo padrão: logon do usuário atual, sem credencial armazenada)
.\scripts\install-bot-service.ps1

# Ou, para 24/7 sem sessão interativa (pede credencial do Windows na hora):
.\scripts\install-bot-service.ps1 -Trigger Startup

# Iniciar agora (sem esperar o próximo logon/boot)
.\scripts\start-bot-service.ps1

# Parar de forma limpa (grace period configurável, default 30s)
.\scripts\stop-bot-service.ps1

# Remover a tarefa (pare antes, se estiver rodando)
.\scripts\uninstall-bot-service.ps1
```

Todos idempotentes: `install` substitui uma instalação anterior em vez de falhar; `uninstall`,
`start` e `stop` reportam estado atual sem erro se já estiverem no estado pedido.

**Nada disto foi executado nesta tarefa** — só validado sintaticamente
(`System.Management.Automation.Language.Parser`) e por teste automatizado.

## Troubleshooting

| Sintoma | Onde olhar |
|---|---|
| Bot não sobe no boot | `Get-ScheduledTask -TaskName BotGitGudSupervisor` — verifique `State`/`LastRunTime`; disparo `Logon` exige sessão ativa |
| Bot cai e não volta | `supervisor.jsonl`: procure `restart_storm_detected` — se presente, o loop parou de propósito, exige `.\start-bot-service.ps1` manual após corrigir a causa |
| Não sei se foi crash ou stop manual | Em `botgitgud.jsonl`: `discord_bot.stop_requested` seguido de `process.stopped` com `exit_code`-equivalente 0 = parada limpa. Ausência de `process.stopped` = crash. Em `supervisor.jsonl`: `child.exited(exit_code=...)` sem `discord_bot.stop_requested` correspondente na mesma janela = crash |
| Dois processos rodando? | Não deveria ser possível (três camadas, ver acima). Se suspeitar: `Get-Process python \| Where-Object {$_.Path -like '*botgitgud*'}` e confira `control/bot.pid` |
| Preciso saber se está saudável agora | `.venv\Scripts\python.exe -m botgitgud.cli ops-status` |

## O que fica para o soak de 24h (não executado nesta tarefa)

1. Rodar `install-bot-service.ps1` (ou `python -m botgitgud.cli supervise` em foreground, para um
   primeiro ensaio supervisionado) de verdade;
2. Provar o ciclo `stop-bot-service.ps1` → `process.stopping`/`process.stopped` reais no JSONL —
   fecha a limitação honesta descrita acima;
3. Provar pelo menos um restart real (matar o processo `serve` manualmente e observar
   `child.exited` → `child.restart_scheduled` → `child.restarted` em `supervisor.jsonl`, e um novo
   `session_id` em `botgitgud.jsonl`);
4. Só então iniciar a janela de 24h propriamente dita.
