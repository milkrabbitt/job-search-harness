"""Real SQLite first-run integration, with isolated provider adapters only."""
import json
import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from job_harness.model_credentials import read_model_key
from job_harness.onboarding import effective_model_config
from job_harness.service import Harness, setting, set_setting
from job_harness.storage import Store


class FakeModel:
    def __init__(self, state="done", detail="虚构样例通过", callback=None):
        self.state, self.detail, self.callback = state, detail, callback
        self.calls = []

    def evaluate(self, config, evidence=None):
        self.calls.append((dict(config), evidence))
        if self.callback:
            self.callback()
        return {"state": self.state, "detail": self.detail, "model": config.get("name") or "fixture",
                "input_tokens": 12, "output_tokens": 8}


class FakeOfficial:
    def discover(self, sources):
        return [{"channel": "official", "company": "虚构测试公司", "title": "财务分析师", "location": "上海",
                 "url": sources[0]["url"] + "/role", "external_id": "finance-example", "status": "open",
                 "description": "负责财务分析、预算管理和经营报告。" * 6, "requirements": "愿意参与财务分析。"}]


class OnboardingTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = Harness(Store(self.root), {"email": {"enabled": False}}, {"official": FakeOfficial()})
        with self.service.store.tx(True) as c:
            set_setting(c, "onboarding_required", True)
            set_setting(c, "stopped", True)
            set_setting(c, "outbound_paused", True)

    def action(self, name, **data):
        return self.service.action(name, data)

    def rows(self, query, args=()):
        with self.service.store.tx() as c:
            return [dict(row) for row in c.execute(query, args)]

    def profile(self):
        self.action("setup_profile", cities="上海", directions="财务分析", summary="我本人确认：完成过预算分析项目。")

    def model(self, **overrides):
        self.action("setup_model", **{"provider": "ollama", "model_name": "qwen3:8b", "enabled": "1",
                    "max_calls": "3", "timeout": "10", "max_output_tokens": "512", **overrides})

    def source(self):
        self.action("setup_source", url="https://fixture.example/careers", company="虚构测试公司")

    def probe(self, model=None):
        model = model or FakeModel()
        with patch("job_harness.model_backends.build_model", return_value=model):
            self.action("setup_test_model")
        return model

    def test_fresh_state_requires_configuration_and_cannot_bypass_with_run(self):
        state = self.service.snapshot()["onboarding"]
        self.assertTrue(state["required"])
        self.assertFalse(state["ready"])
        self.assertEqual(len(state["missing"]), 3)
        for action in ("run", "resume_all", "setup_start"):
            with self.assertRaises(ValueError):
                self.action(action)
        with self.service.store.tx(True) as c:
            set_setting(c, "stopped", False)
            set_setting(c, "outbound_paused", False)
            self.service.enqueue(c, "scan", "official", "unsafe-bypass")
            self.assertFalse(self.service.outbound_enabled(c))
        self.service.schedule()
        self.assertFalse(self.service.task_one())
        self.assertEqual(len(self.rows("SELECT id FROM tasks")), 1)

    def test_existing_install_without_marker_remains_available(self):
        with self.service.store.tx(True) as c:
            c.execute("DELETE FROM settings WHERE key='onboarding_required'")
        self.action("run")
        self.assertEqual(self.service.snapshot()["state"]["state"], "running")
        self.assertFalse(self.service.snapshot()["onboarding"]["required"])

    def test_demo_cannot_save_live_credentials(self):
        with self.service.store.tx(True) as c:
            set_setting(c, "demo_fixture", True)
        with self.assertRaises(ValueError):
            self.model(api_key="not-real-demo-key")
        self.assertFalse((self.root / "private").exists())

    def test_profile_has_explicit_goals_and_only_self_confirmed_facts(self):
        for data in ({"cities": "上海"}, {"directions": "财务分析"}):
            with self.assertRaises(ValueError):
                self.action("setup_profile", **data)
        self.assertEqual(self.rows("SELECT * FROM facts"), [])
        self.profile()
        facts = self.rows("SELECT key,value,source FROM facts")
        self.assertEqual(facts[0]["key"], "summary_general")
        self.assertEqual(facts[0]["value"], "我本人确认：完成过预算分析项目。")
        self.assertEqual(self.service.snapshot()["preferences"]["matching_preset"], "general")
        self.assertTrue(self.service.snapshot()["onboarding"]["profile_ready"])

    def test_target_scope_only_profile_actually_matches_its_keywords(self):
        self.action("setup_profile", cities="上海", target_scope="财务分析")
        self.action("setup_no_model")
        self.source()
        self.action("setup_start")
        self.service.task_one()
        self.assertGreaterEqual(self.rows("SELECT match_score FROM jobs")[0]["match_score"], 60)

    def test_remote_only_profile_is_supported_and_unchecked_remote_becomes_false(self):
        self.action("setup_profile", directions="工程师", allow_remote="1")
        self.assertTrue(self.service.snapshot()["preferences"]["allow_remote"])
        self.action("setup_profile", cities="北京", directions="工程师")
        self.assertFalse(self.service.snapshot()["preferences"]["allow_remote"])

    def test_invalid_profile_does_not_persist_partial_settings(self):
        self.profile()
        previous = self.service.snapshot()["preferences"]
        with self.assertRaises(ValueError):
            self.action("setup_profile", cities="深圳", directions="工程师", salary_floor="bad")
        self.assertEqual(self.service.snapshot()["preferences"], previous)

    def test_saving_private_key_never_calls_provider_or_copies_key_to_database(self):
        secret = "fixture-private-key-do-not-export"
        with patch("job_harness.model_backends.build_model") as factory:
            self.model(api_key=secret)
            factory.assert_not_called()
        self.assertEqual(self.rows("SELECT * FROM model_runs"), [])
        with self.service.store.tx() as c:
            config = effective_model_config(self.service, c)
            database = "\n".join(c.iterdump())
        self.assertEqual(read_model_key(self.root, config), secret)
        self.assertNotIn(secret, database)
        self.assertNotIn(secret, json.dumps(self.service.snapshot(), ensure_ascii=False))
        self.assertNotIn(secret.encode(), self.service.store.backup().read_bytes())
        self.assertTrue(self.service.snapshot()["model"]["key_configured"])
        self.assertEqual(self.service.snapshot()["model"]["key_source"], "private_file")

    def test_invalid_model_settings_do_not_replace_existing_key(self):
        self.model(api_key="fixture-original")
        with self.assertRaises(ValueError):
            self.model(base_url="http://remote.example/v1", api_key="fixture-new")
        with self.service.store.tx() as c:
            cfg = effective_model_config(self.service, c)
        self.assertEqual(read_model_key(self.root, cfg), "fixture-original")

    def test_wizard_can_repair_invalid_manually_edited_model_config(self):
        with self.service.store.tx(True) as c:
            set_setting(c, "model_config", {"provider": "unknown_provider"})
        self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])
        self.model()
        self.assertEqual(self.service.snapshot()["model"]["provider"], "ollama")

    def test_saved_model_hot_configuration_and_no_network_while_database_locked(self):
        self.model()
        def transaction_proof():
            with self.service.store.tx(True) as c:
                set_setting(c, "provider_transaction_proof", True)
        fake = self.probe(FakeModel(callback=transaction_proof))
        self.assertEqual(fake.calls[0][0]["name"], "qwen3:8b")
        self.assertIsNone(fake.calls[0][1])  # synthetic probe, no actual applicant/job content
        self.assertEqual(self.service.snapshot()["state"]["state"], "stopped")
        self.assertTrue(self.service.snapshot()["onboarding"]["model_ready"])
        with self.service.store.tx() as c:
            self.assertTrue(setting(c, "provider_transaction_proof"))

    def test_provider_switch_uses_new_endpoint_and_key_environment(self):
        self.model()
        self.action("setup_model", provider="deepseek", model_name="fixture-chat", enabled="1")
        with self.service.store.tx() as c:
            config = effective_model_config(self.service, c)
        self.assertIn("deepseek.com", config["base_url"])
        self.assertEqual(config["api_key_env"], "DEEPSEEK_API_KEY")
        self.assertNotIn("11434", config["base_url"])

    def test_changing_model_or_key_invalidates_successful_test(self):
        self.model(api_key="fixture-key-one")
        self.probe()
        self.assertTrue(self.service.snapshot()["onboarding"]["model_ready"])
        self.model(api_key="fixture-key-two")
        self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])
        self.probe()
        self.model(model_name="different/model:latest")
        self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])

    def test_environment_key_change_invalidates_test_without_saving_config(self):
        self.model(api_key_env="JOB_HARNESS_TEST_ONBOARDING_KEY")
        with patch.dict(os.environ, {"JOB_HARNESS_TEST_ONBOARDING_KEY": "fixture-one"}):
            self.probe()
            self.assertTrue(self.service.snapshot()["onboarding"]["model_ready"])
            self.assertEqual(self.service.snapshot()["model"]["key_source"], "environment")
        with patch.dict(os.environ, {"JOB_HARNESS_TEST_ONBOARDING_KEY": "fixture-two"}):
            self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])

    def test_changed_credentials_cannot_run_automatically_using_an_old_successful_test(self):
        self.profile()
        self.source()
        self.model(api_key_env="JOB_HARNESS_TEST_ONBOARDING_KEY")
        with patch.dict(os.environ, {"JOB_HARNESS_TEST_ONBOARDING_KEY": "fixture-one"}):
            self.probe()
            self.action("setup_start")
        with patch.dict(os.environ, {"JOB_HARNESS_TEST_ONBOARDING_KEY": "fixture-two"}):
            with patch("job_harness.model_backends.build_model") as factory:
                state, detail = self.service.invoke_model_probe()
                factory.assert_not_called()
            self.assertEqual(state, "held")
            self.assertIn("凭据", detail)

    def test_failed_probe_has_clear_state_and_consumes_budget(self):
        self.model(max_calls="1")
        self.probe(FakeModel("failed", "连接超时，请检查本机服务"))
        self.assertEqual(self.service.snapshot()["onboarding"]["model_test"]["state"], "failed")
        self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])
        fake = self.probe()
        self.assertEqual(fake.calls, [])
        self.assertEqual(len(self.rows("SELECT * FROM model_runs")), 1)
        self.assertIn("每日", self.service.snapshot()["onboarding"]["model_test"]["detail"])

    def test_amount_limit_is_fail_closed_not_fake_cost_enforcement(self):
        self.model(cost_limit="2.5")
        fake = self.probe()
        self.assertEqual(fake.calls, [])
        state = self.service.snapshot()["onboarding"]["model_test"]
        self.assertEqual(state["state"], "held")
        self.assertIn("真实费用未知", state["detail"])

    def test_probe_result_from_changed_configuration_cannot_verify_new_configuration(self):
        self.model()
        entered, release = threading.Event(), threading.Event()
        def block():
            entered.set()
            self.assertTrue(release.wait(3))
        fake = FakeModel(callback=block)
        results = []
        with patch("job_harness.model_backends.build_model", return_value=fake):
            thread = threading.Thread(target=lambda: results.append(self.action("setup_test_model")))
            thread.start()
            self.assertTrue(entered.wait(2))
            self.model(model_name="new-model:latest")
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIn("不再适用", results[0])
        self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])

    def test_concurrent_probes_reserve_daily_budget_atomically(self):
        self.model(max_calls="1")
        entered, release = threading.Event(), threading.Event()
        fake = FakeModel(callback=lambda: (entered.set(), release.wait(3)))
        with patch("job_harness.model_backends.build_model", return_value=fake):
            thread = threading.Thread(target=lambda: self.action("setup_test_model"))
            thread.start()
            self.assertTrue(entered.wait(2))
            notice = self.action("setup_test_model")
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(self.rows("SELECT * FROM model_runs")), 1)
        self.assertIn("正在运行", notice)
        self.assertTrue(self.service.snapshot()["onboarding"]["model_ready"])

    def test_disabled_model_is_not_an_implicit_no_model_choice(self):
        self.profile()
        self.source()
        self.model(enabled="0")
        self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])
        with self.assertRaises(ValueError):
            self.action("setup_start")
        self.action("setup_no_model")
        self.assertTrue(self.service.snapshot()["onboarding"]["model_ready"])

    def test_start_immediate_scan_is_idempotent_and_never_enables_external_sends(self):
        self.service.recover()
        self.assertEqual(self.rows("SELECT * FROM tasks"), [])
        self.profile()
        self.action("setup_no_model")
        with self.assertRaises(ValueError):
            self.action("setup_start")
        self.source()
        self.action("setup_start")
        self.action("setup_start")
        self.service.schedule()
        tasks = self.rows("SELECT * FROM tasks")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["kind"], "scan")
        state = self.service.snapshot()
        self.assertEqual(state["state"]["state"], "running")
        self.assertTrue(state["state"]["paused_all"])
        self.assertFalse(state["preferences"]["auto_send"])
        self.assertTrue(state["onboarding"]["complete"])
        self.assertTrue(self.service.task_one())
        self.assertEqual(len(self.rows("SELECT * FROM jobs")), 1)
        self.assertEqual(self.rows("SELECT * FROM outbox"), [])
        self.assertEqual(self.rows("SELECT * FROM tasks WHERE channel='email'"), [])
        self.service.recover()
        self.assertEqual(len(self.rows("SELECT * FROM tasks WHERE kind='scan'")), 1)

    def test_enabled_model_reviews_matching_job_even_without_contact_email(self):
        self.profile()
        self.model()
        self.probe()
        self.source()
        self.action("setup_start")
        self.assertTrue(self.service.task_one())
        tasks = self.rows("SELECT * FROM tasks WHERE kind='model_probe'")
        self.assertEqual(len(tasks), 1)
        self.assertTrue(json.loads(tasks[0]["payload"])["job_id"])
        with patch("job_harness.model_backends.build_model", return_value=FakeModel()):
            self.assertTrue(self.service.task_one())
        self.assertIn("model_advice", json.loads(self.rows("SELECT match_json FROM jobs")[0]["match_json"]))
        self.assertEqual(self.rows("SELECT * FROM outbox"), [])

    def test_adding_source_after_start_queues_one_immediate_scan_but_stopped_setup_does_not(self):
        self.profile()
        self.action("setup_no_model")
        self.source()
        self.action("setup_start")
        added = {"url": "https://second.fixture.example/careers", "company": "第二家虚构测试公司"}
        notice = self.action("setup_source", **added)
        self.action("setup_source", **added)
        self.assertIn("已安排读取", notice)
        tasks = self.rows("SELECT * FROM tasks WHERE dedupe_key LIKE 'onboarding:source:%'")
        self.assertEqual(len(tasks), 1)
        sid = self.rows("SELECT id FROM sources WHERE url=?", (added["url"],))[0]["id"]
        self.assertEqual(json.loads(tasks[0]["payload"])["source_id"], sid)
        self.assertEqual(tasks[0]["state"], "queued")
        self.assertTrue(self.service.snapshot()["state"]["paused_all"])
        self.action("stop")
        stopped_notice = self.action("setup_source", url="https://third.fixture.example/careers")
        self.assertIn("启动时", stopped_notice)
        self.assertEqual(len(self.rows("SELECT * FROM tasks WHERE dedupe_key LIKE 'onboarding:source:%'")), 1)

    def test_saving_settings_again_pauses_existing_work_and_keeps_data(self):
        self.profile()
        self.action("setup_no_model")
        self.source()
        self.action("setup_start")
        self.service.task_one()
        self.profile()
        state = self.service.snapshot()
        self.assertEqual(state["state"]["state"], "stopped")
        self.assertFalse(state["onboarding"]["complete"])
        self.assertEqual(len(state["jobs"]), 1)

    def test_disabled_email_does_not_schedule_noisy_probes(self):
        with self.service.store.tx(True) as c:
            c.execute("DELETE FROM settings WHERE key='onboarding_required'")
            set_setting(c, "stopped", False)
        self.service.recover()
        self.service.schedule()
        self.assertEqual(self.rows("SELECT * FROM tasks WHERE channel='email'"), [])

    def test_legacy_save_model_is_only_a_save_and_preserves_adapter(self):
        self.model()
        self.action("set_model", model_name="namespace/model:latest", max_calls="2", timeout="12")
        self.assertEqual(self.rows("SELECT * FROM model_runs"), [])
        self.assertEqual(self.rows("SELECT * FROM tasks WHERE kind='model_probe'"), [])
        self.assertEqual(self.service.snapshot()["model"]["provider"], "ollama")

    def test_legacy_save_model_pauses_marked_install_until_retested(self):
        self.profile()
        self.source()
        self.model()
        self.probe()
        self.action("setup_start")
        self.action("set_model", model_name="updated:latest")
        state = self.service.snapshot()
        self.assertEqual(state["state"]["state"], "stopped")
        self.assertFalse(state["onboarding"]["complete"])
        with self.assertRaises(ValueError):
            self.action("run")

    def test_preferences_persists_explicit_preset_and_rejects_unknown_mode(self):
        self.action("preferences", matching_preset="general")
        self.assertEqual(self.service.snapshot()["preferences"]["matching_preset"], "general")
        with self.assertRaises(ValueError):
            self.action("preferences", matching_preset="not-supported")
        self.assertEqual(self.service.snapshot()["preferences"]["matching_preset"], "general")

    def test_model_timeout_task_requires_manual_retry(self):
        fake = FakeModel("failed", "模型超时")
        self.service.model = fake
        with self.service.store.tx(True) as c:
            c.execute("DELETE FROM settings WHERE key='onboarding_required'")
            set_setting(c, "stopped", False)
        self.action("model_probe")
        self.assertTrue(self.service.task_one())
        row = self.rows("SELECT * FROM tasks WHERE kind='model_probe'")[0]
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempts"], 1)
        self.assertFalse(self.service.task_one())

    def test_recover_invalidates_an_interrupted_connectivity_test(self):
        self.model()
        with self.service.store.tx(True) as c:
            set_setting(c, "model_test", {"state": "running", "config_hash": "old"})
        self.service.recover()
        self.assertFalse(self.service.snapshot()["onboarding"]["model_ready"])

    def test_recover_does_not_automatically_retry_model_call_after_process_exit(self):
        with self.service.store.tx(True) as c:
            self.service.enqueue(c, "model_probe", "model", "interrupted-model")
            c.execute("UPDATE tasks SET state='running'")
        self.service.recover()
        task = self.rows("SELECT * FROM tasks WHERE dedupe_key='interrupted-model'")[0]
        self.assertEqual(task["state"], "failed")
        self.assertIn("手动", task["detail"])

    def test_generic_draft_uses_only_confirmed_summary_and_actual_job_title(self):
        self.profile()
        with self.service.store.tx() as c:
            body = self.service.template(c, {"title": "财务分析师", "status": "open"}, {"scope": "general"})
        self.assertIn("财务分析师", body)
        self.assertIn("我本人确认：完成过预算分析项目。", body)
        self.assertNotIn("产品需求和应用交付", body)

    def test_invalid_sources_are_rejected_before_start(self):
        for url in ("http://fixture.example/careers", "https://user:secret@fixture.example/careers"):
            with self.assertRaises(ValueError):
                self.action("setup_source", url=url)
        self.assertEqual(self.rows("SELECT * FROM sources"), [])

    def test_optional_resume_import_stages_verified_pdf_without_send_permission(self):
        source = self.root / "test-resume.pdf"
        content = b"%PDF-1.7\nsynthetic applicant resume\n%%EOF"
        source.write_bytes(content)
        with self.assertRaises(ValueError):
            self.action("setup_resume", path=str(source), label="测试简历", scope="general")
        self.assertEqual(self.rows("SELECT * FROM resumes"), [])
        self.action("setup_resume", path=str(source), label="测试简历", scope="general", approved="1")
        self.action("setup_resume", path=str(source), label="测试简历更新", scope="general,finance", approved="on")
        rows = self.rows("SELECT * FROM resumes")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "测试简历更新")
        self.assertEqual(rows[0]["scope"], "general,finance")
        self.assertEqual(Path(rows[0]["path"]).read_bytes(), content)
        self.assertNotEqual(Path(rows[0]["path"]), source)
        self.assertEqual(source.read_bytes(), content)
        self.assertEqual(rows[0]["approved"], 1)
        self.assertEqual(self.rows("SELECT * FROM outbox"), [])
        self.assertNotIn(str(source), json.dumps(self.rows("SELECT message FROM events")))

    def test_optional_resume_rejects_invalid_scope_and_non_pdf_before_database_write(self):
        source = self.root / "test-resume.pdf"
        source.write_bytes(b"not a PDF")
        with self.assertRaises(ValueError):
            self.action("setup_resume", path=str(source), scope="general", approved="1")
        source.write_bytes(b"%PDF-1.7\nfixture\n%%EOF")
        with self.assertRaises(ValueError):
            self.action("setup_resume", path=str(source), scope="unrecognized", approved="1")
        self.assertEqual(self.rows("SELECT * FROM resumes"), [])
