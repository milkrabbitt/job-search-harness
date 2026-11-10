"""Supported public career feeds and authenticated 126.com transport.

Adapters never establish an automated session against a private recruitment portal.
"""
import base64
import binascii
import contextlib
import hashlib
import html
import http.client
import imaplib
import ipaddress
import json
import re
import smtplib
import socket
import ssl
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from urllib.request import getproxies, proxy_bypass
from urllib.robotparser import RobotFileParser

from . import rules
from .credential_store import default_keychain_locked


USER_AGENT = "PersonalJobHarness/1.0"
FUXIAN_HOST = "research.fuxian.fun"
MAX_RESPONSE = 2_000_000
MAX_MAIL = 1_000_000
MAX_ATTACHMENT = 20_000_000
ADDRESS = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}\Z")
MESSAGE_ID = re.compile(r"<(?:(?![<>@])[!-~])+@(?:(?![<>@])[!-~])+>")
CITY = re.compile(r"(北京|上海|天津|重庆|杭州市?|深圳市?|广州市?|苏州市?|武汉市?|成都市?|宁波市?|西安市?|南京市?|合肥市?|珠海市?|长沙市?|青岛市?|济南市?|福州市?|厦门市?|郑州市?|南昌市?|东莞市?|香港|海外|远程)")
PLATFORM_HANDOFF = {
    "boss": ("BOSS直聘", "https://www.zhipin.com/web/geek/chat"),
    "liepin": ("猎聘", "https://www.liepin.com/"),
    "yupao": ("鱼泡直聘", "https://www.yupao.com/"),
}


class ChannelUnavailable(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _result(state, evidence, provider_id=None):
    return {"state": state, "evidence": evidence, "provider_id": provider_id}


def _smtp_limited(code, response):
    reply = response.decode("utf-8", "replace") if isinstance(response, bytes) else str(response or "")
    return code == 421 or bool(re.search(
        r"rate.limit|sending limit|too frequent|too many (?:messages|connections)|频率|发信频繁|发[信送]过快|发信限额",
        reply, re.I,
    ))


def _smtp_failure(code, evidence, message_id, response=None):
    temporary = 400 <= code < 500
    limited = _smtp_limited(code, response)
    if limited:
        evidence += " 邮箱服务要求退避。"
    return {**_result("failed", evidence, message_id), "retryable": temporary,
            "retry_after": 1800 if temporary else None, "channel_retry_after": 1800 if limited else None}


def _text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _addresses(value):
    return [address.lower() for _, address in getaddresses([str(value or "")])
            if ADDRESS.fullmatch(address)]


def _provider_authenticated(message, senders):
    if len(senders) != 1:
        return False
    results = message.get_all("Authentication-Results", [])
    if not results:
        return False
    authserv, _, checks = str(results[0]).partition(";")
    service_name = authserv.strip().split()[0].lower() if authserv.strip() else ""
    if not any(service_name == d or service_name.endswith("." + d) for d in ("126.com", "163.com", "netease.com")):
        return False
    domain = senders[0].rpartition("@")[2]
    for clause in checks.split(";"):
        if not re.search(r"\bdmarc\s*=\s*pass\b", clause, re.I):
            continue
        aligned = re.search(r"\bheader\.from\s*=\s*([a-zA-Z0-9.-]+)", clause, re.I)
        if aligned and aligned[1].lower().rstrip(".") == domain:
            return True
    return False


def _message_ids(value):
    if isinstance(value, (list, tuple)):
        value = " ".join(str(part) for part in value)
    return MESSAGE_ID.findall(str(value or ""))


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


class ManualPlatform:
    def __init__(self, name):
        self.name = name
        self.label, self.url = PLATFORM_HANDOFF[name]

    def probe(self):
        return {"status": "manual_handoff", "detail": "未核实支持后台读写的求职者接口。"
                "使用已登录的正常页面人工接管，系统不读取浏览器凭据。", "url": self.url,
                "capabilities": {"discover": False, "read": False, "send": False}}

    def _unavailable(self, *args, **kwargs):
        raise ChannelUnavailable("unsupported", self.label + "目前须在网页人工接管。")

    discover = read_messages = send = reconcile = _unavailable


class _PublicFetch:
    """Fetch allowlisted public HTTPS pages, pinning the DNS answer for each socket."""
    def __init__(self, hosts, timeout):
        self.hosts = frozenset(host.lower() for host in hosts)
        self.timeout = max(2, min(float(timeout), 12))
        self.robots = {}

    def _connection(self, hostname, addresses):
        context = ssl.create_default_context()
        fake_ip_range = ipaddress.ip_network("198.18.0.0/15")
        fake_dns = addresses and all(ipaddress.ip_address(ip) in fake_ip_range for ip in addresses)
        global_dns = addresses and all(ipaddress.ip_address(ip).is_global for ip in addresses)
        if fake_dns:
            # The active macOS network client maps allowlisted names to a local TUN route.
            # Respect the existing proxy bypass rule and still require TLS for the original hostname.
            proxy = urlsplit(getproxies().get("https", ""))
            if (proxy.scheme == "http" and proxy.hostname in {"127.0.0.1", "::1"} and proxy.port
                    and not proxy.username and not proxy.password):
                if not proxy_bypass(hostname):
                    connection = http.client.HTTPSConnection(proxy.hostname, proxy.port,
                                                            timeout=self.timeout, context=context)
                    connection.set_tunnel(hostname, 443)
                    return connection
            else:
                fake_dns = False
        if not (global_dns or fake_dns):
            raise ChannelUnavailable("configuration_required", "来源未解析为公网地址，且本机未配置可核实的代理路由。")
        connection = http.client.HTTPSConnection(hostname, timeout=self.timeout, context=context)

        def dial(_address, timeout, source_address=None):
            return socket.create_connection((addresses[0], 443), timeout, source_address)

        connection._create_connection = dial
        return connection

    def _request(self, url, check_robots=False):
        for _ in range(4):
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.hostname not in self.hosts
                    or parts.username is not None or parts.password is not None
                    or parts.port not in (None, 443) or parts.fragment):
                raise ChannelUnavailable("configuration_required", "仅允许已登记的公开 HTTPS 来源及其允许的跳转。")
            hostname = parts.hostname
            if check_robots:
                self._check_robots(url)
            try:
                addresses = list(dict.fromkeys(item[4][0] for item in
                    socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)))
                conn = self._connection(hostname, addresses)
                try:
                    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
                    if parts.query:
                        path += "?" + quote(parts.query, safe="=&%/:?+;,@!$'()*[]-._~")
                    conn.request("GET", path, headers={"User-Agent": USER_AGENT,
                                  "Accept": "text/html,application/json,application/javascript,text/plain",
                                  "Accept-Encoding": "identity"})
                    response = conn.getresponse()
                    if response.status in {301, 302, 303, 307, 308}:
                        target = response.getheader("Location")
                        if not target:
                            raise ChannelUnavailable("page_changed", "公开来源重定向缺少目的地。")
                        url = urljoin(url, target)
                        continue
                    if response.status in {401, 403, 429}:
                        raise ChannelUnavailable("platform_limit", "公开来源拒绝访问或已限流。")
                    if response.status == 404:
                        return url, 404, ""
                    if response.status != 200:
                        raise ChannelUnavailable("network", f"公开来源返回 HTTP {response.status}。")
                    if response.getheader("Content-Encoding", "identity") not in ("", "identity"):
                        raise ChannelUnavailable("page_changed", "来源返回不支持的内容编码。")
                    payload = response.read(MAX_RESPONSE + 1)
                    if len(payload) > MAX_RESPONSE:
                        raise ChannelUnavailable("page_changed", "公开来源响应超出安全大小限制。")
                    charset = response.headers.get_content_charset() or "utf-8-sig"
                    try:
                        return url, 200, payload.decode(charset, "replace")
                    except LookupError:
                        return url, 200, payload.decode("utf-8-sig", "replace")
                finally:
                    conn.close()
            except (OSError, http.client.HTTPException):
                raise ChannelUnavailable("network", "公开来源网络、TLS 或响应失败。") from None
        raise ChannelUnavailable("page_changed", "来源重定向次数超限。")

    def _check_robots(self, url):
        host = urlsplit(url).hostname
        if host not in self.robots:
            _, code, rules = self._request("https://" + host + "/robots.txt")
            parser = RobotFileParser()
            parser.parse(rules.lstrip("\ufeff").splitlines() if code == 200 else [])
            self.robots[host] = parser
        if not self.robots[host].can_fetch(USER_AGENT, url):
            raise ChannelUnavailable("platform_limit", "来源的 robots.txt 不允许该路径。")

    def get(self, url):
        target, status, body = self._request(url, check_robots=True)
        if status != 200:
            raise ChannelUnavailable("page_changed", "公开来源或职位不存在。")
        return target, body


