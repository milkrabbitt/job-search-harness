<p align="center">
  <img src="docs/assets/banner.svg" alt="Job Search Harness" width="100%">
</p>

<h1 align="center">Your next job deserves better tools.</h1>

<p align="center">A personal AI job-search workspace for sorting opportunities, preparing applications, and keeping up with recruiters.</p>

<p align="center"><strong>Less copying, fewer missed details, and more chances to show up prepared.</strong></p>

<p align="center">
  <a href="#quickstart">Try it now</a> ·
  <a href="#ai-install">Let AI install it</a> ·
  <a href="README.md">中文</a> / <a href="README.en.md">English</a> ·
  <a href="LICENSE">MIT</a> ·
  <a href="#tests">Tests</a> ·
  <a href="https://github.com/milkrabbitt/job-search-harness/releases/tag/v0.1.0">v0.1.0 beta</a>
</p>

- **See why a role might fit.** Review the evidence, the gaps, and what the job description leaves open.
- **Keep applications accountable.** Check the recipient and résumé version, then see the draft, send status, and available evidence.
- **Give every recruiter conversation a next step.** Read the context and keep interview scheduling, salary, and offers in your decision queue.

![Actual dashboard with fictional applications and separate task and outbound controls](docs/assets/demo-overview.jpg)

*Screenshot from the running product. Companies, roles, applications, and messages in the demo are fictional. The demo uses simulated sending.*

Saved dozens of postings and lost track of which ones you applied to? Still copying the same details into each application while conversations pile up across platforms? Job Search Harness gives those scattered steps a record and a next action.

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

Open the [local console](http://127.0.0.1:8876/). Closing the browser tab leaves the service running. Press **Ctrl+C** in the terminal to stop the foreground process. The default data directory is `.job-harness-dev/`. To use a different directory, set the same `JOB_HARNESS_DATA_DIR` for bootstrap and serve. See [troubleshooting](#troubleshooting) if a port is occupied or a dependency is missing.

## Three workflows to try

### Turn a saved posting into a shortlist decision

Open the fictional listing under 「岗位」 (Jobs), or add a posting you verified and your own search preferences. You can see the reasons for a match, stated requirements that conflict with your preferences, and details the employer has left open. The source link and the review stay attached to the role. The score helps order opportunities.

![Actual Jobs screen showing match reasons for a fictional listing](docs/assets/demo-jobs.jpg)

### Check what went into an application

Open 「草稿与发送」 (Drafts and sending) to check the recipient, body, and résumé. Use the demo to inspect the simulated outcome and its evidence. With real email configured, a send with an uncertain result stays pending verification. The service checks the original Message-ID before any retry. For a platform application, submit in the original browser session and record the verified result.

![Actual sending queue with a fictional application draft and a simulated receipt](docs/assets/demo-outbox.jpg)

### Keep the recruiter context with the decision

Open a fictional thread under 「招聘消息」 (Recruiter messages), then check 「待我决定」 (Decisions). The original message, related job, and status stay together while you decide when to interview or which conditions to accept.

![Actual recruiter inbox showing a fictional conversation](docs/assets/demo-inbox.jpg)

## Use your own data and accounts

Choose a separate private data directory and omit `--demo` when initializing. Use 「资料与设置」 (Profile and settings) to set preferences, add verified public job sources, and manage personal facts and approved résumé versions. The [configuration template](examples/config.example.json) and [fictional profile](examples/profile.example.json) show the expected formats.

The built-in matching preset focuses on Chinese JDs for AI applications, product, robotics, and financial products. Set your own cities, role terms, salary floor, exclusions, and sending rules. Work arrangement and employment type notes are for manual review. Other industries can still be tracked manually.

| Channel | What is available |
| --- | --- |
| Company websites | Read registered, accessible public job sources. Submit official application forms on the original site. |
| NetEase 126 Mail | On macOS with Keychain configured, check recruiting email, prepare threaded replies, and send and verify authorized mail. |
| Liepin | With candidate authorization configured, search for jobs and check public details. Chat and submissions use browser handoff. |
| BOSS Zhipin and Yupao | Queue and deduplicate platform actions, then record evidence. Complete the action in your logged-in browser. |
| Optional runtime model | A local Codex CLI can review a limited set of match features. The workbench controls authorization and sending. |

See the [channel setup and dependency guide](job_harness/CHANNELS.md) for credentials and troubleshooting. Enable real outbound sending after verifying your account, authorization, and application materials.

<a name="troubleshooting"></a>

## Troubleshooting

[Full diagnostics, service management and backup instructions](docs/troubleshooting.md).

- **Port in use.** If the same instance is already running, open its console. To run a second instance, use a separate data directory and set a free port in its `config.json`.
- **Missing timezone data.** Install your system’s `tzdata` package or run `python -m pip install tzdata` inside the chosen virtual environment.
- **Authorization or access problem.** Check the channel status and error on the dashboard. Complete any login or verification on the provider’s site and check your local configuration against the [channel setup and dependency guide](job_harness/CHANNELS.md).

## Running, privacy, and backups

- **Runs locally.** The console listens on the loopback address. On macOS, the `install` subcommand installs a LaunchAgent for the current user, which can be managed with `start`, `status`, and `stop`. Linux supports foreground operation. Tasks pause when the computer sleeps or shuts down.
- **Separate controls for tasks and outbound sending.** Pause outbound actions while job discovery and inbox checks continue. Pause background tasks to stop scheduling.
- **Private data stays under your control.** Keep SQLite data, résumés, and backups outside the repository. Real channel credentials are stored in the system Keychain. Any enabled external channel receives the material you send through it.
- **Back up and restore.** Back up from the console or run `python3 -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" backup`. Stop the instance before running `restore /path/to/backup.sqlite3` with the same data directory. Check sending evidence before resuming outbound actions after a restore.

<a name="tests"></a>

## Tests

Local tests exercise matching, deduplication, stale drafts, attachment checks, pause controls, and sending recovery with temporary databases, fictional data, and simulated channels.

```sh
python3 -B -m unittest discover -s tests -v
```

If this makes your job search a little less chaotic, a Star is welcome. You can also share how you use it, suggest an improvement, or [open an issue](https://github.com/milkrabbitt/job-search-harness/issues) with a fictional example.

Released under the [MIT license](LICENSE).
