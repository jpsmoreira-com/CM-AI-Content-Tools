$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $python) {
    & $python .\run_server.py
} else {
    python .\run_server.py
}
