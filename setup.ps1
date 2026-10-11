param([switch]$WithModels,[switch]$WithTests)
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
    }
    if ($WithModels) {
        & '.\.venv\Scripts\python.exe' -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
        if ($LASTEXITCODE -ne 0) { throw 'Torch installation failed.' }
        & '.\.venv\Scripts\python.exe' -m pip install -r requirements-ml.txt
        if ($LASTEXITCODE -ne 0) { throw 'Optional ML dependencies failed.' }
    }
    $requirementsFile = if ($WithTests) {'requirements-dev.txt'} else {'requirements.txt'}
    & '.\.venv\Scripts\python.exe' -m pip install -r $requirementsFile
    if ($LASTEXITCODE -ne 0) { throw 'Python dependencies failed.' }
    Push-Location frontend
    try {
        $env:NODE_TLS_REJECT_UNAUTHORIZED='1'
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
    if ($WithModels) {
        & '.\.venv\Scripts\python.exe' scripts/download_models.py
        if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Baseline search is still available.' }
    }
    Write-Host 'Ready. Run .\start.ps1 and open http://localhost:8000'
} finally { Pop-Location }
