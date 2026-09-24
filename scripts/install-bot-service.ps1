<#
.SYNOPSIS
    Registers the Windows Scheduled Task that launches bot-supervisor.ps1.

.DESCRIPTION
    Idempotent: safe to run again (re-registers with the current parameters
    instead of failing because the task already exists).

    Task Scheduler's own job here is intentionally small -- it starts the
    supervisor once at logon/startup and gives it a *bounded, fixed* restart
    policy in case the supervisor SCRIPT ITSELF crashes (a rare failure mode,
    distinct from the bot crashing, which the supervisor's own backoff/storm
    logic already handles). "If the task is already running, do not start a
    new instance" (-MultipleInstances IgnoreNew) is a second, independent
    layer of duplicate-instance protection on top of the supervisor's own
    OS-level lock (see docs/operations.md).

.PARAMETER TaskName
    Name of the Scheduled Task. Default: BotGitGudSupervisor.

.PARAMETER Trigger
    'Logon' (default): runs only while the current user is logged on. No
    Windows credential is stored anywhere -- simplest, safest default for a
    single-operator machine.
    'Startup': runs whether anyone is logged on or not, for genuine 24/7
    unattended operation. Requires a Windows account password, prompted
    interactively via Get-Credential -- NEVER hardcoded, NEVER written to a
    file by this script.

.EXAMPLE
    .\install-bot-service.ps1
    .\install-bot-service.ps1 -Trigger Startup
#>

[CmdletBinding()]
param(
    [string]$TaskName = 'BotGitGudSupervisor',
    [ValidateSet('Logon', 'Startup')]
    [string]$Trigger = 'Logon'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SupervisorScript = Join-Path $PSScriptRoot 'bot-supervisor.ps1'

if (-not (Test-Path $SupervisorScript)) {
    Write-Error "'$SupervisorScript' nao encontrado."
    exit 1
}

# Idempotente: uma instalacao anterior e substituida, nunca duplicada.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Tarefa '$TaskName' ja existe -- removendo antes de reinstalar."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$SupervisorScript`"" `
    -WorkingDirectory $RepoRoot

if ($Trigger -eq 'Startup') {
    Write-Host 'Disparo AtStartup: execucao sem sessao interativa exige credencial do Windows.'
    Write-Host 'A senha e usada apenas para registrar a tarefa e nao e salva por este script.'
    $cred = Get-Credential -Message 'Credencial do Windows para rodar o supervisor sem logon interativo' -UserName $env:USERNAME
    $trig = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $cred.UserName -LogonType Password -RunLevel Limited
    $settingsCred = $cred
}
else {
    $trig = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settingsCred = $null
}

# ExecutionTimeLimit = zero: e um processo de longa duracao por desenho, o
# Task Scheduler nao pode mata-lo por "demorar demais". RestartCount/Interval
# cobre so o crash do PROPRIO script supervisor (raro); o crash do bot em si
# e tratado pelo backoff/storm do supervisor, nao por isto.
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

if ($settingsCred) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trig `
        -Principal $principal -Settings $settings `
        -Password $settingsCred.GetNetworkCredential().Password | Out-Null
}
else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trig `
        -Principal $principal -Settings $settings | Out-Null
}

Write-Host "Tarefa '$TaskName' registrada (disparo: $Trigger)."
Write-Host 'Use .\start-bot-service.ps1 para iniciar agora, sem esperar o proximo logon/boot.'
