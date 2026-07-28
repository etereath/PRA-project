param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$SkipEnvCheck,
    [switch]$SkipQueueServices
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

$OwnedQueueService = $null
if (-not $SkipQueueServices) {
    $ExistingQueueService = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -match "run_shadowbot_queue_services.py"
        } |
        Select-Object -First 1
    if (-not $ExistingQueueService) {
        $RuntimeLogDir = Join-Path $ProjectRoot "data\runtime\logs"
        New-Item -ItemType Directory -Force -Path $RuntimeLogDir | Out-Null
        $QueueStdout = Join-Path $RuntimeLogDir "shadowbot_queue_services.stdout.log"
        $QueueStderr = Join-Path $RuntimeLogDir "shadowbot_queue_services.stderr.log"
        $OwnedQueueService = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList @(
                "scripts\run_shadowbot_queue_services.py",
                "--runtime-db",
                "data\runtime\pra_runtime.sqlite3"
            ) `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $QueueStdout `
            -RedirectStandardError $QueueStderr `
            -PassThru
    }
}

$WebExitCode = 1
try {
    & $PythonExe -m app.cli serve-web --host $HostName --port $Port
    $WebExitCode = $LASTEXITCODE
} finally {
    if ($null -ne $OwnedQueueService -and -not $OwnedQueueService.HasExited) {
        Stop-Process -Id $OwnedQueueService.Id
    }
}

exit $WebExitCode
