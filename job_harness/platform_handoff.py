"""Normalize evidence copied from official candidate pages for local review."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

from .storage import TZ, now


_JOB_PAGES = {
    "boss": ("zhipin.com", re.compile(r"/job_detail/([A-Za-z0-9_~-]{6,128})\.html")),
    "liepin": ("liepin.com", re.compile(r"/job/([0-9]{5,24})\.shtml")),
    "yupao": ("yupao.com", re.compile(r"/zhaogong/([0-9]{5,24})\.html")),
    "zhaopin": ("zhaopin.com", re.compile(r"/jobdetail/([A-Za-z0-9_-]{6,128})\.htm")),
}
_TRACKING = {"ka", "from", "source", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}
_LABEL = re.compile(r"[\w.-]{1,64}")


def platform_job_link(channel, url):
    """Return a public job URL and its stable ID, with no private session state."""
    if channel not in _JOB_PAGES:
        raise ValueError("请选择 BOSS 直聘、猎聘、鱼泡或智联招聘")
    domain, pattern = _JOB_PAGES[channel]
    value = str(url or "").strip()
    if len(value) > 2048 or not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("请输入官方岗位详情页地址")
    try:
        parsed = urlsplit(value)
        fields = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=12)
    except ValueError:
        raise ValueError("岗位链接格式无效") from None
    match = pattern.fullmatch(parsed.path)
    hunter = re.fullmatch(r"/a/([0-9]{5,24})\.shtml", parsed.path) if channel == "liepin" else None
    if (parsed.scheme != "https" or parsed.netloc.lower() not in {domain, "www." + domain, "m." + domain}
            or parsed.fragment or not (match or hunter)):
        raise ValueError("需要所选平台的公开岗位详情页，且地址只能包含公开岗位编号")
    if any(name.lower() not in _TRACKING for name, _ in fields):
        raise ValueError("请去掉链接中的参数，从 HTTPS 地址复制到岗位编号或网页扩展名")
    return f"https://www.{domain}{parsed.path}", ("mcp:1:" + hunter[1] if hunter else match[1])


def normalize_platform_message(data):
    """Return allowlisted fields for a manual platform chat observation."""
    channel = data.get("channel")
    url, platform_id = platform_job_link(channel, data.get("url"))
    identifier = str(data.get("job_id") or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,17}", identifier):
        raise ValueError("请选择控制台中已保存的岗位")
    direction = data.get("direction")
    if direction not in {"inbound", "outbound"}:
        raise ValueError("请选择招聘方来信或本人已发")
    raw_time = str(data.get("occurred_at") or "").strip()
    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}", raw_time):
        raise ValueError("请填写平台显示的实际日期和时间")
    try:
        occurred_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        occurred_at = occurred_at.replace(tzinfo=TZ) if occurred_at.tzinfo is None else occurred_at.astimezone(TZ)
    except (ValueError, OverflowError):
        raise ValueError("消息时间无效") from None
    if occurred_at > now() + timedelta(minutes=5):
        raise ValueError("消息时间在未来，请按平台显示的北京时间填写")
    timestamp = occurred_at.isoformat(timespec="seconds")
    body = str(data.get("body") or "").strip().replace("\r\n", "\n")
    subject = str(data.get("subject") or "").strip() or "招聘沟通"
    if not body or len(body) > 6000 or "\0" in body:
        raise ValueError("请填写一条实际聊天记录，最多 6000 字")
    if len(subject) > 160 or any(ord(char) < 32 for char in subject):
        raise ValueError("聊天标题格式无效")
    thread_ref = str(data.get("thread_ref") or "").strip() or "default"
    message_ref = str(data.get("message_ref") or "").strip()
    if not _LABEL.fullmatch(thread_ref) or (message_ref and not _LABEL.fullmatch(message_ref)):
        raise ValueError("会话和消息标记仅接受简短文字、数字、点、下划线或连字符")
    thread_key = _fingerprint(channel, platform_id, thread_ref)
    message_key = _fingerprint(thread_key, message_ref) if message_ref else _fingerprint(thread_key, direction, timestamp, body)
    return {
        "channel": channel, "job_id": int(identifier), "platform_job_id": platform_id,
        "source_url": url, "source": "manual_platform_snapshot", "direction": direction,
        "thread_external_id": "manual:" + thread_key, "external_id": "manual:" + message_key,
        "occurred_at": timestamp, "body": body, "subject": subject,
    }


def _fingerprint(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