class _JobHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.ld = []
        self.scripts = []
        self.anchor = None
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.anchor = {"href": attrs["href"], "parts": [], "data": attrs}
        if tag == "script":
            if attrs.get("src"):
                self.scripts.append(attrs["src"])
            if attrs.get("type", "").lower() == "application/ld+json":
                self.script = []

    def handle_data(self, data):
        if self.anchor and data.strip():
            self.anchor["parts"].append(data.strip())
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            self.links.append(self.anchor)
            self.anchor = None
        if tag == "script" and self.script is not None:
            self.ld.append("".join(self.script))
            self.script = None


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = False

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())

    def handle_starttag(self, tag, attrs):
        if tag in {"style", "script"}:
            self.hidden = True

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self.hidden = False


def _plain(value):
    parser = _VisibleText()
    parser.feed(html.unescape(str(value or "")))
    return "\n".join(parser.parts)


def _split_requirements(body):
    chunks = re.split(r"(?:^|\n)\s*【?(?:任职要求|任职资格|职位要求|岗位要求|Qualifications|Requirements)】?\s*[:：]?\s*",
                      body, 1, re.I)
    if len(chunks) == 2:
        return chunks[0].strip(), chunks[1].strip()
    return body.strip(), ""


class OfficialChannel:
    name = "official"

    def __init__(self, config):
        self.config = config
        self.last_errors = []
        self.last_sources = []

    def probe(self):
        return {"status": "ready", "detail": "可读取已核验的公开招聘站及 Greenhouse/Lever 公开职位接口。",
                "capabilities": {"discover": True, "read": False, "send": False}}

    def _job(self, source, row, url, external_id, title, location="", salary="", description="",
             requirements="", published_at=None, status=None):
        company = source.get("company") or row.get("company")
        if not company or not title or not external_id:
            return None
        result = {
            "channel": self.name, "company": _text(company), "title": _text(title),
            "external_id": str(external_id), "url": url,
            "domain": source.get("domain") or urlsplit(source.get("url", url)).hostname,
            "location": _text(location), "salary": _text(salary), "description": _text(description)[:18000],
            "requirements": _text(requirements)[:14000], "published_at": published_at,
            "status": status or ("open" if requirements else "partial"), "source_url": source.get("url", url),
        }
        if result["status"] not in rules.RECRUITING_ENDED:
            recruiter = self._listed_recruiter(result["description"] + "\n" + result["requirements"])
            employer_domain = str(result["domain"] or "").lower().removeprefix("www.")
            recruiter_domain = recruiter.rpartition("@")[2]
            if recruiter and employer_domain and (recruiter_domain == employer_domain or recruiter_domain.endswith("." + employer_domain)):
                result["recipient"] = recruiter
                result["recipient_source"] = "职位原文明确收简历 " + url
        return result

    @staticmethod
    def _listed_recruiter(text):
        addresses = set()
        for line in re.split(r"[。；;\n]", text):
            if not re.search(r"(简历|résumé|resume|CV).{0,14}(发送|投递|提交|邮件|邮箱|to)|"
                             r"(发送|投递|提交|send|submit|email).{0,14}(简历|résumé|resume|CV)", line, re.I):
                continue
            for value in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,63}", line):
                if ADDRESS.fullmatch(value) and not re.search(r"example\.|@localhost|test\.", value, re.I):
                    addresses.add(value.lower())
        return next(iter(addresses)) if len(addresses) == 1 else ""

    def _greenhouse(self, source, target, page):
        rows = json.loads(page)["jobs"]
        for row in rows:
            desc, requirements = _split_requirements(_plain(row.get("content", "")))
            yield self._job(source, row, row.get("absolute_url") or target, row["id"], row["title"],
                            row.get("location", {}).get("name"), description=desc,
                            requirements=requirements, published_at=row.get("first_published"))

    def _lever(self, source, target, page):
        for row in json.loads(page):
            cats = row.get("categories") or {}
            requirements = []
            for section in row.get("lists") or []:
                if isinstance(section, dict):
                    requirements.append(_plain(section.get("text", "")) + "\n" + _plain(section.get("content", "")))
            salary = row.get("salaryRange") or {}
            shown_salary = " ".join(str(salary[key]) for key in ("currency", "min", "max", "interval") if salary.get(key))
            yield self._job(source, row, row.get("hostedUrl") or target, row["id"], row["text"],
                            cats.get("location") or ", ".join(cats.get("allLocations") or []),
                            shown_salary, _plain(row.get("descriptionPlain") or row.get("description")),
                            "\n".join(requirements))

    def _fuxian(self, source, target, page):
        parts = urlsplit(target)
        if parts.hostname != FUXIAN_HOST or not re.fullmatch(r"/jobs/[a-z0-9-]+/", parts.path):
            return
        title = re.search(r"<h1[^>]*>([^<]+)</h1>", page, re.I)
        duties, requirements = (re.search(r'id="' + key + r'"[^>]*>[^<]+</h[23]>\s*<ul[^>]*>(.*?)</ul>', page, re.I | re.S)
                                for key in ("duty", "req"))
        address, salary = (re.search(r"<dt>" + key + r"</dt>\s*<dd>([^<]+)</dd>", page, re.I)
                           for key in ("工作地点", "薪资"))
        if not all((title, duties, requirements, address, salary)):
            return
        row = self._job(source, {}, target, target, _plain(title[1]), _plain(address[1]), _plain(salary[1]),
                        _plain(duties[1]), _plain(requirements[1]))
        if not row:
            return
        # Only the pre-verified per-vacancy address qualifies. Ignore footer and generic contact links.
        apply = re.search(r"投递方式\s*[：:]\s*将简历发送至\s*([a-z0-9.+_%\-]+@[a-z0-9.\-]+)\b", _plain(page), re.I)
        approved = str(source.get("recipient") or "").strip().lower()
        if apply and approved and apply[1].lower() == approved and source.get("recipient_source"):
            row.update(recipient=approved, recipient_source="该职位官网明确指定招聘邮箱 · " + target)
        yield row

    def _unitree(self, source, target, parser):
        for anchor in parser.links:
            url = urljoin(target, anchor["href"])
            parts = urlsplit(url)
            match = re.fullmatch(r"/(?:cn|mobile)/position/(\d{8,})/?", parts.path)
            if parts.hostname != urlsplit(target).hostname or not match or len(anchor["parts"]) < 3:
                continue
            title, *rest = anchor["parts"]
            rest = [part for part in rest if part not in {"热招", "急招", "查看详情"}]
            if len(rest) < 2:
                continue
            location, *content = rest
            city = location.split("|")[0].strip() if "|" in location else location
            description, requirements = _split_requirements("\n".join(content))
            # A card can omit part of the full JD. Never send from its evidence alone.
            yield self._job(source, {}, url, match[1], title, city, description=description,
                            requirements=requirements, status="open" if requirements else "partial")

    def _peakvision(self, source, target, parser, fetch):
        scripts = [urljoin(target, src) for src in parser.scripts
                   if urlsplit(src).path.endswith("/jobs-data.js") or urlsplit(src).path == "assets/jobs-data.js"]
        if not scripts:
            # The public careers index has a stable detail link. Its public detail page
            # includes an official static data asset that powers the visible job description.
            listing = next((urljoin(target, anchor["href"]) for anchor in parser.links
                            if "careers-role.html" in anchor["href"] and urlsplit(urljoin(target, anchor["href"])).hostname
                            == urlsplit(target).hostname), None)
            if listing:
                detail_url, detail = fetch.get(listing)
                detail_parser = _JobHTML()
                detail_parser.feed(detail)
                scripts = [urljoin(detail_url, src) for src in detail_parser.scripts
                           if urlsplit(src).path.endswith("assets/jobs-data.js")]
        if not scripts:
            raise ChannelUnavailable("page_changed", "峰瞰公开职位数据入口未找到。")
        _, contents = fetch.get(scripts[0])
        assignment = re.search(r"\bwindow\.PEAK_JOBS\s*=\s*(?=\[)", contents)
        if not assignment:
            raise ChannelUnavailable("page_changed", "峰瞰公开职位数据格式变化。")
        rows, _ = json.JSONDecoder().raw_decode(contents[assignment.end():])
        listed = {parse_qs(urlsplit(link["href"]).query).get("job", [""])[0] for link in parser.links}
        for row in rows:
            slug = row.get("slug", "")
            if slug not in listed or not re.fullmatch(r"[a-z0-9-]+", slug):
                continue
            description, requirements = _split_requirements(row.get("description") or row.get("summary", ""))
            if row.get("group") == "intern":
                requirements = "实习\n" + requirements
            yield self._job(source, row, urljoin(target, "careers-role.html?job=" + slug),
                            slug, row.get("title"), row.get("location"), row.get("salary"),
                            description, requirements, status="open" if requirements else "partial")

    def _schema(self, source, target, parser):
        pending = []
        for block in parser.ld:
            try:
                pending.append(json.loads(block))
            except (TypeError, ValueError):
                continue
        while pending:
            row = pending.pop()
            if isinstance(row, list):
                pending.extend(row)
                continue
            if not isinstance(row, dict):
                continue
            if "@graph" in row:
                pending.append(row["@graph"])
            kind = row.get("@type")
            if kind != "JobPosting" and (not isinstance(kind, list) or "JobPosting" not in kind):
                continue
            company = row.get("hiringOrganization")
            company = company.get("name") if isinstance(company, dict) else ""
            locations = row.get("jobLocation") or []
            if isinstance(locations, dict):
                locations = [locations]
            names = []
            for location in locations:
                address = location.get("address") or {}
                if isinstance(address, dict):
                    names.append(str(address.get("addressLocality") or address.get("addressRegion") or ""))
                else:
                    names.append(str(address))
            if row.get("jobLocationType") == "TELECOMMUTE":
                names.append("远程")
            url = row.get("url") or target
            if not isinstance(url, str) or urlsplit(url).scheme != "https":
                url = target
            ident = row.get("identifier")
            if isinstance(ident, dict):
                ident = ident.get("value")
            desc, requirements = _split_requirements(_plain(row.get("description")))
            requirements = "\n".join(filter(None, (requirements, _plain(row.get("qualifications")),
                                                          _plain(row.get("experienceRequirements")))))
            valid_until = str(row.get("validThrough") or "")
            status = "unknown" if requirements else "partial"
            if re.match(r"^\d{4}-\d\d-\d\d", valid_until):
                if valid_until[:10] < _iso_now()[:10]:
                    status = "closed"
                elif requirements:
                    status = "open"
            salary = row.get("baseSalary")
            if isinstance(salary, dict):
                value = salary.get("value")
                if isinstance(value, dict):
                    salary = " ".join(str(entry) for entry in (salary.get("currency"), value.get("value"),
                        value.get("minValue"), value.get("maxValue"), value.get("unitText")) if entry)
                else:
                    salary = str(value or "")
            yield self._job(source, {"company": company}, url, ident or url, row.get("title") or row.get("name"),
                            " / ".join(filter(None, names)), salary, desc, requirements, row.get("datePosted"), status)

    def discover(self, sources):
        self.last_errors = []
        self.last_sources = []
        jobs = {}
        active_sources = [source for source in sources if source.get("enabled", True)]
        for source in active_sources:
            url = source.get("url", "")
            try:
                host = urlsplit(url).hostname
                hosts = {host} | set(source.get("allowed_hosts", []))
                if not host or not all(isinstance(entry, str) for entry in hosts):
                    raise ChannelUnavailable("configuration_required", "招聘来源主机无效。")
                fetch = _PublicFetch(hosts, self.config.get("timeout_seconds", 7))
                target, page = fetch.get(url)
                kind = source.get("kind", "schema")
                if kind == "greenhouse":
                    extracted = self._greenhouse(source, target, page)
                elif kind == "lever":
                    extracted = self._lever(source, target, page)
                elif kind == "fuxian":
                    extracted = self._fuxian(source, target, page)
                else:
                    parser = _JobHTML()
                    parser.feed(page)
                    if kind == "unitree":
                        extracted = self._unitree(source, target, parser)
                    elif kind == "peakvision":
                        extracted = self._peakvision(source, target, parser, fetch)
                    elif kind == "schema":
                        extracted = self._schema(source, target, parser)
                    else:
                        raise ChannelUnavailable("configuration_required", "未支持此公开来源格式。")
                extracted = [job for job in extracted if job]
                if not extracted:
                    raise ChannelUnavailable("page_changed", "页面已读，但没有可核验的职位结构。")
                for job in extracted:
                    jobs[(job["domain"], job["external_id"])] = job
                self.last_sources.append({"url": target, "count": len(extracted), "checked_at": _iso_now()})
            except ChannelUnavailable as exc:
                self.last_errors.append({"url": url, "code": exc.code, "detail": exc.detail})
            except (KeyError, ValueError, TypeError, AttributeError):
                self.last_errors.append({"url": url, "code": "page_changed", "detail": "公开来源数据无法解析。"})
        if active_sources and not self.last_sources:
            first = self.last_errors[0]
            raise ChannelUnavailable(first["code"], first["detail"])
        return list(jobs.values())

    def read_messages(self):
        raise ChannelUnavailable("unsupported", "公开官网发现渠道不包含收件箱。")

    def send(self, item, attachment):
        raise ChannelUnavailable("unsupported", "官网表单暂须人工提交；官网招聘邮箱请使用邮箱渠道。")

    def reconcile(self, item):
        return _result("unknown", "官网表单尚无受支持的提交记录查询接口。")


