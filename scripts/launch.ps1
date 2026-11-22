param([ValidateSet('start','stop')][string]$Action='start',[int]$Port=8000,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root
$runtime=Join-Path $root '.runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$lockStream=$null
try { $lockStream=[System.IO.File]::Open((Join-Path $runtime 'launch.lock'),'OpenOrCreate','ReadWrite','None') }
catch { Write-Host 'Another start/stop operation is running. Retry when it finishes.'; exit 1 }
$statePath=Join-Path $runtime 'server.json'
function Run([string]$exe,[string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $exe (exit $LASTEXITCODE)" }
}
function FileHash([string]$path) {
    $sha=[System.Security.Cryptography.SHA256]::Create()
    $stream=[System.IO.File]::OpenRead((Join-Path $root $path))
    try { return [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','') }
    finally { $stream.Dispose(); $sha.Dispose() }
}
function Owned($state) {
    $p=Get-CimInstance Win32_Process -Filter "ProcessId=$($state.pid)" -ErrorAction SilentlyContinue
    if ($p -and $p.CommandLine -like '*uvicorn backend.main:app*' -and $p.ExecutablePath -eq $state.python -and $p.CreationDate.ToUniversalTime().Ticks.ToString() -eq $state.created) { return $p }
    return $null
}
try {
    if (Test-Path $statePath) {
        $state=Get-Content -Raw $statePath | ConvertFrom-Json
        $owned=Owned $state
        if ($owned) {
            if ($Action -eq 'stop') { Stop-Process -Id $state.pid; Remove-Item -LiteralPath $statePath; Write-Host 'BeadStudio stopped.'; exit 0 }
            $response=Invoke-WebRequest -UseBasicParsing -Uri "$($state.url)/api/palette" -TimeoutSec 5
            if ($response.StatusCode -eq 200) { if (!$NoBrowser) { Start-Process $state.url }; Write-Host "Already running: $($state.url)"; exit 0 }
        }
        Remove-Item -LiteralPath $statePath
    }
    if ($Action -eq 'stop') { Write-Host 'No managed BeadStudio process is running.'; exit 0 }
    if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be 1024..65535' }
    $probe=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$Port)
    try { $probe.Start() } catch { throw "Port $Port is occupied. Stop its owner or use start.bat -Port 8001. No unrelated process was stopped." } finally { $probe.Stop() }
    $python=Join-Path $root '.venv\Scripts\python.exe'
    if (!(Test-Path $python)) {
        if (Get-Command python -ErrorAction SilentlyContinue) { Run 'python' @('-m','venv','.venv') }
        elseif (Get-Command py -ErrorAction SilentlyContinue) { Run 'py' @('-3','-m','venv','.venv') }
        else { throw 'Install 64-bit Python 3.10+ and Node.js 18+ first.' }
    }
    Run $python @('-c','import sys,struct; assert sys.version_info >= (3,10) and sys.maxsize > 2**32')
    $req=FileHash 'requirements.txt'
    $reqStamp=Join-Path $runtime 'requirements.sha256'
    if (!(Test-Path $reqStamp) -or (Get-Content $reqStamp) -ne $req) {
        Run $python @('-m','pip','install','-r','requirements.txt')
        Set-Content $reqStamp $req
    }
    Run $python @('-c','from backend.v3.core import _kernel; _kernel(); import fastapi,uvicorn')
    $files=@(Get-ChildItem -Path frontend/src,frontend/public -Recurse -File)+@(Get-Item frontend/package.json,frontend/package-lock.json,frontend/index.html,frontend/tsconfig.json,frontend/vite.config.ts)
    $signature=($files | Sort-Object FullName | ForEach-Object { FileHash $_.FullName.Substring($root.Length+1) }) -join ''
    $buildStamp=Join-Path $runtime 'frontend.sha256'
    if (!(Test-Path frontend/dist/index.html) -or !(Test-Path $buildStamp) -or (Get-Content $buildStamp -Raw).Trim() -ne $signature) {
        if (!(Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js 18+ (includes npm), then retry.' }
        Run 'node' @('-e','if(parseInt(process.versions.node)<18)process.exit(1)')
        $lock=FileHash 'frontend/package-lock.json'
        $lockStamp=Join-Path $runtime 'npm.sha256'
        if (!(Test-Path frontend/node_modules) -or !(Test-Path $lockStamp) -or (Get-Content $lockStamp) -ne $lock) {
            Run 'npm.cmd' @('ci','--prefix','frontend'); Set-Content $lockStamp $lock
        }
        Run 'npm.cmd' @('run','build','--prefix','frontend'); Set-Content $buildStamp $signature
    }
    $process=Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'server.log') -RedirectStandardError (Join-Path $runtime 'server-error.log')
    $info=Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)"
    $url="http://127.0.0.1:$Port"
    @{pid=$process.Id;python=$python;created=$info.CreationDate.ToUniversalTime().Ticks.ToString();url=$url} | ConvertTo-Json | Set-Content $statePath
    $ready=$false
    for ($i=0;$i -lt 60;$i++) {
        $process.Refresh(); if ($process.HasExited) { throw 'Server exited. See .runtime/server-error.log' }
        try { $r=Invoke-WebRequest -UseBasicParsing -Uri "$url/api/palette" -TimeoutSec 1; if ($r.StatusCode -eq 200) { $ready=$true; break } } catch {}
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Server readiness timeout. See .runtime/server-error.log' }
    if (!$NoBrowser) { Start-Process $url }
    Write-Host "BeadStudio V3 ready: $url (stop.bat to stop)"
} catch {
    if ($process -and !$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $statePath -ErrorAction SilentlyContinue }
    Write-Host $_ -ForegroundColor Red
    exit 1
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}

