<#
.SYNOPSIS
    Requests a clean shutdown of the bot and waits for it to actually happen.

.DESCRIPTION
    Never sends an OS signal or force-kills as the first move. It writes
    control/stop.request (the same file the supervisor and the bot both poll
    -- see src/botgitgud/ops/control.py) and waits: the bot process notices
    it, calls bot.close() from inside its own event loop, and exits on its
    own -- that graceful path is what produces the process.stopping /
    process.stopped lines in botgitgud.jsonl. Only past GraceSeconds without
    the PID disappearing does this escalate to Stop-Process -Force, and even
    then only against that one PID, never a blind taskkill.

    The supervisor sees the same stop.request and does not restart the child
    once it exits -- see run_supervisor_loop in src/botgitgud/ops/supervisor.py.

.PARAMETER TaskName
    Must match the name used at install time. Default: BotGitGudSupervisor.

.PARAMETER DataDir
    Must match DATA_DIR in .env. Default: data.

.PARAMETER GraceSeconds
    How long to wait for the bot's own clean shutdown before escalating.
    Default: 30 (matches SUPERVISOR_STOP_GRACE_S's default in Settings).
#>

[CmdletBinding()]
param(
    [string]$TaskName = 'BotGitGudSupervisor',
    [string]$DataDir = 'data',
    [int]$GraceSeconds = 30
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ResolvedDataDir = if ([System.IO.Path]::IsPathRooted($DataDir)) { $DataDir } else { Join-Path $RepoRoot $DataDir }
$ControlDir = Join-Path $ResolvedDataDir 'control'
$StopRequestPath = Join-Path $ControlDir 'stop.request'
$PidPath = Join-Path $ControlDir 'bot.pid'

New-Item -ItemType Directory -Path $ControlDir -Force | Out-Null
# Idempotente: um segundo pedido de parada enquanto o primeiro ainda esta
# sendo atendido so re-toca o arquivo, nunca falha.
New-Item -ItemType File -Path $StopRequestPath -Force | Out-Null
Write-Host 'Pedido de parada registrado.'

if (-not (Test-Path $PidPath)) {
    Write-Host 'Nenhum bot.pid encontrado -- o bot pode ja estar parado, ou nunca ter subido.'
    exit 0
}

$botPid = (Get-Content $PidPath -Raw).Trim()
if (-not $botPid) {
    Write-Host 'bot.pid esta vazio -- nada para aguardar.'
    exit 0
}

Write-Host "Aguardando o processo $botPid encerrar sozinho (ate ${GraceSeconds}s)..."
$deadline = (Get-Date).AddSeconds($GraceSeconds)
$stillAlive = $true
while ((Get-Date) -lt $deadline) {
    $proc = Get-Process -Id $botPid -ErrorAction SilentlyContinue
    if (-not $proc) {
        $stillAlive = $false
        break
    }
    Start-Sleep -Seconds 1
}

if ($stillAlive -and (Get-Process -Id $botPid -ErrorAction SilentlyContinue)) {
    Write-Warning "Processo $botPid nao encerrou dentro do prazo -- escalando para termino forcado."
    Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
    Write-Host 'Termino forcado enviado. Isto NAO deixa as linhas process.stopping/process.stopped no log -- e o sinal de que o caminho normal falhou.'
}
else {
    Write-Host 'Bot encerrado normalmente.'
}

# O supervisor decide sozinho nao reiniciar (ve o mesmo stop.request) e sai do
# proprio loop; nao forcamos a tarefa do Task Scheduler aqui -- apenas
# reportamos o estado atual dela.
Start-Sleep -Seconds 2
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Estado da tarefa '$TaskName': $($task.State)"
}
