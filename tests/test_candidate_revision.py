"""Candidate edits and model changes must invalidate only the right state."""
from unittest import TestCase

import test_candidate_scheduler as fixtures
from job_harness.service import set_setting
from job_harness.storage import one


class CandidateRevisionTests(TestCase):
    def setUp(self):
        self.fixture = fixtures.CandidateSchedulerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.h

    def test_new_confirmed_profile_regenerates_an_unattempted_application(self):
        self.fixture.discover()
        self.assertTrue(self.service.task_one())
        original = self.fixture.rows("SELECT * FROM outbox")[0]
        self.assertEqual(original["state"], "queued")
        self.fixture.save_profile(
            summary="我做过角色对话应用的需求分析、交互原型和回归测试。",
            intro="我完成过角色对话产品的需求取舍与回归评测。",
        )
        self.assertEqual(self.fixture.rows("SELECT state FROM outbox")[0]["state"], "stale")
        self.assertTrue(self.service.task_one())
        updated = self.fixture.rows("SELECT * FROM outbox")
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["state"], "queued")
        self.assertEqual(updated[0]["attempts"], 0)
        self.assertIn("需求取舍与回归评测", updated[0]["body"])
        self.assertNotEqual(updated[0]["approval_hash"], original["approval_hash"])
        self.assertEqual(updated[0]["approval_hash"], self.service.approval_hash(updated[0]))
        self.assertEqual(self.fixture.mail.calls, [])

    def test_changed_model_requires_a_new_candidate_review(self):
        job_id = self.fixture.discover()
        self.assertTrue(self.service.task_one())
        with self.service.store.tx(True) as connection:
            job = one(connection, "SELECT * FROM jobs WHERE id=?", (job_id,))
            before = self.service.job_match(connection, job, self.service.preferences(connection))
            self.assertEqual(before["candidate_review"]["advice"]["fit"], "related")
            set_setting(connection, "model_config", {
                "provider": "codex_cli", "enabled": True, "name": "another-fictional-model", "max_calls": 50,
            })
            after = self.service.job_match(connection, job, self.service.preferences(connection))
            self.assertIn("candidate_gate", after)
            self.assertNotEqual(before["candidate_review"]["context_hash"], after["candidate_review"]["context_hash"])
            self.assertNotEqual(after["candidate_review"].get("advice", {}).get("fit"), "related")
        self.assertEqual(len(self.fixture.model.calls), 1)
        self.assertEqual(self.fixture.mail.calls, [])

    def test_profile_edit_preserves_an_uncertain_attempt_without_requeueing(self):
        self.fixture.discover()
        self.service.task_one()
        with self.service.store.tx(True) as connection:
            connection.execute("UPDATE outbox SET state='unknown',attempts=1,evidence='隔离测试：结果待核验'")
        self.fixture.save_profile(summary="我做过角色对话应用的需求整理和回归测试。")
        self.service.task_one()
        records = self.fixture.rows("SELECT state,attempts,evidence FROM outbox")
        self.assertEqual(records, [{"state": "unknown", "attempts": 1, "evidence": "隔离测试：结果待核验"}])
        self.assertEqual(self.fixture.mail.calls, [])

    def test_profile_edit_before_worker_does_not_spend_budget_twice_on_same_context(self):
        self.fixture.discover()
        self.fixture.save_profile(summary="我做过角色对话应用的需求分析、交互原型和回归测试。")
        self.service.task_one()
        self.service.task_one()
        self.assertEqual(len(self.fixture.model.calls), 1)
        self.assertEqual(len(self.fixture.rows("SELECT context_hash FROM candidate_reviews WHERE state='done'")), 1)

    def test_liepin_full_jd_enrichment_schedules_current_candidate_review(self):
        class LiepinFixture:
            def read_job(self, *arguments):
                return {"url": "https://www.liepin.com/job/fictional.shtml",
                        "description": fixtures.ROLE["description"], "requirements": fixtures.ROLE["requirements"],
                        "location": fixtures.ROLE["location"], "salary": fixtures.ROLE["salary"], "status": "open"}

        self.service.adapters["liepin"] = LiepinFixture()
        with self.service.store.tx(True) as connection:
            job_id = self.service.upsert_job(connection, {
                **fixtures.ROLE, "channel": "liepin", "external_id": "liepin-partial",
                "description": "", "requirements": "", "status": "partial",
            })
            self.service.enqueue(connection, "liepin_detail", "liepin", "fictional-detail",
                                 payload={"job_id": job_id, "external_id": "liepin-partial"})
            task_id = connection.execute("SELECT id FROM tasks WHERE kind='liepin_detail'").fetchone()[0]
        self.service.read_liepin_detail({"job_id": job_id, "external_id": "liepin-partial"}, task_id)
        tasks = self.fixture.rows("SELECT payload,state FROM tasks WHERE kind='model_probe'")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["state"], "queued")

    def test_new_exclusion_blocks_an_already_queued_automatic_reply(self):
        job_id = self.fixture.discover()
        self.service.task_one()
        self.service.send_one()
        with self.service.store.tx(True) as connection:
            application = one(connection, "SELECT message_id FROM outbox WHERE kind='application'")
            self.service.ingest_message(connection, {
                "channel": "email", "external_id": "fictional-routine-question",
                "message_id": "<routine@paper-bridge.example>",
                "thread_external_id": application["message_id"], "references": [application["message_id"]],
                "job_id": job_id, "sender": fixtures.ROLE["recipient"], "subject": "岗位沟通",
                "body": "对岗位有什么疑问？",
            })
        self.assertEqual(self.fixture.rows("SELECT state FROM outbox WHERE kind='reply'"), [{"state": "queued"}])
        self.service.action("add_exclusion", {"pattern": fixtures.ROLE["company"], "note": "本人排除"})
        self.service.send_one()
        self.assertEqual(len(self.fixture.mail.calls), 1)
        self.assertNotEqual(self.fixture.rows("SELECT state FROM outbox WHERE kind='reply'")[0]["state"], "confirmed")

    def test_profile_edit_hands_old_attachment_reply_to_a_new_unapproved_draft(self):
        job_id = self.fixture.discover()
        self.service.task_one()
        with self.service.store.tx(True) as connection:
            self.service.ingest_message(connection, {
                "channel": "email", "external_id": "fictional-request-before-application",
                "message_id": "<request@paper-bridge.example>", "thread_external_id": "fictional-material-thread",
                "job_id": job_id, "sender": fixtures.ROLE["recipient"], "subject": "材料补充",
                "body": "请发一下简历。",
            })
        previous = self.fixture.rows("SELECT * FROM outbox WHERE kind='reply'")[0]
        self.assertEqual(previous["state"], "queued")
        self.assertIsNotNone(previous["resume_id"])
        self.fixture.save_profile(intro="本人更正后的真实项目介绍。")
        decisions = self.fixture.rows("SELECT * FROM decisions WHERE kind='experience' AND state='pending'")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["message_id"], previous["anchor_id"])
        resolution = "您好，我已更新项目介绍，希望进一步核对您需要的材料后再补充。"
        self.service.action("decide", {"id": decisions[0]["id"], "resolution": resolution})
        current = self.fixture.rows("SELECT * FROM outbox WHERE kind='reply'")
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["id"], previous["id"])
        self.assertEqual(current[0]["state"], "draft")
        self.assertEqual(current[0]["body"], resolution)
        self.assertEqual(current[0]["approved"], 0)
        self.assertEqual(current[0]["policy_generated"], 0)
        for field in ("resume_id", "attachment_sha", "attachment_filename", "approval_hash"):
            self.assertIsNone(current[0][field])
        self.service.send_one()
        self.assertEqual(self.fixture.mail.calls, [])
