"""Read public Liepin descriptions without candidate credentials or private endpoints."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .channels import ChannelUnavailable, _PublicFetch
from .rules import title_key


DIRECT = re.compile(r"/job/([0-9]{5,24})\.shtml")
HUNTER = re.compile(r"/a/([0-9]{5,24})\.shtml")
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
REQUIREMENTS = re.compile(r"(?:^|\n)\s*(?:[一二三四五六1-6][、.)．]\s*)?[【\[]?"
                          r"(?:任职(?:要求|资格|条件)|岗位要求|职位要求|招聘要求|资格要求|能力要求|基本要求|你需要|我们希望)"
                          r"[】\]]?(?:[ \t]*[：:][ \t]*|[ \t]*(?=\n|$))")
QUALIFICATION_START = re.compile(r"^(?:1[.、．)]\s*)?(?:本科|大专|硕士|全日制|学士)(?:及以上|以上|学历)")


def detail_url(external_id, listing_url):
    public_path = urlsplit(listing_url or "").path
    direct = DIRECT.fullmatch(public_path)
    if direct and direct[1] == external_id:
        return "https://www.liepin.com" + public_path
    hunter = re.fullmatch(r"mcp:1:([0-9]{5,24})", external_id)
    if hunter:
        return f"https://www.liepin.com/a/{hunter[1]}.shtml"
    raise ValueError("岗位没有经核实的公开猎聘详情编号")


class DetailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = {key: [] for key in ("title", "place", "salary", "jd", "page_title", "control")}
        self.action_ids = set()
        self.apply_ids = set()

    def has_class(self, name):
        return any(name in classes for _, classes, _ in self.stack)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        selector = attrs.get("data-selector")
        field = None
        if tag == "title":
            field = "page_title"
        if self.has_class("job-apply-content"):
            if tag == "span" and "name" in classes and self.has_class("name-box"):
                field = "title"
            elif "job-properties" in classes:
                field = "place"
            elif "salary" in classes:
                field = "salary"
        if tag == "dd" and selector == "job-intro-content" and self.has_class("job-intro-container"):
            field = "jd"
        if "job-apply-operate" in classes:
            field = "control"
        if self.has_class("job-apply-operate") and self.has_class("apply-box"):
            identity = (attrs.get("data-jobkind"), attrs.get("data-jobid"))
            if selector == "chat-chat" and attrs.get("data-params"):
                try:
                    data = json.loads(attrs["data-params"])
                except ValueError:
                    data = {}
                if isinstance(data, dict):
                    identity = (str(data.get("jobKind", "")), str(data.get("jobId", "")))
            if selector in {"apply-job", "chat-chat"} and identity[0] in {"1", "2"}:
                self.action_ids.add(identity)
                if selector == "apply-job":
                    self.apply_ids.add(identity)
        if tag in {"br", "p", "li", "div"}:
            self.handle_data("\n")
        if tag not in VOID:
            self.stack.append((tag, classes, field))

    def handle_endtag(self, tag):
        if tag in {"p", "li", "div", "span"}:
            self.handle_data("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for tag, _, field in reversed(self.stack):
            if tag in {"script", "style", "noscript"}:
                return
            if field:
                self.text[field].append(data)
                return

    def value(self, field):
        return "\n".join(line.strip() for line in "".join(self.text[field]).splitlines() if line.strip())


def parse_detail(html, url, title):
    parser = DetailHTML()
    parser.feed(html)
    page_title = parser.value("page_title")
    if re.search(r"安全验证|访问异常|访问频繁|请先登录", page_title):
        raise ChannelUnavailable("platform_limit", "猎聘详情要求安全验证或登录，已停止公开读取")
    if not parser.value("title") or title_key(parser.value("title")) != title_key(title):
        raise ValueError("公开职位标题与搜索记录不符")
    path = urlsplit(url).path
    direct, hunter = DIRECT.fullmatch(path), HUNTER.fullmatch(path)
    if not (direct or hunter):
        raise ValueError("详情链接缺少公开猎聘编号")
    allowed = {("1", hunter[1])} if hunter else {("2", direct[1]), ("2", direct[1].removeprefix("19"))}
    if parser.action_ids and not parser.action_ids & allowed:
        raise ValueError("公开职位页面的申请编号与搜索记录不符")
    jd = parser.value("jd")
    if len(jd) < 40:
        raise ValueError("公开页面没有完整职位描述")
    requirements = REQUIREMENTS.search(jd)
    requirements_only = not requirements and QUALIFICATION_START.match(jd)
    fields = parser.value("place").splitlines()
    status = "partial"
    if parser.apply_ids & allowed:
        status = "open"
    elif re.search(r"已下线|已结束|已停止招聘|职位关闭", parser.value("control")):
        status = "closed"
    duties = jd[:requirements.start()].strip() if requirements else ("" if requirements_only else jd)
    qualifications = jd[requirements.end():].strip() if requirements else (jd if requirements_only else "")
    return {"description": duties[:24000], "requirements": qualifications[:24000],
            "location": fields[0][:180] if fields else "", "salary": parser.value("salary")[:120],
            "status": status, "url": url, "detail_complete": True}


class PublicDetails:
    def __init__(self, timeout=10):
        self.fetch = _PublicFetch({"www.liepin.com", "liepin.com"}, timeout)

    def read(self, external_id, listing_url, title):
        url = detail_url(external_id, listing_url)
        target, html = self.fetch.get(url)
        if urlsplit(target).path != urlsplit(url).path:
            raise ChannelUnavailable("platform_limit", "猎聘详情已跳转，暂停并保留原始职位记录")
        return parse_detail(html, url, title)
