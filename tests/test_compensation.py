"""Salary comparisons use only fictional postings and never call a provider."""

from decimal import Decimal
from types import SimpleNamespace
import unittest

from job_harness.compensation import format_range, parse_salary
from job_harness.compensation_ui import render


class SalaryParsingTests(unittest.TestCase):
    def test_explicit_cny_monthly_range_and_thirteen_payments(self):
        result = parse_salary("人民币20–30K元/月 · 13薪")
        self.assertEqual((result.currency, result.period), ("CNY", "month"))
        self.assertEqual((result.monthly_min, result.monthly_max), (Decimal(20000), Decimal(30000)))
        self.assertEqual((result.annual_min, result.annual_max), (Decimal(260000), Decimal(390000)))
        self.assertEqual(result.tax_basis, "待确认")

    def test_units_decimal_fixed_pay_and_explicit_tax_basis(self):
        for text, low, high, annual in (
            ("CNY 20K-30K/month 14薪", 20000, 30000, 280000),
            ("RMB20-30K/月 13薪", 20000, 30000, 260000),
            ("月薪人民币2.5万-3万元 12薪 税前", 25000, 30000, 300000),
            ("20,000–30,000元/月 13薪", 20000, 30000, 260000),
            ("人民币2万-30000元/月 13薪", 20000, 30000, 260000),
            ("月薪人民币25000元 13薪 税后", 25000, 25000, 325000),
        ):
            with self.subTest(text=text):
                result = parse_salary(text)
                self.assertEqual((result.monthly_min, result.monthly_max), (Decimal(low), Decimal(high)))
                self.assertEqual(result.annual_min, Decimal(annual))
        self.assertEqual(parse_salary("月薪人民币2万元 12薪 税后").tax_basis, "税后")

    def test_twelve_to_thirteen_payments_stays_a_range(self):
        for months in ("12薪-13薪", "12-13薪", "12至13薪"):
            result = parse_salary("人民币2-3万元/月，" + months)
            self.assertEqual((result.months_min, result.months_max), (12, 13))
            self.assertEqual((result.annual_min, result.annual_max), (Decimal(240000), Decimal(390000)))

    def test_missing_pay_months_does_not_assume_twelve(self):
        result = parse_salary("人民币20-30K元/月")
        self.assertIsNotNone(result.monthly_min)
        self.assertIsNone(result.annual_min)
        self.assertIn("发薪月数待确认，年额待确认", result.issues)

    def test_bare_platform_range_has_no_inferred_currency_or_period(self):
        result = parse_salary("20-30K")
        self.assertEqual((result.currency, result.period), ("unknown", "unknown"))
        self.assertIsNone(result.monthly_min)
        self.assertIsNone(result.annual_min)
        result = parse_salary("20-30K/月 · 13薪")
        self.assertEqual((result.currency, result.period), ("unknown", "month"))
        self.assertIsNone(result.monthly_min)

    def test_missing_salary_and_negotiable_remain_unknown(self):
        for text in (None, "", "面议", "薪资面议", "Negotiable"):
            with self.subTest(text=text):
                result = parse_salary(text)
                self.assertEqual(result.issues, ("薪资待确认",))
                self.assertIsNone(result.monthly_min)

    def test_explicit_annual_hourly_and_daily_are_not_monthly(self):
        for text, period in (
            ("年薪人民币30-50万元", "year"),
            ("CNY 300000-500000/year", "year"),
            ("300-500元/小时", "hour"),
            ("人民币300-500元/天", "day"),
        ):
            with self.subTest(text=text):
                result = parse_salary(text)
                self.assertEqual(result.period, period)
                self.assertIsNone(result.monthly_min)
                self.assertIsNone(result.annual_min)
                self.assertEqual(result.raw, text)

    def test_foreign_currency_and_yen_symbol_are_not_treated_as_rmb(self):
        for text, currency in (
            ("USD 20-30K/month · 13薪", "USD"),
            ("USD20-30K/month · 13薪", "USD"),
            ("美元20-30K/月", "USD"),
            ("JPY ¥300000-500000/月", "JPY"),
            ("日元300000-500000/月", "JPY"),
            ("港元30000-50000/月", "HKD"),
            ("欧元3000-5000/月", "EUR"),
            ("韩元3000000-5000000/月", "KRW"),
            ("新加坡元5000-7000/月", "SGD"),
            ("$20-30K/month", "unknown"),
            ("¥20-30K/月", "unknown"),
        ):
            with self.subTest(text=text):
                result = parse_salary(text)
                self.assertEqual(result.currency, currency)
                self.assertIsNone(result.monthly_min)
                self.assertIsNone(result.annual_min)

    def test_mixed_units_currencies_and_multiple_components_require_review(self):
        for text in ("人民币20-30K/月，年薪30-40万元", "CNY 20-30K/月 USD 1-2K/月",
                     "人民币20-30K/月+5K绩效", "人民币20-30K/月 税前/税后"):
            with self.subTest(text=text):
                self.assertIsNone(parse_salary(text).monthly_min)

    def test_invalid_ranges_negative_pay_and_open_ended_ranges_require_review(self):
        for text in ("人民币3-2万元/月", "人民币0元/月", "人民币-20000元/月", "人民币20,30元/月",
                     "人民币−20000元/月", "人民币20K元/月起", "人民币20K元/月以上", "人民币≥20K元/月"):
            with self.subTest(text=text):
                self.assertIsNone(parse_salary(text).monthly_min)

    def test_conditional_or_conflicting_pay_months_do_not_create_annual_amount(self):
        for months in ("最高14薪", "13薪以上", "13薪，14薪", "0薪", "14-12薪"):
            with self.subTest(months=months):
                result = parse_salary("人民币20-30K/月，" + months)
                self.assertIsNotNone(result.monthly_min)
                self.assertIsNone(result.annual_min)

    def test_display_does_not_truncate_significant_zeroes(self):
        self.assertEqual(format_range(Decimal("20000"), Decimal("30000")), "20,000–30,000")
        self.assertEqual(format_range(Decimal("1234.500"), Decimal("1234.500")), "1,234.5")
        self.assertEqual(format_range(None, None), "待确认")


