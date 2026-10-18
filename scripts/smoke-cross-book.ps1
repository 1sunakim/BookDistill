$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath (Join-Path $repo 'integrations/deepagents')

uv sync --extra test --locked
if ($LASTEXITCODE -ne 0) { throw 'Agent dependency sync failed.' }
uv run --extra test pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Agent tests failed.' }
uv run bookdistill-agent 'synthetic research question' --demo
if ($LASTEXITCODE -ne 0) { throw 'Deep Agents synthetic demo failed.' }

Set-Location -LiteralPath $repo
docker compose --profile agent config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration failed.' }
Write-Host 'Synthetic Agent checks and Compose configuration passed.'