_RECRUITMENT_TERMS = re.compile(
    r"招聘|求职|面试|笔试|候选人|录用|申请回执|投递|应聘|简历|岗位|作业截止|人才库|退信|"
    r"\b(?:recruit(?:ment|er|ing)?|interview|job application|candidate|offer letter|undeliver(?:ed|able)|"
    r"delivery status notification)\b", re.I)
_IMAP_LIST = re.compile(rb'^\(([^)]*)\) (?:"[^\"]*"|NIL) (.+)$', re.I)
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_IMAP_FIELDS = ("MESSAGE-ID REFERENCES IN-REPLY-TO FROM TO REPLY-TO SUBJECT DATE AUTO-SUBMITTED AUTHENTICATION-RESULTS "
                "PRECEDENCE CONTENT-TYPE CONTENT-TRANSFER-ENCODING MIME-VERSION")


def _wire_folder_name(raw):
    raw = raw.strip(b'"')
    try:
        name = raw.decode("ascii")
        def decode(match):
            token = match.group(1).replace(",", "/")
            if not token:
                return "&"
            return base64.b64decode(token + "=" * (-len(token) % 4), validate=True).decode("utf-16-be")
        return re.sub(r"&([^-]*)-", decode, name)
    except (UnicodeError, binascii.Error):
        return ""


