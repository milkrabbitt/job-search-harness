"""Local, Chinese-language operator console for the job harness."""

import base64
import hashlib
import ipaddress
import json
import secrets
from datetime import datetime
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
NAV = {
    "/": "运行概览",
    "/jobs": "岗位",
    "/threads": "招聘消息",
    "/outbox": "草稿与发送",
    "/decisions": "待我决定",
    "/settings": "资料与设置",
    "/events": "执行日志",
}
STATES = {
    "active": "在招", "open": "待处理", "ready": "可用", "available": "可用", "running": "运行中",
    "ok": "正常", "connected": "已接通", "healthy": "正常", "verified": "已核实",
    "manual": "需人工", "manual_only": "需人工", "manual_handoff": "浏览器人工接管", "disabled": "未启用",
    "blocked": "受阻", "unavailable": "不可用", "paused": "已暂停",
    "stopped": "已停止", "offline": "离线", "unknown": "未知",
    "auth_required": "待登录", "needs_login": "待登录", "needs_setup": "待配置",
    "needs_auth": "待授权", "login_expired": "登录失效", "captcha": "需本人验证",
    "configuration_required": "待配置", "unverified": "待核验", "partial": "信息不全",
    "authentication_failed": "认证失败", "protocol_unavailable": "协议不可用", "rate_limited": "访问受限",
    "closed": "已关闭", "rejected": "已拒绝", "excluded": "已排除",
    "draft": "草稿", "pending": "待处理", "queued": "待执行",
    "pending_send": "待发送", "approved": "已批准", "sending": "发送中",
    "confirmed": "已确认发送", "sent": "已发送", "uncertain": "结果不明",
    "ambiguous": "结果不明", "failed": "失败", "retry": "待重试",
    "cancelled": "已取消", "canceled": "已取消", "done": "已完成",
    "success": "已完成", "succeeded": "已完成", "completed": "已完成",
    "stale": "草稿失效", "invalid": "无效", "resolved": "已决定",
    "waiting": "等待", "waiting_approval": "待批准", "matched": "匹配", "match": "匹配",
    "review": "待核实", "conflict": "待核对冲突", "duplicate": "重复",
    "contacted": "已联系", "inquiry": "已询问", "expired": "已过期", "recruiting": "在招",
    "manual_followup": "已建立联系，人工跟进",
    "hold": "保留待核", "excluded": "不投递", "cleared": "复核通过",
    "web_only": "需浏览器接管", "manual_review": "待人工核对",
    "attachment_requested": "附件请求待接受",
}
KIND = {
    "reply_check": "查收回复", "poll": "查收回复", "check": "查收回复",
    "probe": "核验渠道", "model_probe": "模型调用", "model_match": "模型复核岗位",
    "replies": "读取并分流邮件", "mail_audit": "历史邮件证据复核", "platform_search": "猎聘官方搜索",
    "liepin_detail": "猎聘详情核对",
    "discover": "搜索岗位", "scan": "搜索岗位", "match": "筛选岗位",
    "send": "发送", "reconcile": "核对外发结果", "verify_sent": "核验已发送目录", "follow_up": "主动跟进",
    "backup": "备份", "interview": "面试", "assessment": "笔试或作业",
    "salary": "薪资", "start_date": "到岗日期", "relocation": "地点或搬迁",
    "travel": "出差", "offer": "Offer", "contract": "合同",
    "sensitive": "敏感资料", "unknown": "待核实", "incoming": "招聘方",
    "outgoing": "我方", "inbound": "招聘方", "outbound": "我方",
    "application": "申请岗位", "reply": "招聘回复", "followup": "主动跟进",
    "contact": "首次招呼", "attachment": "发送简历附件",
    "compensation": "薪资待遇", "availability": "到岗、地点或出差",
    "assignment": "笔试或作业", "resume": "简历请求", "portfolio": "作品请求",
    "auto": "自动回复", "review": "待核实", "experience": "经历问答",
    "stop": "要求停止联系", "bounce": "邮件退信", "reject": "拒绝",
    "manual_channel_pending": "原渠道由本人处理", "delivery_failed": "投递失败", "delivery_delay": "投递延迟",
    "historical_sent": "已发并关联台账", "sent_unlinked": "已发待关联", "historical_review": "历史邮件待核对",
    "automatic": "自动通知", "promotion": "活动推广", "unrelated": "非招聘邮件", "acknowledged": "已收悉",
    "handled": "已有处理证据", "superseded": "已有更新消息", "reply_queued": "回复已排队",
    "manual_followup": "已建立联系，人工跟进",
    "reply_draft": "回复草稿", "decision_pending": "已转待我决定",
}
CHANNELS = {
    "boss": "BOSS 直聘", "liepin": "猎聘", "yupao": "鱼泡", "zhaopin": "智联招聘",
    "website": "公司官网", "official": "公司官网", "netease": "网易邮箱",
    "mail126": "网易邮箱", "email": "网易邮箱", "mail": "网易邮箱",
}
HANDOFF_LINKS = {
    "boss": "https://www.zhipin.com/web/geek/chat",
    "liepin": "https://www.liepin.com/",
    "yupao": "https://www.yupao.com/",
    "email": "https://mail.126.com/",
}
PREFERENCE_FIELDS = (
    ("target_scope", "当前求职范围", "text"),
    ("cities", "目标城市", "text"),
    ("directions", "岗位方向", "text"),
    ("work_arrangement", "坐班与远程偏好", "text"),
    ("work_types", "工作类型", "text"),
    ("salary_floor", "已确认薪资底线", "text"),
    ("reply_minutes", "查收回复间隔（分钟）", "number"),
    ("scan_times", "自动搜索时间（逗号分隔）", "text"),
    ("send_start", "工作日外发开始", "time"),
    ("send_end", "工作日外发结束", "time"),
    ("daily_limit", "每日新增投递上限", "number"),
    ("follow_up_business_days", "主动跟进间隔（工作日）", "number"),
)
BOOL_FIELDS = (
    ("auto_send", "在已授权范围内自动外发"),
    ("email_only_new", "新岗位仅通过已核招聘邮箱外联"),
    ("allow_follow_up", "允许符合间隔的单次主动跟进"),
    ("allow_remote", "纳入时间已核实的远程岗位"),
    ("require_robotics", "仅筛选具身智能和机器人岗位"),
    ("allow_unknown_recruiting", "招聘状态未知时，允许申请匹配且已有核实入口的岗位"),
    ("send_anytime", "随时发送，保留平台实际限流和失败退避"),
)
PREFERENCE_DEFAULTS = {
    "reply_minutes": 30, "scan_times": "10:00,15:30", "send_start": "09:30",
    "send_end": "18:30", "daily_limit": 10, "follow_up_business_days": 5,
    "target_scope": "", "cities": "",
    "directions": "",
    "work_arrangement": "", "work_types": "", "salary_floor": "",
    "auto_send": False, "email_only_new": True, "allow_follow_up": False, "allow_remote": False, "require_robotics": False,
    "allow_unknown_recruiting": False, "send_anytime": False,
}

