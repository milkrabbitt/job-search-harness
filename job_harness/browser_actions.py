"""Durable claims for an authorized foreground browser operator."""

import hashlib
import re
import secrets
from pathlib import Path

from . import rules
from .platform_handoff import normalize_platform_message, platform_job_link
from .storage import fetch, manual_followup, one, shift, stamp


KINDS = ("contact", "application", "attachment", "reply")
RESULTS = ("confirmed", "attachment_requested", "blocked", "unknown")
FORMAL = {"application", "attachment"}
LEASE_SECONDS = 300
EXPIRED = "前台认领超时，须先回原平台核对结果。"
DOMAINS = {"zhipin.com": "boss", "liepin.com": "liepin", "yupao.com": "yupao", "zhaopin.com": "zhaopin"}
SENSITIVE = re.compile(r"\b\d{17}[\dXx]\b|银行卡号|私有仓库|身份证照片|验证码|银行账号")
PRIVATE_EVIDENCE = re.compile(r"\b\d{17}[\dXx]\b|(?:验证码|银行账号|银行卡号)[：:\s]+\d{4,}")
NON_REPLY = {"auto", "automatic", "acknowledged", "closed", "reject", "stop", "sensitive",
             "promotion", "unrelated", "delivery_failed", "delivery_delay", "bounce"}
SALES_MESSAGE = re.compile(r"(?:招聘|岗位是|职位是|考虑|在招).{0,10}(?:电话销售|销售岗|保险顾问|贷款顾问|渠道销售|销售经理)")


def browser_job_link(url):
    """Identify the platform and strip permitted public tracking fields."""
    channel = DOMAINS.get(rules.host(url).removeprefix("m."))
    canonical, identifier = platform_job_link(channel, url)
    return channel, canonical, identifier


