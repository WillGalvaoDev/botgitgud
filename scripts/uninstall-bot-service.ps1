<#
.SYNOPSIS
    Removes the Windows Scheduled Task installed by install-bot-service.ps1.

.DESCRIPTION
    Idempotent: running this when the task isn't installed is a no-op, not an
    error. Does NOT stop a currently-running bot -- run stop-bot-service.ps1
    first if one is running, or the supervisor process (and the bot child it
    is watching) will keep running even after the task definition is gone.

.PARAMETER TaskName
    Must match the name used at install time. Default: BotGitGudSupervisor.
#>

[CmdletBinding()]
param(
    [string]$TaskName = 'BotGitGudSupervisor'
)

$ErrorActionPreference = 'Stop'

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Tarefa '$TaskName' nao esta instalada -- nada a fazer."
    exit 0
}

if ($existing.State -eq 'Running') {
    Write-Warning "Tarefa '$TaskName' esta em execucao. Rode .\stop-bot-service.ps1 primeiro para um encerramento limpo do bot; desinstalar agora so remove a definicao da tarefa, nao para o processo."
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Tarefa '$TaskName' removida."
