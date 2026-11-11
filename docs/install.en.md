# Installation and first run

[Project home](../README.en.md) · [中文](install.md)

Installation still needs your goals, an available model and recruiting sources. Keep fictional demo data separate from real use.

<a name="ai-install"></a>

## Prompt for your coding assistant

Use a terminal-capable Codex, Claude Code, Cursor Agent or similar assistant. The tool doing the installation and the runtime model are separate choices.

```text
Install and verify Job Search Harness on my computer.
Repository: https://github.com/milkrabbitt/job-search-harness

1. Read README.en.md, docs/install.en.md, docs/models.md, docs/onboarding.md and job_harness/CHANNELS.md. Check the OS, Python, SQLite, timezone data and port. Do not overwrite existing installations or data.
2. Clone to a new directory and create an isolated Python virtual environment. Use scripts/doctor.sh or scripts/doctor.ps1 for this OS and set PYTHON_BIN to the virtual environment's Python. Install only missing dependencies; do not disable global execution protections.
3. Initialize a separate demo data directory with --demo (-Demo in PowerShell). Start it and verify fictional jobs, applications, recruiter messages and decisions. Run the test suite. Keep real accounts and outbound sending disabled.
4. Choose a private data directory outside the repository for real use. Set JOB_HARNESS_DATA_DIR, initialize without demo mode, start the server and open /setup. Ask me for goals, an available model and permitted recruiting sources; do not invent them.
5. Explain Codex CLI authentication, cloud API access and local Ollama/LM Studio. Use an exact model name available to my account or loaded local service. Let me enter secrets in the local wizard or environment, never in chat, source, screenshots or logs.
6. Explain that a model connection test may incur API charges. Test the provider I chose and confirm its current model and endpoint. Saving a configuration is not a successful connection test. Do not use real accounts I have not provided or authorized.
7. Complete setup and start scanning registered sources. Keep outbound sending off; enabling email, platform submissions or automatic replies needs my separate explicit instructions.
8. Report passed and failed checks, the local URL, data directory, start/stop commands, call limits and backup steps. Explain that sleep pauses work and no configured sources means no automatic job discovery.
```

## Requirements

- Python 3.9+ with `sqlite3`, `ssl` and `zoneinfo`; SQLite 3.24+.
- IANA timezone data including `Asia/Shanghai`. Install `tzdata` in the selected virtual environment if missing, commonly on Windows or minimal systems.
- macOS, Linux or Windows PowerShell. Windows supports the core workbench and model APIs. NetEase 126 and authorized Liepin credential integration still requires macOS Keychain.
- A free local port, default `8876`. No Node.js, Docker or frontend build is required by default.

Check actual platform automation results in [CI](https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml). CI and simulated provider tests cannot verify your own API access, balance, mailbox or platform account.

<a name="quickstart"></a>

## macOS / Linux

```sh
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
python3 -m venv .venv
export PYTHON_BIN="$PWD/.venv/bin/python"
./scripts/doctor.sh
```

If diagnostics report missing timezone data, run `"$PYTHON_BIN" -m pip install tzdata` and retry diagnostics.

For the demo:

```sh
./scripts/bootstrap.sh --demo
./scripts/serve.sh
```

Stop the demo before configuring a separate private directory for real use:

```sh
export JOB_HARNESS_DATA_DIR="$HOME/.local/share/job-search-harness"
./scripts/bootstrap.sh
./scripts/serve.sh
```

Reuse the same `JOB_HARNESS_DATA_DIR` and `PYTHON_BIN` in each new terminal session.

## Windows PowerShell

```powershell
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
py -3 -m venv .venv
$env:PYTHON_BIN = "$PWD\.venv\Scripts\python.exe"
.\scripts\doctor.ps1
```

If `py` is unavailable but `python` is Python 3.9+, use `python -m venv .venv`. If timezone data is missing, run `& $env:PYTHON_BIN -m pip install tzdata`.

For the demo:

```powershell
.\scripts\bootstrap.ps1 -Demo
.\scripts\serve.ps1
```

Stop the demo, then configure real use separately:

```powershell
$env:JOB_HARNESS_DATA_DIR = Join-Path $env:LOCALAPPDATA "JobSearchHarness"
.\scripts\bootstrap.ps1
.\scripts\serve.ps1
```

If PowerShell blocks scripts, use the same Python entry point without changing the global execution policy, after configuring the Python environment above:

```powershell
$env:JOB_HARNESS_DATA_DIR = Join-Path $env:LOCALAPPDATA "JobSearchHarness"
& $env:PYTHON_BIN -B -m job_harness --data-dir $env:JOB_HARNESS_DATA_DIR init --empty
& $env:PYTHON_BIN -B -m job_harness --data-dir $env:JOB_HARNESS_DATA_DIR serve
```

If device management also restricts Python, use your organization's approved installation method.

## Complete setup

Open the address printed by the server and visit `/setup`:

1. Enter a role direction and cities or remote preference; salary floor and summary are optional.
2. Choose the provider, exact model and endpoint. Supply credentials locally and test the connection, or explicitly select rules-only operation.
3. Register a public recruiting source. Saving validates URL structure; the first scan checks actual access, parsing and available jobs.
4. Review and start. Starting is idempotent and keeps outbound sending disabled.

A queued task is not a completed result. Review scan and model errors in the console. Keep the terminal running; close the browser freely, but use **Ctrl+C** to stop the service. Sleep or shutdown pauses work. macOS additionally supports LaunchAgent; the default Windows/Linux setup does not register a startup service.

<a name="private-profile"></a>

## Register verified facts and a résumé

Use `/setup` to enter a self-confirmed summary and optionally register an **absolute PDF path on the computer running Harness**, a version label and a scope. Check the explicit approval box after reviewing the PDF. Only PDFs up to 20 MB are accepted. Harness checks and copies the file into private storage, records its fingerprint and leaves the original unchanged. Identical content is deduplicated; registering it again can refresh metadata and approval. This does not send it.

A résumé is optional for scanning. Preparing a formal application requires an approved résumé matching the scope, a verified factual summary and a verified recipient. A cloud-storage link is not a local file path.

### Advanced: import a private JSON profile

Use this for multiple facts, prepared answers or résumé versions; it is not a prerequisite for first setup.

1. Save a private `profile.json` outside the repository, based on the [fictional example](../examples/profile.example.json). Include only verified experience, answers and public portfolio links.
2. Register each approved PDF under `resumes` with `path`, `label`, `scope` (`general`, `ai`, `robotics` or `finance`), `sha256` and `approved: true`. Harness verifies the file and hash; it does not extract work history from a PDF.
3. Calculate the hash using `shasum -a 256`, `sha256sum` or PowerShell `(Get-FileHash -Algorithm SHA256 'C:\path\resume.pdf').Hash.ToLower()`.
4. Stop the instance, import into the same private data directory, restart and check the result.

macOS / Linux:

```sh
"$PYTHON_BIN" -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" init --inheritance /path/to/private/profile.json
./scripts/serve.sh
```

Windows:

```powershell
& $env:PYTHON_BIN -B -m job_harness --data-dir $env:JOB_HARNESS_DATA_DIR init --inheritance C:\path\private\profile.json
.\scripts\serve.ps1
```

Keep private profiles, PDFs, databases and secrets out of Git. Locally saved model keys are unencrypted files under the data directory's `private/model-keys/` and are excluded from database backups. See [credentials](models.md#credentials) and [maintenance](onboarding.md#maintenance).