class SalaryComparisonPageTests(unittest.TestCase):
    def setUp(self):
        self.jobs = [{"id": n, "company": "虚构公司 " + str(n), "title": "产品经理", "location": "示例市",
                      "salary": "人民币20-30K元/月 · 13薪", "url": "https://jobs.example/roles/" + str(n)}
                     for n in range(1, 8)]
        self.console = SimpleNamespace(data={"jobs": self.jobs})

    def test_selected_jobs_show_original_source_and_arithmetic(self):
        self.jobs[1]["salary"] = "人民币20-30K元/月"
        result = render(self.console, {"job": ["1", "2"]})
        self.assertIn("薪资信息对比", result)
        self.assertIn("260,000–390,000 元/年", result)
        self.assertIn("发薪月数待确认，年额待确认", result)
        self.assertIn('href="https://jobs.example/roles/1"', result)
        self.assertIn("人民币20-30K元/月 · 13薪", result)
        self.assertIn("示例市", result)
        self.assertEqual(self.console.data["jobs"], self.jobs)
        self.assertNotIn('method="post"', result)

    def test_selection_bounds_and_stale_or_malicious_ids(self):
        for selected in (["1"], [str(n) for n in range(1, 8)], ["1", "999"], ["1", '<script>']):
            with self.subTest(selected=selected):
                result = render(self.console, {"job": selected})
                self.assertIn('role="alert"', result)
                self.assertNotIn("<h2>薪资信息对比</h2>", result)
        self.assertIn("薪资信息对比", render(self.console, {"job": ["1", "2", "2"]}))
        self.assertIn('role="alert"', render(self.console, {"compare": ["1"]}))

    def test_external_job_fields_are_escaped_and_unsafe_links_do_not_execute(self):
        self.jobs[0].update(company='<script>alert("x")</script>', title='"><img src=x onerror=alert(1)>',
                            salary='<svg onload="alert(1)">', url="javascript:alert(1)")
        result = render(self.console, {"job": ["1", "2"]})
        self.assertNotIn("<script>", result)
        self.assertNotIn("<svg", result)
        self.assertNotIn("<img", result)
        self.assertNotIn('href="javascript:', result)
        self.assertIn("&lt;script&gt;", result)

    def test_empty_snapshot_renders_next_action(self):
        result = render(SimpleNamespace(data={}), {})
        self.assertIn("暂无岗位", result)
        self.assertIn('href="/jobs"', result)
        self.assertNotIn("<h2>薪资信息对比</h2>", result)

    def test_console_route_uses_comparison_in_existing_layout(self):
        from job_harness.console import Console

        result = Console(self.console.data, "fixture-token", "/salary").render({"job": ["1", "2"]})
        self.assertIn('<a href="/salary" aria-current="page">', result)
        self.assertIn("薪资信息对比", result)
        self.assertIn("260,000–390,000 元/年", result)
        self.assertIn('form method="get" action="/salary"', result)


if __name__ == "__main__":
    unittest.main()