def _literal(data):
    return next((entry for entry in data or [] if isinstance(entry, tuple)
                 and len(entry) == 2 and isinstance(entry[1], bytes)), (b"", b""))


def _header_bytes(data):
    return _literal(data)[1]


def _parse_date(value):
    try:
        value = parsedate_to_datetime(str(value or ""))
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _delivery_notice(message):
    if message.get_content_type() != "multipart/report" or message.get_param("report-type") != "delivery-status":
        return [], ""
    ids, statuses = [], []
    for part in message.iter_parts():
        payload = part.get_payload()
        if part.get_content_type() == "text/rfc822-headers" and isinstance(payload, str):
            original = BytesParser(policy=policy.default).parsebytes(payload.encode("utf-8"), headersonly=True)
            ids += _message_ids(original.get("Message-ID"))
            continue
        if not isinstance(payload, list):
            continue
        if part.get_content_type() == "message/rfc822" and payload:
            ids += _message_ids(payload[0].get("Message-ID"))
        if part.get_content_type() == "message/delivery-status":
            for block in payload:
                ids += _message_ids(block.get("Original-Message-ID"))
                action, code = block.get("Action"), block.get("Status")
                if action and re.fullmatch(r"[245]\.\d{1,3}\.\d{1,3}", str(code or "")):
                    statuses.append(f"{action} {code}")
    return list(dict.fromkeys(ids)), "; ".join(statuses)


