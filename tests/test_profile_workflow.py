"""Candidate workflow acceptance through a real, isolated loopback console.

All PDFs, contacts and messages are fictional. No provider or scheduler runs.
"""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from job_harness.console import make_handler
from job_harness.demo import synthetic_pdf
from job_harness.service import Harness
from job_harness.storage import Store, one


class NeverSendMailbox:
    enabled = True

    def send(self, *_args, **_kwargs):
        raise AssertionError("Acceptance fixtures must never send externally")


class CandidateProfileWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "private"
        self.service = Harness(Store(self.root), {"email": {"enabled": False}}, {})
        self.token = "fictional-profile-token"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.server.server_port
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server.RequestHandlerClass = make_handler(self.service, self.token, self.port)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            connection.close()

    def form(self, action, **fields):
        values = {"token": self.token, "action": action, "next": "/profile", **fields}
        status, content = self.request("POST", "/action", urlencode(values), {
            "Origin": self.origin, "Sec-Fetch-Site": "same-origin", "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        return status, json.loads(content)

    def upload(self, *, filename="fictional.pdf", origin=None, token=None, extra_fields=(), extra_file=False):
        boundary = "job-harness-fictional-upload-boundary"
        fields = [("token", self.token if token is None else token), ("action", "resume_upload"),
                  ("next", "/profile"), ("label", "Fictional AI resume"), ("scope", "ai"), ("approved", "on")]
        fields.extend(extra_fields)
        parts = []
        for key, value in fields:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        for _ in range(2 if extra_file else 1):
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="resume"; '
                          f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n').encode()
                         + synthetic_pdf() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        status, content = self.request("POST", "/action", b"".join(parts), {
            "Origin": self.origin if origin is None else origin, "Sec-Fetch-Site": "same-origin",
            "Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}",
        })
        return status, json.loads(content)

    def rows(self, query, args=()):
        with self.service.store.tx() as connection:
            return [dict(row) for row in connection.execute(query, args)]

    def assert_no_resume_saved(self):
        self.assertFalse((self.root / "resumes").exists())
        self.assertEqual(self.rows("SELECT id FROM resumes"), [])
        self.assertEqual(self.rows("SELECT resume_id FROM resume_texts"), [])

    def upload_confirmed(self):
        status, result = self.upload()
        self.assertEqual(status, 200, result)
        row = self.rows("SELECT * FROM resume_texts")[0]
        self.assertEqual(row["status"], "extracted")
        status, result = self.form("confirm_resume_text", id=row["resume_id"], text=row["text"], confirmed="on")
        self.assertEqual(status, 200, result)
        return row["resume_id"]

    def job(self):
        with self.service.store.tx(True) as connection:
            return self.service.upsert_job(connection, {
                "channel": "official", "external_id": "fictional-ai-role", "company": "Fictional Orbit",
                "title": "AI 应用产品经理", "location": "杭州", "status": "open", "domain": "orbit.example",
                "url": "https://orbit.example/careers/ai-product", "description": "负责 AI 应用需求、原型与评测迭代。",
                "requirements": "看重完整项目和产品设计。", "recipient": "careers@orbit.example",
                "recipient_source": "https://orbit.example/careers",
            })

    def enable_test_mailbox(self, *, status="ready", paused=False):
        self.service.config["email"] = {"enabled": True, "account": "candidate@fixture.example"}
        self.service.adapters["email"] = NeverSendMailbox()
        with self.service.store.tx(True) as connection:
            self.service.channel_update(connection, "email", status, "isolated acceptance fixture")
            connection.execute("UPDATE channels SET paused=? WHERE name='email'", (paused,))

    def test_real_multipart_upload_review_profile_snapshot_and_page(self):
        status, response = self.upload()
        self.assertEqual(status, 200, response)
        row = self.rows("SELECT * FROM resume_texts")[0]
        self.assertEqual(row["state"], "pending")
        self.assertIn("Fictional resume", row["text"])
        self.assertEqual(self.rows("SELECT * FROM facts"), [])
        self.assertEqual(self.service.snapshot()["candidate"]["profile"], {})
        status, response = self.form("confirm_resume_text", id=row["resume_id"], text=row["text"], confirmed="on")
        self.assertEqual(status, 200, response)
        summary = "虚构资料：完成过 AI 应用原型、需求判断和测试迭代。 <b>literal text</b>"
        status, response = self.form("save_candidate_profile", summary=summary, intro="虚构自我介绍",
                                     confirmed="on", profile_model_consent="on")
        self.assertEqual(status, 200, response)
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["candidate"]["profile"]["summary"], summary)
        self.assertTrue(snapshot["candidate"]["profile"]["confirmed"])
        self.assertTrue(snapshot["preferences"]["profile_model_consent"])
        self.assertEqual(snapshot["candidate"]["resume_texts"][0]["state"], "confirmed")
        status, page = self.request("GET", "/profile")
        self.assertEqual(status, 200)
        self.assertIn("我的经历与简历", page)
        self.assertIn("Fictional AI resume", page)
        self.assertIn("Fictional resume", page)
        self.assertIn("&lt;b&gt;literal text&lt;/b&gt;", page)
        self.assertNotIn("<b>literal text</b>", page)
        self.assertIn('enctype="multipart/form-data"', page)
        self.assertIn('name="profile_model_consent"', page)

    def test_wrong_origin_and_token_never_stage_a_resume(self):
        for changes in ({"origin": "https://unrelated.example"}, {"origin": "null"}, {"token": "expired"}):
            with self.subTest(changes=changes):
                status, result = self.upload(**changes)
                self.assertEqual(status, 403, result)
                self.assert_no_resume_saved()

    def test_duplicate_multipart_fields_and_files_are_blocked_before_staging(self):
        for changes in ({"extra_fields": [("scope", "finance")]},
                        {"extra_fields": [("token", self.token)]}, {"extra_file": True}):
            with self.subTest(changes=changes):
                status, result = self.upload(**changes)
                self.assertEqual(status, 400, result)
                self.assert_no_resume_saved()

    def test_filename_traversal_is_rejected_by_actual_upload_endpoint(self):
        for filename in ("../escape.pdf", "nested/escape.pdf", "C:\\fakepath\\escape.pdf"):
            with self.subTest(filename=filename):
                status, result = self.upload(filename=filename)
                self.assertEqual(status, 400, result)
                self.assert_no_resume_saved()
        self.assertFalse((self.root.parent / "escape.pdf").exists())

    def test_profile_and_extraction_require_explicit_confirmation(self):
        status, _ = self.upload()
        self.assertEqual(status, 200)
        row = self.rows("SELECT * FROM resume_texts")[0]
        status, _ = self.form("confirm_resume_text", id=row["resume_id"], text=row["text"])
        self.assertEqual(status, 400)
        status, _ = self.form("save_candidate_profile", summary="虚构经历", profile_model_consent="on")
        self.assertEqual(status, 400)
        self.assertEqual(self.rows("SELECT state FROM resume_texts"), [{"state": "pending"}])
        self.assertEqual(self.service.snapshot()["candidate"]["profile"], {})
        self.assertFalse(self.service.snapshot()["preferences"].get("profile_model_consent", False))

    def test_followup_authorization_requires_enabled_ready_unpaused_mailbox(self):
        for status, paused, enabled in (("unverified", False, False), ("ready", False, False),
                                        ("unverified", False, True), ("ready", True, True)):
            with self.subTest(status=status, paused=paused, enabled=enabled):
                self.enable_test_mailbox(status=status, paused=paused)
                self.service.config["email"]["enabled"] = enabled
                code, result = self.form("save_followup_authorization", enabled="on", scope_confirmed="on",
                                         allow_follow_up="on")
                self.assertEqual(code, 400, result)
                snapshot = self.service.snapshot()
                self.assertFalse(snapshot["preferences"]["auto_send"])
                self.assertEqual(snapshot["candidate"]["authorization"], {})
        self.enable_test_mailbox()
        code, _ = self.form("save_followup_authorization", enabled="on")
        self.assertEqual(code, 400)

    def test_interview_invite_stays_pending_even_with_email_authorization(self):
        self.enable_test_mailbox()
        status, response = self.form("save_followup_authorization", enabled="on", scope_confirmed="on")
        self.assertEqual(status, 200, response)
        job_id = self.job()
        body = "请确认 2030-11-14 15:30 的面试时间，可以吗？"
        with self.service.store.tx(True) as connection:
            self.assertTrue(self.service.ingest_message(connection, {
                "channel": "email", "external_id": "fictional-interview", "message_id": "<invite@orbit.example>",
                "thread_external_id": "fictional-interview-thread", "job_id": job_id, "company": "Fictional Orbit",
                "sender": "careers@orbit.example", "direction": "inbound", "subject": "AI 应用岗位面试邀请", "body": body,
            }))
        decisions = self.rows("SELECT kind,state,draft FROM decisions")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["kind"], "interview")
        self.assertEqual(decisions[0]["state"], "pending")
        self.assertNotIn("15:30", decisions[0]["draft"])
        self.assertEqual(self.rows("SELECT id FROM outbox"), [])
        self.assertEqual(self.rows("SELECT id FROM history WHERE status='confirmed'"), [])
        self.assertEqual(self.service.snapshot()["decisions"][0]["state"], "pending")

    def test_changing_confirmed_profile_stales_previously_queued_application(self):
        rid = self.upload_confirmed()
        job_id = self.job()
        with self.service.store.tx(True) as connection:
            resume = one(connection, "SELECT * FROM resumes WHERE id=?", (rid,))
            oid = self.service.create_outbox(connection, job_id=job_id, kind="application",
                recipient="careers@orbit.example", subject="Fictional application", body="虚构的申请正文", resume=resume, auto=True)
        self.assertEqual(self.rows("SELECT state,approved FROM outbox WHERE id=?", (oid,)),
                         [{"state": "queued", "approved": 1}])
        status, result = self.form("save_candidate_profile", summary="本人确认的虚构更新：完成过工作流产品原型。",
                                   confirmed="on", profile_model_consent="on")
        self.assertEqual(status, 200, result)
        row = self.rows("SELECT state,approved,approval_hash,evidence FROM outbox WHERE id=?", (oid,))[0]
        self.assertEqual(row["state"], "stale")
        self.assertEqual(row["approved"], 0)
        self.assertIsNone(row["approval_hash"])
        self.assertIn("重新生成", row["evidence"])
        self.assertEqual(self.rows("SELECT id FROM history WHERE status='confirmed'"), [])


if __name__ == "__main__":
    unittest.main()
