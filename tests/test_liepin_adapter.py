import json
import unittest
from unittest.mock import Mock, patch

from job_harness.channels import ChannelUnavailable
from job_harness.liepin_adapter import HOST, SEARCH_PATH, LiepinChannel


class LiepinAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LiepinChannel({"queries": [{"keyword": "交互产品经理", "location": "杭州"}]})

    def test_search_uses_verified_host_and_read_only_endpoint(self):
        expected_job = {
            "jobId": 123456789, "jobKind": "2", "jobName": "交互产品经理", "companyName": "模型公司",
            "jobUrl": "https://m.liepin.com/job/123456789.shtml?utm_source=app", "dq": "杭州",
            "description": "机器人交互产品设计与实验", "requirements": "有可核实的独立项目", "salary": "20-35k",
        }
        response = Mock(status=200)
        response.getheader.return_value = "application/json"
        response.read.return_value = json.dumps({"jobs": [expected_job]}, ensure_ascii=False).encode()
        transport = Mock()
        transport.getresponse.return_value = response
        with patch.object(self.adapter, "_authorization", return_value="fixture-token"), patch(
                "job_harness.liepin_adapter.http.client.HTTPSConnection", return_value=transport) as https:
            jobs = self.adapter.discover([{"keyword": "交互产品经理", "location": "杭州"}])
        self.assertEqual(https.call_args.args[0], HOST)
        self.assertEqual(transport.request.call_args.args, ("POST", SEARCH_PATH))
        args = transport.request.call_args.kwargs
        self.assertEqual(json.loads(args["body"]), {"jobName": "交互产品经理", "address": "杭州", "page": 0})
        self.assertEqual(args["headers"]["x-user-token"], "fixture-token")
        self.assertEqual(jobs[0]["external_id"], "123456789")
        self.assertEqual(jobs[0]["url"], "https://www.liepin.com/job/123456789.shtml")
        self.assertEqual(jobs[0]["status"], "open")
        self.assertNotIn("recipient", jobs[0])

    def test_auth_and_security_responses_are_distinguished(self):
        for status, expected in ((401, "login_required"), (403, "login_required"), (302, "platform_limit"),
                                 (429, "platform_limit"), (502, "network")):
            response = Mock(status=status)
            transport = Mock()
            transport.getresponse.return_value = response
            with self.subTest(status=status), patch(
                    "job_harness.liepin_adapter.http.client.HTTPSConnection", return_value=transport):
                with self.assertRaises(ChannelUnavailable) as cm:
                    self.adapter._search("fixture-token", "产品经理", "杭州")
                self.assertEqual(cm.exception.code, expected)
                self.assertNotIn("fixture-token", str(cm.exception))
                transport.request.assert_called_once()
                response.read.assert_not_called()

    def test_configured_auth_required_and_probe_does_not_claim_chat_or_send(self):
        with patch.object(self.adapter, "_authorization", side_effect=ChannelUnavailable("configuration_required", "需授权")):
            self.assertEqual(self.adapter.probe()["capabilities"], {"discover": False, "read": False, "send": False})
        with patch.object(self.adapter, "_authorization", return_value="fixture-token"), patch.object(self.adapter, "_search", return_value=[]):
            probe = self.adapter.probe()
        self.assertEqual(probe["status"], "read_only")
        self.assertEqual(probe["capabilities"], {"discover": True, "read": False, "send": False})
        for operation in (self.adapter.read_messages, self.adapter.send, self.adapter.reconcile):
            with self.assertRaises(ChannelUnavailable):
                operation()

    def test_provider_job_id_without_public_link_remains_partial(self):
        row = {"jobId": 123456789, "jobKind": 2, "jobName": "产品经理", "companyName": "甲公司",
               "description": "已提供职责", "requirements": "已提供要求", "location": "杭州"}
        job = self.adapter._job(row)
        self.assertEqual(job["external_id"], "mcp:2:123456789")
        self.assertEqual(job["status"], "partial")
        with patch.object(self.adapter, "_authorization", return_value="fixture-token"), patch.object(self.adapter, "_search", return_value=[{"unknown": True}]):
            self.assertEqual(self.adapter.probe()["status"], "page_changed")

    def test_real_search_envelope_fields_never_turn_summary_into_complete_jd(self):
        row = {"jobId": 123456789, "jobType": "1", "jobName": "机器人产品经理", "company": "示例公司",
               "location": "杭州", "salary": "12-25k", "workYears": "1-3年", "education": "本科",
               "jobDetailUrl": "https://www.liepin.com/job/123456789.shtml?d_sfrom=search"}
        job = self.adapter._job(row)
        self.assertEqual(job["external_id"], "123456789")
        self.assertEqual(job["url"], "https://www.liepin.com/job/123456789.shtml")
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["description"], "")
        self.assertEqual(job["requirements"], "列表条件：1-3年／本科")

    def test_error_envelope_with_empty_rows_is_not_a_successful_probe(self):
        with self.assertRaises(ValueError):
            self.adapter._rows({"code": 1234, "data": {"jobs": []}})
        self.assertEqual(self.adapter._rows({"code": 200, "data": {"jobs": []}}), [])
