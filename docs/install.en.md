# Installation and first run

[Back to the project](../README.en.md) · [中文](install.md)

<a name="ai-install"></a>

## Let an AI assistant install it

Paste this into Codex, Claude Code, or Cursor Agent. Your installation assistant sets up the project. The optional runtime integration uses a locally authenticated Codex CLI to review job matches. The demo runs with local Python.

```text
Install and verify Job Search Harness on my computer.
Repository https://github.com/milkrabbitt/job-search-harness

1. Clone the repository into a new directory. Read README.en.md and job_harness/CHANNELS.md.
2. Run ./scripts/doctor.sh to check Python, SQLite, timezone data, and the local port. Create a virtual environment with python3 -m venv .venv and point PYTHON_BIN to .venv/bin/python. Check requirements.txt and install timezone data only if needed.
3. Run ./scripts/bootstrap.sh --demo, then ./scripts/serve.sh. Open the local URL printed by the server. Inspect the fictional job matches, application draft and simulated sending evidence, recruiter thread, and decision queue.
4. Run .venv/bin/python -B -m unittest discover -s tests -v. Keep real accounts and outbound sending disabled during the demo and tests.
5. If I want the optional runtime model, check for a Codex CLI installation authenticated by me on this computer. Once I have authenticated locally, set model.enabled to true in config.json in a separate non-demo data directory. Set model.executable if needed and restart that instance. In Settings, configure a model actually available to me, set call and timeout limits, and schedule a model check.
6. Tell me what you verified and provide the local URL, data directory, and start and stop instructions. Leave any existing job-search data alone. I will enter secrets in the local system credential manager or the provider's official authentication flow. Keep credentials out of chat, source code, and Git.
```

<a name="quickstart"></a>

## Try it now

Requires macOS or Linux, Python 3.9+, SQLite 3.24+, and IANA timezone data for `Asia/Shanghai`. The current console is in Chinese. The default runtime uses the Python standard library.

```sh
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
./scripts/doctor.sh
./scripts/bootstrap.sh --demo
./scripts/serve.sh
```

Open the [local console](http://127.0.0.1:8876/). Closing the browser tab leaves the service running. Press **Ctrl+C** in the terminal to stop the foreground process. The default data directory is `.job-harness-dev/`. To use a different directory, set the same `JOB_HARNESS_DATA_DIR` for bootstrap and serve. See [troubleshooting](troubleshooting.md) if a port is occupied or a dependency is missing.

