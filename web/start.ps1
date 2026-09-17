param(
    [int]$Port = 8765,
    [string]$Python = "python",
    [switch]$NoBrowser,
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$webRoot = $PSScriptRoot
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port must be between 1024 and 65535.' }
Push-Location $webRoot
try {
    # Avoid embedded quotes: Windows PowerShell 5.1 strips them in native arguments.
    $version = & $Python -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'
    if ($LASTEXITCODE -ne 0 -or $version -ne '3.12') { throw 'Python 3.12 is required. Use -Python C:\path\to\python.exe' }
    if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
    }
    $runtime = Join-Path $webRoot '.venv\Scripts\python.exe'
    & $runtime -m pip install -r requirements.lock --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if (!$SkipBuild) {
        Push-Location (Join-Path $webRoot 'frontend')
        try {
            & npm.cmd ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
    if (!(Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'No frontend build. Run without -SkipBuild.' }
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try { $listener.Start() } catch { throw "Port $Port is in use. Choose another -Port." } finally { $listener.Stop() }
    $address = "http://127.0.0.1:$Port"
    Write-Host "NA70 Point Cloud Studio: $address (Ctrl+C to stop)"
    $runArgs = @('-m', 'backend.run', '--port', $Port)
    if ($NoBrowser) { $runArgs += '--no-browser' }
    & $runtime @runArgs
} finally { Pop-Location }
