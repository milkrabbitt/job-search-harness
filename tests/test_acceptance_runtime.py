"""Run the real scheduler against isolated, in-process providers."""

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from job_harness.__main__ import protect_later_sends, runtime_locks
from job_harness.channels import ChannelUnavailable
from job_harness.service import Harness
from job_harness.storage import Store, TZ


SOURCE = "https://fixture.example/careers"
RECIPIENT = "recruiting@fixture.example"
ROLE = {
    "channel": "official", "company": "示例机器人", "title": "陪伴机器人交互产品经理",
    "external_id": "fixture-interaction", "url": SOURCE + "/interaction", "domain": "fixture.example",
    "location": "杭州", "salary": "20–30K", "status": "open",
    "description": "负责杭州陪伴机器人的配套 App 产品设计、对话语音交互原型及迭代，梳理真实用户场景并进行测试验证。",
    "requirements": "接受个人项目，希望了解实际产品设计、AI 应用及对话工作流贡献。",
    "recipient": RECIPIENT, "recipient_source": SOURCE,
}


class FixtureCareers:
    def discover(self, _sources):
        return [ROLE]


class FixtureMailbox:
    def __init__(self):
        self.fail_before_submit = False
        self.crash_after_submit = False
        self.calls = []
        self.receipts = {}
        self.reads = 0

    def probe(self):
        return {"status": "ready", "detail": "隔离测试邮箱", "capabilities": {"read": True}}

    def read_messages(self, **_evidence):
        self.reads += 1
        return []

    def send(self, item, attachment):
        self.calls.append((item["message_id"], item["recipient"], attachment["sha256"]))
        if self.fail_before_submit:
            raise ChannelUnavailable("network", "隔离测试在提交前断网")
        if not item["_before_submit"]():
            raise AssertionError("fixture tried to submit after external sends were paused")
        receipt = {"state": "confirmed", "evidence": "隔离服务的已发送回执", "provider_id": item["message_id"]}
        self.receipts[item["message_id"]] = receipt
        if self.crash_after_submit:
            raise SystemExit("隔离测试在服务端接受后模拟进程退出")
        return receipt

    def reconcile(self, item):
        return self.receipts.get(item["message_id"], {"state": "unknown", "evidence": "隔离服务无匹配回执"})


