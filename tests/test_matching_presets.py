"""Public presets must not silently impose the original user's career profile."""
import unittest

from job_harness import rules


ROLE = {
    "company": "示例公司", "title": "销售经理", "location": "杭州", "salary": "15–25K",
    "description": "负责客户开拓与销售任务，整理客户需求并与团队协作，维护长期客户关系及项目交付进度。",
    "requirements": "有沟通协作与业务分析经验，能够清楚记录客户需求与项目进度。",
    "published_at": "2026-01-01", "status": "open",
}


class MatchingPresetTests(unittest.TestCase):
    def assess(self, role=None, **prefs):
        return rules.assess(role or ROLE, {"matching_preset": "general", "directions": "销售", **prefs}, [])

    def test_legacy_is_unchanged_without_explicit_preset(self):
        original = rules.assess(ROLE, {"directions": "销售"}, [])
        named = rules.assess(ROLE, {"directions": "销售", "matching_preset": "legacy_ai_application"}, [])
        self.assertEqual(original, named)
        self.assertIn("明确销售或获客", original["hard"])
        self.assertEqual(original["state"], "excluded")

    def test_sales_target_is_not_excluded_or_labeled_as_ai_experience(self):
        result = self.assess()
        self.assertEqual(result["hard"], [])
        self.assertEqual(result["state"], "match")
        self.assertEqual(result["scope"], "general")
        self.assertEqual(result["strengths"], ["目标关键词：销售"])
        self.assertTrue(any("销售或获客" in gap for gap in result["gaps"]))
        self.assertNotIn("真实项目关联", result["reason"])
        self.assertIn("不代表已核实", result["reason"])

    def test_algorithm_degree_seniority_and_coding_are_review_points(self):
        role = {**ROLE, "title": "算法工程师", "description": "负责大模型预训练和算法评测，承担模型质量分析、数据处理、研究与迭代交付工作。",
                "requirements": "必须独立编码；专业要求计算机相关专业；至少5年产品开发经验。"}
        result = self.assess(role, directions="算法")
        self.assertEqual(result["hard"], [])
        self.assertEqual(result["state"], "review")
        self.assertTrue(any("算法" in x for x in result["gaps"]))
        self.assertTrue(any("独立编码" in x for x in result["gaps"]))
        self.assertIn("任职资格是否符合本人实际情况", result["unknown"])
        reviewed = rules.assess(role, {"matching_preset": "general", "directions": "算法"}, [], review_cleared=True)
        self.assertEqual(reviewed["state"], "match")
        self.assertTrue(reviewed["gaps"])

    def test_unrelated_industry_uses_explicit_target_keywords(self):
        role = {**ROLE, "title": "餐饮运营", "description": "负责餐饮门店运营，管理排班、原料采购和服务质量，定期分析门店经营情况并协调团队。",
                "requirements": "具备门店协作经验，理解采购流程和服务质量要求。"}
        result = self.assess(role, directions="餐饮,门店")
        self.assertEqual(result["state"], "match")
        self.assertEqual(result["scope"], "general")
        self.assertGreaterEqual(result["score"], 60)
        self.assertEqual(result["strengths"], ["目标关键词：餐饮", "目标关键词：门店"])

    def test_explicit_city_salary_exclusion_and_status_still_gate(self):
        self.assertIn("工作地点超出当前范围", self.assess(cities="北京")["hard"])
        self.assertIn("公开薪资上限低于本人确认的底线", self.assess(salary_floor="30000")["hard"])
        explicit = rules.assess(ROLE, {"matching_preset": "general", "directions": "销售"}, ["示例公司"])
        self.assertEqual(explicit["state"], "excluded")
        self.assertIn("排除名单 示例公司", explicit["hard"])
        self.assertIn("岗位关闭或已停止联系", self.assess({**ROLE, "status": "closed"})["hard"])

    def test_remote_preference_does_not_assume_city(self):
        self.assertEqual(self.assess()["state"], "match")
        remote = {**ROLE, "location": "远程"}
        self.assertEqual(self.assess(remote, cities="北京", allow_remote=True)["state"], "match")
        self.assertEqual(self.assess(remote, cities="北京", allow_remote=False)["state"], "excluded")

    def test_no_target_or_no_keyword_match_needs_review(self):
        for directions in ("", ",，", "会计"):
            result = self.assess(directions=directions)
            self.assertEqual(result["state"], "review")
            self.assertLess(result["score"], 60)
        normal = self.assess(directions="销售")
        duplicated = self.assess(directions="销售,销售,销售")
        self.assertEqual(normal["score"], duplicated["score"])
        self.assertEqual(normal["strengths"], duplicated["strengths"])

    def test_scope_only_target_is_used_without_overriding_role_keywords(self):
        result = self.assess(directions="  ", target_scope="销售,客户")
        self.assertEqual(result["state"], "match")
        self.assertEqual(result["strengths"], ["目标关键词：销售", "目标关键词：客户"])
        specific = self.assess(directions="会计", target_scope="销售")
        self.assertEqual(specific["state"], "review")
        self.assertEqual(specific["strengths"], [])

    def test_uncertain_or_nonmonthly_pay_is_not_a_false_rejection(self):
        for salary in ("USD 10–20K", "年薪 10–20万", "面议", "20美元/小时"):
            result = self.assess({**ROLE, "salary": salary}, salary_floor="30000")
            self.assertNotIn("公开薪资上限低于本人确认的底线", result["hard"])
            self.assertIn("薪资能否满足本人明确条件", result["unknown"])
        self.assertEqual(self.assess({**ROLE, "salary": "8K–12K"}, salary_floor="10000")["hard"], [])
        self.assertEqual(self.assess({**ROLE, "salary": "8000–12000元/月"}, salary_floor="10000")["hard"], [])
        self.assertIn("公开薪资上限低于本人确认的底线",
                      self.assess({**ROLE, "salary": "8000–12000元/月"}, salary_floor="15000")["hard"])

    def test_invalid_preset_and_salary_are_not_silently_accepted(self):
        for invalid in ("everything", None, [], True):
            with self.subTest(preset=invalid), self.assertRaisesRegex(ValueError, "未知匹配预设"):
                self.assess(matching_preset=invalid)
        with self.assertRaisesRegex(ValueError, "薪资底线"):
            self.assess(salary_floor="about 20000")


if __name__ == "__main__":
    unittest.main()