class MailChannel:
    """A scoped 126.com adapter. The web session is intentionally separate from IMAP/SMTP."""
    name = "email"
    uses_mail_cursor = True
    imap_host, smtp_host = "imap.126.com", "smtp.126.com"
    imap_port, smtp_port = 993, 465

    def __init__(self, config):
        self.config = config
        self.account = str(config.get("account", "")).strip().lower()
        self.timeout = max(3, min(int(config.get("timeout_seconds", 12)), 30))
        self.enabled = config.get("enabled", True)
        self.known_contacts = {_text(address).lower() for address in config.get("known_contacts", [])}
        self.known_message_ids = set(_message_ids(config.get("known_message_ids", [])))

    def _authorization(self):
        if not self.enabled:
            raise ChannelUnavailable("disabled", "126 邮箱适配器已停用。")
        if not ADDRESS.fullmatch(self.account) or not self.account.endswith("@126.com"):
            raise ChannelUnavailable("configuration_required", "需要配置现有的 @126.com 邮箱账号。")
        service = str(self.config.get("keychain_service") or "job-harness-126-imap-smtp")
        if sys.platform != "darwin" or not Path("/usr/bin/security").exists():
            raise ChannelUnavailable("configuration_required", "本机钥匙串不可用。")
        if default_keychain_locked() is True:
            raise ChannelUnavailable("configuration_required", "Mac 登录钥匙串锁定，网易邮箱已暂停读取凭据。")
        try:
            result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service,
                                     "-a", self.account, "-w"], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ChannelUnavailable("configuration_required", "无法读取此账号专用的钥匙串条目。") from None
        try:
            secret = result.stdout.decode("ascii").strip() if result.returncode == 0 else ""
        except UnicodeError:
            secret = ""
        if not secret or any(character in secret for character in "\r\n\x00"):
            raise ChannelUnavailable("configuration_required", "需要在网易邮箱启用 IMAP/SMTP，并把客户端授权码保存至本机专用钥匙串。")
        return secret

    @contextlib.contextmanager
    def _imap(self, secret):
        client = None
        try:
            client = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=self.timeout,
                                      ssl_context=ssl.create_default_context())
            try:
                client.login(self.account, secret)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                raise ChannelUnavailable("login_required", "网易 IMAP 未接受客户端授权，请核对协议开关及授权码。") from None
            # NetEase requires RFC 2971 identification for custom clients.
            imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
            code, _ = client.xatom("ID", '("name" "PersonalJobHarness" "version" "1.0")')
            if code != "OK":
                raise ChannelUnavailable("platform_limit", "网易 IMAP 拒绝客户端标识，已暂停查询。")
            yield client
        except imaplib.IMAP4.abort:
            raise ChannelUnavailable("network", "网易 IMAP 连接中断。") from None
        except imaplib.IMAP4.error:
            raise ChannelUnavailable("platform_limit", "网易 IMAP 查询被拒绝，需核对账号设置或权限。") from None
        except (OSError, EOFError):
            raise ChannelUnavailable("network", "网易 IMAP 网络或 TLS 不可用。") from None
        finally:
            if client is not None:
                with contextlib.suppress(OSError, imaplib.IMAP4.error):
                    client.logout()

    def _smtp(self, secret):
        client = None
        try:
            client = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout,
                                      context=ssl.create_default_context())
            client.login(self.account, secret)
            return client
        except (smtplib.SMTPException, OSError) as exc:
            if client is not None:
                with contextlib.suppress(OSError):
                    client.close()
            if isinstance(exc, smtplib.SMTPResponseException) and _smtp_limited(exc.smtp_code, exc.smtp_error):
                raise ChannelUnavailable("smtp_rate_limited", "网易 SMTP 拒绝本次连接，需退避后再发送。") from None
            if isinstance(exc, smtplib.SMTPAuthenticationError) and exc.smtp_code >= 500:
                raise ChannelUnavailable("login_required", "网易 SMTP 未接受客户端授权，请核对协议开关及授权码。") from None
            raise ChannelUnavailable("network", "网易 SMTP 连接或 TLS 不可用。") from None

    def _sent_folder(self, client):
        code, folders = client.list()
        if code != "OK":
            raise ChannelUnavailable("configuration_required", "未能列出网易邮箱的已发送目录。")
        known_names = {"sent", "sent items", "sent messages", "已发送", "已发送邮件"}
        configured = self.config.get("sent_folder")
        preferred, fallback = [], []
        for entry in folders or []:
            match = _IMAP_LIST.fullmatch(entry) if isinstance(entry, bytes) else None
            if not match:
                continue
            flags, wire = match.groups()
            if b"\\noselect" in flags.lower().split():
                continue
            name = _wire_folder_name(wire)
            if not name or not re.fullmatch(rb'"[^"\r\n\\]+"|[^\s(){}"\\]+', wire):
                continue
            if configured and name == configured:
                return wire
            if b"\\sent" in flags.lower().split():
                preferred.append(wire)
            if name.casefold() in known_names:
                fallback.append(wire)
        if not configured and len(preferred) == 1:
            return preferred[0]
        if not configured and not preferred and len(fallback) == 1:
            return fallback[0]
        raise ChannelUnavailable("configuration_required", "未唯一核实 IMAP 已发送目录，可在本机配置中指定目录名称。")

    def probe(self):
        capabilities = {"discover": False, "read": False, "send": False}
        try:
            secret = self._authorization()
            with self._imap(secret) as client:
                if client.select("INBOX", readonly=True)[0] != "OK":
                    raise ChannelUnavailable("login_required", "已连接网易 IMAP，收件箱不可读。")
                capabilities["read"] = True
                sent = self._sent_folder(client)
                if client.select(sent, readonly=True)[0] != "OK":
                    raise ChannelUnavailable("configuration_required", "已发送目录不可读，无法核验发信记录。")
            smtp = self._smtp(secret)
            with contextlib.suppress(OSError, smtplib.SMTPException):
                smtp.close()
            capabilities["send"] = True
            return {"status": "ready", "detail": "网易 IMAP 收件箱、已发送目录及 SMTP 登录均已核实。",
                    "capabilities": capabilities}
        except ChannelUnavailable as exc:
            return {"status": exc.code, "detail": exc.detail, "capabilities": capabilities}

    def discover(self, sources):
        raise ChannelUnavailable("unsupported", "网易邮箱适配器只处理招聘来信和已核实招聘地址的外联。")

    def _relevant(self, message):
        return bool(self.known_contacts.intersection(_addresses(message.get("From")) + _addresses(message.get("To")))
                    or self.known_message_ids.intersection(_message_ids(message.get("References"))
                                                           + _message_ids(message.get("In-Reply-To")))
                    or _RECRUITMENT_TERMS.search(str(message.get("Subject", ""))))

    def _folder_messages(self, client, folder, folder_key, direction, cursor=None, completed=None):
        if client.select(folder, readonly=True)[0] != "OK":
            raise ChannelUnavailable("login_required", "网易邮件目录无法只读打开。")
        values = client.response("UIDVALIDITY")[1]
        validity = next((entry for entry in values or [] if isinstance(entry, bytes)), b"?")
        validity_key = validity.decode("ascii", "ignore")
        previous = (cursor or {}).get(folder_key, {})
        last_uid = previous.get("uid", 0) if validity.isdigit() and previous.get("validity") == validity_key else 0
        last_uid = last_uid if isinstance(last_uid, int) and last_uid > 0 else 0
        days = max(1, min(int(self.config.get("history_days", 14)), 60))
        limit = max(1, min(int(self.config.get("scan_limit", 120)), 250))
        since = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)
        code, found = client.uid("search", None, "SINCE", f"{since.day:02d}-{_IMAP_MONTHS[since.month-1]}-{since.year}")
        if code != "OK":
            raise ChannelUnavailable("platform_limit", "网易 IMAP 未完成邮件搜索。")
        uids = sorted({uid for uid in (found[0] or b"").split() if uid.isdigit()}, key=int) if found else []
        uids = [uid for uid in uids if int(uid) > last_uid]
        selected = uids[:limit] if last_uid else uids[-limit:]
        for uid in selected:
            code, result = client.uid("fetch", uid, f"(INTERNALDATE BODY.PEEK[HEADER.FIELDS ({_IMAP_FIELDS})])")
            if code != "OK":
                raise ChannelUnavailable("platform_limit", "网易 IMAP 未完成邮件头读取。")
            metadata, raw_headers = _literal(result)
            if not raw_headers:
                continue
            message = BytesParser(policy=policy.default).parsebytes(raw_headers, headersonly=True)
            if re.search(r"密码重置|重置.{0,8}密码|password reset|验证码|新设备登录", str(message.get("Subject","")), re.I):
                continue
            if not self._relevant(message):
                continue
            code, result = client.uid("fetch", uid, f"(BODY.PEEK[TEXT]<0.{MAX_MAIL}>)")
            if code != "OK":
                raise ChannelUnavailable("platform_limit", "网易 IMAP 未完成相关邮件读取。")
            raw_body = _header_bytes(result)
            message = BytesParser(policy=policy.default).parsebytes(raw_headers.rstrip(b"\r\n") + b"\r\n\r\n" + raw_body)
            body_part = message.get_body(preferencelist=("plain", "html"))
            body = ""
            if body_part is not None:
                try:
                    body = body_part.get_content()
                    if body_part.get_content_type() == "text/html":
                        body = _plain(body)
                except (LookupError, UnicodeError, KeyError, TypeError, ValueError):
                    body = ""
            sender = _addresses(message.get("From"))
            reply_to_headers = message.get_all("Reply-To", [])
            reply_to = _addresses(reply_to_headers[0]) if len(reply_to_headers) == 1 else []
            recipients = _addresses(message.get("To"))
            own_ids = _message_ids(message.get("Message-ID"))
            parents = _message_ids(message.get("In-Reply-To"))
            refs = _message_ids(message.get("References"))
            returned_ids, delivery_status = _delivery_notice(message)
            refs = list(dict.fromkeys(refs + parents + returned_ids))
            internal_date = re.search(rb'INTERNALDATE "([^"\r\n]+)"', metadata or b"", re.I)
            arrived = _parse_date(internal_date[1].decode("ascii", "ignore").replace("-", " ", 2)) if internal_date else None
            external_id = own_ids[0] if len(own_ids) == 1 else ":".join((folder_key, validity.decode("ascii", "ignore"), uid.decode("ascii")))
            yield {"channel": "email", "external_id": external_id,
                   "thread_external_id": (refs or parents or [external_id])[0],
                   "subject": str(message.get("Subject", ""))[:1000],
                   "sender": sender[0] if len(sender) == 1 else "",
                   "sender_authenticated": _provider_authenticated(message, sender),
                   "reply_to": reply_to[0] if len(reply_to) == 1 else "",
                   "reply_to_unverified": bool(reply_to_headers) and len(reply_to) != 1,
                   "recipient": recipients[0] if len(recipients) == 1 else "",
                   "body": str(body)[:24_000], "occurred_at": arrived or _parse_date(message.get("Date")),
                   "mail_date": _parse_date(message.get("Date")),
                   "message_id": own_ids[0] if len(own_ids) == 1 else None,
                   "in_reply_to": parents[0] if len(parents) == 1 else None,
                   "references": refs, "direction": direction,
                   "auto_submitted": str(message.get("Auto-Submitted", "")),
                   "delivery_status": delivery_status,
                   "possibly_truncated": len(raw_body) >= MAX_MAIL or len(str(body)) > 24_000
                   or (bool(raw_body) and not body)}
        if completed is not None and validity.isdigit():
            completed[folder_key] = {"validity": validity_key, "uid": int(selected[-1]) if selected else last_uid}

    def read_messages(self, known_contacts=None, known_message_ids=None, cursor=None):
        self.known_contacts.update(_text(address).lower() for address in known_contacts or [])
        self.known_message_ids.update(_message_ids(known_message_ids or []))
        secret = self._authorization()
        completed = {}
        self.completed_cursor = None
        with self._imap(secret) as client:
            incoming = list(self._folder_messages(client, "INBOX", "INBOX", "incoming", cursor, completed))
            try:
                sent_folder = self._sent_folder(client)
            except ChannelUnavailable as exc:
                if exc.code != "configuration_required":
                    raise
                return incoming
            outgoing = list(self._folder_messages(client, sent_folder, "SENT", "outgoing", cursor, completed))
        self.completed_cursor = completed
        return sorted(incoming + outgoing, key=lambda item: item["occurred_at"] or "")

    def _prepare(self, item, attachment):
        recipient = str(item.get("recipient") or "").strip().lower()
        subject = str(item.get("subject") or "").strip()
        body = str(item.get("body") or "").strip()
        mid = str(item.get("message_id") or "").strip()
        if not ADDRESS.fullmatch(recipient) or recipient == self.account:
            raise ValueError("需要已核实的单一招聘收件地址。")
        if not subject or len(subject) > 500 or not body or len(body) > 40_000 or not MESSAGE_ID.fullmatch(mid):
            raise ValueError("邮件主题、正文或已登记的 Message-ID 无效。")
        if any(c in subject for c in "\r\n\x00"):
            raise ValueError("邮件主题含无效控制字符。")
        parents = _message_ids(item.get("in_reply_to"))
        refs = _message_ids(item.get("references") or item.get("refs"))
        threaded = item.get("kind") in {"reply", "followup"} or bool(parents)
        if threaded and len(parents) != 1:
            raise ValueError("缺少可核验的原信 Message-ID，邮件无法在线程内回复。")
        if threaded and item.get("expected_reply_to") and item["expected_reply_to"].lower() != recipient:
            raise ValueError("收件人与原信指定的回复地址不一致。")
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = self.account
        message["To"] = recipient
        message["Subject"] = "Re: " + subject if threaded and not re.match(r"^\s*(?:re|回复)\s*[:：]", subject, re.I) else subject
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        message["Message-ID"] = mid
        if threaded:
            message["In-Reply-To"] = parents[0]
            message["References"] = " ".join(dict.fromkeys(refs[:1] + refs[-25:] + parents))
        message.set_content(body)
        if attachment:
            path = Path(attachment.get("path") or "")
            expected = str(attachment.get("sha256") or attachment.get("sha") or "").lower()
            filename = str(attachment.get("filename") or path.name)
            if (not re.fullmatch(r"[a-f0-9]{64}", expected) or Path(filename).name != filename
                    or not filename.lower().endswith(".pdf") or any(c in filename for c in "\r\n\x00")):
                raise ValueError("简历附件缺少有效的版本校验或文件名。")
            with path.open("rb") as handle:
                data = handle.read(MAX_ATTACHMENT + 1)
            if (len(data) > MAX_ATTACHMENT or not data.startswith(b"%PDF-")
                    or hashlib.sha256(data).hexdigest() != expected):
                raise ValueError("简历附件内容、大小或校验值不符，已拦截发送。")
            message.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
        return recipient, mid, message.as_bytes()

    def _find_sent(self, client, recipient, mid):
        folder = self._sent_folder(client)
        if client.select(folder, readonly=True)[0] != "OK":
            raise ChannelUnavailable("configuration_required", "无法读取 IMAP 已发送目录，不能核验发送结果。")
        quoted_mid = '"' + mid.replace('\\', '\\\\').replace('"', '\\"') + '"'
        code, ids = client.uid("search", None, "HEADER", "Message-ID", quoted_mid)
        if code != "OK":
            raise ChannelUnavailable("platform_limit", "已发送邮件的 Message-ID 搜索失败。")
        uids = (ids[0] or b"").split() if ids else []
        for uid in uids[-50:]:
            if not uid.isdigit():
                continue
            code, data = client.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID TO FROM)])")
            if code != "OK":
                raise ChannelUnavailable("platform_limit", "已发送邮件的邮件头读取失败。")
            headers = BytesParser(policy=policy.default).parsebytes(_header_bytes(data), headersonly=True)
            if (_message_ids(headers.get("Message-ID")) == [mid] and _addresses(headers.get("To")) == [recipient]
                    and _addresses(headers.get("From")) == [self.account]):
                return _result("confirmed", f"IMAP 已发送目录核实 Message-ID、收件人和发件人，UID {uid.decode('ascii')}。", mid)
        return None

    def reconcile(self, item):
        recipient = str(item.get("recipient") or "").strip().lower()
        mid = str(item.get("message_id") or "").strip()
        if not ADDRESS.fullmatch(recipient) or not MESSAGE_ID.fullmatch(mid):
            return _result("unknown", "缺少可核验的收件地址或 Message-ID，保持结果不明。")
        secret = self._authorization()
        with self._imap(secret) as client:
            return self._find_sent(client, recipient, mid) or _result(
                "unknown", "已发送目录未找到完全相同的 Message-ID 和收件人，保持结果不明。", mid)

    def _find_sent_application(self, client, recipient, subject):
        if client.select(self._sent_folder(client), readonly=True)[0] != "OK":
            raise ChannelUnavailable("configuration_required", "无法读取 IMAP 已发送目录，不能核验是否已投递。")
        code, ids = client.uid("search", None, "TO", '"' + recipient + '"')
        if code != "OK":
            raise ChannelUnavailable("platform_limit", "已发送目录的收件人搜索失败。")
        uids = (ids[0] or b"").split() if ids else []
        if len(uids) > 250:
            raise ChannelUnavailable("dedup_required", "该收件人的历史邮件过多，须先人工核验是否重复。")
        expected = re.sub(r"\s+", "", _text(subject).casefold())
        for uid in reversed(uids):
            if not uid.isdigit():
                continue
            code, data = client.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID TO FROM SUBJECT)])")
            if code != "OK":
                raise ChannelUnavailable("platform_limit", "已发送目录的岗位主题读取失败。")
            headers = BytesParser(policy=policy.default).parsebytes(_header_bytes(data), headersonly=True)
            actual = re.sub(r"\s+", "", _text(headers.get("Subject")).casefold())
            if (actual == expected and _addresses(headers.get("To")) == [recipient]
                    and _addresses(headers.get("From")) == [self.account]):
                message_ids = _message_ids(headers.get("Message-ID"))
                return _result("confirmed", f"IMAP 已发送目录有相同收件人和岗位主题的邮件，UID {uid.decode('ascii')}。",
                               message_ids[0] if len(message_ids) == 1 else None)
        return None

    def find_sent_application(self, item):
        recipient = str(item.get("recipient") or "").strip().lower()
        subject = str(item.get("subject") or "").strip()
        if not ADDRESS.fullmatch(recipient) or not subject or any(ch in subject for ch in "\r\n\x00"):
            raise ValueError("缺少可核验的准确收件地址或岗位主题。")
        with self._imap(self._authorization()) as client:
            return self._find_sent_application(client, recipient, subject)

    def send(self, item, attachment=None):
        try:
            recipient, mid, payload = self._prepare(item, attachment)
        except ValueError as exc:
            return _result("failed", str(exc))
        except (OSError, TypeError):
            return _result("failed", "读取已核准的简历文件失败，未提交邮件。")
        secret = self._authorization()
        with self._imap(secret) as client:
            existing = self._find_sent(client, recipient, mid)
            if not existing and (item.get("kind") == "application" or item.get("send_once")):
                duplicate = self._find_sent_application(client, recipient, item["subject"])
                if duplicate:
                    return _result("failed", duplicate["evidence"] + "本次未提交。", duplicate["provider_id"])
        if existing:
            return existing
        try:
            client = self._smtp(secret)
        except ChannelUnavailable as exc:
            if exc.code == "smtp_rate_limited":
                return _smtp_failure(421, exc.detail, mid)
            raise
        submitting = False
        try:
            code, response = client.mail(self.account)
            if code != 250:
                return _smtp_failure(code, f"SMTP MAIL 返回 {code}，未提交正文。", mid, response)
            code, response = client.rcpt(recipient)
            if code not in {250, 251}:
                return _smtp_failure(code, f"SMTP RCPT 返回 {code}，未提交正文。", mid, response)
            guard = item.get("_before_submit")
            if guard is not None and guard() is False:
                return _result("failed", "停止检查未通过，未提交邮件正文。", mid)
            submitting = True
            code, response = client.data(payload)
            if code == 250:
                return _result("confirmed", "网易 SMTP 返回 250，服务端已接受邮件，收件和阅读状态未知。", mid)
            if 400 <= code < 600:
                return _smtp_failure(code, f"SMTP DATA 明确返回 {code}。", mid, response)
            return _result("unknown", f"SMTP DATA 返回非预期状态 {code}，必须先核对已发送记录。", mid)
        except smtplib.SMTPResponseException as exc:
            if not submitting or 400 <= exc.smtp_code < 600:
                return _smtp_failure(exc.smtp_code, f"SMTP 明确返回 {exc.smtp_code}。", mid, exc.smtp_error)
            return _result("unknown", "提交邮件后收到非预期状态，必须先核对已发送记录。", mid)
        except (smtplib.SMTPException, OSError):
            state = "unknown" if submitting else "failed"
            evidence = "提交正文后连接中断，必须先核对已发送记录。" if submitting else "提交正文前连接中断。"
            return {**_result(state, evidence, mid), "retryable": not submitting,
                    "retry_after": 120 if not submitting else None}
        finally:
            with contextlib.suppress(OSError, smtplib.SMTPException):
                client.close()


def build_channels(config):
    from .liepin_adapter import LiepinChannel
    settings = config.get("channels", {})
    result = {name: ManualPlatform(name) for name in PLATFORM_HANDOFF}
    result["official"] = OfficialChannel(settings.get("official", {}))
    result["email"] = MailChannel({**settings.get("email", {}), **config.get("email", {})})
    result["liepin"] = LiepinChannel(settings.get("liepin", {}))
    return result
