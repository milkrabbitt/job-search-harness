import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from job_harness.__main__ import build_service, load_config, runtime_locks, write_private
from job_harness.demo import initialize, seed
from job_harness import rules
from job_harness.demo import ROLES
from job_harness.service import set_setting


class DemoTest(TestCase):
    def test_fixture_exercises_workflow_and_reopening_never_enables_providers(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = {"demo": True, "email": {"enabled": True}, "model": {"enabled": True}}
            self.assertTrue(seed(root, config))
            self.assertFalse(seed(root, config))
            with patch("job_harness.__main__.build_channels", side_effect=AssertionError("would enable provider")):
                harness = build_service(root, config)
                # An edited config and resumed controls cannot activate adapters for the demo DB.
                self.assertTrue(build_service(root, {"email": {"enabled": True}}).snapshot()["demo"])
                with harness.store.tx(True) as c:
                    set_setting(c, "outbound_paused", False)
                    self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 3)
                    self.assertEqual(c.execute("SELECT count(*) FROM job_aliases").fetchone()[0], 4)
                    self.assertFalse(harness.outbound_enabled(c))
                snapshot = harness.snapshot()
                self.assertEqual({row["state"] for row in snapshot["outbox"]}, {"draft", "confirmed"})
                receipt = next(row for row in snapshot["outbox"] if row["state"] == "confirmed")
                self.assertIn("离线模拟回执", receipt["evidence"])
                self.assertEqual([row["kind"] for row in snapshot["decisions"]], ["interview"])
                self.assertFalse(harness.send_one())

    def test_refuses_to_mix_demo_and_existing_profile(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            before = json.dumps({"port": 8876}).encode()
            (root / "config.json").write_bytes(before)
            with self.assertRaisesRegex(ValueError, "非演示"):
                initialize(root, {}, load_config, runtime_locks, write_private)
            self.assertEqual((root / "config.json").read_bytes(), before)
            self.assertFalse((root / "harness.sqlite3").exists())

    def test_configured_direction_limits_automatic_matching(self):
        role = ROLES[0]
        prefs = {"cities": "示例市", "directions": "实体零售"}
        assessment = rules.assess(role, prefs, [])
        self.assertIn("目标方向匹配", assessment["unknown"])
        self.assertNotEqual(assessment["state"], "match")
        self.assertEqual(rules.assess(role, {**prefs, "directions": "对话"}, [])["state"], "match")
