#!/usr/bin/env python3
"""Developer-only rendered onboarding check; uses fixtures and loopback only.

Install Playwright separately for this check, then run:
    python scripts/browser-smoke.py --screenshots /tmp/job-harness-browser
No Playwright package or browser is required by the application itself.
"""

import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_harness.console import make_handler
from job_harness.model_backends import PROVIDERS
from job_harness.service import Harness, set_setting
from job_harness.storage import Store


class FictionalOfficial:
    """A source adapter with no network methods or real recruiting data."""

    def __init__(self):
        self.calls = 0

    def discover(self, sources):
        self.calls += 1
        assert len(sources) == 1 and sources[0]["url"] == "https://fixture.example/careers"
        return [{
            "company": "浏览器测试示例公司", "title": "后端开发工程师", "location": "杭州",
            "description": "虚构的 Python 后端服务开发岗位，仅用于自动测试。",
            "requirements": "熟悉 Python 与后端开发。", "status": "open", "channel": "official",
            "url": "https://fixture.example/careers/backend", "domain": "fixture.example",
            "external_id": "browser-fixture-backend",
        }]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path, default=os.environ.get("JOB_HARNESS_SCREENSHOTS"),
                        help="Optional directory for fictional-data screenshots (or JOB_HARNESS_SCREENSHOTS)")
    parser.add_argument("--executable", help="Optional Chromium executable; otherwise use Playwright's installed browser")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright, expect

    with TemporaryDirectory(prefix="job-harness-browser-") as directory:
        root = Path(directory)
        pdf = root / "fictional-resume.pdf"
        pdf.write_bytes(b"%PDF-1.4\n% Fictional browser fixture. No personal information.\n%%EOF\n")
        official = FictionalOfficial()
        service = Harness(Store(root / "private-data"), {"model": {"enabled": False}}, {"official": official})
        with service.store.tx(True) as connection:
            for key, value in {"onboarding_required": True, "stopped": True, "outbound_paused": True,
                               "preferences": {"matching_preset": "general"}}.items():
                set_setting(connection, key, value)
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = make_handler(service, "browser-fixture-token", server.server_port)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                launch = {"headless": True}
                if args.executable:
                    launch["executable_path"] = args.executable
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                external_requests = []
                errors = []

                def local_only(route):
                    parsed = urlsplit(route.request.url)
                    if parsed.hostname == "127.0.0.1" and parsed.port == server.server_port:
                        if parsed.path == "/favicon.ico":
                            route.fulfill(status=204, body="")
                        else:
                            route.continue_()
                    else:
                        external_requests.append(route.request.url)
                        route.abort()

                context.route("**/*", local_only)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(origin + "/")
                expect(page).to_have_url(origin + "/setup")
                expect(page.get_by_role("heading", name="让求职助手真正开始工作")).to_be_visible()

                def form(action):
                    return page.locator(f'form:has(input[name="action"][value="{action}"])')

                def submit(action):
                    with page.expect_navigation(wait_until="domcontentloaded"):
                        form(action).locator("button").click()

                profile = form("setup_profile")
                profile.locator('[name="directions"]').fill("后端开发")
                profile.locator('[name="cities"]').fill("杭州")
                profile.locator('[name="salary_floor"]').fill("15000")
                profile.locator('[name="summary"]').fill("本人确认：有 Python 后端项目实践。此段为虚构测试资料。")
                submit("setup_profile")
                assert service.snapshot()["onboarding"]["profile_ready"]

                # Check dynamic provider defaults without saving or calling a provider.
                model = form("setup_model")
                model.locator('[name="provider"]').select_option("deepseek")
                expect(model.locator('[name="base_url"]')).to_have_value(PROVIDERS["deepseek"]["base_url"])
                expect(model.locator('[name="api_key_env"]')).to_have_value("DEEPSEEK_API_KEY")
                expect(page.locator("#setup-launch button")).to_be_disabled()
                submit("setup_no_model")
                assert service.snapshot()["model"]["provider"] == "none"
                expect(page.get_by_text("当前已选择规则模式，无需模型连接测试。")).to_be_visible()

                page.get_by_text("选填：导入已核对的 PDF 简历", exact=True).click()
                resume = form("setup_resume")
                resume.locator('[name="path"]').fill(str(pdf))
                resume.locator('[name="label"]').fill("浏览器测试简历")
                resume.locator('[name="scope"]').select_option("general")
                resume.locator('[name="approved"]').check()
                submit("setup_resume")
                registered = service.snapshot()["resumes"]
                assert len(registered) == 1 and registered[0]["approved"]
                assert registered[0]["path"] != str(pdf) and pdf.exists()

                source = form("setup_source")
                source.locator('[name="url"]').fill("https://fixture.example/careers")
                source.locator('[name="company"]').fill("浏览器测试示例公司")
                source.locator('[name="location"]').fill("杭州")
                submit("setup_source")
                assert service.snapshot()["onboarding"]["ready"]
                expect(page.locator("#setup-launch button")).to_be_enabled()
                submit("setup_start")
                snapshot = service.snapshot()
                assert snapshot["onboarding"]["complete"] and snapshot["state"]["paused_all"]
                assert not snapshot["preferences"]["auto_send"]
                assert service.task_one() and official.calls == 1
                assert len(service.snapshot()["jobs"]) == 1
                assert not service.snapshot()["outbox"]

                for width, height, name in ((1280, 900, "desktop"), (390, 844, "mobile")):
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(origin + "/setup")
                    expect(page.get_by_text("配置已完成。可以随时修改；更换模型后需要重新测试连接。")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), name
                    if args.screenshots:
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.screenshots / f"setup-{name}.png"), full_page=True)
                page.get_by_role("navigation").get_by_role("link", name="岗位", exact=True).click()
                expect(page).to_have_url(origin + "/jobs")
                expect(page.get_by_role("heading", name="浏览器测试示例公司 · 后端开发工程师", exact=True)).to_be_visible()
                page.get_by_role("navigation").get_by_role("link", name="运行概览", exact=True).click()
                expect(page).to_have_url(origin + "/")
                assert not external_requests, external_requests
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("PASS: browser onboarding, optional PDF import, fictional source scan, paused outbound, responsive layout, navigation; no external requests or JS errors")


if __name__ == "__main__":
    main()
