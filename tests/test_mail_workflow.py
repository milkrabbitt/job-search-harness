"""Isolated inbox and Sent reconciliation via the Harness reader."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from job_harness.__main__ import protect_later_sends
from job_harness.service import Harness
from job_harness.storage import Store, TZ


OWN = "candidate@fixture.example"
HR = "jobs@fixture.example"
ROLE = "杭州陪伴机器人产品经理"
COMPANY = "示例机器人"
ORIGINAL = "<application@fixture.example>"
TODAY = datetime(2020, 11, 9, 10, 0, tzinfo=TZ)


class FixtureMailbox:
    def __init__(self):
        self.rows = []

    def read_messages(self, **_options):
        return self.rows


class MailWorkflowAcceptance(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for name in ("job_harness.storage.now", "job_harness.service.now"):
            patched = patch(name, return_value=TODAY)
            patched.start()
            self.addCleanup(patched.stop)
        self.store = Store(Path(temp.name) / "data")
        self.mailbox = FixtureMailbox()
        self.service = Harness(self.store, {"email": {"account": OWN}}, {"email": self.mailbox})
        resume = Path(temp.name) / "隔离简历.pdf"
        resume.write_bytes(b"%PDF-1.4\nfixture resume\n%%EOF")
        self.service.ingest_legacy({
            "preferences": {"auto_send": True},
            "resumes": [{"label": "隔离简历", "path": str(resume), "scope": ["ai", "robotics"],
                         "sha256": hashlib.sha256(resume.read_bytes()).hexdigest(), "approved": True}],
            "facts": [{"key": "portfolio_public", "value": "https://portfolio.example/public", "source": "fixture"}],
        })
        with self.store.tx(True) as c:
            self.job = self.service.upsert_job(c, {
                "channel": "official", "company": COMPANY, "title": ROLE, "domain": "fixture.example",
                "url": "https://fixture.example/careers/interaction", "external_id": "fixture-interaction",
                "status": "open", "location": "杭州",
                "description": "负责杭州陪伴机器人的配套 App 产品设计、对话语音交互原型及迭代。",
                "requirements": "接受个人项目，欢迎有 AI 应用及对话工作流项目的人选。",
                "recipient": HR, "recipient_source": "https://fixture.example/careers/interaction",
            })
            self.history = c.execute("""INSERT INTO history(job_id,channel,company,title,status,source,evidence,
                occurred_at,recipient,fingerprint) VALUES(?,'email',?,?, 'inquiry','fixture',?,?,?,?)""",
                (self.job, COMPANY, ROLE, "咨询" + ROLE + "岗位", TODAY.date().isoformat(), HR, "history-inquiry")).lastrowid

    def message(self, mid, body, *, outgoing=False, minute=-15, parents=(), **fields):
        ts = (TODAY + timedelta(minutes=minute)).isoformat()
        return {"channel": "email", "direction": "outbound" if outgoing else "inbound",
                "external_id": mid, "message_id": mid, "thread_external_id": parents[0] if parents else mid,
                "sender": OWN if outgoing else HR, "recipient": HR if outgoing else OWN, "body": body,
                "subject": "咨询" + ROLE + "岗位" if not parents else "Re: 咨询" + ROLE + "岗位",
                "occurred_at": ts, "mail_date": ts, "references": list(parents),
                "in_reply_to": parents[-1] if parents else None, "sender_authenticated": True, **fields}

    def sent(self):
        return self.message(ORIGINAL, "请问本岗位还在招聘吗？", outgoing=True, minute=-45)

    def read(self, *rows):
        self.mailbox.rows = rows
        return self.service.check_replies("email")

    def select(self, query, params=()):
        with self.store.tx() as c:
            return [tuple(row) for row in c.execute(query, params)]

    def audit(self, mid):
        return self.select("""SELECT a.state,a.job_id,a.history_id FROM mail_audits a
                           JOIN messages m ON m.id=a.message_id WHERE m.message_id=?""", (mid,))[0]

    def test_first_sync_records_baseline_and_never_sends_historical_request(self):
        initial = self.message("<initial@fixture.example>", "您好，可以发一份简历吗？", parents=(ORIGINAL,))
        status = self.read(self.sent(), initial)
        self.assertIn("首次读取建立基线", status)
        self.assertEqual(self.audit(ORIGINAL), ("historical_sent", self.job, self.history))
        self.assertEqual(self.audit(initial["message_id"]), ("historical_review", self.job, self.history))
        self.assertEqual(self.select("SELECT id FROM outbox"), [])

    def test_exact_recruiter_without_provider_authentication_is_held(self):
        self.read(self.sent())
        spoof = self.message("<exact-from@fixture.example>", "您好，请提供作品链接。",
                             parents=(ORIGINAL,), sender_authenticated=False)
        self.read(spoof)
        self.assertEqual(self.audit(spoof["message_id"])[0], "decision_pending")
        self.assertEqual(self.select("SELECT id FROM outbox"), [])

    def test_successful_retry_clears_network_error_without_assuming_smtp_is_ready(self):
        with self.store.tx(True) as c:
            c.execute("UPDATE channels SET status='network' WHERE name='email'")
        self.read()
        self.assertEqual(self.select("SELECT status FROM channels WHERE name='email'"), [("read_only",)])
        self.assertEqual(self.select("SELECT kind,state FROM tasks WHERE dedupe_key LIKE 'recovered-smtp:%'"),
                         [("probe", "queued")])

    def test_rfc_reply_already_in_sent_does_not_make_a_decision_or_duplicate_reply(self):
        received = self.message("<interview@fixture.example>", "请确认 11 月 11 日下午的面试安排。", parents=(ORIGINAL,))
        response = self.message("<reply@fixture.example>", "您好，已收到您的安排。", outgoing=True, minute=-5,
                                parents=(ORIGINAL, received["message_id"]))
        self.read(self.sent(), received, response)
        self.assertEqual(self.audit(received["message_id"]), ("handled", self.job, self.history))
        self.read(received, response)
        self.assertEqual(self.select("SELECT count(*) FROM messages"), [(3,)])
        self.assertEqual(self.select("SELECT id FROM decisions"), [])
        self.assertEqual(self.select("SELECT id FROM outbox"), [])

    def test_new_message_supersedes_queued_reply_and_creates_manual_decision(self):
        self.read(self.sent())
        first = self.message("<portfolio@fixture.example>", "您好，请提供作品链接。", parents=(ORIGINAL,))
        self.read(first)
        self.assertEqual(self.audit(first["message_id"])[0], "reply_queued")
        self.assertEqual(self.select("SELECT state,resume_id,in_reply_to FROM outbox"), [("queued", None, first["message_id"])])
        later = self.message("<interview@fixture.example>", "我们想改期面试，可以周三下午三点吗？", minute=-5,
                             parents=(ORIGINAL, first["message_id"]))
        self.read(later)
        self.assertEqual(self.audit(first["message_id"])[0], "superseded")
        self.assertEqual(self.audit(later["message_id"])[0], "decision_pending")
        self.assertEqual(self.select("SELECT state FROM outbox"), [("stale",)])
        self.assertEqual(self.select("SELECT kind,state FROM decisions"), [("interview", "pending")])

    def test_explicit_expired_deadline_does_not_create_a_send_or_live_decision(self):
        self.read(self.sent())
        old = self.message("<assignment@fixture.example>", "请完成作业，截止 2020-11-08 18:00。", parents=(ORIGINAL,))
        self.read(old)
        self.assertEqual(self.audit(old["message_id"])[0], "expired")
        self.assertEqual(self.select("SELECT id FROM decisions"), [])
        self.assertEqual(self.select("SELECT id FROM outbox"), [])

    def test_forged_sender_or_alternate_reply_to_does_not_receive_attachment(self):
        for fields in ({"sender": "fake@unverified.example"}, {"reply_to": "fake@unverified.example"}):
            with self.subTest(fields=fields):
                self.read(self.sent())
                mid = f"<spoof-{next(iter(fields))}@fixture.example>"
                inbound = self.message(mid, "您好，可以发一份简历吗？", parents=(ORIGINAL,), **fields)
                self.read(inbound)
                self.assertEqual(self.audit(mid)[0], "decision_pending")
                self.assertEqual(self.select("SELECT id FROM outbox"), [])

    def test_prior_confirmed_attachment_prevents_an_automatic_second_copy(self):
        with self.store.tx(True) as c:
            c.execute("UPDATE history SET status='confirmed',evidence=? WHERE id=?", ("原版 PDF 已发", self.history))
        self.read(self.sent())
        inbound = self.message("<second-copy@fixture.example>", "您好，可以发一份简历吗？", parents=(ORIGINAL,))
        self.read(inbound)
        self.assertEqual(self.audit(inbound["message_id"])[0], "decision_pending")
        self.assertEqual(self.select("SELECT kind,state FROM decisions"), [("resume", "pending")])
        self.assertEqual(self.select("SELECT id FROM outbox"), [])

    def test_manual_company_followup_closes_wechat_decision_and_keeps_later_messages_local(self):
        wechat = self.message("<wechat@fixture.example>", "请通过我发送的微信好友申请。", parents=(ORIGINAL,))
        self.read(self.sent(), wechat)
        decision_id = self.select("SELECT id FROM decisions WHERE kind='manual_channel_pending'")[0][0]
        evidence = "已建立联系，人工跟进。本人已通过微信。"
        self.service.action("employer_manual_followup", {"job_id": self.job, "decision_id": decision_id,
                                                         "resolution": evidence})
        self.assertEqual(self.select("SELECT state,resolution FROM decisions"), [("resolved", evidence)])
        self.assertEqual(self.audit(wechat["message_id"])[0], "manual_followup")
        new_request = self.message("<new-request@fixture.example>", "您好，请提供作品链接。", minute=-5,
                                   parents=(ORIGINAL, wechat["message_id"]))
        self.read(new_request)
        self.assertEqual(self.audit(new_request["message_id"])[0], "manual_followup")
        self.assertEqual(self.select("SELECT kind,state FROM decisions ORDER BY id"),
                         [("manual_channel_pending", "resolved"), ("portfolio", "pending")])
        self.assertEqual(self.select("SELECT id FROM outbox"), [])

    def test_company_followup_keeps_actual_send_evidence_and_distinct_same_name_employers(self):
        with self.store.tx(True) as c:
            unresolved = self.service.upsert_job(c, {"channel": "liepin", "company": COMPANY, "title": "另一个产品岗位",
                                                    "url": "https://www.liepin.com/job/1234567890.shtml"})
            distinct = self.service.upsert_job(c, {"channel": "official", "company": COMPANY, "title": "同名公司的产品岗位",
                                                  "url": "https://different.example/jobs/role"})
            known_send = self.service.create_outbox(c, job_id=self.job, kind="application", recipient=HR,
                                                     subject="已进入发送", body="测试内容", auto=True)
            c.execute("UPDATE outbox SET state='unknown',evidence='服务端可能已接收' WHERE id=?", (known_send,))
            pending_send = self.service.create_outbox(c, job_id=unresolved, kind="application", recipient=HR,
                                                       subject="等待发送", body="测试内容", auto=True)
        self.service.action("employer_manual_followup", {"job_id": self.job, "resolution": "本人已联系"})
        self.assertEqual(self.select("SELECT state,evidence FROM outbox WHERE id=?", (known_send,)),
                         [("unknown", "服务端可能已接收")])
        self.assertEqual(self.select("SELECT state,approved FROM outbox WHERE id=?", (pending_send,)), [("stale", 0)])
        with self.store.tx(True) as c:
            blocked = self.service.create_outbox(c, job_id=unresolved, kind="reply", recipient=HR,
                                                 subject="人工已接管", body="测试内容")
            allowed = self.service.create_outbox(c, job_id=distinct, kind="application", recipient=HR,
                                                 subject="同名且域名不同", body="测试内容")
        self.assertIsNone(blocked)
        self.assertIsNotNone(allowed)

    def test_backup_from_released_period_does_not_remove_a_reinstated_hold(self):
        data = {"job_id": self.job, "resolution": "本人已联系"}
        employer_id = self.select("SELECT employer_id FROM jobs WHERE id=?", (self.job,))[0][0]
        self.service.action("employer_manual_followup", data)
        self.service.action("employer_resume_outreach", {"employer_id": employer_id})
        older = self.store.backup()
        self.service.action("employer_manual_followup", data)
        with self.store.tx() as current, closing(sqlite3.connect(older)) as previous:
            with self.assertRaisesRegex(ValueError, "人工跟进的暂停记录"):
                protect_later_sends(current, previous)
