$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12 with the Python launcher and rerun setup." }
}
$collectorPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $collectorPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $collectorPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $collectorPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "Chromium installation failed. Check internet/proxy settings." }
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
New-Item -ItemType Directory -Force secrets | Out-Null
Write-Host "Setup complete. Configure .env and Google credentials using LOCAL_SETUP.md."
Write-Host "Then run: .\.venv\Scripts\python.exe local_runner.py --check"
