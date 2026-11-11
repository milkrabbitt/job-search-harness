<#
Create an isolated Python environment and initialize Job Search Harness.
Examples:
  .\scripts\bootstrap.ps1 -Demo -InstallTimezone
  .\scripts\bootstrap.ps1 -InstallTimezone
The optional -InstallTimezone switch explicitly allows installing PyPI tzdata
in the selected environment. No system software or execution policy is changed.
#>
[CmdletBinding()]
param(
    [switch]$Demo,
    [switch]$Example,
    [switch]$InstallTimezone,
    [switch]$InstallDependencies
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Demo -and $Example) { throw 'Choose either -Demo or -Example, not both.' }
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
    throw 'Python 3.9+ was not found. Install Python from python.org, or set PYTHON_BIN to its executable path.'
}

Push-Location -LiteralPath $projectDir
try {
    & $python @pythonArgs -X utf8 -c 'import sys; sys.exit(sys.version_info < (3,9))'
    if ($LASTEXITCODE -ne 0) { throw 'The selected Python interpreter is unavailable or too old.' }
    if (-not $env:PYTHON_BIN -and -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if (Test-Path -LiteralPath (Join-Path $projectDir '.venv')) {
            throw 'An existing .venv does not contain Scripts\python.exe. Set PYTHON_BIN to a valid interpreter; the existing environment was left untouched.'
        }
        & $python @pythonArgs -X utf8 -m venv (Join-Path $projectDir '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Check that Python includes venv and that this directory is writable.' }
        $python = $venvPython
        $pythonArgs = @()
    }
    if ($InstallDependencies) {
        & $python @pythonArgs -X utf8 -m pip install -r (Join-Path $projectDir 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Installing PDF dependencies failed. Retry -InstallDependencies in the selected environment.' }
    }
    if ($InstallTimezone) {
        & $python @pythonArgs -X utf8 -m pip install tzdata
        if ($LASTEXITCODE -ne 0) { throw 'Installing tzdata failed. Check network access and retry -InstallTimezone.' }
    }
    $check = @'
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
if sqlite3.sqlite_version_info < (3, 24):
    raise SystemExit("SQLite 3.24+ is required. Install a current Python build.")
try:
    ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    raise SystemExit("Timezone data is missing. Re-run bootstrap.ps1 with -InstallTimezone, or install tzdata in the interpreter selected by PYTHON_BIN.")
'@
    $check | & $python @pythonArgs -X utf8 -B -
    if ($LASTEXITCODE -ne 0) { throw 'Runtime checks failed; no application data was initialized.' }

    if ($Demo) {
        & $python @pythonArgs -X utf8 -B -m job_harness --data-dir $dataDir demo
    } elseif ($Example) {
        & $python @pythonArgs -X utf8 -B -m job_harness --data-dir $dataDir init --inheritance (Join-Path $projectDir 'examples\profile.example.json')
    } else {
        & $python @pythonArgs -X utf8 -B -m job_harness --data-dir $dataDir init --empty
    }
    if ($LASTEXITCODE -ne 0) { throw 'Initialization failed. Review the error above; existing data was not removed.' }
    Write-Host 'Ready. Run .\scripts\serve.ps1, then open the local URL it prints.'
    Write-Host 'Use Ctrl+C in that terminal to stop. Live NetEase 126 / Liepin Keychain integrations still require macOS.'
} finally {
    Pop-Location
}
