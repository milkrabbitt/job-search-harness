#!/usr/bin/env python3
"""Rendered candidate and follow-up acceptance with fictional data only.

Developer setup uses an isolated Python environment with requirements.txt,
Playwright and its Chromium browser installed. Then run:
    python scripts/browser-workflow.py --screenshots /tmp/job-harness-workflow
The application itself does not require Playwright. This check uses a new
loopback server, temporary database and browser context, and blocks external
browser requests. No real recruiting account, mailbox or model is configured.
"""

import argparse
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_harness.console import make_handler
from job_harness.demo import synthetic_pdf
from job_harness.service import Harness, set_setting
from job_harness.storage import Store


class FictionalOfficial:
    """An explicitly local source adapter; it never opens a network connection."""

    def discover(self, _sources):
        return []


def add_jobs(service):
    jobs = []
    samples = (
        ("paper-orbit", "纸环实验室（虚构）", "AI 对话产品经理", "人民币20–30K元/月 · 13薪"),
        ("moon-workshop", "月栈工坊（虚构）", "陪伴机器人交互产品", "面议"),
        ("cloud-lantern", "云盏系统（虚构）", "AI 应用产品经理", "人民币24–36万元/年"),
    )
    with service.store.tx(True) as connection:
        for slug, company, title, salary in samples:
            jobs.append(service.upsert_job(connection, {
                "channel": "official", "external_id": "workflow-" + slug,
                "company": company, "title": title, "location": "杭州", "salary": salary,
                "domain": slug + ".example", "url": f"https://{slug}.example/careers/product",
                "description": "负责 AI 应用需求、对话交互原型和产品测试，根据反馈推进迭代。",
                "requirements": "接受完整项目，重视产品判断和交付过程。", "status": "open",
            }))
    return jobs


