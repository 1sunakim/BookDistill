param([switch]$Build)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

if (-not $env:BOOKDISTILL_API_KEY -or -not $env:BOOKDISTILL_AGENT_API_KEY) {
    throw 'Set BOOKDISTILL_API_KEY and BOOKDISTILL_AGENT_API_KEY in your private shell environment.'
}
$env:BOOKDISTILL_AGENT_ENABLED = 'true'
docker compose --profile agent config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration failed.' }

$arguments = @('compose', '--profile', 'agent', 'up', '-d')
if ($Build) { $arguments += '--build' }
& docker @arguments
if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed.' }
docker compose --profile agent ps
