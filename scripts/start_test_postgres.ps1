param([string]$BinDirectory=(Join-Path $PSScriptRoot '../tmp/postgres-runtime/pgsql/bin'),[int]$Port=55432)
$ErrorActionPreference='Stop'
$taskCache=Join-Path $env:LOCALAPPDATA 'Codex/FSPStage03'
$taskData=Join-Path $taskCache 'pgdata'
$taskConfig=Join-Path $PSScriptRoot '../tmp/postgres-test.json'
$taskPasswordFile=Join-Path $taskCache 'pg-password.txt'
New-Item -ItemType Directory -Force -Path $taskCache | Out-Null
if (!(Test-Path -LiteralPath $taskPasswordFile)) {
    $taskBytes=New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($taskBytes)
    [System.IO.File]::WriteAllText($taskPasswordFile,[Convert]::ToBase64String($taskBytes))
}
$taskBin=(Resolve-Path -LiteralPath $BinDirectory).Path
if ($taskBin -match '[^\x00-\x7F]') {
    # initdb on Windows embeds runtime paths in SQL using the system code page.
    $taskRuntime=Join-Path $taskCache 'pgsql'
    if (!(Test-Path -LiteralPath (Join-Path $taskRuntime 'bin/initdb.exe'))) { Copy-Item -LiteralPath (Split-Path $taskBin) -Destination $taskRuntime -Recurse }
    $taskBin=Join-Path $taskRuntime 'bin'
}
if (!(Test-Path -LiteralPath (Join-Path $taskData 'PG_VERSION'))) {
    & (Join-Path $taskBin 'initdb.exe') -D $taskData -U fspstage03 -E UTF8 --locale=C -A scram-sha-256 --pwfile=$taskPasswordFile
    if ($LASTEXITCODE -ne 0) { throw 'Test PostgreSQL initialization failed' }
}
if (!(Test-Path -LiteralPath (Join-Path $taskData 'postmaster.pid'))) {
    $taskArguments=@('start','-D',('"'+$taskData+'"'),'-l',('"'+(Join-Path $taskCache 'postgres.log')+'"'),'-o',('"-h 127.0.0.1 -p '+$Port+'"'))
    $taskProcess=Start-Process -FilePath (Join-Path $taskBin 'pg_ctl.exe') -ArgumentList $taskArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskCache 'pg-start.log') -RedirectStandardError (Join-Path $taskCache 'pg-start-error.log')
}
$taskReady=$false
for ($taskTry=0; $taskTry -lt 20; $taskTry++) {
    & (Join-Path $taskBin 'pg_isready.exe') -h 127.0.0.1 -p $Port -t 1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $taskReady=$true; break }
    Start-Sleep -Milliseconds 500
}
if (!$taskReady) { throw 'Test PostgreSQL is not ready; inspect private runtime logs.' }
$taskSettings=@{host='127.0.0.1';port=$Port;user='fspstage03';password_file=$taskPasswordFile;data_directory=$taskData;bin_directory=$taskBin}
$taskSettings | ConvertTo-Json | Set-Content -LiteralPath $taskConfig -Encoding UTF8
Write-Host "Test PostgreSQL is running on localhost:$Port; credentials are private."
