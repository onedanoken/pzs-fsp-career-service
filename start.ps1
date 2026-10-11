param([int]$Port=8000,[string]$BindAddress='127.0.0.1',[switch]$Baseline)
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    if (Test-Path -LiteralPath '.env') {
        foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
            if ($line -match '^\s*(FSP_[A-Z_]+)\s*=\s*(.*?)\s*$') {
                [Environment]::SetEnvironmentVariable($Matches[1],$Matches[2],'Process')
            }
        }
    }
    if ($Baseline) { $env:FSP_ENABLE_ML='0' }
    if (!$env:FSP_ALLOWED_ORIGINS) { $env:FSP_ALLOWED_ORIGINS="http://localhost:$Port,http://127.0.0.1:$Port,http://localhost:5173,http://127.0.0.1:5173" }
    if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run .\setup.ps1 first.' }
    if (!(Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Run npm run build in frontend first.' }
    Write-Host "FSP Career: http://localhost:$Port"
    & '.\.venv\Scripts\python.exe' -m uvicorn backend.main:app --host $BindAddress --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Server exited with an error.' }
} finally { Pop-Location }
