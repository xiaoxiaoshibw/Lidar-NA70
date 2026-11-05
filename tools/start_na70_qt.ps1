param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
try {
    if (-not $Python) {
        $Python = Join-Path $repo 'web\.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $Python)) {
            $qtVenv = Join-Path $repo '.venv-qt'
            $Python = Join-Path $qtVenv 'Scripts\python.exe'
            if (-not (Test-Path -LiteralPath $Python)) {
                & py -3.12 -m venv $qtVenv
                if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 environment creation failed.' }
            }
        }
    }
    & $Python -c "import importlib.util as u, importlib.metadata as m, sys; sys.exit(0 if all(u.find_spec(n) for n in ('PySide6', 'serial', 'numpy')) and m.version('PySide6-Essentials') == '6.12.0' else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing Qt recorder dependencies (first launch)...'
        & $Python -m pip install -r (Join-Path $PSScriptRoot 'requirements-qt.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Qt dependency installation failed.' }
    }
    & $Python (Join-Path $PSScriptRoot 'na70_qt_capture.py')
    exit $LASTEXITCODE
} catch {
    Write-Host "Qt recorder startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
