import tempfile
import unittest
from unittest.mock import Mock, patch

from job_harness.liepin_detail import detail_url, parse_detail
from job_harness.service import Harness, set_setting
from job_harness.storage import Store, one


JD = """<div class='job-apply-content'><div class='name-box'><span class='name'>AI 产品经理</span></div>
<div class='job-properties'><span>杭州</span><span>本科</span></div></div>
<div class='job-apply-operate'><div class='apply-box'>
<a data-selector='apply-job' data-jobid='12345678' data-jobkind='2'>投简历</a></div></div>
<section class='job-intro-container'><dd data-selector='job-intro-content'>工作职责<br>
负责 Agent 产品设计、交互原型和任务验证，定义用户需求并基于实测更新工作流。<br>
【职位要求】<br>有已上线的应用项目或个人作品优先，能够以原型展示应用效果。</dd></section>"""
LINK = "https://www.liepin.com/job/1912345678.shtml"


class LiepinDetailTests(unittest.TestCase):
    def test_parse_source_identity_and_requirements(self):
        detail = parse_detail(JD, LINK, "AI 产品经理")
        self.assertEqual((detail["location"], detail["status"]), ("杭州", "open"))
        self.assertIn("原型和任务验证", detail["description"])
        self.assertIn("个人作品", detail["requirements"])
        with self.assertRaises(ValueError):
            parse_detail(JD, LINK, "另一岗位")
        with self.assertRaises(ValueError):
            parse_detail(JD.replace("12345678", "23456789"), LINK, "AI 产品经理")

    def test_hunter_without_apply_is_not_marked_active(self):
        url = detail_url("mcp:1:12345678", "https://www.liepin.com/zhaopin/")
        self.assertEqual(url, "https://www.liepin.com/a/12345678.shtml")
        hunter = JD.replace("apply-job", "chat-chat").replace("data-jobkind='2'", "data-jobkind='1'")
        self.assertEqual(parse_detail(hunter, url, "AI 产品经理")["status"], "partial")

    def test_worker_keeps_enriched_jd_after_summary_and_does_not_use_unverified_recipient(self):
        with tempfile.TemporaryDirectory() as directory:
            liepin = Mock()
            liepin.read_job.return_value = parse_detail(JD, LINK, "AI 产品经理")
            service = Harness(Store(directory), {}, {"liepin": liepin})
            job = {"channel": "liepin", "external_id": "1912345678", "url": LINK, "company": "示例公司",
                   "title": "AI 产品经理", "location": "杭州", "status": "partial", "requirements": "列表条件：本科"}
            with service.store.tx(True) as c:
                set_setting(c, "preferences", {"require_robotics": False})
                service.platform_results(c, [job], None)
            self.assertTrue(service.task_one())
            with service.store.tx(True) as c:
                jid = service.upsert_job(c, job)
                saved = one(c, "SELECT * FROM jobs WHERE id=?", (jid,))
                self.assertEqual(saved["status"], "open")
                self.assertIn("个人作品", saved["requirements"])
                self.assertIsNone(one(c, "SELECT id FROM outbox"))
                self.assertEqual(one(c, "SELECT state FROM tasks")["state"], "done")


if __name__ == "__main__":
    unittest.main()
