import unittest

from job_harness.platform_handoff import normalize_platform_message, platform_job_link


class PlatformHandoffTests(unittest.TestCase):
    def test_public_job_ids_and_canonical_urls(self):
        for channel, host, path, job_id in (
            ("boss", "zhipin.com", "/job_detail/fd23ddfa31e33aabcD_.html", "fd23ddfa31e33aabcD_"),
            ("liepin", "liepin.com", "/job/1234567890.shtml", "1234567890"),
            ("yupao", "yupao.com", "/zhaogong/123456789.html", "123456789"),
        ):
            with self.subTest(channel=channel):
                result = platform_job_link(channel, f"https://m.{host}{path}?utm_source=app")
                self.assertEqual(result, (f"https://www.{host}{path}", job_id))

    def test_session_wrong_channel_and_non_job_urls_rejected(self):
        for url in (
            "https://www.zhipin.com/job_detail/fd23ddfa.html?sessionId=example",
            "https://www.zhipin.com/job_detail/fd23ddfa.html?authorization=example",
            "https://www.zhipin.com/job_detail/fd23ddfa.html#token=example",
            "https://www.zhipin.com@elsewhere.example/job_detail/fd23ddfa.html",
            "https://www.zhipin.com.elsewhere.example/job_detail/fd23ddfa.html",
            "https://www.zhipin.com/web/geek/chat",
            "https://www.liepin.com/job/1234567890.shtml",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                platform_job_link("boss", url)

    def test_snapshot_is_deduped_and_never_enables_outbound(self):
        data = {"channel": "liepin", "job_id": "42", "url": "https://www.liepin.com/job/1234567890.shtml",
                "direction": "inbound", "occurred_at": "2020-07-12T19:20", "body": "可以周二面谈吗？",
                "sender": "outside@example.com", "reply_to": "outside@example.com", "auto_send": True,
                "classification": "sent", "message_id": "<outside@example.com>"}
        one = normalize_platform_message(data)
        two = normalize_platform_message({**data, "occurred_at": "2020-07-12T11:20:00Z"})
        self.assertEqual(one, two)
        self.assertEqual(one["job_id"], 42)
        self.assertEqual(one["occurred_at"], "2020-07-12T19:20:00+08:00")
        self.assertEqual(one["source"], "manual_platform_snapshot")
        self.assertNotIn("sender", one)
        self.assertNotIn("reply_to", one)
        self.assertNotIn("auto_send", one)
        self.assertNotIn("classification", one)
        self.assertNotIn("message_id", one)
        alternate = normalize_platform_message({**data, "thread_ref": "另一个招聘方"})
        self.assertNotEqual(one["thread_external_id"], alternate["thread_external_id"])

    def test_native_message_ref_keeps_identity_after_copy_correction(self):
        data = {"channel": "yupao", "job_id": "3", "url": "https://www.yupao.com/zhaogong/123456789.html",
                "direction": "outbound", "occurred_at": "2020-07-12T19:20", "body": "已确认回执。",
                "message_ref": "visible-172"}
        one = normalize_platform_message(data)
        two = normalize_platform_message({**data, "body": "已核实回执。"})
        self.assertEqual(one["external_id"], two["external_id"])
        self.assertNotIn("visible-172", one["external_id"])
