"""Bounded advisory calls through the locally authenticated Codex CLI."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .candidate_match import ADVICE_INSTRUCTIONS, ADVICE_SCHEMA, PROBE, parse_advice, sanitize_evidence

BUNDLED = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
SCHEMA = ADVICE_SCHEMA
SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}\Z")

class CodexModel:
    def __init__(self, config):
        executable = str(config.get("model", {}).get("executable") or "").strip()
        if executable:
            # A name such as "codex" is a PATH lookup, not a file in the
            # application's working directory. shutil.which honors PATHEXT
            # on Windows, including the installed codex.exe launcher.
            if "/" in executable or "\\" in executable or Path(executable).is_absolute():
                path = Path(executable).expanduser()
                self.exe = str(path if path.is_absolute() else path.resolve())
            else:
                self.exe = shutil.which(executable) or executable
        else:
            self.exe = shutil.which("codex") or shutil.which("codex.exe") or str(BUNDLED)
        self.home = Path.home()

    def configured_default(self):
        try:
            config_home = Path(os.environ.get("CODEX_HOME") or self.home / ".codex").expanduser()
            with (config_home / "config.toml").open() as stream:
                for line in stream:
                    if line.lstrip().startswith("["):
                        break
                    found = re.fullmatch(r'\s*model\s*=\s*["\']([^"\']+)["\']\s*(?:#.*)?', line.strip())
                    if found and SAFE_LABEL.fullmatch(found[1]):
                        return found[1]
        except OSError:
            pass
        return ""

    def evaluate(self, config, evidence=None):
        model = config.get("name") or self.configured_default()
        if model and not SAFE_LABEL.fullmatch(model):
            return {"state": "failed", "model": "未识别", "detail": "模型名称不符合官方 CLI 参数格式"}
        if not Path(self.exe).is_file():
            return {"state": "failed", "model": model or "未识别", "detail": "未找到 Codex CLI"}
        try:
            facts = sanitize_evidence(evidence)
        except ValueError:
            return {"state": "failed", "model": model or "CLI 默认", "detail": "匹配依据格式无效"}
        prompt = ADVICE_INSTRUCTIONS + "\n" + json.dumps(facts, ensure_ascii=False)
        with tempfile.TemporaryDirectory(prefix="job-harness-model-") as temp:
            schema, output = Path(temp) / "schema.json", Path(temp) / "result.json"
            schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
            args = [self.exe, "--disable", "shell_tool", "--disable", "multi_agent",
                    "--disable", "shell_snapshot", "--disable", "skill_search", "-a", "never", "-c", 'web_search="disabled"',
                    "exec", "--ignore-user-config", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check",
                    "--json", "--output-schema", str(schema), "--output-last-message", str(output)]
            if model:
                args.extend(["--model", model])
            args.append("-")
            env = {key: value for key, value in os.environ.items() if key.upper() in {
                "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME", "HTTPS_PROXY", "HTTP_PROXY",
                "NO_PROXY", "SYSTEMROOT", "COMSPEC", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "WINDIR", "PATHEXT"
            }}
            try:
                finished = subprocess.run(args, input=prompt, cwd=temp, env=env, text=True, encoding="utf-8",
                                          capture_output=True, timeout=config["timeout"], check=False, shell=False)
            except subprocess.TimeoutExpired:
                return {"state": "failed", "model": model or "CLI 默认", "detail": "Codex CLI 调用超时"}
            except OSError:
                return {"state": "failed", "model": model or "CLI 默认", "detail": "无法运行 Codex CLI"}
            try:
                result = parse_advice(output.read_text(encoding="utf-8"), facts)
                if finished.returncode:
                    raise ValueError("invalid")
            except (OSError, TypeError, ValueError, KeyError):
                diagnostic = (finished.stderr + finished.stdout).casefold()
                if "unexpected argument" in diagnostic:
                    detail = "本机 Codex CLI 拒绝调用参数"
                elif re.search("429|rate limit|usage limit|quota", diagnostic):
                    detail = "当前账号模型调用受限或达到额度"
                elif re.search("401|403|authentication|login required|not logged", diagnostic):
                    detail = "Codex CLI 后台调用未能通过账号认证"
                else:
                    detail = "Codex CLI 未返回可核验的结构化结果"
                return {"state": "failed", "model": model or "CLI 默认", "detail": detail}
            usage = {}
            resolved = model or ""
            for line in finished.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "turn.completed":
                    usage = event.get("usage") or {}
                if isinstance(event.get("model"), str) and SAFE_LABEL.fullmatch(event["model"]):
                    resolved = event["model"]
            review = [str(x)[:120] for x in result["review"][:3]]
            return {"state": "done", "model": resolved or "CLI 默认（未返回名称）",
                    "detail": ("已核实账号调用 · " if evidence is None else "建议 · ") + result["summary"][:200]
                    + (" · " + "；".join(review) if review else ""),
                    "advice": result,
                    "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}