ACTIONS_SCRIPT = """
document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!form.matches('form[method=post][action="/action"]')) return;
    event.preventDefault();
    const button = event.submitter || form.querySelector('button');
    if (button.disabled) return;
    button.disabled = true;
    const old = form.querySelector('[role=alert]');
    if (old) old.remove();
    try {
        const response = await fetch(form.getAttribute('action'), {
            method: 'POST', mode: 'same-origin', redirect: 'error',
            headers: {Accept: 'application/json'}, body: new URLSearchParams(new FormData(form))
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.notice);
        window.location.assign(result.location);
    } catch (error) {
        const notice = document.createElement('p');
        notice.className = 'notice error';
        notice.setAttribute('role', 'alert');
        notice.textContent = error instanceof Error && error.message && !(error instanceof TypeError || error instanceof SyntaxError)
            ? error.message : '未收到有效的后台响应。请先刷新核对当前状态，再决定是否重试。';
        form.append(notice);
        button.disabled = false;
    }
});
"""
SCRIPT_HASH = base64.b64encode(hashlib.sha256(ACTIONS_SCRIPT.encode()).digest()).decode('ascii')

STYLE = """
:root{color-scheme:light;--ink:#13262d;--muted:#52656a;--page:#f1f4f1;
--panel:#fff;--line:#d6e1df;--accent:#145e54;--soft:#e7f2eb;--warm:#fff3dd;
--danger:#9a352e;--danger-soft:#f9e8e5;font-family:-apple-system,BlinkMacSystemFont,
"PingFang SC","Microsoft YaHei",sans-serif;background:var(--page);color:var(--ink)}
*,*:before,*:after{box-sizing:border-box}
body{margin:0;font-size:14px;line-height:1.56}
a{color:var(--accent);text-underline-offset:3px}
header{background:#122e32;color:#f6faf7;padding:26px 5vw 0}
.heading{max-width:1320px;margin:auto;display:flex;align-items:end;justify-content:space-between;gap:14px}
.eyebrow{font-size:12px;letter-spacing:.10em;color:#b3cfc8;margin:0 0 3px}
.brand{font-size:clamp(24px,4vw,38px);letter-spacing:-.03em;font-weight:700;margin:0}
.local{color:#d3e6e0;margin:0 0 7px;white-space:nowrap}
nav{display:flex;gap:4px;max-width:1320px;margin:17px auto 0;flex-wrap:wrap}
nav a{display:block;color:#dcebe7;text-decoration:none;padding:10px 13px;border-radius:8px 8px 0 0}
nav a[aria-current="page"]{background:var(--page);color:var(--ink);font-weight:650}
main{max-width:1420px;padding:25px 5vw 45px;margin:auto}
h1{font-size:24px;margin:0 0 7px;letter-spacing:-.02em}
h2{font-size:18px;margin:0 0 10px}
h3{font-size:16px;margin:0 0 8px;overflow-wrap:anywhere}
p{margin:0 0 10px}
small,.muted{color:var(--muted)}
.row,.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.spread{justify-content:space-between}
.section{margin-top:24px}
.grid,.stats{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.stats{grid-template-columns:repeat(auto-fit,minmax(145px,1fr));margin:14px 0}
.panel,.stat{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:15px;min-width:0}
.stat b{font-size:25px;display:block;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.table{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:680px}
th,td{text-align:left;padding:10px 12px;vertical-align:top;border-bottom:1px solid var(--line)}
th{color:#325154;font-size:12px;white-space:nowrap;background:#eef5f0}
tr:last-child td{border-bottom:0}
.stack>*+*{margin-top:12px}
.chips{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.badge{display:inline-block;background:#edf0f2;color:#344c54;border-radius:99px;padding:3px 8px;font-size:12px;white-space:nowrap}
.badge.good{background:#dff0e5;color:#1b5b41}
.badge.warn{background:var(--warm);color:#6c4811}
.badge.bad{background:var(--danger-soft);color:#842e28}
.notice{padding:10px 13px;border-radius:9px;background:var(--soft);border:1px solid #cbded2;margin:0 0 16px;overflow-wrap:anywhere}
.notice.error{background:var(--danger-soft);border-color:#e2b9b5}
details{overflow-wrap:anywhere}
summary{cursor:pointer;font-weight:600;color:var(--accent);margin:6px 0}
summary:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,
button:focus-visible,a:focus-visible{outline:2px solid #278676;outline-offset:2px}
form{margin:0}
fieldset{border:1px solid var(--line);border-radius:9px;margin:12px 0;padding:12px;min-width:0}
legend{font-weight:600}
label{display:block;font-size:13px;font-weight:600}
input,select,textarea{font:inherit;color:inherit;width:100%;padding:7px 9px;border:1px solid #aabeb8;border-radius:6px;background:#fff}
input[type=checkbox]{width:auto;accent-color:var(--accent)}
.checks label{font-weight:450}
textarea{min-height:100px;resize:vertical}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px 13px}
.field{margin:5px 0}
button,.button{display:inline-block;font:inherit;font-weight:650;cursor:pointer;border:1px solid #a2b9b2;color:var(--accent);background:#f6fbf7;border-radius:7px;padding:6px 10px;text-decoration:none}
button.primary{background:var(--accent);border-color:var(--accent);color:white}
button.danger{border-color:#cba7a1;color:var(--danger);background:#fff5f3}
button[disabled]{cursor:default;opacity:.5}
.toolbar{margin:0 0 14px}
.toolbar input{max-width:380px}
.text-block{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8f6;border-radius:7px;padding:10px 12px;margin:8px 0}
.message{border-left:3px solid #b1c4bd;margin:10px 0;padding-left:11px}
.message.out{border-color:#4d9581}
.mono{font-family:ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere;font-size:12px}
.two-col{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr))}
.empty{color:var(--muted);padding:14px;border:1px dashed #bdcdc8;border-radius:8px;background:#f7faf7}
dl{margin:6px 0 9px}
.stats+.panel>dl,article[id^="job-"] dl,article[id^="send-"]>dl{
  display:grid;gap:8px 20px;grid-template-columns:repeat(auto-fit,minmax(min(100%,215px),1fr));margin:12px 0 15px}
article[id^="send-"]>dl{grid-template-columns:repeat(auto-fit,minmax(min(100%,285px),1fr))}
dt{font-weight:650;font-size:12px;color:var(--muted);margin-top:4px}
dd{margin:0;overflow-wrap:anywhere}
.id{font-variant-numeric:tabular-nums;color:var(--muted)}
@media(max-width:650px){header,main{padding-left:13px;padding-right:13px}.heading{align-items:start;flex-direction:column}
nav a{padding:8px}.panel{padding:12px}main{padding-top:16px}.local{font-size:12px}}
"""


def _text(value, missing="—"):
    if value is None or value == "":
        return escape(missing)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        value = "、".join(str(item) for item in value)
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    return escape(str(value))


