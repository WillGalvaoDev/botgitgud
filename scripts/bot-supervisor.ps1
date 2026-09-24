<#
.SYNOPSIS
    Launches the Python process supervisor (`python -m botgitgud.cli supervise`)
    with the correct interpreter and working directory. This is the script
    Task Scheduler's Action points at -- see install-bot-service.ps1.

.DESCRIPTION
    Thin wrapper only: all restart/backoff/storm/clean-stop policy lives in
    src/botgitgud/ops/supervisor.py (Python), covered by pytest. This script's
    only job is to guarantee the venv interpreter and the repo root are used,
    independent of how or from where it was launched -- never relies on PATH.

    See docs/operations.md for the full design.

.NOTES
    Does not read or print any secret. .env is loaded by Python's own
    Settings() the same way every other command already does; this script
    never opens or parses it.

    ASCII only, deliberately: PowerShell 5.1 reads a .ps1 without a BOM using
    the system codepage, and accented characters written as UTF-8 can corrupt
    string-literal parsing on some locales. Every script in scripts/ follows
    this rule, matching the accent-free convention already used for Portuguese
    comments throughout src/.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    Write-Error "Interpretador da venv nao encontrado em '$Python'. Crie a venv (python -m venv .venv) e instale as dependencias antes de instalar o servico."
    exit 1
}

$EnvFile = Join-Path $RepoRoot '.env'
if (-not (Test-Path $EnvFile)) {
    Write-Error "'$EnvFile' nao existe. Copie .env.example para .env e preencha as credenciais antes de subir o supervisor."
    exit 1
}

Set-Location $RepoRoot

# `supervise` nunca chama a WCL nem o Discord -- so relanca `serve` como
# subprocesso. O proprio `serve`, quando lancado, carrega .env normalmente.
& $Python -m botgitgud.cli supervise
exit $LASTEXITCODE
