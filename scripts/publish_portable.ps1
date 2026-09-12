# Build a portable Kurama folder: double-click Kurama.exe for the terminal GUI.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Get-Command python -ErrorAction SilentlyContinue
if (-not $Py) { $Py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $Py) { throw "Python not found on PATH (needed to run the publisher)" }

Write-Host "==> python scripts/publish_portable.py"
& $Py.Source (Join-Path $Root "scripts\publish_portable.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
