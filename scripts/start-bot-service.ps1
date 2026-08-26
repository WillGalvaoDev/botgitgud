<#
.SYNOPSIS
    Clears any pending stop request and starts the supervisor task.

.DESCRIPTION
    Clearing stop.request is exclusively this script's responsibility (see
    src/botgitgud/ops/control.py's module docstring) -- neither the bot nor
    the supervisor ever deletes it themselves, so a stale marker can't
    silently stop a future run and a start always means a genuinely fresh
    intent.

.PARAMETER TaskName
    Must match the name used at install time. Default: BotGitGudSupervisor.

.PARAMETER DataDir
    Repo-relative or absolute path matching DATA_DIR in .env. Default: data
    (the Settings() default). Only needs overriding if .env sets a different
    DATA_DIR.
#>

[CmdletBinding()]
param(
    [string]$TaskName = 'BotGitGudSupervisor',
    [string]$DataDir = 'data'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ResolvedDataDir = if ([System.IO.Path]::IsPathRooted($DataDir)) { $DataDir } else { Join-Path $RepoRoot $DataDir }
$StopRequestPath = Join-Path $ResolvedDataDir 'control\stop.request'

if (Test-Path $StopRequestPath) {
    Remove-Item -Path $StopRequestPath -Force
    Write-Host 'Pedido de parada anterior removido.'
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Error "Tarefa '$TaskName' nao esta instalada. Rode .\install-bot-service.ps1 primeiro."
    exit 1
}

if ($task.State -eq 'Running') {
    Write-Host "Tarefa '$TaskName' ja esta em execucao."
    exit 0
}

Start-ScheduledTask -TaskName $TaskName
Write-Host "Tarefa '$TaskName' iniciada."
Write-Host 'Verifique com: .venv\Scripts\python.exe -m botgitgud.cli ops-status'
