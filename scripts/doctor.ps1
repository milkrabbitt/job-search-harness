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
    throw 'Python 3.9+ was not found. Install Python from python.org, or set PYTHON_BIN to its executable path.'
}

$diagnostic = @'
import importlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

if sys.version_info < (3, 9):
    raise SystemExit("[FAIL] Python 3.9+ is required.")
print("[OK] Python " + sys.version.split()[0])
for name in ("sqlite3", "ssl", "zoneinfo", "msvcrt" if os.name == "nt" else "fcntl"):
    try:
        importlib.import_module(name)
    except (ImportError, OSError):
        raise SystemExit("[FAIL] Missing runtime module: " + name)
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
if sqlite3.sqlite_version_info < (3, 24):
    raise SystemExit("[FAIL] SQLite 3.24+ is required.")
with sqlite3.connect(":memory:") as connection:
    connection.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO probe VALUES (1) ON CONFLICT(id) DO NOTHING")
print("[OK] SQLite " + sqlite3.sqlite_version + "; TLS and locking modules available")
try:
    ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    raise SystemExit("[FAIL] Missing timezone data. Run bootstrap.ps1 -InstallTimezone, or install tzdata in the selected Python environment.")
print("[OK] Asia/Shanghai timezone")
for name in ("job_harness/__main__.py", "job_harness/filelock.py", "job_harness/storage.py", "job_harness/console.py"):
    if not Path(name).is_file():
        raise SystemExit("[FAIL] Missing project file: " + name)
print("[OK] Application source files found")
profile = Path(sys.argv[1]).expanduser()
try:
    writable = next(item for item in (profile, *profile.parents) if item.exists())
    with tempfile.TemporaryFile(dir=writable) as probe:
        probe.write(b"writable")
except (OSError, StopIteration):
    raise SystemExit("[FAIL] Data directory is not writable. Set JOB_HARNESS_DATA_DIR to a private writable folder.")
config_path = profile / "config.json"
settings = {}
if config_path.exists():
    try:
        settings = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError
    except (OSError, ValueError):
        raise SystemExit("[FAIL] config.json is not a readable JSON object.")
    print("[OK] Existing configuration is readable")
else:
    print("[NEXT] Run bootstrap.ps1 for first-run setup, or bootstrap.ps1 -Demo for a fictional demo.")
port = settings.get("port", 8876)
if type(port) is not int or not 1024 <= port <= 65535:
    raise SystemExit("[FAIL] Configured port must be an integer from 1024 through 65535.")
try:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
except OSError:
    raise SystemExit("[FAIL] This environment cannot bind an IPv4 loopback socket.")
try:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
except OSError:
    print("[NOTE] Port " + str(port) + " is unavailable. Open the existing console or choose another port in config.json.")
else:
    print("[OK] Local console port " + str(port) + " is available")
print("[NEXT] Configure and test a model in the local setup page. This diagnostic does not verify credentials or perform model calls.")
print("[NOTE] Windows supports the foreground console. NetEase 126 and Liepin Keychain integrations and LaunchAgent require macOS.")
print("Diagnostics complete. No credentials, provider endpoints or recruitment channels were accessed.")
'@

Push-Location -LiteralPath $projectDir
try {
    $diagnostic | & $python @pythonArgs -X utf8 -B - $dataDir
    if ($LASTEXITCODE -ne 0) { throw 'Environment checks failed. Follow the instructions above.' }
} finally {
    Pop-Location
}
