import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_harness.model import CodexModel


class CodexModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root / "codex.exe"
        self.executable.write_text("fake executable for mocked subprocess")
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def model(self):
        return CodexModel({"model": {"executable": str(self.executable)}})

    def complete(self, args, **kwargs):
        output = Path(args[args.index("--output-last-message") + 1])
        output.write_text(json.dumps({"summary": "应核实岗位职责", "review": ["确认下一步"]}), encoding="utf-8")
        stdout = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 21, "output_tokens": 9}})
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def test_configured_command_name_resolves_on_path(self):
        with patch("job_harness.model.shutil.which", return_value=str(self.executable)) as which:
            model = CodexModel({"model": {"executable": "codex"}})
        which.assert_called_once_with("codex")
        self.assertEqual(model.exe, str(self.executable))
        with patch("job_harness.model.subprocess.run", side_effect=self.complete) as run:
            result = model.evaluate({"name": "test-model", "timeout": 5})
        self.assertEqual(result["state"], "done")
        self.assertEqual(run.call_args.args[0][0], str(self.executable))

    def test_windows_exe_command_name_uses_path_lookup(self):
        with patch("job_harness.model.shutil.which", return_value=str(self.executable)) as which:
            model = CodexModel({"model": {"executable": "codex.exe"}})
        which.assert_called_once_with("codex.exe")
        self.assertEqual(model.exe, str(self.executable))

    def test_automatic_discovery_checks_exe_launcher(self):
        with patch("job_harness.model.shutil.which", side_effect=[None, str(self.executable)]) as which:
            model = CodexModel({"model": {}})
        self.assertEqual([call.args[0] for call in which.call_args_list], ["codex", "codex.exe"])
        self.assertEqual(model.exe, str(self.executable))

    def test_explicit_absolute_path_is_preserved(self):
        with patch("job_harness.model.shutil.which") as which:
            model = self.model()
        which.assert_not_called()
        self.assertEqual(model.exe, str(self.executable))

    def test_explicit_relative_path_is_resolved_before_temporary_cwd(self):
        relative = "./somewhere/codex"
        with patch("job_harness.model.shutil.which") as which:
            model = CodexModel({"model": {"executable": relative}})
        which.assert_not_called()
        self.assertEqual(model.exe, str(Path(relative).resolve()))

    def test_missing_command_has_safe_failure_without_subprocess(self):
        with patch("job_harness.model.shutil.which", return_value=None), patch("job_harness.model.subprocess.run") as run:
            model = CodexModel({"model": {"executable": "missing-codex-binary"}})
            result = model.evaluate({"name": "test-model", "timeout": 5})
        self.assertEqual(result["state"], "failed")
        self.assertIn("未找到", result["detail"])
        run.assert_not_called()

    def test_general_scope_and_public_feature_boundary(self):
        evidence = {"scope": "general", "state": "match", "score": 83, "hard": ["private reason"],
                    "unknown": ["private question"], "strengths": ["目标关键词：销售"], "chat": "private conversation"}
        with patch("job_harness.model.subprocess.run", side_effect=self.complete) as run:
            result = self.model().evaluate({"name": "test-model", "timeout": 5}, evidence)
        prompt = run.call_args.kwargs["input"]
        facts = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(result["state"], "done")
        self.assertEqual(facts["scope"], "general")
        self.assertEqual(facts["strengths"], [])
        self.assertEqual(facts["hard_count"], 1)
        self.assertNotIn("private", prompt)
        self.assertNotIn("销售", prompt)

    def test_model_identifiers_and_default_respect_codex_home(self):
        config_home = self.root / "auth-config"
        config_home.mkdir()
        (config_home / "config.toml").write_text('model = "owner/model:version"\n[profile]\nmodel = "ignored"\n')
        with patch.dict(os.environ, {"CODEX_HOME": str(config_home)}), patch("job_harness.model.subprocess.run", side_effect=self.complete) as run:
            result = self.model().evaluate({"name": "", "timeout": 5})
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--model") + 1], "owner/model:version")
        self.assertEqual(result["model"], "owner/model:version")

    def test_windows_auth_env_is_preserved_without_unrelated_secrets(self):
        env = {"SystemRoot": "C:\\Windows", "COMSPEC": "C:\\Windows\\cmd.exe", "USERPROFILE": "C:\\Users\\Person",
               "APPDATA": "roaming", "LOCALAPPDATA": "local", "TEMP": "temp", "TMP": "tmp", "PATHEXT": ".EXE;.CMD",
               "CODEX_HOME": str(self.root / "codex-home"), "https_proxy": "http://proxy.test:1234",
               "OPENAI_API_KEY": "do-not-pass", "AWS_SECRET_ACCESS_KEY": "do-not-pass", "UNRELATED_PRIVATE": "do-not-pass"}
        with patch.dict(os.environ, env), patch("job_harness.model.subprocess.run", side_effect=self.complete) as run:
            self.model().evaluate({"name": "test-model", "timeout": 5})
        received = {key.upper(): value for key, value in run.call_args.kwargs["env"].items()}
        for key in ["SystemRoot", "COMSPEC", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PATHEXT", "CODEX_HOME", "https_proxy"]:
            self.assertEqual(received[key.upper()], env[key])
        for key in ["OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "UNRELATED_PRIVATE"]:
            self.assertNotIn(key, received)

    def test_protective_flags_and_no_shell_are_retained(self):
        with patch("job_harness.model.subprocess.run", side_effect=self.complete) as run:
            self.model().evaluate({"name": "test-model", "timeout": 5})
        args, options = run.call_args.args[0], run.call_args.kwargs
        for feature in ["shell_tool", "multi_agent", "shell_snapshot", "skill_search"]:
            self.assertEqual(args[args.index(feature) - 1], "--disable")
        self.assertIn("--ignore-user-config", args)
        self.assertIn("--ephemeral", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertEqual(args[args.index("-a") + 1], "never")
        self.assertIn('web_search="disabled"', args)
        self.assertFalse(options["shell"])
        self.assertEqual(options["timeout"], 5)
        self.assertEqual(args[-1], "-")

    def test_unsupported_cli_flags_fail_without_unsafe_retry(self):
        response = subprocess.CompletedProcess([], 2, stdout="", stderr="unexpected argument '--ignore-user-config'")
        with patch("job_harness.model.subprocess.run", return_value=response) as run:
            result = self.model().evaluate({"name": "test-model", "timeout": 5})
        self.assertEqual(result["state"], "failed")
        self.assertIn("拒绝调用参数", result["detail"])
        run.assert_called_once()

    def test_timeout_is_safe_and_not_retried(self):
        with patch("job_harness.model.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 5)) as run:
            result = self.model().evaluate({"name": "test-model", "timeout": 5})
        self.assertEqual(result["state"], "failed")
        self.assertIn("超时", result["detail"])
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
