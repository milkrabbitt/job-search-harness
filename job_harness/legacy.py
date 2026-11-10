"""Read the prior ledgers and immutable original resumes without editing them."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
URL_RE = re.compile(r"https?://[^\s<>`\]\)｜|，；。]+")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
INDEX_RE = re.compile(r"^[BL]\d+$|^邮\s*\d+|^\d{1,2}$", re.I)
CHANNELS = {
    "boss": "boss", "boss直聘": "boss", "猎聘": "liepin", "鱼泡": "yupao",
    "官网": "official", "邮件": "email", "邮箱": "email", "126": "email", "官方招聘邮箱": "email",
}
IDENTITY_QUERY_KEYS = {
    "id", "job", "jobadid", "jobid", "positionid", "postid", "requisitionid", "title", "type", "zpxxbh",
}
CITIES = re.compile(r"杭州|上海|北京|深圳|广州|苏州|宁波|南京|成都|合肥|香港|远程")
REDACT_PATH = re.compile(r"/(?:apply|application|candidate)/(?:thanks|success|status|[a-f0-9]{16,})", re.I)
SHARED_DOMAINS = {
    "zhipin.com", "liepin.com", "yupao.com", "zhaopin.com", "jobs.feishu.cn",
    "mokahr.com", "v2ex.com", "zju.edu.cn", "hust.edu.cn", "nowcoder.com", "bonjour.bio",
    "zhizuobiao.com", "haolietou.com",
}


def _json(path: Path) -> dict:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("继承文件须是 JSON 对象。")
    return parsed


def _url(value: str) -> str:
    link = urlsplit(value.rstrip(".,;:"))
    if link.scheme not in ("http", "https") or not link.hostname or REDACT_PATH.search(link.path):
        return ""
    query = urlencode([(k, v) for k, v in parse_qsl(link.query) if k.lower() in IDENTITY_QUERY_KEYS])
    fragment = link.fragment if re.match(r"^(?:/job/|careers$|recruitment$)", link.fragment) else ""
    return urlunsplit((link.scheme, link.netloc, link.path, query, fragment))


def _url_details(url: str) -> tuple[str, str]:
    link = urlsplit(url)
    domain = link.hostname or ""
    job_id = re.search(
        r"/(?:job_detail|job|zhaogong|jobs|position)/([\w~-]+)(?:\.\w+|/|$)",
        link.path, flags=re.ASCII,
    )
    query = {k.lower(): v for k, v in parse_qsl(link.query)}
    fragment_job = re.match(r"/job/([a-f0-9-]{12,})", link.fragment, flags=re.I)
    external_id = next((query[k] for k in ("postid", "positionid", "jobid", "jobadid", "zpxxbh", "id", "job", "title") if query.get(k)), "")
    if fragment_job:
        external_id = fragment_job[1]
    elif job_id and job_id[1] not in {"list", "index", "all", "job-product", "detail"}:
        external_id = external_id or job_id[1]
    return domain, external_id


def _company_key(name: str) -> str:
    return "".join(re.findall(r"\w+", unicodedata.normalize("NFKC", name).casefold()))


def _company_domain(host: str) -> str:
    host = host.lower().removeprefix("www.")
    if any(host == shared or host.endswith("." + shared) for shared in SHARED_DOMAINS):
        return ""
    return host


def _infer_channel(url: str) -> str:
    domain, _ = _url_details(url)
    for suffix, channel in (("zhipin.com", "boss"), ("liepin.com", "liepin"), ("yupao.com", "yupao")):
        if domain == suffix or domain.endswith("." + suffix):
            return channel
    return ""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("**", "").replace("`", "")).strip(" ·，,；;。")


def _company(value: str) -> str:
    value = _clean(value)
    value = re.split(r"·(?=[^·]*?(?:先生|女士|招聘|顾问|经理))", value, maxsplit=1)[0]
    value = re.sub(r"\s+[^\s]{1,10}(?:先生|女士|经理)$", "", value)
    return value


def _classify(value: str) -> str:
    if re.search(r"已退信|邮件退信|退信已|确认退信|发送失败|发送不成功|未成功投递|bounce", value, re.I):
        return "failed"
    if re.search(r"已下线|岗位关闭|职位关闭|职位下架|停止招聘|不再招", value):
        return "closed"
    if re.search(r"已被拒|招聘方婉拒|暂不匹配已拒|收到拒信", value):
        return "rejected"
    if re.search(r"岗位询问.{0,4}(?:已发|送达)|仅发询问邮件|未附简历.{0,4}询问", value):
        return "inquiry"
    if re.search(r"发送成功|发送已完成|申请成功|已提交|投递成功|确认成功|已收回执|官网申请已收到|已投递", value) and not re.search(
        r"未提交|未投递|没有提交|未计成功|不能确认.*成功", value
    ):
        return "confirmed"
    if re.search(r"已发送|已发|已联系|已沟通|已核.{0,8}未读|核实未读|显示.{0,4}送达|(?:已核实|已确认)?送达|附件卡未读|继续聊|继续沟通", value) and not re.search(
        r"从未发送|未重复投|未新投", value
    ):
        return "contacted"
    if re.search(r"结果不明|状态不明|状态待核|无法核实|发送中", value):
        return "unknown"
    if re.search(r"明确排除|未投|不匹配|岗位不符|不再联系", value):
        return "excluded"
    if re.search(r"待验证|待本人|待处理|待登录|尚未发送|未提交|待发送|待确认|草稿", value):
        return "pending"
    return "review"


def _make_history(
    *, company: str, title: str, channel: str, location: str, url: str, source: str,
    status: str, evidence: str, day: Optional[str], recipient: str = "", kind: str = "position",
    **extra: object,
) -> dict:
    url = _url(url) if url else ""
    domain, external_id = _url_details(url)
    if not external_id:
        # An employer index or a generic chat URL may describe many distinct roles.
        key = "\0".join((company, title, location, kind)).encode()
        external_id = "legacy:" + hashlib.sha256(key).hexdigest()
    return {
        "channel": channel, "company": _company(company), "title": _clean(title),
        "location": _clean(location), "external_id": external_id, "url": url,
        "domain": _company_domain(domain), "status": status, "source": source, "evidence": _clean(URL_RE.sub(lambda match: _url(match[0]), evidence)),
        "occurred_at": day, "recipient": recipient, "kind": kind, **extra,
    }


def _split_target(value: str, *, compact: bool = False) -> tuple[str, str, str]:
    value = _clean(value)
    if " · " in value:
        company, title = value.split(" · ", 1)
        return company, title, ""
    fields = re.split(r"[，,；;]", value, maxsplit=2)
    if len(fields) > 1:
        return fields[0], fields[1], fields[2] if len(fields) > 2 else ""
    if compact:
        # Some Liepin entries use a space between the company and the role.
        fields = value.split(" ", 1)
        if len(fields) == 2:
            return fields[0], fields[1], ""
    return "", "", ""


def _location(value: str, *, hangzhou: bool = False) -> str:
    city = CITIES.search(value)
    if city:
        return city[0]
    if hangzhou or re.search(r"余杭|滨江|拱墅|临平|上城|萧山|钱塘|西湖|临安|富阳", value):
        return "杭州"
    return ""


def _legacy_table(path: Path, source: str) -> list[dict]:
    history = []
    headings = []
    section = ""
    day = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("#"):
            section = line
            found_date = DATE_RE.search(line)
            if found_date:
                day = found_date[1]
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0].startswith("---"):
            continue
        if re.match(r"^(平台|公司|本轮|猎聘本轮|序号|编号|岗位|渠道)", cells[0]) and not URL_RE.search(line):
            headings = cells
            continue
        urls = URL_RE.findall(line)
        if not urls:
            continue
        job_links = [url for url in urls if _infer_channel(url) and _url_details(url)[1]]
        url = (job_links or urls)[0]
        channel = CHANNELS.get(cells[0].lower()) or _infer_channel(url)
        target_col = 0
        company = title = location = ""
        recipient_match = EMAIL_RE.search(line)
        recipient = recipient_match[0] if recipient_match else ""
        if channel and cells[0].lower() in CHANNELS:
            target_col = 1
            if len(cells) >= 7 and len(headings) >= 3 and headings[2] in {"职位", "岗位"}:
                company, title, location = cells[1:4]
                company = company.split("·", 1)[0]
        elif INDEX_RE.match(cells[0]):
            target_col = 1
            if cells[1].lower() in CHANNELS:
                channel = CHANNELS[cells[1].lower()]
                target_col = 2
            if cells[0].startswith("邮") or (recipient and not _infer_channel(url)):
                channel = "email"
        if not company:
            label_match = re.search(r"\[([^\]]+)\]\(", cells[target_col])
            raw = label_match[1] if label_match else cells[target_col].split("https://", 1)[0]
            company, title, location = _split_target(
                raw, compact=bool(label_match and re.match(r"^L\d+$", cells[0]))
            )
            if not company and channel and re.match(r"^[BL]\d+$", cells[0]):
                fields = re.split(r"[，,；;]", _clean(raw))
                if len(fields) > 1:
                    company, title = fields[:2]
                    if len(fields) > 2:
                        location = fields[2]
            if not _location(location):
                adjacent = cells[target_col + 1] if len(cells) > target_col + 1 else ""
                hangzhou = "杭州" in section or any("杭州地点" in heading for heading in headings)
                location = _location(adjacent, hangzhou=hangzhou) or _location(cells[target_col])
        if cells[0].lower() == "猎聘" and len(headings) > 2 and headings[1] == "招聘方":
            # Historic anonymous outreach should not mint a fictitious employer.
            continue
        if not company or not title or not channel:
            continue
        # Read the state/evidence columns after the link, keeping next-step negatives
        # separate from what already happened.
        url_index = next((i for i, cell in enumerate(cells) if url in cell), target_col)
        statuses = [cell for i, cell in enumerate(cells) if i > url_index]
        if not statuses:
            continue
        states = [(item, _classify(item)) for item in statuses]
        state = next((state for _, state in states if state in {"confirmed", "contacted", "inquiry"}), "")
        if not state:
            state = next((state for _, state in states if state != "review"), "review")
        evidence = "｜".join(item for item, candidate in states if candidate == state) or "｜".join(statuses)
        if channel == "email" and state == "contacted" and re.search(
            r"邮件成功|页面明确.{0,10}成功|附件.{0,10}已发", evidence
        ):
            state = "confirmed"
        resume_card = any(re.search(r"附件.{0,8}(?:卡片|卡未读|发送成功|已发送)", cell) for cell in statuses)
        history.append(_make_history(
            company=company, title=title, location=location, channel=channel, url=url,
            source=f"{source}:{number}", status=state, evidence=evidence,
            day=day, recipient=recipient, attachment_status="confirmed" if resume_card else "",
        ))
    return history


def _resumes(base: Path, seed: dict) -> list[dict]:
    resumes = []
    for definition in seed.get("resumes", []):
        configured_path = definition.get("path")
        if not configured_path:
            continue
        path = base / configured_path
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        modified = datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI).isoformat(timespec="seconds")
        expected = definition.get("sha256", "")
        approved = bool(definition.get("approved")) and digest == expected
        resumes.append({
            "label": definition.get("label") or path.name, "path": str(path.resolve()), "sha256": digest,
            "modified_at": definition.get("modified_at") or modified,
            "scope": definition.get("scope", definition.get("scopes", [])),
            "approved": approved, "source": definition.get("source", "继承清单"),
            "integrity": "verified" if digest == expected else "unverified",
        })
    return resumes


def _history_additions(seed: dict, ledger: Optional[Path]) -> list[dict]:
    lines = ledger.read_text(encoding="utf-8").splitlines() if ledger is not None else []
    records = []
    for row in seed.get("history", []):
        if "line" not in row:
            records.append(row)
            continue
        line = row["line"]
        if line < 1 or line > len(lines) or row.get("anchor", row["company"]) not in lines[line - 1]:
            continue
        extras = {"attachment_status": row["attachment_status"]} if "attachment_status" in row else {}
        records.append(_make_history(
            company=row["company"], title=row["title"], channel=row["channel"], location=row.get("location", ""),
            url=row.get("url", ""), source=f"{ledger.name}:{line}", status=row["status"], evidence=lines[line - 1],
            day=row.get("day"), recipient=row.get("recipient", ""), kind=row.get("kind", "position"), **extras,
        ))
    return records


def load_legacy(manifest: Path) -> dict:
    """Load only the given JSON manifest and files explicitly declared in it."""
    manifest = manifest.expanduser().resolve()
    seed = _json(manifest)
    ledger = manifest.parent / seed["ledger_path"] if seed.get("ledger_path") else None
    history = _legacy_table(ledger, ledger.name) if ledger is not None else []
    history.extend(_history_additions(seed, ledger))
    domains = {_company_key(company): _company_domain(domain) for company, domain in seed.get("domains", {}).items()}
    sources = []
    for source in seed.get("sources", []):
        domain = _company_domain(source.get("domain") or _url_details(source["url"])[0])
        if domain:
            domains.setdefault(_company_key(source["company"]), domain)
        sources.append({**source, "domain": domain})
    for item in history:
        # A shared platform hostname proves where the record appeared, not the
        # identity of the company posting. Prefer its separately verified domain.
        if item.get("company"):
            item["domain"] = domains.get(_company_key(item["company"]), item.get("domain", ""))
    messages = []
    for item in seed.get("messages", []):
        channel = item["channel"]
        origin = item.get("url", "")
        external_job_id = item.get("job_external_id") or _url_details(origin)[1]
        contact = item.get("contact")
        title = item.get("subject") or item.get("title") or ""
        subject = f"{title}（{contact}）" if contact else title
        original = json.dumps({**item, "_history": True}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        external_id = item.get("external_id") or hashlib.sha256(original.encode()).hexdigest()
        messages.append({**item, "subject": subject, "job_external_id": external_job_id, "external_id": external_id,
                         "thread_external_id": item.get("thread_external_id") or f"legacy:{channel}:{origin or external_id}"})
    return {
        "email": seed.get("email", {}),
        "facts": seed.get("facts", []),
        "preferences": seed.get("preferences", {}),
        "resumes": _resumes(manifest.parent, seed),
        "history": history,
        "sources": sources,
        "messages": messages,
    }
