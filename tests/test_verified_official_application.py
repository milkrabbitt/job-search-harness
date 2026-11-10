import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from job_harness.channels import OfficialChannel
from job_harness.service import DEFAULTS, Harness
from job_harness.storage import TZ, Store


HOST = "research.employer.example"
URL = f"https://{HOST}/jobs/example/"
ADDRESS = "recruit@employer.example"
SOURCE = {"url": URL, "company": "示例雇主", "recipient": ADDRESS, "recipient_source": URL}
PAGE = """<h1>AI 应用工程师</h1><h2 id="duty">岗位职责</h2><ul><li>设计 Agent 原型和验证工作流</li></ul>
<h3 id="req">任职要求</h3><ul><li>有 AI 项目经验优先</li></ul>
<dt>工作地点</dt><dd>杭州市余杭区</dd><dt>薪资</dt><dd>面议</dd>
<p>投递方式：将简历发送至 recruit@employer.example，标题注明应聘 AI 应用工程师。</p>"""


class VerifiedOfficialApplicationTests(unittest.TestCase):
    def setUp(self):
        fixture_host = patch("job_harness.channels.FUXIAN_HOST", HOST)
        fixture_host.start()
        self.addCleanup(fixture_host.stop)

    def test_official_exact_address_needs_live_instructions_and_preverified_source(self):
        adapter = OfficialChannel({})
        row = next(adapter._fuxian(SOURCE, URL, PAGE))
        self.assertEqual(row["recipient"], ADDRESS)
        changed = PAGE.replace("将简历发送至", "产品咨询可联系")
        self.assertNotIn("recipient", next(adapter._fuxian(SOURCE, URL, changed)))
        self.assertNotIn("recipient", next(adapter._fuxian({**SOURCE, "recipient": "other@example.net"}, URL, PAGE)))

    def test_source_change_blocks_before_email_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            row = next(OfficialChannel({})._fuxian(SOURCE, URL, PAGE))
            official = Mock()
            official.discover.return_value = [{**row, "recipient": "different@example.net"}]
            email = Mock()
            service = Harness(Store(directory), {}, {"official": official, "email": email})
            with service.store.tx(True) as c:
                service.add_source(c, SOURCE)
                jid = service.upsert_job(c, row)
                oid = service.create_outbox(c, job_id=jid, kind="application", recipient=ADDRESS,
                                            subject="应聘 AI 应用工程师", body="申请正文", auto=True)
                c.execute("UPDATE outbox SET state='sending' WHERE id=?", (oid,))
            item = {"job_id": jid, "id": oid, "kind": "application", "to_address": ADDRESS, "attempts": 0, "channel": "email"}
            self.assertFalse(service.verify_live_source(item))
            email.send.assert_not_called()
            with service.store.tx() as c:
                state, evidence = c.execute("SELECT state,evidence FROM outbox WHERE id=?", (oid,)).fetchone()
            self.assertEqual(state, "failed")
            self.assertIn("未提交", evidence)

    def test_receipt_is_reconciled_without_sending_again(self):
        with tempfile.TemporaryDirectory() as directory:
            email = Mock()
            service = Harness(Store(directory), {"email": {"account": "candidate@fixture.example"}}, {"email": email})
            row = next(OfficialChannel({})._fuxian(SOURCE, URL, PAGE))
            with service.store.tx(True) as c:
                jid = service.upsert_job(c, row)
                oid = service.create_outbox(c, job_id=jid, kind="application", recipient=ADDRESS,
                                            subject="应聘 AI 应用工程师", body="申请正文", auto=True)
                c.execute("UPDATE outbox SET state='sending' WHERE id=?", (oid,))
            service.finish_send({"id": oid, "job_id": jid, "kind": "application", "channel": "email",
                                 "to_address": ADDRESS, "message_id": "<candidate@fixture.example>", "thread_id": None,
                                 "body": "申请正文", "subject": "应聘 AI 应用工程师", "refs": None},
                                {"state": "confirmed", "evidence": "SMTP 250"})
            email.reconcile.return_value = {"state": "confirmed", "evidence": "IMAP 已核实"}
            with service.store.tx(True) as c:
                task_id = c.execute("SELECT id FROM tasks WHERE kind='verify_sent'").fetchone()[0]
                c.execute("UPDATE tasks SET due_at='2000-01-01T00:00:00+08:00' WHERE id=?", (task_id,))
            self.assertTrue(service.task_one())
            with service.store.tx() as c:
                self.assertEqual(tuple(c.execute("SELECT state,evidence FROM outbox").fetchone()), ("confirmed", "SMTP 250 IMAP 已核实"))
                self.assertEqual(c.execute("SELECT count(*) FROM history").fetchone()[0], 1)
            email.send.assert_not_called()

    def test_earliest_send_respects_workday_window(self):
        with patch("job_harness.service.now", return_value=datetime(2020, 11, 7, 17, tzinfo=TZ)):
            self.assertEqual(Harness.next_send_window(DEFAULTS, None), "2020-11-09T09:30:00+08:00")


if __name__ == "__main__":
    unittest.main()
