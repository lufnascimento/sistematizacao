param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("server", "scenario-e2e")]
    [string]$Mode,

    [ValidateRange(1, 86400)]
    [int]$TimeoutSeconds = 14400
)

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDirectory = Join-Path $workspace "platform_runtime\detached_logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location $workspace

if ($Mode -eq "server") {
    $env:TERRAFLUX_DATA_ROOT = Join-Path $workspace "platform_runtime\ui_live_final_20260831"
    $process = Start-Process -FilePath "python" `
        -ArgumentList "platform\run.py" `
        -WorkingDirectory $workspace `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDirectory "task_server.stdout.log") `
        -RedirectStandardError (Join-Path $logDirectory "task_server.stderr.log") `
        -PassThru -Wait
    exit $process.ExitCode
}

$process = Start-Process -FilePath "python" `
    -ArgumentList @("platform\tests\e2e_scenario_pipeline_api.py", "--timeout-seconds", "$TimeoutSeconds") `
    -WorkingDirectory $workspace `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDirectory "task_pipeline.stdout.log") `
    -RedirectStandardError (Join-Path $logDirectory "task_pipeline.stderr.log") `
    -PassThru -Wait
exit $process.ExitCode