def _datetime(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, SHANGHAI)
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return date.astimezone(SHANGHAI) if date.tzinfo else date.replace(tzinfo=SHANGHAI)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def _when(value):
    if isinstance(value, str) and len(value) == 10:
        return _text(value)
    date = _datetime(value)
    return _text(date.strftime("%Y-%m-%d %H:%M")) if date else _text(value)


def _today(value):
    date = _datetime(value)
    return bool(date and date.date() == datetime.now(SHANGHAI).date())


def _channel(value):
    return _text(CHANNELS.get(str(value).lower(), value))


def _kind(value):
    return _text(KIND.get(str(value).lower(), value))


def _badge(value):
    state = str(value or "unknown").lower()
    good = {
        "active", "ok", "connected", "healthy", "verified", "ready", "running",
        "confirmed", "sent", "done", "success", "succeeded", "completed", "resolved",
        "matched",
    }
    bad = {"failed", "rejected", "excluded", "closed", "blocked", "invalid", "cancelled", "canceled"}
    warn = {
        "manual", "manual_only", "paused", "stopped", "offline", "uncertain",
        "ambiguous", "stale", "review", "conflict", "captcha", "auth_required",
        "needs_login", "login_expired", "needs_auth", "needs_setup", "waiting_approval", "attachment_requested",
    }
    tone = "good" if state in good else "bad" if state in bad else "warn" if state in warn else ""
    return f'<span class="badge {tone}">{_text(STATES.get(state, value))}</span>'


def _block(value, missing="无记录"):
    return f'<div class="text-block">{_text(value, missing)}</div>'


def _url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def _source(value, title="打开来源"):
    url = _url(value)
    if not url:
        return _text(value)
    return f'<a href="{_text(url)}" target="_blank" rel="noopener noreferrer">{escape(title)}</a>'


def _details(label, body):
    return f'<details><summary>{escape(label)}</summary>{body}</details>'


def _empty(label="暂无记录"):
    return f'<p class="empty">{escape(label)}</p>'


def _dl(items):
    return "<dl>" + "".join(f"<div><dt>{escape(label)}</dt><dd>{value}</dd></div>" for label, value in items) + "</dl>"


def _table(headers, rows, empty="暂无记录"):
    if not rows:
        return _empty(empty)
    head = "".join(f"<th scope=\"col\">{escape(title)}</th>" for title in headers)
    body = "".join("<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>" for row in rows)
    return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _field(name, label, value="", field_type="text", required=False):
    title = escape(label)
    key = escape(name, quote=True)
    attr = " required" if required else ""
    shown = _text(value, "")
    if field_type == "textarea":
        widget = f'<textarea name="{key}" maxlength="24000"{attr}>{shown}</textarea>'
    else:
        limit = ' maxlength="4000"' if field_type in {"text", "url", "email"} else ""
        widget = f'<input name="{key}" type="{field_type}" value="{shown}"{limit}{attr}>'
    return f'<label class="field">{title}{widget}</label>'


def _hidden(name, value):
    return f'<input type="hidden" name="{escape(name)}" value="{_text(value, "")}">'


def _check(name, label, current):
    selected = " checked" if str(current).lower() in {"true", "1", "yes", "on"} else ""
    return f'<label class="field"><input type="checkbox" name="{escape(name)}" value="1"{selected}> {escape(label)}</label>'