def add_interview(service, job_id):
    body = "您好，想进一步了解您的对话原型项目。可以安排 2030-11-14 15:30 的视频面试吗？"
    with service.store.tx(True) as connection:
        assert service.ingest_message(connection, {
            "channel": "email", "external_id": "workflow-fictional-interview",
            "thread_external_id": "workflow-fictional-thread", "message_id": "<workflow-invite@paper-orbit.example>",
            "job_id": job_id, "company": "纸环实验室（虚构）", "direction": "inbound",
            "sender": "careers@paper-orbit.example", "subject": "虚构岗位 · 面试意向", "body": body,
        })
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path, default=os.environ.get("JOB_HARNESS_SCREENSHOTS"),
                        help="Optional directory for fictional-data screenshots")
    parser.add_argument("--executable", help="Optional Chromium executable path")
    args = parser.parse_args()
    from playwright.sync_api import expect, sync_playwright

    with TemporaryDirectory(prefix="job-harness-workflow-") as temporary:
        root = Path(temporary)
        pdf = root / "fictional-resume.pdf"
        pdf.write_bytes(synthetic_pdf())
        service = Harness(Store(root / "private-data"), {"email": {"enabled": False}, "model": {"enabled": False}},
                          {"official": FictionalOfficial()})
        with service.store.tx(True) as connection:
            set_setting(connection, "onboarding_required", True)
            set_setting(connection, "stopped", True)
            set_setting(connection, "outbound_paused", True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = make_handler(service, "fictional-workflow-token", server.server_port)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        external_requests = []
        browser_errors = []
        try:
            with sync_playwright() as playwright:
                launch = {"headless": True}
                if args.executable:
                    launch["executable_path"] = args.executable
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")

                def local_only(route):
                    target = urlsplit(route.request.url)
                    if target.scheme == "http" and target.hostname == "127.0.0.1" and target.port == server.server_port:
                        if target.path == "/favicon.ico":
                            route.fulfill(status=204, body="")
                        else:
                            route.continue_()
                    else:
                        external_requests.append(route.request.url)
                        route.abort()

                context.route("**/*", local_only)
                page = context.new_page()
                page.on("pageerror", lambda error: browser_errors.append(str(error)))
                page.on("console", lambda message: browser_errors.append(message.text) if message.type == "error" else None)

                def form(action):
                    return page.locator(f'form:has(input[name="action"][value="{action}"])')

                def submit(action):
                    with page.expect_navigation(wait_until="domcontentloaded"):
                        form(action).locator("button").click()

                def screenshot(name):
                    if args.screenshots:
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.screenshots / (name + ".png")), full_page=True)

                page.goto(origin + "/profile")
                upload = form("resume_upload")
                page.set_input_files('input[name="resume"]', str(pdf))
                upload.locator('[name="label"]').fill("虚构 AI 应用简历")
                upload.locator('[name="scope"]').select_option("ai")
                upload.locator('[name="approved"]').check()
                submit("resume_upload")
                texts = service.snapshot()["candidate"]["resume_texts"]
                assert len(texts) == 1 and texts[0]["status"] == "extracted" and texts[0]["state"] == "pending"
                extracted = form("confirm_resume_text")
                expect(extracted.locator('[name="text"]')).to_have_value("Fictional resume - Job Search Harness demo")
                extracted.locator('[name="confirmed"]').check()
                submit("confirm_resume_text")
                assert service.snapshot()["candidate"]["resume_texts"][0]["state"] == "confirmed"

                summary = "虚构演示：做过角色对话原型，负责需求设计、交互取舍和多轮测试迭代。"
                intro = "您好，我有 AI 应用产品原型与交互评测项目，期待交流相关产品机会。此段为虚构演示。"
                profile = form("save_candidate_profile")
                profile.locator('[name="summary"]').fill(summary)
                profile.locator('[name="intro"]').fill(intro)
                profile.locator('[name="confirmed"]').check()
                profile.locator('[name="profile_model_consent"]').check()
                submit("save_candidate_profile")
                snapshot = service.snapshot()
                assert snapshot["candidate"]["profile"]["summary"] == summary
                assert snapshot["candidate"]["profile"]["intro"] == intro
                assert snapshot["preferences"]["profile_model_consent"] is True

                page.goto(origin + "/setup")
                preferences = form("setup_profile")
                preferences.locator('[name="directions"]').fill("AI 产品,交互,应用")
                preferences.locator('[name="cities"]').fill("杭州")
                preferences.locator('[name="salary_floor"]').fill("18000")
                preferences.locator('[name="matching_preset"]').select_option("general")
                preferences.locator('[name="summary"]').fill(summary)
                submit("setup_profile")
                snapshot = service.snapshot()
                assert snapshot["preferences"]["cities"] == "杭州"
                assert snapshot["preferences"]["directions"] == "AI 产品,交互,应用"
                assert str(snapshot["preferences"]["salary_floor"]) == "18000"
                submit("setup_no_model")
                source = form("setup_source")
                source.locator('[name="url"]').fill("https://fictional.example/careers")
                source.locator('[name="company"]').fill("工作流测试公司（虚构）")
                source.locator('[name="location"]').fill("杭州")
                submit("setup_source")
                expect(page.locator("#setup-launch button")).to_be_enabled()
                submit("setup_start")
                assert service.snapshot()["onboarding"]["complete"] is True
                assert service.snapshot()["state"]["paused_all"] is True

                page.goto(origin + "/")
                submit("stop")
                assert service.snapshot()["state"]["state"] == "stopped"
                submit("run")
                assert service.snapshot()["state"]["state"] == "running"
                submit("resume_all")
                assert service.snapshot()["state"]["paused_all"] is False
                submit("pause_all")
                assert service.snapshot()["state"]["paused_all"] is True
                assert service.snapshot()["state"]["live_armed"] is False
                screenshot("workflow-controls")

                jobs = add_jobs(service)
                page.goto(origin + "/salary")
                page.locator(f'input[name="job"][value="{jobs[0]}"]').check()
                page.locator(f'input[name="job"][value="{jobs[1]}"]').check()
                with page.expect_navigation(wait_until="domcontentloaded"):
                    page.get_by_role("button", name="查看对比", exact=True).click()
                expect(page.get_by_role("heading", name="薪资信息对比", exact=True)).to_be_visible()
                monthly = page.get_by_role("row").filter(has=page.get_by_role("cell", name="明示人民币月薪", exact=True))
                expect(monthly).to_contain_text("20,000–30,000 元/月")
                expect(monthly).to_contain_text("待确认")
                annual = page.get_by_role("row").filter(has=page.get_by_role("cell", name="按明示月薪与薪数折算的年额", exact=True))
                expect(annual).to_contain_text("260,000–390,000 元/年")
                expect(annual).to_contain_text("待确认")
                screenshot("salary")

                interview = add_interview(service, jobs[0])
                page.goto(origin + "/threads")
                page.get_by_text("查看已同步会话", exact=True).click()
                expect(page.get_by_text(interview, exact=True)).to_be_visible()
                screenshot("workflow-thread")
                page.goto(origin + "/decisions")
                expect(page.get_by_role("heading", name="待我决定", exact=True)).to_be_visible()
                expect(page.locator("article h3").filter(has_text="面试")).to_be_visible()
                expect(form("decide").locator('[name="resolution"]')).to_be_empty()
                decision = service.snapshot()["decisions"][0]
                assert decision["kind"] == "interview" and decision["state"] == "pending"
                assert "15:30" not in decision["draft"]
                assert service.snapshot()["outbox"] == []
                screenshot("workflow-decisions")

                page.goto(origin + "/profile")
                expect(form("save_candidate_profile").locator('[name="intro"]')).to_have_value(intro)
                screenshot("profile")
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                screenshot("workflow-profile-mobile")
                assert not external_requests, external_requests
                assert not browser_errors, browser_errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("PASS: real file upload, extracted-text confirmation, summary/intro and model consent, preferences, "
          "salary checkbox comparison, interview decision, actual pause/resume, mobile profile; "
          "0 external browser requests, 0 browser errors, 0 external messages")


if __name__ == "__main__":
    main()
