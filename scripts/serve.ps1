[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $PSScriptRoot
$dataDir = if ($env:JOB_HARNESS_DATA_DIR) { $env:JOB_HARNESS_DATA_DIR } else { Join-Path $projectDir '.job-harness-dev' }
$venvPython = Join-Path $projectDir '.venv\Scripts\python.exe'
$pythonArgs = @()
if ($env:PYTHON_BIN) {
    $python = $env:PYTHON_BIN
} elseif (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $python = $venvPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = 'py'
    $pythonArgs = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = 'python'
} else {
    throw 'Python was not found. Run .\scripts\bootstrap.ps1 first, or set PYTHON_BIN.'
}

Push-Location -LiteralPath $projectDir
try {
    & $python @pythonArgs -X utf8 -B -m job_harness --data-dir $dataDir serve
    if ($LASTEXITCODE -ne 0) { throw 'The server stopped with an error. Check the message above and run .\scripts\doctor.ps1.' }
} finally {
    Pop-Location
}