class SchedulerAcceptance(TestCase):
    WAIT_TIMEOUT = 30

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clock = datetime(2020, 11, 9, 10, 0, tzinfo=TZ)
        for target in ("job_harness.storage.now", "job_harness.service.now"):
            mocked = patch(target, side_effect=lambda: self.clock)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.mail = FixtureMailbox()
        self.service = self.open_service()
        self.resume = self.root / "隔离测试.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nisolated fixture\n%%EOF")
        self.sha256 = hashlib.sha256(self.resume.read_bytes()).hexdigest()
        self.service.ingest_legacy({
            "preferences": {"auto_send": True, "cities": "杭州", "target_scope": "杭州具身智能与机器人"},
            "sources": [{"url": SOURCE, "company": ROLE["company"], "channel": "official"}],
            "facts": [{"key": "summary_dialogue", "value": "隔离测试项目，完成角色交互原型。", "source": "fixture"}],
            "resumes": [{"label": "隔离测试原版", "path": str(self.resume), "sha256": self.sha256,
                         "scope": ["ai", "robotics"], "approved": True}],
        })

    def open_service(self):
        return Harness(Store(self.root / "data"), {"email": {"account": "candidate@fixture.example"}},
                       {"official": FixtureCareers(), "email": self.mail})

    def start_worker(self, service):
        def run():
            try:
                service.run()
            except SystemExit:
                pass

        worker = threading.Thread(target=run, daemon=True)
        worker.start()

        def stop():
            service.shutdown.set()
            service.wake.set()
            worker.join(self.WAIT_TIMEOUT)
            self.assertFalse(worker.is_alive())

        self.addCleanup(stop)
        return worker

    def rows(self, query, params=()):
        with self.service.store.tx() as connection:
            return [dict(row) for row in connection.execute(query, params)]

    def until(self, predicate):
        # These are state-machine assertions, not a storage-speed benchmark.
        # Hosted Windows runners can spend several seconds flushing the real
        # SQLite fixture; allow more than the store's 8-second busy timeout.
        deadline = time.monotonic() + self.WAIT_TIMEOUT
        while time.monotonic() < deadline:
            if value := predicate():
                return value
            time.sleep(.02)
        self.fail("timed out waiting for the isolated scheduler: " + repr({
            "tasks": self.rows("SELECT kind,state,detail FROM tasks ORDER BY id"),
            "outbox": self.rows("SELECT state,evidence FROM outbox ORDER BY id"),
            "events": self.rows("SELECT message FROM events ORDER BY id DESC LIMIT 8"),
        }))

    def outbox(self, state):
        return self.rows("SELECT * FROM outbox WHERE state=?", (state,))

    def test_background_crash_recovers_provider_receipt_without_resend(self):
        self.mail.crash_after_submit = True
        first = self.start_worker(self.service)
        first.join(self.WAIT_TIMEOUT)
        self.assertFalse(first.is_alive())
        item, = self.outbox("sending")
        self.assertEqual(self.rows("SELECT * FROM history"), [])
        self.assertEqual(self.mail.calls, [(item["message_id"], RECIPIENT, self.sha256)])
        job, = self.rows("SELECT * FROM jobs")
        self.assertEqual(json.loads(job["match_json"])["state"], "match")
        self.assertIn("交互", json.loads(job["match_json"])["reason"])
        self.assertTrue(self.rows("SELECT id FROM tasks WHERE kind='scan' AND state='done' AND detail LIKE '%生成 1 项%'"))

        self.mail.crash_after_submit = False
        self.service = self.open_service()
        self.start_worker(self.service)
        self.until(lambda: self.rows("SELECT id FROM tasks WHERE kind='reconcile' AND state='done'"))
        confirmed, = self.outbox("confirmed")
        history, = self.rows("SELECT * FROM history")
        self.assertEqual(confirmed["attempts"], 1)
        self.assertEqual(confirmed["message_id"], item["message_id"])
        self.assertEqual(history["fingerprint"], item["message_id"])
        self.assertEqual(history["evidence"], self.mail.receipts[item["message_id"]]["evidence"])
        self.assertEqual(len(self.mail.calls), 1)
        steps = self.rows("SELECT stage,outcome,outbox_id FROM task_steps WHERE task_id=?", (confirmed["origin_task_id"],))
        self.assertIn({"stage": "匹配", "outcome": "match", "outbox_id": None}, steps)
        self.assertIn({"stage": "申请", "outcome": "queued", "outbox_id": confirmed["id"]}, steps)
        self.assertIn({"stage": "官网读取", "outcome": "done", "outbox_id": None}, steps)
        self.assertIn({"stage": "发送证据核验", "outcome": "confirmed", "outbox_id": confirmed["id"]}, steps)

    def test_pause_leaves_read_scheduler_active_and_network_retry_keeps_id(self):
        self.service.action("pause_all", {})
        self.mail.fail_before_submit = True
        self.start_worker(self.service)
        initial, = self.until(lambda: self.outbox("queued"))
        self.until(lambda: self.mail.reads)
        self.assertEqual(self.mail.calls, [])
        self.assertTrue(self.rows("SELECT next_check FROM channels WHERE name='email' AND next_check IS NOT NULL"))
        self.service.action("resume_all", {})
        failure, = self.until(lambda: self.outbox("failed"))
        self.assertEqual(failure["retryable"], 1)
        self.assertEqual(self.mail.receipts, {})
        self.assertEqual(self.rows("SELECT * FROM history"), [])

        self.clock += timedelta(hours=1)
        self.mail.fail_before_submit = False
        self.service.action("resume_channel", {"channel": "email"})
        self.service.action("retry_send", {"id": failure["id"]})
        confirmed, = self.until(lambda: self.outbox("confirmed"))
        self.assertEqual(confirmed["message_id"], initial["message_id"])
        self.assertEqual(confirmed["attempts"], 2)
        self.assertEqual(len(self.mail.receipts), 1)
        self.assertEqual(len(self.rows("SELECT id FROM history")), 1)

    def test_weekend_override_sends_only_one_application_and_respects_pause(self):
        self.clock -= timedelta(days=2)
        with self.service.store.tx(True) as c:
            c.execute("UPDATE channels SET status='ready' WHERE name='email'")
            first_job = self.service.upsert_job(c, ROLE)
            second_job = self.service.upsert_job(c, {**ROLE, "company": "另一示例机器人", "external_id": "other-role"})
            first = self.service.prepare(c, first_job)
            second = self.service.prepare(c, second_job)
        self.assertFalse(self.service.send_one())
        self.service.action("pause_all", {})
        with self.assertRaisesRegex(ValueError, "外发已暂停"):
            self.service.authorize_send_once(second)
        self.service.action("resume_all", {})
        self.service.authorize_send_once(second)
        self.assertTrue(self.service.send_one())
        self.assertFalse(self.service.send_one())
        self.assertEqual([row["id"] for row in self.outbox("queued")], [first])
        self.assertEqual([row["id"] for row in self.outbox("confirmed")], [second])
        self.assertEqual(len(self.mail.receipts), 1)
        with self.service.store.tx() as c:
            self.assertEqual(self.service.send_once_authorization(c), {})
        with self.assertRaisesRegex(ValueError, "从未尝试"):
            self.service.authorize_send_once(second)

    def test_anytime_sends_back_to_back_on_weekend_and_respects_provider_backoff(self):
        self.clock -= timedelta(days=2, hours=7)
        self.service.action("preferences", {"send_anytime": "true", "daily_limit": "1"})
        with self.service.store.tx(True) as c:
            c.execute("UPDATE channels SET status='ready' WHERE name='email'")
            for index in range(3):
                jid = self.service.upsert_job(c, {**ROLE, "company": f"示例机器人{index}", "external_id": f"role{index}"})
                self.service.prepare(c, jid)
        self.assertTrue(self.service.send_one())
        self.assertTrue(self.service.send_one())
        self.assertEqual(len(self.outbox("confirmed")), 2)

        until = (self.clock + timedelta(minutes=30)).isoformat()
        with self.service.store.tx(True) as c:
            c.execute("INSERT INTO settings VALUES('provider_send_after:email', ?)", (json.dumps(until),))
        waiting, = self.outbox("queued")
        pending = next(item for item in self.service.snapshot()["outbox"] if item["id"] == waiting["id"])
        self.assertEqual(pending["expected_send_at"], until)
        self.assertFalse(self.service.send_one())
        self.clock += timedelta(minutes=30)
        self.assertTrue(self.service.send_one())
        self.assertEqual(len(self.outbox("confirmed")), 3)

    def test_anytime_automatically_retries_only_known_pre_submit_failure(self):
        self.service.action("preferences", {"send_anytime": "true"})
        with self.service.store.tx(True) as c:
            c.execute("UPDATE channels SET status='ready' WHERE name='email'")
            self.service.prepare(c, self.service.upsert_job(c, ROLE))
        self.mail.fail_before_submit = True
        self.assertTrue(self.service.send_one())
        failed_attempt, = self.outbox("queued")
        self.assertEqual(failed_attempt["attempts"], 1)
        self.assertFalse(self.service.send_one())
        self.assertFalse(self.mail.receipts)

        self.clock += timedelta(minutes=2)
        self.mail.fail_before_submit = False
        with self.service.store.tx(True) as c:
            c.execute("UPDATE channels SET status='ready' WHERE name='email'")
        self.assertTrue(self.service.send_one())
        confirmed, = self.outbox("confirmed")
        self.assertEqual(confirmed["message_id"], failed_attempt["message_id"])
        self.assertEqual(confirmed["attempts"], 2)
        self.assertEqual(len(self.mail.receipts), 1)

    def test_weekend_override_does_not_survive_stop_during_an_attempt(self):
        self.clock -= timedelta(days=2)
        with self.service.store.tx(True) as c:
            c.execute("UPDATE channels SET status='ready' WHERE name='email'")
            oid = self.service.prepare(c, self.service.upsert_job(c, ROLE))
        self.service.authorize_send_once(oid)
        claimed = self.service._claim_send()
        self.assertTrue(self.service.before_submit(claimed))
        self.service.action("stop", {})
        self.service.action("run", {})
        self.assertFalse(self.service.before_submit(claimed))
        self.assertEqual([row["id"] for row in self.outbox("unknown")], [oid])
        self.assertEqual(self.mail.calls, [])

    def test_backup_guard_refuses_to_lose_a_confirmed_provider_receipt(self):
        backup = self.service.store.backup()
        self.start_worker(self.service)
        self.until(lambda: self.outbox("confirmed"))
        with self.service.store.tx() as current, closing(sqlite3.connect(backup)) as saved:
            with self.assertRaisesRegex(ValueError, "早于当前的外发证据"):
                protect_later_sends(current, saved)

    def test_backup_guard_refuses_to_lose_a_do_not_contact_record(self):
        with self.service.store.tx(True) as connection:
            job_id = self.service.upsert_job(connection, ROLE)
        backup = self.service.store.backup()
        self.service.action("record_manual", {"job_id": job_id, "state": "stopped", "evidence": "隔离停止联系"})
        with self.service.store.tx() as current, closing(sqlite3.connect(backup)) as saved:
            with self.assertRaisesRegex(ValueError, "备份缺少当前台账的外联记录"):
                protect_later_sends(current, saved)

    def test_runtime_account_lock_excludes_a_second_data_directory(self):
        account = "candidate@fixture.example"
        first, second = self.root / "first", self.root / "second"
        with patch("job_harness.__main__.PRIVATE_ROOT", self.root / "locks"):
            with runtime_locks(first, account):
                with self.assertRaisesRegex(ValueError, "邮箱已有运行中的 Harness"):
                    runtime_locks(second, account)
            with runtime_locks(second, account):
                self.assertTrue((second / "runtime.lock").is_file())
