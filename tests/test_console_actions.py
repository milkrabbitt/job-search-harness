import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.parse import urlencode

from job_harness.console import Console, make_handler


class ConsoleActionTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.snapshot.return_value = {}
        self.service.action.return_value = "后台任务已恢复"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.server.server_port
        self.server.RequestHandlerClass = make_handler(self.service, "fixture-token", self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, origin, site="same-origin", token="fixture-token"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            conn.request("POST", "/action", urlencode({"token": token, "action": "run", "next": "/"}), headers={
                "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                "Origin": origin, "Sec-Fetch-Site": site,
            })
            response = conn.getresponse()
            self.assertEqual(response.getheader("Referrer-Policy"), "same-origin")
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_loopback_origin_and_token_both_required(self):
        origin = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.post("null")[0], 403)
        self.assertEqual(self.post("https://other.example", "cross-site")[0], 403)
        self.assertEqual(self.post(f"http://localhost:{self.port}")[0], 403)
        status, failure = self.post(origin, token="expired-token")
        self.assertEqual(status, 403)
        self.assertIn("令牌已失效", failure["notice"])
        self.service.action.assert_not_called()
        status, result = self.post(origin)
        self.assertEqual(status, 200)
        self.assertTrue(result["location"].startswith("/?notice="))
        self.service.action.assert_called_once_with("run", {})

    def test_button_receives_specific_failure_without_redirect(self):
        self.service.action.side_effect = ValueError("邮箱渠道尚未就绪")
        status, result = self.post(f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 400)
        self.assertEqual(result, {"notice": "邮箱渠道尚未就绪"})

    def test_browser_attachment_request_keeps_platform_evidence_and_pending_state(self):
        job_url = "https://www.zhipin.com/job_detail/fixture-boss-fde.html"
        snapshot = {"browser_actions": [{"id": 31, "job_id": 72, "company": "示例公司", "title": "FDE",
                                         "channel": "boss", "kind": "attachment", "state": "attachment_requested",
                                         "url": job_url, "evidence": "等待对方同意 <确认>", "resume_label": "AI 简历"}]}
        page = Console(snapshot, "fixture-token", "/outbox").render({})
        self.assertIn('id="browser-actions"', page)
        self.assertIn('href="' + job_url + '"', page)
        self.assertIn("附件请求待接受", page)
        self.assertIn("等待对方同意 &lt;确认&gt;", page)
        self.assertNotIn("已确认发送", page)
