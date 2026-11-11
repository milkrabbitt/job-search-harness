"""First-run UI and the same-origin boundary around model credentials."""

import http.client
import json
import threading
import unittest
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.parse import urlencode

from job_harness.console import Console, make_handler
from job_harness.model_backends import PROVIDERS


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "form":
            self.current = {"inputs": {}, "buttons": []}
        elif self.current is not None and tag == "input":
            self.current["inputs"][attributes.get("name")] = attributes
            if attributes.get("name") == "action":
                self.forms[attributes["value"]] = self.current
        elif self.current is not None and tag == "button":
            self.current["buttons"].append(attributes)

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def fixture(ready=False, provider="ollama"):
    return {
        "onboarding": {
            "required": True, "complete": False, "profile_ready": ready,
            "model_ready": ready, "source_ready": ready, "ready": ready,
            "missing": [] if ready else ["填写求职目标", "测试模型", "添加岗位来源"],
            "model_test": {"state": "done" if ready else "unverified", "detail": "使用当前保存的配置"},
        },
        "preferences": {"matching_preset": "general", "directions": "后端开发", "cities": "杭州"},
        "model": {
            "provider": provider, "configured_name": "test-model", "enabled": provider != "none",
            "base_url": "http://127.0.0.1:11434/v1", "api_key_env": "OLLAMA_API_KEY",
            "key_configured": True, "max_calls": 20, "timeout": 45, "max_output_tokens": 1024,
            "providers": [dict(value, id=key) for key, value in PROVIDERS.items()],
        },
        "sources": [{"id": 1, "company": "示例公司", "url": "https://example.com/jobs", "enabled": True}] if ready else [],
    }


class OnboardingRenderTests(unittest.TestCase):
    def render(self, data=None, page="/setup"):
        return Console(data or fixture(), "fixture-token", page).render({})

    def test_first_run_has_four_steps_and_registry_provider_choices(self):
        page = self.render()
        forms = Forms(page).forms
        for key in ("profile", "model", "sources", "start"):
            self.assertIn('id="setup-' + key + '"', page)
        for identifier, provider in PROVIDERS.items():
            if identifier != "none":
                self.assertIn('<option value="' + identifier + '"', page)
                self.assertIn(provider["label"], page)
        self.assertIn('value="general" selected', page)
        self.assertIn("setup_no_model", forms)
        self.assertIn("disabled", forms["setup_start"]["buttons"][0])
        self.assertNotIn("disabled", forms["setup_test_model"]["buttons"][0])
        self.assertIn("网页版聊天订阅不等于 API 凭据", page)
        self.assertIn("外发仍保持暂停", page)
        self.assertIn("关闭终端或电脑休眠会暂停", page)
        self.assertNotIn('value="none"', page)

    def test_secrets_never_prefilled_and_untrusted_values_are_escaped(self):
        data = fixture()
        data["model"]["api_key"] = "secret-raw-api-key"
        data["model"]["configured_name"] = '\"><img src=x onerror=alert(1)>'
        data["onboarding"]["model_test"]["detail"] = "<script>unsafe()</script>"
        data["preferences"]["directions"] = "<script>goal()</script>"
        page = self.render(data)
        key_input = Forms(page).forms["setup_model"]["inputs"]["api_key"]
        self.assertEqual(key_input["type"], "password")
        self.assertEqual(key_input["autocomplete"], "new-password")
        self.assertNotIn("value", key_input)
        self.assertNotIn("secret-raw-api-key", page)
        self.assertNotIn("<script>unsafe()", page)
        self.assertNotIn("<script>goal()", page)
        self.assertNotIn('<img src=x', page)
        self.assertIn("&lt;script&gt;unsafe()&lt;/script&gt;", page)
        self.assertIn("已配置（密钥不会显示）", page)

    def test_ready_and_explicit_rules_mode_have_correct_controls(self):
        forms = Forms(self.render(fixture(ready=True))).forms
        self.assertNotIn("disabled", forms["setup_start"]["buttons"][0])
        self.assertNotIn("disabled", forms["setup_test_model"]["buttons"][0])
        page = self.render(fixture(ready=True, provider="none"))
        forms = Forms(page).forms
        self.assertNotIn("disabled", forms["setup_start"]["buttons"][0])
        self.assertIn("disabled", forms["setup_test_model"]["buttons"][0])
        self.assertIn("当前已选择规则模式", page)
        self.assertIn("启动时抓取核验", page)

    def test_demo_keeps_offline_notice_and_disables_setup_mutations(self):
        data = fixture(ready=True)
        data["demo"] = True
        page = self.render(data)
        self.assertIn("离线演示", page)
        self.assertIn("不会发邮件", page)
        for action, form in Forms(page).forms.items():
            self.assertIn("disabled", form["buttons"][0], action)

    def test_confirmed_summary_matches_preset_and_resume_import_is_explicit(self):
        data = fixture(ready=True)
        data["facts"] = [
            {"key": "summary_general", "state": "confirmed", "value": "本人确认的一般经历"},
            {"key": "summary_app", "state": "verified", "value": "历史 AI 应用经历"},
            {"key": "other", "state": "verified", "value": "不属于摘要的个人资料"},
        ]
        page = self.render(data)
        self.assertIn("本人确认的一般经历", page)
        self.assertNotIn("历史 AI 应用经历", page)
        self.assertNotIn("不属于摘要的个人资料", page)
        forms = Forms(page).forms
        resume = forms["setup_resume"]
        self.assertEqual(resume["inputs"]["path"]["type"], "text")
        self.assertIn("required", resume["inputs"]["path"])
        self.assertIn("required", resume["inputs"]["approved"])
        self.assertNotIn("checked", resume["inputs"]["approved"])
        self.assertNotIn("disabled", forms["setup_start"]["buttons"][0])
        self.assertIn("尚未登记简历，不影响先采集和筛选岗位", page)
        self.assertIn("这一步不会发送简历", page)
        data["preferences"]["matching_preset"] = "legacy_ai_application"
        page = self.render(data)
        self.assertIn("历史 AI 应用经历", page)
        self.assertNotIn("本人确认的一般经历", page)

    def test_first_run_banner_and_settings_link(self):
        data = fixture()
        page = self.render(data, "/")
        self.assertIn("先完成首次配置", page)
        data["onboarding"]["complete"] = True
        self.assertNotIn("先完成首次配置", self.render(data, "/"))
        settings = self.render(data, "/settings")
        self.assertIn('href="/setup#setup-model"', settings)
        self.assertIn('id="resumes"', settings)
        self.assertIn('id="sources"', settings)
        self.assertIn('name="matching_preset"', settings)


class OnboardingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.snapshot.return_value = fixture()
        self.service.action.return_value = "模型配置已保存，请测试连接"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.port = self.server.server_port
        self.origin = f"http://127.0.0.1:{self.port}"
        self.server.RequestHandlerClass = make_handler(self.service, "fixture-token", self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, data=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                   "Origin": self.origin if origin is None else origin, "Sec-Fetch-Site": "same-origin"}
        try:
            connection.request(method, path, None if data is None else urlencode(data), headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    def test_setup_route_and_security_headers(self):
        status, headers, page = self.request("GET", "/setup")
        self.assertEqual(status, 200)
        self.assertIn('aria-current="page">开始配置', page)
        self.assertIn("script-src 'sha256-", headers["Content-Security-Policy"])
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(headers["Referrer-Policy"], "same-origin")

    def test_home_redirects_new_users_but_keeps_demo_and_completed_dashboard(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/setup")
        self.service.snapshot.assert_called_once()
        self.service.snapshot.return_value["onboarding"]["complete"] = True
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.service.snapshot.return_value["onboarding"]["complete"] = False
        self.service.snapshot.return_value["demo"] = True
        self.assertEqual(self.request("GET", "/")[0], 200)

    def test_model_submit_returns_to_setup_without_exposing_key(self):
        data = {"token": "fixture-token", "action": "setup_model", "next": "/setup",
                "provider": "openai", "model_name": "test-model", "api_key": "test-private-key"}
        status, _, body = self.request("POST", "/action", data)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["location"].startswith("/setup?notice="))
        self.assertNotIn("test-private-key", body)
        self.service.action.assert_called_once_with("setup_model", {
            "provider": "openai", "model_name": "test-model", "api_key": "test-private-key",
        })

    def test_cross_origin_and_stale_tokens_cannot_save_credentials(self):
        data = {"token": "fixture-token", "action": "setup_model", "next": "/setup", "api_key": "test-private-key"}
        self.assertEqual(self.request("POST", "/action", data, origin="https://foreign.example")[0], 403)
        data["token"] = "expired"
        self.assertEqual(self.request("POST", "/action", data)[0], 403)
        self.service.action.assert_not_called()

    def test_validation_stays_inline_and_next_cannot_redirect_off_site(self):
        data = {"token": "fixture-token", "action": "setup_start", "next": "/setup"}
        self.service.action.side_effect = ValueError("请先测试已保存的模型配置")
        status, _, body = self.request("POST", "/action", data)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body), {"notice": "请先测试已保存的模型配置"})
        self.service.action.side_effect = None
        data["next"] = "https://foreign.example/collect"
        status, _, body = self.request("POST", "/action", data)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["location"].startswith("/?notice="))


if __name__ == "__main__":
    unittest.main()