class Console:
    def __init__(self, snapshot, token, page):
        self.data = snapshot
        self.token = token
        self.page = page

    def form(self, name, title, body="", tone="", **values):
        hidden = _hidden("token", self.token) + _hidden("action", name) + _hidden("next", self.page)
        hidden += "".join(_hidden(key, val) for key, val in values.items())
        return f'<form method="post" action="/action">{hidden}{body}<button class="{escape(tone)}">{escape(title)}</button></form>'

    def render(self, query):
        notice = query.get("notice", [""])[0]
        message = ""
        if notice:
            tone = " error" if query.get("error") == ["1"] else ""
            message = f'<div class="notice{tone}" role="status">{_text(notice)}</div>'
        current = ' aria-current="page"'
        nav = "".join(
            f'<a href="{path}"{current if path == self.page else ""}>{label}</a>' for path, label in NAV.items()
        )
        handlers = {
            "/": self.overview, "/jobs": lambda: self.jobs(query), "/threads": self.threads,
            "/outbox": self.outbox, "/decisions": self.decisions, "/settings": self.settings,
            "/events": self.events,
        }
        title = NAV[self.page]
        if self.data.get("demo"):
            message = ('<div class="notice" role="status"><strong>离线演示</strong> · 岗位、对话和附件全部虚构；'
                       '显示的回执只来自本机模拟，不会发邮件。</div>' + message)
        return (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title} · 个人求职 Harness</title><style>{STYLE}</style><body>'
            '<header><div class="heading"><div><p class="eyebrow">本地控制台</p>'
            '<p class="brand">个人求职 Harness</p></div>'
            f'<p class="local">上海时间 {_when(datetime.now(SHANGHAI).timestamp())}</p>'
            f'</div><nav aria-label="主导航">{nav}</nav></header>'
            f'<main>{message}{handlers[self.page]()}</main><script>{ACTIONS_SCRIPT}</script></body></html>'
        )

    def overview(self):
        state = self.data.get("service") or self.data.get("state") or {}
        model = self.data.get("model") or {}
        state_label = state.get("status")
        if state.get("stopped") or state.get("stop_requested"):
            state_label = "stopped"
        elif not state_label:
            state_label = "running" if state.get("heartbeat") else "unknown"
        paused = state.get("outbound_paused") or state.get("paused_all") or state.get("global_pause") or state.get("send_paused") or state.get("paused")
        armed = state.get("live_armed", state.get("armed", state.get("live_send")))
        anytime = bool(self.data.get("preferences", {}).get("send_anytime"))
        stopped = state_label == "stopped"
        task_control = self.form("run", "恢复后台任务") if stopped else self.form("stop", "暂停后台任务", tone="danger")
        send_control = self.form("resume_all", "恢复发送") if paused else self.form("pause_all", "暂停全部外发", tone="danger")
        if self.data.get("demo"):
            send_control = '<span class="badge">仅演示，外发关闭</span>'
        sending_status = "就绪即发送" if anytime else "按发送窗口执行"
        flags = _dl([
            ("后台任务", _badge("paused" if stopped else state_label)),
            ("下一次任务执行（上海时间）", _when(state.get("next_task_at")) if not stopped else "恢复后台任务后继续"),
            ("后台邮件发送", _badge("paused") if paused else
             ("等待后台恢复" if stopped else sending_status if armed else "等待发送渠道")),
            ("待发送的邮件申请", _text(self.data.get("stats", {}).get("queued_applications", 0))),
            ("待执行的站内任务", _text(sum(action.get("state") == "queued" for action in self.data.get("browser_actions", [])))),
            ("下一次预计发送（上海时间）", _when(state.get("next_send_at"))),
            ("最近心跳", _when(state.get("heartbeat"))),
            ("模型", _text(model.get("name") or model.get("model_name") or model.get("model"))),
        ])
        controls = ('<div class="two-col"><div><h3>运行任务</h3><p class="muted">控制搜索、查收、核验和后台调度。</p>'
                    f'{task_control}</div><div><h3>发送申请</h3>'
                    '<p class="muted">暂停外发时，搜索和查收会继续。</p>'
                    f'{send_control}<p><a href="/outbox">查看发送队列与等待原因</a> · '
                    '<a href="/outbox#browser-actions">查看站内浏览器任务</a></p></div></div>')
        channel_panels = []
        for item in self.data.get("channels", []):
            channel = item.get("name")
            paused_channel = item.get("paused")
            toggle = self.form(
                "resume_channel" if paused_channel else "pause_channel",
                "恢复渠道" if paused_channel else "暂停渠道", channel=channel,
            )
            scan = self.form("scan", "安排检查", channel=channel)
            handoff = _source(HANDOFF_LINKS[channel], "在浏览器接管") if channel in HANDOFF_LINKS else ""
            content = _dl([
                ("最近检查", _when(item.get("updated_at"))),
                ("下次检查", _when(item.get("next_check") or item.get("next_at"))),
                ("详情", _text(item.get("detail"))),
            ])
            channel_panels.append(
                f'<article class="panel"><div class="row spread"><h3>{_channel(channel)}</h3>'
                f'{_badge("paused" if paused_channel else item.get("status"))}</div>{content}'
                f'<div class="actions">{toggle}{scan}{handoff}</div></article>'
            )
        tasks = self.task_table(self.data.get("tasks", []))
        today_jobs = [
            [
                f'<a href="/jobs#job-{_text(item.get("id"))}">{_text(item.get("company"))} · {_text(item.get("title"))}</a>',
                _channel(item.get("channel")), _badge(item.get("status")),
                _text(item.get("match_score")), _when(item.get("created_at") or item.get("observed_at")),
            ]
            for item in self.data.get("jobs", [])
            if item.get("status") != "unknown" and _today(item.get("created_at") or item.get("observed_at"))
        ]
        today_sends = [
            [
                f'<a href="/outbox#send-{_text(item.get("id"))}">{_text(item.get("company"))} · {_text(item.get("title") or item.get("subject"))}</a>',
                _channel(item.get("channel")), _badge(item.get("state")), _when(item.get("updated_at")),
            ]
            for item in self.data.get("outbox", [])
            if item.get("state") in {"sent", "confirmed"} and _today(item.get("updated_at"))
        ]
        daily = (
            _table(("岗位", "渠道", "状态", "匹配排序分", "发现时间"), today_jobs, "今天还没有新岗位记录")
            + '<div class="section"><h3>今日已确认外发</h3>'
            + _table(("岗位或邮件", "渠道", "状态", "时间"), today_sends, "今天还没有已确认的外发")
            + "</div>"
        )
        return (
            '<h1>运行概览</h1><p class="muted">查看今日进度、渠道能力和后台任务。</p>'
            f'{self.stats()}<div class="panel"><h2>后台与发送控制</h2>{flags}'
            f'{controls}</div>'
            f'<section class="section"><h2>渠道状态</h2><div class="grid">{"".join(channel_panels)}</div></section>'
            f'<section class="section"><h2>今日岗位与发送</h2>{daily}</section>'
            f'<section class="section"><h2>任务与下次运行</h2>{tasks}</section>'
        )

    def stats(self):
        values = self.data.get("stats") or {}
        labels = (
            ("今日发现", ("found_today", "today_found", "jobs_today", "discovered_today", "today_discovered", "found")),
            ("今日筛选", ("screened_today", "today_screened", "matched_today", "today_matched", "screened")),
            ("今日模拟确认" if self.data.get("demo") else "今日确认投递", ("sent_today", "today_sent", "applications_today", "today_applications", "today_confirmed")),
            ("待我决定", ("decisions_open", "pending_decisions", "decisions_pending", "decisions")),
        )
        boxes = []
        for label, keys in labels:
            result = next((values[key] for key in keys if key in values), None)
            boxes.append(f'<div class="stat"><span class="muted">{label}</span><b>{_text(result)}</b></div>')
        return '<div class="stats">' + "".join(boxes) + '</div>'

    def task_table(self, tasks):
        steps = {}
        for step in reversed(self.data.get("task_steps", [])):
            steps.setdefault(step["task_id"], []).append(step)
        rows = []
        for item in tasks:
            state = item.get("state")
            retry = ""
            if state in {"failed", "retry"} and int(item.get("attempts") or 0) < 3:
                retry = self.form("retry", "重试此任务", id=item.get("id"))
            trace = steps.get(item.get("id"), [])
            explanation = _text(item.get("detail"))
            if trace:
                explanation += _details("查看任务步骤与证据", _table(
                    ("上海时间", "阶段", "结果", "依据"),
                    [[_when(x.get("at")), _text(x.get("stage")), _badge(x.get("outcome")), _block(x.get("detail"))]
                     for x in trace], ""))
            rows.append([
                _text(item.get("id")), _kind(item.get("kind")), _channel(item.get("channel")),
                _badge(state), _when(item.get("due_at")), _text(item.get("attempts")),
                explanation, retry or "—",
            ])
        return _table(("编号", "任务", "渠道", "状态", "计划时间", "尝试次数", "详情", "操作"), rows)

    def queue_browser_action(self, url, job_id, anchor_id=None):
        if "browser_actions" not in self.data:
            return ""
        from .browser_actions import browser_job_link

        try:
            _, canonical_url, _ = browser_job_link(url)
        except ValueError:
            return ""
        if anchor_id:
            return self.form("browser_queue", "加入站内回复队列",
                             _field("body", "回复正文", "", "textarea", True),
                             job_id=job_id, kind="reply", url=canonical_url, anchor_id=anchor_id)
        operations = ('<label>操作<select name="kind">'
                      '<option value="contact">首次招呼</option>'
                      '<option value="application">申请岗位</option>'
                      '<option value="attachment">补发简历附件</option></select></label>')
        form = self.form("browser_queue", "加入站内浏览器队列", operations
                         + _field("body", "正文（首次招呼必填）", "", "textarea"),
                         job_id=job_id, url=canonical_url)
        return _details("安排站内投递或联系", form)

    def jobs(self, query):
        term = query.get("q", [""])[0].strip()[:200]
        records = self.data.get("jobs", [])
        if term:
            records = [
                item for item in records
                if term.casefold() in " ".join(str(item.get(k, "")) for k in ("company", "title", "location", "channel")).casefold()
            ]
        search = (
            '<form class="row toolbar" action="/jobs" method="get">'
            f'<input aria-label="搜索岗位" placeholder="公司、岗位或城市" name="q" value="{_text(term, "")}" maxlength="200">'
            '<button>筛选</button><a class="button" href="/export.csv">导出台账 CSV</a></form>'
        )
        job_form = "".join(_field(*field) for field in (
            ("company", "公司"), ("title", "岗位"), ("location", "实际工作地点"),
            ("salary", "薪资范围"), ("channel", "来源渠道", "official"),
            ("url", "岗位来源 URL", "", "url"), ("external_id", "平台岗位编号"),
            ("domain", "公司官网域名"), ("published_at", "发布时间"),
            ("description", "完整岗位职责", "", "textarea"), ("requirements", "完整任职要求", "", "textarea"),
        ))
        job_form += ('<label>招聘状态<select name="status"><option value="unknown">状态待确认</option>'
                     '<option value="open">已核实在招</option><option value="closed">已关闭</option></select></label>')
        add = self.form("add_job", "保存岗位", '<div class="form-grid">' + job_form + '</div>', "primary")
        cards = []
        for job in records:
            job_id = job.get("id")
            title = f'{_text(job.get("company"))} · {_text(job.get("title"))}'
            match = self.match(job.get("match_json") or job.get("match"))
            review = job.get("review") or {}
            reviewed = (_dl([("逐条复核", _badge(review["state"])), ("判断依据", _text(review["note"])),
                             ("复核来源", _source(review["source"])), ("更新时间", _when(review["updated_at"]))])
                        if review else "")
            fields = _dl([
                ("地点／薪资", f'{_text(job.get("location"))} ／ {_text(job.get("salary"))}'),
                ("岗位编号", _text(job.get("external_id") or job.get("source_id"))),
                ("官网域名", _text(job.get("domain"))), ("发布日期", _when(job.get("published_at"))),
                ("来源", _source(job.get("url"))),
            ])
            score = _text(job.get("match_score"))
            description = job.get("description") or job.get("responsibilities")
            requirements = job.get("requirements")
            full = _details("查看职责和要求", "<p>职责</p>" + _block(description) + "<p>要求</p>" + _block(requirements))
            review_form = self.form("record_job_review", "保存 JD 复核", (
                '<label>结论<select name="state"><option value="hold">保留待核</option>'
                '<option value="excluded">不投递</option><option value="cleared">复核通过</option></select></label>'
                + _field("source", "实际核对的来源", review.get("source", job.get("url")), "url", True)
                + _field("note", "核对依据", review.get("note", ""), "textarea", True)), job_id=job_id)
            recipient = self.form(
                "authorize_recipient", "核验并保存收件人",
                _field("address", "明确用于招聘的邮箱", "", "email", True)
                + _field("source", "招聘联系的来源或证据", "", "textarea", True),
                job_id=job_id,
            )
            manual_options = (
                '<label>状态<select name="state"><option value="contacted">已联系</option>'
                '<option value="confirmed">已核实提交</option><option value="unknown">结果不明</option>'
                '<option value="rejected">已拒绝</option><option value="closed">已关闭</option>'
                '<option value="stopped">要求停止</option></select></label>'
            )
            manual = self.form("record_manual", "保存人工核验", manual_options
                               + _field("evidence", "页面结果或其他证据", "", "textarea", True), job_id=job_id)
            held = job.get("manual_followup")
            if held:
                manual += _block(held.get("evidence"))
                if held.get("employer_id") == job.get("employer_id"):
                    manual += self.form("employer_resume_outreach", "恢复该公司外联规则", employer_id=held["employer_id"])
            else:
                manual += self.form("employer_manual_followup", "标记人工跟进并暂停该公司外联",
                                    _field("resolution", "建立联系的依据", "", "textarea", True), job_id=job_id)
            transcript = ""
            if job.get("channel") in {"boss", "liepin", "yupao"}:
                direction = ('<label>方向<select name="direction"><option value="inbound">招聘方来信</option>'
                             '<option value="outbound">本人已发</option></select></label>')
                transcript = self.form("record_platform_message", "登记平台会话",
                    _field("url", "平台公开职位链接", job.get("url"), "url", True)
                    + direction + _field("occurred_at", "平台显示的上海时间", "", "datetime-local", True)
                    + _field("thread_ref", "会话备注（同一会话用同一标识）")
                    + _field("body", "一条已核对的聊天记录", "", "textarea", True),
                    job_id=job_id, channel=job["channel"])
            model_check = self.form("model_match", "安排模型复核", job_id=job_id) if job.get("match_json") else ""
            job_status = "active" if job.get("status") == "open" else job.get("status")
            followup = _badge("manual_followup") if held else ""
            browser_form = (self.queue_browser_action(job.get("url"), job_id)
                            if not held and job_status not in {"closed", "rejected", "stopped", "expired"}
                            and review.get("state") != "excluded" else "")
            cards.append(
                f'<article class="panel" id="job-{_text(job_id)}"><h3>{title}</h3><div class="chips">'
                f'{_badge(job_status)}{followup}<span class="badge">{_channel(job.get("channel"))}</span>'
                f'<span class="badge">匹配排序分 {score}</span></div>{fields}'
                f'{_details("匹配点、缺口与未知项", reviewed + match + model_check)}{full}'
                f'{_details("补充 JD 复核", review_form)}'
                f'{browser_form}{_details("核验招聘收件人", recipient)}'
                f'{_details("登记人工接管结果", manual + transcript)}</article>'
            )
        return (
            '<h1>岗位发现与筛选</h1><p class="muted">排序分用于比较匹配度。未知条件和明确不符合分别记录。</p>'
            f'{search}<div class="panel">{_details("补录已核对的岗位", add)}</div><div class="section stack">'
            + ("".join(cards) or _empty("没有符合当前筛选的记录")) + '</div>'
        )

    @staticmethod
    def match(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return _block(value, "待筛选")
        if not isinstance(value, dict):
            return _block(value, "待筛选")
        groups = (
            ("匹配点", ("strengths", "matches", "points", "fit")),
            ("缺口", ("gaps", "weaknesses")),
            ("未知项", ("unknowns", "unknown", "missing")),
            ("明确不符合", ("hard", "hard_failures", "hard_reasons", "reject_reasons", "hard_blocks")),
            ("投递理由", ("reason", "rationale")),
            ("模型复核", ("model_advice",)),
        )
        items = []
        known = set()
        for title, keys in groups:
            key = next((key for key in keys if key in value), None)
            known.update(keys)
            if key:
                items.append((title, _text(value[key], "无")))
        remaining = {key: item for key, item in value.items() if key not in known}
        return _dl(items) + (_block(remaining) if remaining else "")

    def threads(self):
        records = self.data.get("threads", [])
        messages = self.data.get("messages", [])
        by_thread = {}
        if isinstance(messages, dict):
            by_thread = {str(key): items for key, items in messages.items()}
        else:
            for message in messages:
                by_thread.setdefault(str(message.get("thread_id")), []).append(message)
        cards = []
        for thread in records:
            thread_id = thread.get("id")
            job_id = thread.get("job_id")
            content = []
            thread_messages = thread.get("messages") or by_thread.get(str(thread_id), [])
            for message in thread_messages:
                direction = message.get("direction")
                tone = " out" if direction in {"outgoing", "outbound", "sent"} else ""
                sender = message.get("sender") or message.get("from_address") or message.get("from_name")
                arrived = message.get("occurred_at") or message.get("sent_at") or message.get("received_at") or message.get("created_at")
                body = ("非招聘内容已隐藏" if message.get("hide_body") else
                        message.get("body") or message.get("body_text") or message.get("text"))
                kind = message.get("kind") or message.get("classification")
                disposition = (f'<p class="muted">{_kind(message.get("audit_state"))} · {_text(message.get("audit_evidence"))}</p>'
                               if message.get("audit_state") else "")
                if message.get("source_url"):
                    disposition += f'<p class="muted">人工核对 · {_source(message["source_url"])}</p>'
                content.append(
                    f'<div class="message{tone}"><div class="row"><strong>{_text(sender)}</strong>'
                    f'<small>{_when(arrived)} · {_kind(direction)} · {_kind(kind)}</small></div>'
                    f'{disposition}{_block(body)}</div>'
                )
            suffix = "".join(content) or _empty("该会话暂无同步正文")
            link_text = "查看已导入的历史消息" if str(thread.get("external_id", "")).startswith("legacy:") else "查看已同步会话"
            linked_job = f'<a href="/jobs#job-{_text(job_id)}">关联岗位</a>' if job_id else ""
            stopped = (_badge("manual_followup") if thread.get("manual_followup") else
                       _badge("stopped") if thread.get("stopped") else "")
            latest = thread_messages[-1] if thread_messages else {}
            reply = ""
            if (job_id and not stopped and latest.get("direction") == "inbound" and latest.get("id") and latest.get("source_url")
                    and latest.get("classification") not in {"sensitive", "stop", "closed", "reject", "unrelated", "sales"}):
                reply = self.queue_browser_action(latest["source_url"], job_id, latest["id"])
            cards.append(
                f'<article class="panel" id="thread-{_text(thread_id)}">'
                f'<h3>{_text(thread.get("company"))} · {_text(thread.get("subject"))}</h3>'
                f'<div class="chips"><span class="badge">{_channel(thread.get("channel"))}</span>'
                f'<small>更新 {_when(thread.get("updated_at"))}</small>{stopped}{linked_job}</div>'
                f'{_details(link_text, suffix)}{reply}</article>'
            )
        return '<h1>招聘消息</h1><div class="stack">' + ("".join(cards) or _empty("尚无已同步会话")) + "</div>"

    def browser_actions(self):
        rows = []
        for action in self.data.get("browser_actions", []):
            job_id = action.get("job_id")
            status = action.get("state")
            proof = "".join(
                _details(label, _block(action[key]))
                for key, label in (("evidence", "查看页面证据"), ("receipt", "查看平台回执"), ("body", "查看正文"))
                if action.get(key)
            )
            rows.append([
                _text(action.get("id")),
                f'<a href="/jobs#job-{_text(job_id)}">{_text(action.get("company"))} · {_text(action.get("title"))}</a>',
                _channel(action.get("channel")), _kind(action.get("kind")),
                _badge("uncertain" if status == "unknown" else status),
                _text(action.get("resume_label")), _source(action.get("url"), "打开原岗位"),
                proof or "—", _when(action.get("updated_at")),
            ])
        table = _table(("编号", "岗位", "渠道", "操作", "状态", "简历", "入口", "证据", "更新（上海时间）"),
                       rows, "暂无站内浏览器任务")
        return ('<section class="section" id="browser-actions"><h2>站内浏览器任务</h2>'
                '<p class="muted">站内投递和回复由前台执行器在已登录的浏览器中操作。'
                '待执行表示任务已登记，页面回执会在核实后写入。结果不明时先核对原会话。</p>'
                f'{table}</section>')

    def outbox(self):
        resumes = {str(item.get("id")): item for item in self.data.get("resumes", [])}
        cards = []
        for item in self.data.get("outbox", []):
            send_id = item.get("id")
            state = item.get("state")
            thread_id = item.get("thread_id")
            resume = resumes.get(str(item.get("resume_id")), {})
            draft_valid = str(item.get("draft_valid", True)).lower() not in {"false", "0", "no"}
            fields = _dl([
                ("收件人", _text(item.get("to_address"))), ("主题", _text(item.get("subject"))),
                ("简历版本", _text(resume.get("label") or item.get("resume_id"), "无附件")),
                ("附件 SHA-256", _text(item.get("attachment_sha") or resume.get("sha256"))),
                ("更新", _when(item.get("updated_at"))), ("最早可发（上海时间）", _when(item.get("expected_send_at"))),
                ("当前等待原因", _text(item.get("waiting_reason"))),
                ("消息标识", _text(item.get("message_id") or item.get("external_id"))),
                ("平台返回标识", _text(item.get("provider_id"))),
            ])
            edit = ""
            approve = ""
            if state in {"draft", "pending", "pending_send", "waiting_approval"} and draft_valid:
                edit = _details(
                    "编辑正文",
                    self.form(
                        "edit_draft", "保存修改",
                        _field("subject", "主题", item.get("subject"))
                        + _field("body", "正文", item.get("body"), "textarea", True),
                        id=send_id,
                    ),
                )
            if state in {"draft", "pending", "pending_send", "waiting_approval"} and draft_valid:
                approve = self.form("approve", "批准并加入发送队列", tone="primary", id=send_id)
            reconcile = ""
            if state in {"uncertain", "ambiguous", "unknown"}:
                reconcile = self.form("reconcile", "先查询发送证据", id=send_id)
            retry = ""
            if state == "failed" and item.get("retryable") and int(item.get("attempts") or 0) < 3:
                retry = self.form("retry_send", "重试此发送", id=send_id)
            state_label = {"unknown": "uncertain", "queued": "pending_send"}.get(state, state)
            stale = "" if draft_valid else '<p class="notice">最新会话已有变化。此草稿不能批准，等待重新处理。</p>'
            linked_thread = f'<a href="/threads#thread-{_text(thread_id)}">查看关联会话</a>' if thread_id else ""
            cards.append(
                f'<article class="panel" id="send-{_text(send_id)}"><h3>{_text(item.get("company"))} · '
                f'{_text(item.get("title") or item.get("subject"))}</h3><div class="chips">'
                f'{_badge(state_label)}<span class="badge">{_channel(item.get("channel"))}</span>'
                f'<small>记录 {_text(send_id)}</small>{linked_thread}</div>{fields}{stale}'
                f'{_details("查看实际正文", _block(item.get("body")))}'
                f'{_details("查看发送证据", _block(item.get("evidence")))}'
                f'{edit}<div class="actions">{approve}{reconcile}{retry}</div></article>'
            )
        history = _table(
            ("公司和岗位", "渠道", "历史状态", "时间", "历史依据"),
            [
                [
                    f'{_text(item.get("company"))} · {_text(item.get("title"))}', _channel(item.get("channel")),
                    _badge(item.get("status")), _when(item.get("occurred_at")),
                    _details("查看依据", _block(item.get("evidence") or item.get("source"))),
                ]
                for item in self.data.get("history", [])
            ],
            "暂无已导入历史",
        )
        waiting_jobs = _table(("机会", "等待原因", "依据"), [
            [f'<a href="/jobs#job-{_text(job["id"])}">{_text(job["company"])} · {_text(job["title"])}</a>',
             _text(job["review"]["note"]), _source(job["review"]["source"])]
            for job in self.data.get("jobs", []) if (job.get("review") or {}).get("state") == "hold"
        ], "暂无待核实的机会")
        return (
            '<h1>草稿与发送证据</h1><p class="muted">已确认仅代表当前渠道有发送证据。'
            '收件和已读状态以招聘方或平台凭据为准。</p>'
            + self.browser_actions() + '<section class="section"><h2>邮件草稿与回执</h2><div class="stack">'
            + ("".join(cards) or _empty()) + '</div></section>'
            + f'<section class="section"><h2>待核实机会</h2>{waiting_jobs}</section>'
            + f'<section class="section"><h2>继承的历史记录</h2>{history}</section>'
        )

    def decisions(self):
        cards = []
        for item in self.data.get("decisions", []):
            decision_id = item.get("id")
            thread_id = item.get("thread_id")
            thread = f'<a href="/threads#thread-{_text(thread_id)}">查看关联会话</a>' if thread_id else ""
            due = _when(item.get("due_at"))
            choice = ""
            if item.get("state") not in {"done", "resolved", "confirmed", "closed", "cancelled", "canceled"}:
                resolution = _field("resolution", "你的决定或你认可的回复", item.get("resolution"), "textarea", True)
                choice = self.form("decide", "保存我的决定", resolution, "primary", id=decision_id)
                if item.get("kind") == "manual_channel_pending" and item.get("job_id"):
                    choice += self.form("employer_manual_followup", "标记已联系并关闭待办",
                                        _field("resolution", "建立联系的依据", "", "textarea", True),
                                        job_id=item["job_id"], decision_id=decision_id)
            answer = _block(item.get("resolution")) if item.get("resolution") else ""
            cards.append(
                f'<article class="panel"><h3>{_kind(item.get("kind"))} · {_text(item.get("question"))}</h3>'
                f'<div class="chips">{_badge(item.get("state"))}<small>截止 {due}</small>{thread}</div>'
                f'{_details("建议回复草稿", _block(item.get("draft")))}{answer}{choice}</article>'
            )
        return (
            '<h1>待我决定</h1><p class="muted">面试时间、薪资、到岗、搬迁、出差、Offer 和合同等集中在这里。'
            '保存决定后由后台按授权规则重新检查。</p>'
            '<div class="stack">' + ("".join(cards) or _empty("暂无需要你决定的事项")) + '</div>'
        )

    def settings(self):
        prefs = {**PREFERENCE_DEFAULTS, **(self.data.get("preferences") or {})}
        legacy_send_fields = {"send_start", "send_end", "daily_limit"}
        fields = "".join(_field(key, label, prefs.get(key), field_type) for key, label, field_type in PREFERENCE_FIELDS
                         if not prefs["send_anytime"] or key not in legacy_send_fields)
        checks = "".join(_check(key, label, prefs.get(key)) for key, label in BOOL_FIELDS)
        field_list = '<div class="form-grid">' + fields + '</div><div class="checks">' + checks + '</div>'
        preference = self.form("preferences", "保存求职偏好", field_list, "primary")
        exclusions = self.data.get("exclusions", [])
        exclusion_table = _table(
            ("排除项", "备注", "操作"),
            [
                [_text(item.get("pattern")), _text(item.get("note")),
                 self.form("remove_exclusion", "移除", pattern=item.get("pattern"))]
                for item in exclusions
            ],
            "暂无自定义排除项",
        )
        exclusion_add = self.form(
            "add_exclusion", "加入排除名单",
            _field("pattern", "排除关键词或公司", required=True) + _field("note", "原因"),
        )
        fact_rows = []
        for item in self.data.get("facts", []):
            facts_edit = self.form(
                "edit_fact", "保存核实资料",
                _field("value", "内容", item.get("value"), "textarea", True)
                + _field("source", "来源", item.get("source"), "textarea", True),
                id=item.get("id"),
            )
            fact_rows.append([
                _text(item.get("key")), _text(item.get("value")), _source(item.get("source")),
                _when(item.get("updated_at")), _badge(item.get("state")), _details("编辑", facts_edit),
            ])
        facts = _table(("事实项", "值", "来源", "更新时间", "状态", "操作"), fact_rows)
        resumes = _table(
            ("版本", "适用范围", "修改时间", "文件位置与 SHA-256", "可发送"),
            [
                [_text(item.get("label")), _text(item.get("scope")), _when(item.get("modified_at")),
                 f'<div class="mono">{_text(item.get("path"))}<br>{_text(item.get("sha256"))}</div>',
                 _text(item.get("approved"))]
                for item in self.data.get("resumes", [])
            ],
        )
        model = self.data.get("model") or {}
        model_view = _dl([
            ("适配器", _text(model.get("adapter") or model.get("provider"))),
            ("模型", _text(model.get("name") or model.get("model_name") or model.get("model"))),
            ("当前状态", _badge(model.get("status"))), ("今日调用", _text(model.get("calls_today"))),
            ("最近输入／输出 token", f'{_text(model.get("input_tokens"))} ／ {_text(model.get("output_tokens"))}'),
            ("费用", _text(model.get("cost") or model.get("cost_today"), "未知")),
            ("说明", _text(model.get("detail"))),
        ])
        model_set = self.form(
            "set_model", "保存模型设置",
            _field("model_name", "官方模型名称（空白使用已配置的默认）", model.get("configured_name", ""))
            + _field("max_calls", "每日调用上限", model.get("max_calls", model.get("daily_limit")), "number")
            + _field("timeout", "单任务超时（秒）", model.get("timeout"), "number")
            + _field("cost_limit", "费用上限（费用未知时会阻止模型调用）", model.get("cost_limit")),
        )
        source_add = self.form(
            "add_source", "添加公开来源",
            _field("url", "已核验的官网招聘 URL", "", "url", True)
            + _field("company", "公司") + _field("domain", "公司官网域名")
            + _field("location", "来源明确标注的工作地点"),
            channel="official",
        )
        sources = _table(
            ("公司", "公开来源", "上次检查", "状态", "操作"),
            [
                [_text(item.get("company")), _source(item.get("url")), _when(item.get("last_checked")),
                 _text(item.get("last_status")), self.form("pause_source" if item.get("enabled") else "resume_source",
                     "暂停此来源" if item.get("enabled") else "恢复此来源", id=item.get("id"))]
                for item in self.data.get("sources", [])
            ],
            "暂无可调度的公开来源",
        )
        return (
            '<h1>资料与设置</h1><p class="muted">时间与节奏统一按 Asia/Shanghai。未确认的薪资底线可保持空白。</p>'
            f'<section class="panel"><h2>求职条件与节奏</h2>{preference}</section>'
            f'<section class="section"><h2>个人事实、作品与常用回答</h2>{facts}</section>'
            f'<section class="section"><h2>简历版本</h2>{resumes}</section>'
            f'<section class="section two-col"><article class="panel"><h2>模型与用量</h2>{model_view}'
            f'{_details("修改模型与调用上限", model_set)}{self.form("model_probe", "安排模型检查")}'
            '</article><article class="panel"><h2>来源与备份</h2>'
            f'{source_add}<div class="section">{self.form("backup", "创建数据库备份")}</div></article></section>'
            f'<section class="section"><h2>公开岗位来源</h2>{sources}</section>'
            f'<section class="section"><h2>排除名单</h2><div class="panel">{exclusion_add}</div>{exclusion_table}</section>'
        )

    def events(self):
        rows = [
            [_when(item.get("created_at")), _text(item.get("level")), _channel(item.get("channel")), _text(item.get("message"))]
            for item in self.data.get("events", [])
        ]
        return '<h1>执行日志</h1>' + _table(("上海时间", "级别", "渠道", "事件"), rows)


def make_handler(service, token, port):
    """Create a no-cache handler to be served exclusively on the loopback interface."""
    hostnames = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if port == 80:
        hostnames.update({"127.0.0.1", "localhost", "[::1]"})
    class Handler(BaseHTTPRequestHandler):
        server_version = "JobHarness"
        sys_version = ""
        timeout = 12

        def log_message(self, _format, *args):
            # Operational events are persisted by the service. Avoid request data in logs.
            return

        def _respond(self, status, body=b"", mime="text/html; charset=utf-8", headers=None, head=False):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{SCRIPT_HASH}'; "
                "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if not head and body:
                self.wfile.write(body)

        def _guard(self):
            try:
                local = ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                local = False
            headers = self.headers.get_all("Host", [])
            if local and len(headers) == 1 and headers[0].lower() in hostnames:
                return True
            self._respond(HTTPStatus.FORBIDDEN, "控制台仅接受本机地址。", "text/plain; charset=utf-8")
            return False

        def do_HEAD(self):
            self._get(head=True)

        def do_GET(self):
            self._get()

        def _get(self, head=False):
            if not self._guard():
                return
            try:
                parsed = urlsplit(self.path)
                if parsed.netloc or parsed.scheme:
                    self._respond(HTTPStatus.BAD_REQUEST, "请求地址无效。", head=head)
                    return
                if parsed.path == "/healthz":
                    self._respond(HTTPStatus.OK, '{"status":"ok"}', "application/json; charset=utf-8", head=head)
                elif parsed.path == "/export.csv":
                    export = service.export_csv()
                    if isinstance(export, bytes):
                        export = export.decode("utf-8-sig")
                    self._respond(
                        HTTPStatus.OK, "\ufeff" + export.lstrip("\ufeff"), "text/csv; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="job-application-ledger.csv"'}, head=head,
                    )
                elif parsed.path in NAV:
                    query = parse_qs(parsed.query, max_num_fields=20)
                    result = Console(service.snapshot(), token, parsed.path).render(query)
                    self._respond(HTTPStatus.OK, result, head=head)
                else:
                    self._respond(HTTPStatus.NOT_FOUND, "页面不存在。", head=head)
            except (BrokenPipeError, ConnectionResetError):
                return
            except ValueError:
                self._respond(HTTPStatus.BAD_REQUEST, "无法读取当前请求。", head=head)
            except Exception:
                self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, "控制台读取失败，请检查后台服务。", head=head)

        def _action_result(self, status, notice, page=None):
            if self.headers.get("Accept") == "application/json":
                result = {"notice": notice}
                if status == HTTPStatus.OK and page:
                    result["location"] = page + "?" + urlencode({"notice": notice[:450]})
                self._respond(status, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
            elif page:
                location = page + "?" + urlencode({"notice": notice[:450], "error": int(status != HTTPStatus.OK)})
                self._respond(HTTPStatus.SEE_OTHER, headers={"Location": location})
            else:
                self._respond(status, '<!doctype html><meta charset="utf-8"><title>操作未完成</title>'
                              f'<p role="alert">{escape(notice)}</p><p><a href="/">返回运行概览</a></p>')

        def do_POST(self):
            if not self._guard():
                return
            if self.path != "/action":
                self._action_result(HTTPStatus.NOT_FOUND, "操作不存在。")
                return
            request_origin = "http://" + self.headers["Host"].lower()
            origins = self.headers.get_all("Origin", [])
            sites = self.headers.get_all("Sec-Fetch-Site", [])
            origin = origins[0] if origins else None
            site = sites[0] if sites else None
            referers = self.headers.get_all("Referer", [])
            page = None
            if len(referers) == 1:
                try:
                    ref = urlsplit(referers[0])
                    if ref.scheme + "://" + ref.netloc.lower() == request_origin and ref.path in NAV:
                        page = ref.path
                except ValueError:
                    pass
            if (
                len(origins) > 1 or len(sites) > 1
                or (origin is not None and origin != request_origin)
                or (site is not None and site != "same-origin")
                or (origin is None and site != "same-origin" and page is None)
            ):
                origin_kind = "null" if origin == "null" else "same-origin" if origin == request_origin else "other" if origin else "absent"
                site_kind = site if site in {"none", "same-origin", "same-site", "cross-site"} else "absent-or-other"
                print(f"控制台 POST /action 拒绝 403，Origin={origin_kind}，Sec-Fetch-Site={site_kind}", flush=True)
                self._action_result(HTTPStatus.FORBIDDEN, "浏览器未提供可核实的同源信息。请从本机控制台重新加载后操作。")
                return
            if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                self._action_result(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "提交格式无效。请重新加载控制台后操作。", page)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 200_000 or self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                    raise ValueError
                self.connection.settimeout(12)
                raw = self.rfile.read(size)
                fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=45)
                if len(raw) != size or any(len(values) != 1 for values in fields.values()):
                    raise ValueError
            except (OSError, UnicodeError, ValueError):
                self._action_result(HTTPStatus.BAD_REQUEST, "表单字段无效、重复或超出长度限制。", page)
                return
            data = {key: values[0] for key, values in fields.items()}
            supplied = data.pop("token", "")
            if not secrets.compare_digest(supplied.encode(), token.encode()):
                self._action_result(HTTPStatus.FORBIDDEN, "此页面的安全令牌已失效。请刷新控制台后再操作。", page)
                return
            page = data.pop("next", "/")
            if page not in NAV:
                page = "/"
            name = data.pop("action", "")
            if name == "preferences":
                for field, _ in BOOL_FIELDS:
                    data.setdefault(field, "0")
            try:
                notice = str(service.action(name, data) or "操作已记录。")
            except ValueError as exc:
                self._action_result(HTTPStatus.BAD_REQUEST, str(exc), page)
            except Exception as exc:
                error_type = type(exc).__name__
                print(f"控制台 POST /action 后台错误 500，{error_type}", flush=True)
                self._action_result(HTTPStatus.INTERNAL_SERVER_ERROR, f"后台处理失败（{error_type}）。请查看执行日志或刷新核对状态。", page)
            else:
                self._action_result(HTTPStatus.OK, notice, page)

    return Handler
