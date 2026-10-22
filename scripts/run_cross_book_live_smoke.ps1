param([switch]$NoBuild)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo
$privateDir = Join-Path $repo '.runtime-check'
New-Item -ItemType Directory -Path $privateDir -Force | Out-Null
$keyPath = Join-Path $privateDir 'cross-book-smoke-keys.json'

if (Test-Path -LiteralPath $keyPath) {
    $keys = Get-Content -LiteralPath $keyPath -Raw | ConvertFrom-Json
} else {
    $keys = [pscustomobject]@{
        bookdistill = [guid]::NewGuid().ToString()
        agent = [guid]::NewGuid().ToString()
    }
    $keys | ConvertTo-Json | Set-Content -LiteralPath $keyPath -Encoding UTF8
}
$env:BOOKDISTILL_API_KEY = $keys.bookdistill
$env:BOOKDISTILL_AGENT_API_KEY = $keys.agent
$env:BOOKDISTILL_AGENT_ENABLED = 'true'
$env:BOOKDISTILL_LLM_PROVIDER = 'demo'
$env:BOOKDISTILL_LLM_MODEL = 'demo-extractive-v1'
$env:BOOKDISTILL_LLM_API_KEY = ''

$project = 'bookdistill-crossbook-smoke'
docker compose -p $project --profile agent config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration failed.' }
$arguments = @('compose', '-p', $project, '--profile', 'agent', 'up', '-d')
if (-not $NoBuild) { $arguments += '--build' }
& docker @arguments
if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed.' }

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health/ready' -TimeoutSec 3
        if ($response) { $ready = $true; break }
    } catch { Start-Sleep -Seconds 3 }
}
if (-not $ready) { throw 'BookDistill API did not become ready within 180 seconds.' }

& (Join-Path $repo '.venv/Scripts/python.exe') (Join-Path $repo 'scripts/smoke_cross_book_live.py') --base-url 'http://127.0.0.1:8000'
if ($LASTEXITCODE -ne 0) { throw 'Cross-book synthetic API smoke failed.' }
Write-Host "Synthetic UI: http://127.0.0.1:3000"
Write-Host "Private test keys: $keyPath"
Write-Host "Result: $repo/artifacts/cross-book-live-smoke/result.json"
