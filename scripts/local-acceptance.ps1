<#
.SYNOPSIS
  Start, inspect, or stop the isolated BookDistill local acceptance stack.
.EXAMPLE
  .\scripts\local-acceptance.ps1
.EXAMPLE
  .\scripts\local-acceptance.ps1 -Action Status
.EXAMPLE
  .\scripts\local-acceptance.ps1 -Action Stop
#>
[CmdletBinding()]
param(
    [ValidateSet('Start', 'Status', 'Stop')]
    [string]$Action = 'Start',
    [switch]$NoBuild,
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeArgs = @(
    'compose', '--env-file', '.env.example', '-p', 'bookdistill-toc-acceptance',
    '-f', 'compose.yml', '-f', 'compose.toc-acceptance.yml'
)
$apiUrl = 'http://127.0.0.1:18026/api/v1/health/ready'
$frontendUrl = 'http://127.0.0.1:13026/'
$requiredServices = @('api', 'beat', 'frontend', 'postgres', 'qdrant', 'redis', 'worker')

function Invoke-Compose {
    param([string[]]$Arguments)
    & docker @composeArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed: $($Arguments -join ' ')"
    }
}

function Test-StackReady {
    try {
        $ready = Invoke-RestMethod -Uri $apiUrl -TimeoutSec 5
        $frontend = Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 5
        if ($ready.status -ne 'ok' -or $frontend.StatusCode -ne 200) { return $false }
        foreach ($check in $ready.checks.PSObject.Properties) {
            if ($check.Value -ne 'ok') { return $false }
        }
        $running = @(& docker @composeArgs ps --status running --services)
        if ($LASTEXITCODE -ne 0) { return $false }
        foreach ($service in $requiredServices) {
            if ($service -notin $running) { return $false }
        }
        return $true
    }
    catch {
        return $false
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found. Install and start Docker Desktop first.'
}

Push-Location -LiteralPath $projectRoot
try {
    if ($Action -eq 'Stop') {
        Invoke-Compose @('down')
        Write-Host 'BookDistill local acceptance stack stopped. Data volumes were kept.'
        return
    }

    if ($Action -eq 'Start') {
        Invoke-Compose @('config', '--quiet')
        $upArgs = @('up', '-d')
        if (-not $NoBuild) { $upArgs += '--build' }
        Invoke-Compose $upArgs
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            if (Test-StackReady) { break }
            Start-Sleep -Seconds 3
        }
    }

    if (-not (Test-StackReady)) {
        Invoke-Compose @('ps')
        throw 'BookDistill is not ready. Check: docker compose --env-file .env.example -p bookdistill-toc-acceptance -f compose.yml -f compose.toc-acceptance.yml logs --tail 80 api worker frontend'
    }

    Write-Host "Frontend: $frontendUrl"
    Write-Host "API ready: $apiUrl"
    Write-Host 'Local acceptance access Key: toc-acceptance-key'
    Write-Host 'All required services are running and health checks passed.'
}
finally {
    Pop-Location
}
