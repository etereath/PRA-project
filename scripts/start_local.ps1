param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$SkipEnvCheck
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LocalEnvPath = Join-Path $PSScriptRoot "local_env.ps1"
$ExampleEnvPath = Join-Path $PSScriptRoot "local_env.example.ps1"

Set-Location $ProjectRoot
$env:PYTHONIOENCODING = "utf-8"

$PythonExe = (& python -c "import sys; print(sys.executable)").Trim()
if (-not $PythonExe) {
    throw "Unable to resolve the active Python interpreter."
}

if (Test-Path $LocalEnvPath) {
    . $LocalEnvPath
} else {
    Write-Warning "Missing scripts/local_env.ps1. Copy scripts/local_env.example.ps1 and fill in local secrets."
    Write-Warning "Example file: $ExampleEnvPath"
}

if (-not $SkipEnvCheck) {
    & $PythonExe scripts/check_runtime_env.py
}

& $PythonExe -m app.cli serve-web --host $HostName --port $Port
exit $LASTEXITCODE