class BrowserActions:
    def __init__(self, harness):
        self.harness = harness

    @staticmethod
    def list(c, job_id=None):
        return fetch(c, """SELECT b.id,b.job_id,b.channel,b.url,b.kind,b.body,b.anchor_id,b.claimed_at,
                        CASE WHEN b.state='sending' AND b.claim_until<=? THEN 'unknown' ELSE b.state END AS state,
                        CASE WHEN b.state='sending' AND b.claim_until<=? THEN ? ELSE b.evidence END AS evidence,
                        b.receipt,b.created_at,b.updated_at,j.company,j.title,r.label AS resume_label
                        FROM browser_actions b JOIN jobs j ON j.id=b.job_id LEFT JOIN resumes r ON r.id=b.resume_id
                        WHERE (? IS NULL OR b.job_id=?) ORDER BY b.id DESC LIMIT 250""",
                     (stamp(), stamp(), EXPIRED, job_id, job_id))

    def reap(self):
        with self.harness.store.tx(True) as c:
            c.execute("""UPDATE browser_actions SET state='unknown',evidence=?,updated_at=?
                      WHERE state='sending' AND claim_until<=?""", (EXPIRED, stamp(), stamp()))

    @staticmethod
    def stop(c):
        c.execute("""UPDATE browser_actions SET state='unknown',evidence=?,updated_at=? WHERE state='sending'""",
                  ("停止时尚未核实前台操作结果，请查原平台。", stamp()))

    @staticmethod
    def verify_job_url(c, job, url):
        channel, canonical, identifier = browser_job_link(url)
        alias_channel = "official" if channel == "zhaopin" else channel
        exact = one(c, "SELECT job_id FROM job_aliases WHERE channel=? AND external_id=?",
                    (alias_channel, identifier))
        if exact and exact["job_id"] != job["id"]:
            raise ValueError("公开岗位编号与 Harness 中的岗位不一致")
        if not exact and not one(c, """SELECT job_id FROM job_aliases WHERE job_id=? AND channel=?
                                   AND (url=? OR url=?)""", (job["id"], alias_channel, canonical, url)):
            # A legacy official alias may store the complete URL as its identifier.
            stored = browser_job_link(job["url"])
            if job["channel"] != alias_channel or stored != (channel, canonical, identifier):
                raise ValueError("公开岗位编号与 Harness 中的岗位不一致")
        return channel, canonical, identifier

    @staticmethod
    def _anchor(c, job, channel, anchor_id, revision=None):
        anchor = one(c, """SELECT m.*,t.external_id AS thread_external_id,t.revision,t.stopped FROM messages m
                     JOIN threads t ON t.id=m.thread_id WHERE m.id=? AND t.job_id=? AND m.channel=? AND m.direction='inbound'""",
                     (anchor_id, job["id"], channel))
        if not anchor or anchor["stopped"] or (revision is not None and anchor["revision"] != revision):
            raise ValueError("会话不匹配、已停止，或回复锚点已变更")
        newest = one(c, "SELECT id FROM messages WHERE thread_id=? ORDER BY occurred_at DESC,id DESC LIMIT 1",
                     (anchor["thread_id"],))
        if newest["id"] != anchor["id"]:
            raise ValueError("已有较新的来信或回复，请先核对原会话")
        if (anchor["classification"] in NON_REPLY or rules.classify(anchor["subject"], anchor["body"]) in NON_REPLY
                or SALES_MESSAGE.search(anchor["body"])):
            raise ValueError("该消息属于销售、系统通知、拒绝联系或敏感请求")
        return anchor

    def _check_policy(self, c, job, channel, kind, action_id=None):
        if job["status"] in rules.RECRUITING_ENDED or manual_followup(c, job["id"]) or one(c, """
                SELECT id FROM history WHERE job_id=? AND status IN ('closed','rejected','stopped')""", (job["id"],)):
            raise ValueError("岗位已关闭、已拒绝、已停止联系或由本人跟进")
        match = self.harness.job_match(c, job, self.harness.preferences(c))
        if kind == "reply":
            if "明确销售或获客" in match["hard"]:
                raise ValueError("销售岗位无需回复")
            return match
        if match["hard"]:
            raise ValueError("岗位不符合硬性条件：" + "、".join(match["hard"]))
        review = one(c, "SELECT state,note FROM job_reviews WHERE job_id=?", (job["id"],))
        if review and (review["state"] == "excluded" or kind in FORMAL and review["state"] == "hold"):
            raise ValueError("须先完成岗位复核：" + review["note"])
        if kind in FORMAL and (not job["description"] or not job["requirements"]):
            raise ValueError("正式投递前请保存完整岗位职责和要求")
        prior = self.harness.duplicate_reason(c, job, platform_channel=channel if kind in FORMAL else "",
                                              browser_work=True)
        if prior:
            raise ValueError(prior)
        if one(c, """SELECT id FROM outbox WHERE job_id=? AND kind='application'
                  AND state IN ('queued','sending','confirmed','unknown')""", (job["id"],)):
            raise ValueError("同一岗位已有邮件申请记录")
        active = one(c, """SELECT id FROM browser_actions WHERE job_id=? AND id<>COALESCE(?,0)
                     AND state IN ('queued','sending','unknown','attachment_requested','confirmed')
                     AND (kind IN ('application','attachment') OR
                          (kind='contact' AND (?='contact' OR state IN ('queued','sending','unknown'))))""",
                     (job["id"], action_id, kind))
        if active:
            raise ValueError("同一岗位已有浏览器动作 #" + str(active["id"]))
        return match

    def queue(self, c, data):
        job_id = int(data.get("job_id") or 0)
        job = one(c, "SELECT * FROM jobs WHERE id=?", (job_id,))
        kind = data.get("kind")
        if not job or kind not in KINDS:
            raise ValueError("请选择已登记岗位及有效浏览器操作")
        body = str(data.get("body") or "").strip()
        if len(body) > 6000 or "\x00" in body or SENSITIVE.search(body) or (kind not in FORMAL and not body):
            raise ValueError("请填写合适的正文，且不包含敏感资料")
        channel, url, _ = self.verify_job_url(c, job, data.get("url") or job["url"])
        match = self._check_policy(c, job, channel, kind)
        anchor_id = int(data["anchor_id"]) if data.get("anchor_id") else None
        if kind == "reply" and not anchor_id:
            raise ValueError("请先登记招聘方最新消息，再选择回复锚点")
        anchor = self._anchor(c, job, channel, anchor_id) if anchor_id else None
        if anchor and one(c, """SELECT id FROM browser_actions WHERE anchor_id=? AND kind=?
                               AND state IN ('queued','sending','unknown','attachment_requested','confirmed')""",
                          (anchor_id, kind)):
            raise ValueError("该招聘方消息已有对应浏览器动作")
        resume = self.harness.select_resume(c, match["scope"]) if kind in FORMAL else None
        if kind in FORMAL and not resume:
            raise ValueError("没有适用于岗位方向的已批准简历")
        ts = stamp()
        action_id = c.execute("""INSERT INTO browser_actions(job_id,channel,url,kind,body,anchor_id,thread_revision,
                              resume_id,attachment_sha,state,created_at,updated_at)
                              VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?)""",
                              (job_id, channel, url, kind, body, anchor_id, anchor["revision"] if anchor else None,
                               resume["id"] if resume else None, resume["sha256"] if resume else None, ts, ts)).lastrowid
        self.harness.event(c, f"浏览器动作 #{action_id} 已排队，等待已登录浏览器核验")
        return action_id

    def _check(self, c, action):
        channel = action["channel"]
        channel_status = one(c, "SELECT paused FROM channels WHERE name=?",
                             ("official" if channel == "zhaopin" else channel,))
        if not self.harness.outbound_enabled(c) or channel_status and channel_status["paused"]:
            raise ValueError("外发或此渠道已暂停")
        job = one(c, "SELECT * FROM jobs WHERE id=?", (action["job_id"],))
        self.verify_job_url(c, job, action["url"])
        match = self._check_policy(c, job, channel, action["kind"], action["id"])
        if action["anchor_id"]:
            self._anchor(c, job, channel, action["anchor_id"], action["thread_revision"])
            if one(c, """SELECT id FROM browser_actions WHERE anchor_id=? AND id<>? AND kind=?
                         AND state IN ('queued','sending','unknown','attachment_requested','confirmed')""",
                   (action["anchor_id"], action["id"], action["kind"])):
                raise ValueError("该招聘方消息已有对应浏览器动作")
        if not action["resume_id"]:
            return None
        resume = self.harness.select_resume(c, match["scope"])
        if not resume or resume["id"] != action["resume_id"] or resume["sha256"] != action["attachment_sha"]:
            raise ValueError("获准使用的简历已经变更，请先核对版本")
        path = Path(resume["path"])
        try:
            valid = (path.suffix.lower() == ".pdf" and path.stat().st_size <= 20_000_000
                     and hashlib.sha256(path.read_bytes()).hexdigest() == resume["sha256"])
        except OSError:
            valid = False
        if not valid:
            raise ValueError("已批准简历无法读取或文件校验值不一致")
        return {"path": str(path), "sha256": resume["sha256"], "label": resume["label"]}

    def claim(self, action_id, retry_evidence=None):
        self.reap()
        with self.harness.store.tx(True) as c:
            action = one(c, "SELECT * FROM browser_actions WHERE id=?", (action_id,))
            if not action or action["state"] not in {"queued", "blocked", "attachment_requested"}:
                raise ValueError("该动作不可认领。结果不明须先核对原平台")
            if action["state"] != "queued":
                retry_evidence = str(retry_evidence or "").strip()
                if len(retry_evidence) < 8 or len(retry_evidence) > 500 or PRIVATE_EVIDENCE.search(retry_evidence):
                    raise ValueError("再次认领须记录原平台显示上次未正式提交的证据")
            if one(c, "SELECT id FROM browser_actions WHERE state='sending'"):
                raise ValueError("已有一个前台浏览器动作在执行")
            attachment = self._check(c, action)
            token = secrets.token_urlsafe(24)
            claimed_at, claim_until = stamp(), shift(LEASE_SECONDS)
            c.execute("""UPDATE browser_actions SET state='sending',claim_token=?,claimed_at=?,claim_until=?,updated_at=?
                      WHERE id=?""", (token, claimed_at, claim_until, claimed_at, action_id))
            note = f"浏览器动作 #{action_id} 已认领，提交前须核对原平台"
            if retry_evidence:
                note += "。复核依据 " + str(retry_evidence).strip()[:500]
            self.harness.event(c, note)
            job = one(c, "SELECT company,title FROM jobs WHERE id=?", (action["job_id"],))
            return {**job, **{k: action[k] for k in ("id", "job_id", "channel", "url", "kind", "body", "anchor_id")},
                    "claim_token": token, "claim_until": claim_until, "attachment": attachment}

    def check(self, action_id, token):
        self.reap()
        with self.harness.store.tx() as c:
            action = one(c, "SELECT * FROM browser_actions WHERE id=?", (action_id,))
            if not action or action["state"] != "sending" or not self._token_matches(action, token):
                raise ValueError("认领已失效或不是当前操作者，请先核对原平台")
            self._check(c, action)

    @staticmethod
    def _token_matches(action, token):
        return bool(token and action["claim_token"] and secrets.compare_digest(action["claim_token"], token))

    def finish(self, action_id, state, evidence, receipt=None, token=None, reconcile=False):
        evidence = str(evidence or "").strip()
        receipt = str(receipt or "").strip() or None
        if state not in RESULTS or len(evidence) < 8 or len(evidence) > 2000 or PRIVATE_EVIDENCE.search(evidence):
            raise ValueError("请填写可核对的实际页面结果，且不含敏感资料")
        if receipt and not re.fullmatch(r"[\w.-]{1,128}", receipt):
            raise ValueError("回执仅支持公开编号")
        self.reap()
        with self.harness.store.tx(True) as c:
            action = one(c, "SELECT * FROM browser_actions WHERE id=?", (action_id,))
            if not action or action["state"] == "confirmed":
                raise ValueError("该动作不存在或已经确认")
            if reconcile and (action["state"] not in {"unknown", "attachment_requested"} or state not in {"confirmed", "blocked"}):
                raise ValueError("仅可核对结果不明或附件待确认的动作，原平台须明确已提交或未提交")
            if (not reconcile and action["state"] in {"sending", "unknown"}
                    and not self._token_matches(action, token)):
                raise ValueError("请使用本次认领令牌核对结果")
            if action["state"] in {"queued", "blocked"} and state != "blocked":
                raise ValueError("须先认领再登记实际发送结果")
            if state == "attachment_requested" and action["kind"] not in FORMAL:
                raise ValueError("仅申请或简历附件可以等待平台确认")
            c.execute("""UPDATE browser_actions SET state=?,evidence=?,receipt=?,updated_at=? WHERE id=?""",
                      (state, evidence, receipt, stamp(), action_id))
            if state == "confirmed":
                self._record_confirmation(c, action, evidence, receipt)
            self.harness.event(c, f"浏览器动作 #{action_id} · {state} · {evidence}",
                               level="warning" if state in {"blocked", "unknown"} else "info")

    def _record_confirmation(self, c, action, evidence, receipt):
        job_id, channel, kind = action["job_id"], action["channel"], action["kind"]
        job = one(c, "SELECT * FROM jobs WHERE id=?", (job_id,))
        fingerprint = "browser-action:" + str(action["id"])
        if kind != "reply":
            state = "contacted" if kind == "contact" else "confirmed"
            history_evidence = evidence + ("。平台回执 " + receipt if receipt else "")
            c.execute("""INSERT OR IGNORE INTO history(job_id,channel,company,title,status,source,evidence,occurred_at,fingerprint)
                      VALUES(?,?,?,?,?,'Harness 浏览器',?,?,?)""",
                      (job_id, channel, job["company"], job["title"], state, history_evidence, stamp(), fingerprint))
        if kind not in {"contact", "reply", "attachment"}:
            return
        body = action["body"]
        if kind == "attachment":
            resume = one(c, "SELECT label FROM resumes WHERE id=?", (action["resume_id"],))
            body = "［平台简历附件］" + resume["label"]
        chat = normalize_platform_message({"channel": channel, "job_id": job_id, "url": action["url"],
                                          "direction": "outbound", "occurred_at": stamp(), "body": body})
        if action["anchor_id"]:
            anchor = one(c, """SELECT t.external_id FROM threads t JOIN messages m ON m.thread_id=t.id WHERE m.id=?""",
                         (action["anchor_id"],))
            chat["thread_external_id"] = anchor["external_id"]
        if self.harness.ingest_message(c, {**chat, "company": job["company"], "external_id": fingerprint, "_history": True}):
            message = one(c, "SELECT id FROM messages WHERE channel=? AND external_id=?", (channel, fingerprint))
            c.execute("INSERT INTO platform_message_sources VALUES(?,?,?,?)",
                      (message["id"], chat["source_url"], chat["platform_job_id"], stamp()))
