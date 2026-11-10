"""Read-only adapter for the documented Liepin candidate search endpoint."""

import http.client
import json
import re
import ssl
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

from .channels import ChannelUnavailable
from .credential_store import default_keychain_locked
from .platform_handoff import platform_job_link


HOST = "open-agent.liepin.com"
SEARCH_PATH = "/mcp/search-job"
AUTH_URL = "https://www.liepin.com/mcp/auth"
SEARCH_URL = "https://www.liepin.com/zhaopin/"
KEYCHAIN_SERVICE = "job-harness-liepin-candidate"
MAX_BODY = 1_500_000


class LiepinChannel:
    name = "liepin"

    def __init__(self, config):
        self.config = config or {}
        self.timeout = max(3, min(int(self.config.get("timeout_seconds", 10)), 30))
        from .liepin_detail import PublicDetails
        self.public_details = PublicDetails(self.timeout)

    def read_job(self, external_id, listing_url, title):
        return self.public_details.read(external_id, listing_url, title)

    def _authorization(self):
        if not self.config.get("enabled", True) or sys.platform != "darwin":
            raise ChannelUnavailable("configuration_required", "猎聘官方搜索需要本机钥匙串授权")
        if default_keychain_locked() is True:
            raise ChannelUnavailable("configuration_required", "Mac 登录钥匙串锁定，猎聘后台已暂停读取凭据")
        service = self.config.get("keychain_service") or KEYCHAIN_SERVICE
        try:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", service, "-a", "candidate", "-w"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ChannelUnavailable("configuration_required", "未能读取猎聘专用钥匙串项目") from None
        token = result.stdout.rstrip(b"\r\n") if result.returncode == 0 else b""
        if not re.fullmatch(rb"[!-~]{12,4096}", token):
            raise ChannelUnavailable("configuration_required", "在猎聘官方授权页完成个人求职授权后，将授权码存入专用钥匙串")
        return token.decode("ascii")

    def _search(self, token, keyword, location):
        payload = json.dumps({"jobName": keyword, "address": location, "page": 0}, ensure_ascii=False).encode()
        conn = http.client.HTTPSConnection(HOST, timeout=self.timeout, context=ssl.create_default_context())
        try:
            conn.request("POST", SEARCH_PATH, body=payload, headers={
                "x-user-token": token, "Content-Type": "application/json", "Accept": "application/json",
            })
            response = conn.getresponse()
            status = response.status
            if status in {401, 403}:
                raise ChannelUnavailable("login_required", "猎聘未接受个人求职授权，请在官方页面确认或刷新授权")
            if 300 <= status < 400 or status == 429:
                raise ChannelUnavailable("platform_limit", "猎聘搜索返回安全跳转或频率限制，已停止请求")
            if status >= 500:
                raise ChannelUnavailable("network", "猎聘官方搜索暂时未响应")
            if status != 200:
                raise ChannelUnavailable("page_changed", "猎聘搜索返回未预期的状态")
            if "application/json" not in str(response.getheader("Content-Type", "")).lower():
                raise ChannelUnavailable("page_changed", "猎聘搜索未返回可核验的 JSON 结果")
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ChannelUnavailable("page_changed", "猎聘搜索结果超过本地读取上限")
            try:
                return self._rows(json.loads(raw))
            except (ValueError, UnicodeError):
                raise ChannelUnavailable("page_changed", "猎聘搜索结果格式暂未核实") from None
        except (OSError, http.client.HTTPException):
            raise ChannelUnavailable("network", "猎聘官方搜索的网络或 TLS 连接中断") from None
        finally:
            conn.close()

    @staticmethod
    def _rows(data):
        if isinstance(data, dict):
            code = data.get("code")
            if str(code) in {"401", "403"}:
                raise ChannelUnavailable("login_required", "猎聘未接受个人求职授权，请在官方页面确认或刷新授权")
            if str(code) == "429":
                raise ChannelUnavailable("platform_limit", "猎聘搜索返回频率限制，已停止请求")
            if data.get("success") is False or (code is not None and str(code).upper() not in {"0", "200", "OK", "SUCCESS"}):
                raise ValueError("unsuccessful response")
            for envelope in (data, data.get("data")):
                if isinstance(envelope, list):
                    data = envelope
                    break
                if isinstance(envelope, dict):
                    for key in ("jobs", "jobList", "records", "list"):
                        if isinstance(envelope.get(key), list):
                            data = envelope[key]
                            break
                    if isinstance(data, list):
                        break
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ValueError("unrecognized search response")
        return data

    def probe(self):
        detail = "已通过猎聘官方授权搜索读取职位结果，站内私聊和已发送记录由浏览器接管"
        status, capabilities = "read_only", {"discover": True, "read": False, "send": False}
        jobs = []
        try:
            jobs = self.discover()
            detail = "已通过猎聘官方授权读取 " + str(len(jobs)) + " 条搜索结果；站内私聊由浏览器接管"
        except ChannelUnavailable as exc:
            status, detail = exc.code, exc.detail
            capabilities["discover"] = False
        return {"status": status, "detail": detail, "url": AUTH_URL, "capabilities": capabilities, "jobs": jobs}

    def discover(self, queries=None):
        token = self._authorization()
        found = {}
        configured = queries if queries is not None else self.config.get("queries")
        if not configured:
            raise ChannelUnavailable("configuration_required", "请在配置中填写猎聘搜索关键词和城市")
        for query in list(configured)[:4]:
            keyword = str(query.get("keyword") or "").strip()
            location = str(query.get("location") or "").strip()
            if not (0 < len(keyword) <= 60 and 0 < len(location) <= 30):
                raise ChannelUnavailable("configuration_required", "请为猎聘搜索填写明确的岗位关键词和城市")
            rows = self._search(token, keyword, location)
            jobs = [job for row in rows if (job := self._job(row))]
            if rows and not jobs:
                raise ChannelUnavailable("page_changed", "猎聘搜索结果的职位字段暂未核实")
            found.update((job["external_id"], job) for job in jobs)
        return list(found.values())

    @staticmethod
    def _job(row):
        job = row.get("job") if isinstance(row.get("job"), dict) else row
        company = row.get("comp") if isinstance(row.get("comp"), dict) else row
        title = _field(job, "title", "jobName", "jobTitle", "职位名称", "职位")
        name = _field(company, "companyName", "compName", "company", "公司名称", "公司")
        raw_id = _field(job, "jobId", "id", "职位ID", "职位id")
        kind = _field(job, "jobKind", "jobType", "kind", "职位类型")
        source = _field(job, "jobDetailUrl", "jobUrl", "jobLink", "url", "link", "职位链接")
        if source.startswith("/job/"):
            source = "https://www.liepin.com" + source
        try:
            parsed = urlsplit(source)
            source = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            url, external_id = platform_job_link("liepin", source)
        except ValueError:
            if not (re.fullmatch(r"[0-9]{5,24}", raw_id) and re.fullmatch(r"[0-9]", kind)):
                return None
            url, external_id = SEARCH_URL, f"mcp:{kind}:{raw_id}"
        if not title or not name:
            return None
        description = _field(job, "description", "jobDesc", "jobDescription", "职责", "职位描述")
        requirements = _field(job, "requirements", "jobRequirements", "任职要求")
        full_jd = bool(description and requirements)
        if not requirements:
            summary = [_field(job, field) for field in ("workYears", "education")]
            requirements = "列表条件：" + "／".join(filter(None, summary)) if any(summary) else ""
        location = _field(job, "location", "dq", "jobDq", "address", "city", "工作地点", "城市")
        return {"channel": "liepin", "external_id": external_id, "url": url, "company": name[:150],
                "title": title[:180], "location": location[:120], "description": description[:24000],
                "requirements": requirements[:24000], "salary": _field(job, "salary", "salaryDesc", "薪资")[:120],
                "status": "open" if full_jd and location and url != SEARCH_URL else "partial"}

    def read_messages(self):
        raise ChannelUnavailable("unsupported", "猎聘官方个人接口未核实有站内私聊读取，使用浏览器接管")

    def send(self, *args, **kwargs):
        raise ChannelUnavailable("unsupported", "猎聘官方投递未核实可绑定已批准的指定 PDF 或核验异常后结果，使用浏览器接管")

    def reconcile(self, *args, **kwargs):
        raise ChannelUnavailable("unsupported", "猎聘官方个人接口未核实有站内发送记录查询，使用浏览器核验")


def _field(row, *keys):
    for key in keys:
        value = row.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return ""
