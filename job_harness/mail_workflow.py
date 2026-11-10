"""Reconcile inbox snapshots with the application ledger before deciding what may be sent."""
import json
import re

from . import rules
from .storage import fetch, manual_followup, now, one, shift, stamp


def new_text(body):
    return re.split(r"(?im)^(?:\s*>+|On .+ wrote:|[-_ ]{3,}.*(?:Original Message|原始邮件)|发件人[：:])",
                    body or "", maxsplit=1)[0].strip()


def classify(subject, body, headers=None):
    headers = headers or {}
    text = new_text(body)
    delivery = headers.get("delivery_status") or ""
    if re.search(r"重置.*密码|密码.*重置|password reset|验证码|新设备登录|登录验证码", subject or "", re.I):
        return "unrelated"
    if re.search(r"(?:招聘会|博览会|招聘专场|双选会|人才招聘会).{0,12}(邀请|报名)|邀请函.{0,12}(?:招聘会|博览会)", subject or ""):
        return "promotion"
    if delivery.startswith(("delayed ", "failed ")):
        return "delivery_delay" if delivery.startswith("delayed ") else "delivery_failed"
    if re.search(r"已关闭|招聘已结束|不再继续.{0,8}(招聘|填补)|position.{0,45}(?:closed|filled|cancelled)|no longer.{0,65}(?:position|role|recruit)|not be filling (?:this |the )?position", text, re.I):
        return "closed"
    sender = headers.get("sender") or ""
    if (re.search(r"^(?:postmaster|mailer-daemon)@", sender, re.I)
            and re.search(r"^\s*(?:系统退信|退信|Undelivered|Delivery Status Notification)", subject or "", re.I)):
        return "delivery_delay" if re.search(r"延迟|连接超时|timeout|temporar|4\.4\.", text, re.I) else "delivery_failed"
    if str(headers.get("auto_submitted") or "").lower() not in {"", "no"}:
        return "automatic"
    if re.search(r"(?:添加|通过|查收|接受).{0,18}(?:微信|好友申请)|(?:微信|好友).{0,22}(?:通过|添加|请求|验证|申请)", text):
        return "manual_channel_pending"
    kind = rules.classify(re.sub(r"(?i)^(?:re|fw|fwd)\s*:\s*.*$", "", subject or ""), text)
    if kind in {"resume", "portfolio"} and not rules.explicit_request(text, kind):
        return "acknowledged" if re.search(r"收到|感谢|谢谢|thanks|received", text, re.I) else "review"
    return {"auto": "automatic", "bounce": "review"}.get(kind, kind)


def persist_metadata(c, message, data, historical, normalize_when):
    c.execute("""INSERT INTO mail_metadata(message_id,recipient,reply_to,in_reply_to,mail_date,auto_submitted,delivery_status,
                   reply_to_unverified,truncated,sender_authenticated,baseline,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(message_id) DO UPDATE SET recipient=excluded.recipient,reply_to=excluded.reply_to,
                   in_reply_to=excluded.in_reply_to,mail_date=excluded.mail_date,auto_submitted=excluded.auto_submitted,
                   delivery_status=excluded.delivery_status,reply_to_unverified=excluded.reply_to_unverified,
                   truncated=excluded.truncated,sender_authenticated=excluded.sender_authenticated,observed_at=excluded.observed_at""",
              (message["id"], rules.address(data.get("recipient")), rules.address(data.get("reply_to")),
               data.get("in_reply_to"), normalize_when(data.get("mail_date")), str(data.get("auto_submitted") or "")[:32],
               str(data.get("delivery_status") or "")[:100], bool(data.get("reply_to_unverified")),
               bool(data.get("possibly_truncated")), bool(data.get("sender_authenticated")), historical, stamp()))


def audit(c, row, job_id, history_id, state, evidence):
    c.execute("""INSERT INTO mail_audits(message_id,job_id,history_id,state,evidence,updated_at) VALUES(?,?,?,?,?,?)
                 ON CONFLICT(message_id) DO UPDATE SET job_id=excluded.job_id,history_id=excluded.history_id,
                    state=excluded.state,evidence=excluded.evidence,updated_at=excluded.updated_at
                 WHERE mail_audits.job_id IS NOT excluded.job_id OR mail_audits.history_id IS NOT excluded.history_id
                    OR mail_audits.state<>excluded.state OR mail_audits.evidence<>excluded.evidence""",
              (row["id"], job_id, history_id, state, evidence, stamp()))


def _link(c, row, job_id, company=None):
    if job_id:
        job = one(c, "SELECT company FROM jobs WHERE id=?", (job_id,))
        if job:
            company = job["company"]
    if job_id or company:
        c.execute("UPDATE threads SET job_id=COALESCE(?,job_id),company=COALESCE(?,company) WHERE id=?",
                  (job_id, company, row["thread_id"]))


def _history_match(row, history):
    # A sent message may be an existing inquiry or a completed application. No row is created by inference.
    def aliases(record):
        return {rules.title_key(x) for x in re.split(r"[/／｜|]", record["title"] or "") if len(rules.title_key(x)) >= 5}

    subject = rules.norm(row["subject"])
    to = row["recipient"]
    exact = [h for h in history if len(subject) >= 12 and subject in rules.norm(h["evidence"])]
    if len({h["job_id"] for h in exact}) == 1 and exact:
        record = next((h for h in exact if h["recipient"] == to and to), exact[0])
        return record, "已发送主题与历史台账原始主题吻合"
    by_address = [h for h in history if to and rules.address(h["recipient"]) == to and
                  (any(part in subject for part in aliases(h)) or h["job_id"] == row["thread_job"])]
    if len({h["job_id"] for h in by_address}) == 1 and by_address:
        return by_address[0], "已发送收件人与岗位标题或已有会话锚点吻合"
    if row["thread_job"]:
        previous = [h for h in history if h["job_id"] == row["thread_job"]]
        if previous:
            return previous[0], "保留原同步中已由主题与收件人唯一关联的历史投递"
    return None, ""


def _age_holds(row):
    dates = [row["occurred_at"], row["mail_date"]]
    return (not all(dates) or min(dates) < shift(-72 * 3600) or max(dates) > shift(600))


def _put_decision(c, row, kind, question, draft="", due=None):
    c.execute("""INSERT OR IGNORE INTO decisions(thread_id,message_id,kind,question,draft,state,due_at,created_at,updated_at)
                 VALUES(?,?,?,?,?,'pending',?,?,?)""",
              (row["thread_id"], row["id"], kind, question, draft, due, stamp(), stamp()))


def _suppress(c, row, explanation):
    c.execute("""UPDATE outbox SET state='stale',evidence=?,updated_at=? WHERE anchor_id=? AND state IN ('draft','queued')""",
              (explanation, stamp(), row["id"]))
    c.execute("""UPDATE decisions SET state='resolved',resolution=?,updated_at=? WHERE message_id=?
                 AND state='pending' AND kind NOT IN ('manual_channel_pending','delivery_failed','delivery_delay')""",
              (explanation, stamp(), row["id"]))


def reconcile(harness, c):
    """Apply idempotent classifications after the inbox and Sent have both been read."""
    rows = fetch(c, """SELECT m.*,t.job_id thread_job,t.company thread_company,md.recipient,md.reply_to,md.in_reply_to,
                       md.mail_date,md.auto_submitted,md.delivery_status,md.reply_to_unverified,md.truncated,md.baseline,md.sender_authenticated,
                       a.state previous_state FROM messages m JOIN threads t ON t.id=m.thread_id
                       LEFT JOIN mail_metadata md ON md.message_id=m.id LEFT JOIN mail_audits a ON a.message_id=m.id
                       WHERE m.channel='email' ORDER BY m.occurred_at,m.id""")
    pins = {r["external_id"]: r for r in fetch(c, "SELECT * FROM mail_baseline_evidence")}
    history = fetch(c, """SELECT * FROM history WHERE channel='email' AND job_id IS NOT NULL
                       AND status IN ('confirmed','contacted','inquiry','pending','unknown')
                       ORDER BY (status='confirmed') DESC,id""")
    outbox = {r["message_id"]: r for r in fetch(c, "SELECT * FROM outbox WHERE channel='email'")}
    own = rules.address(harness.config.get("email", {}).get("account"))
    sent_by_id, sent_by_thread, incoming = {}, {}, []
    counts = {}

    def add(state):
        counts[state] = counts.get(state, 0) + 1

    for row in rows:
        if row["direction"] != "outbound":
            incoming.append(row)
            continue
        pin = pins.get(row["external_id"])
        actual = outbox.get(row["message_id"])
        h, reason = _history_match(row, history) if not pin and not actual else (None, "")
        evidence = pin or actual or h
        job_id = evidence["job_id"] if evidence else row["thread_job"]
        history_id = pin["history_id"] if pin else h["id"] if h else None
        if pin:
            reason = pin["evidence"]
        if actual:
            reason = "Harness SMTP／IMAP 发送记录的 Message-ID 吻合"
        state = "historical_sent" if job_id else "sent_unlinked"
        _link(c, row, job_id)
        audit(c, row, job_id, history_id, state, reason or "已发送目录存在邮件，缺少唯一的既有申请关联")
        add(state)
        row["linked_job"], row["linked_history"] = job_id, history_id
        if row["message_id"]:
            sent_by_id[row["message_id"]] = row
        sent_by_thread.setdefault(row["thread_id"], []).append(row)
        # A confirmed historical address only establishes continuity for that exact previous role.
        verified = h or (next((entry for entry in history if entry["id"] == history_id), None) if pin else None)
        if own and row["sender"] == own and row["recipient"] and job_id and verified and verified["status"] == "confirmed" and (
                rules.address(verified["recipient"]) == row["recipient"]):
            existing_contact = one(c, "SELECT contact_id FROM job_contacts WHERE job_id=?", (job_id,))
            if not existing_contact:
                harness.authorize_recipient(c, job_id, row["recipient"], "网易已发送证据与历史台账收件人一致")

    latest_substantive = {}
    for row in incoming:
        pin = pins.get(row["external_id"])
        actual_kind = classify(row["subject"], row["body"], row)
        row["kind"] = (pin["state"] if pin and pin["state"] and not (
            pin["state"] == "delivery_failed" and actual_kind == "delivery_delay") else actual_kind)
        if row["kind"] not in {"unrelated", "promotion", "automatic", "delivery_failed", "delivery_delay"}:
            latest_substantive[row["thread_id"]] = row["id"]

    for row in incoming:
        pin = pins.get(row["external_id"])
        refs = json.loads(row["refs"] or "[]")
        direct = row["in_reply_to"] or (refs[-1] if refs else None)
        prior = sent_by_id.get(direct)
        if not prior:
            prior = next((sent_by_id[ref] for ref in reversed(refs) if ref in sent_by_id), None)
        linked_job = pin["job_id"] if pin else prior["linked_job"] if prior else row["thread_job"]
        history_id = pin["history_id"] if pin else prior["linked_history"] if prior else None
        kind = row["kind"]
        actual = outbox.get(direct)
        if actual and actual["job_id"]:
            linked_job = actual["job_id"]
        _link(c, row, linked_job, pin["company"] if pin else None)
        c.execute("UPDATE messages SET classification=? WHERE id=? AND classification<>?", (kind, row["id"], kind))
        evidence = (pin["evidence"] if pin else "原信 Message-ID 在已发送目录中吻合" if prior
                    else "按入站邮件的来源、主题和最新正文分流")
        if kind in {"unrelated", "promotion", "automatic", "acknowledged"}:
            state = kind
        elif kind in {"delivery_failed", "delivery_delay"}:
            state = kind
            if actual:
                marker = "；后续退信：投递失败" if kind == "delivery_failed" else "；后续服务器回执：投递延迟"
                c.execute("""UPDATE outbox SET evidence=COALESCE(evidence,'') || ?,updated_at=? WHERE id=?
                             AND instr(COALESCE(evidence,''),?)=0""", (marker, stamp(), actual["id"], marker))
            if row["baseline"] == 0 and linked_job:
                _put_decision(c, row, kind, "核对退信或延迟，避免重复投递")
        elif kind in {"closed", "reject", "stop"}:
            state = kind
            if linked_job and (pin or harness.trusted_thread(c, {"job_id": linked_job}, row["sender"])):
                c.execute("UPDATE threads SET stopped=1 WHERE id=?", (row["thread_id"],))
                if kind != "stop":
                    c.execute("UPDATE jobs SET status='closed' WHERE id=?", (linked_job,))
                job = one(c, "SELECT company,title FROM jobs WHERE id=?", (linked_job,))
                c.execute("""INSERT OR IGNORE INTO history(job_id,channel,company,title,status,source,evidence,occurred_at,fingerprint)
                             VALUES(?,'email',?,?,?,?,?,?,?)""",
                          (linked_job, job["company"], job["title"], {"stop": "stopped", "reject": "rejected", "closed": "closed"}[kind],
                           "招聘方消息", row["subject"], row["occurred_at"], "mail-disposition:" + row["external_id"]))
        else:
            already = any(row["message_id"] and row["message_id"] in json.loads(s["refs"] or "[]") and
                          s["occurred_at"] >= row["occurred_at"] and s["recipient"] == row["sender"]
                          for s in sent_by_thread.get(row["thread_id"], []))
            due = harness.extract_deadline(row["body"]) if kind in {"assignment", "interview", "offer"} else None
            if already:
                state, evidence = "handled", "已发送目录有较晚且回复同一来信 Message-ID 的邮件"
                _suppress(c, row, evidence)
            elif hold := manual_followup(c, linked_job):
                state, evidence = "manual_followup", hold["evidence"]
                _suppress_outbox_only(c, row, "公司外联由本人跟进")
                if (row["baseline"] == 0 and not _age_holds(row) and latest_substantive.get(row["thread_id"]) == row["id"]
                        and not (due and due < stamp())):
                    _put_decision(c, row, kind, "公司已建立联系，后续消息由本人处理。", due=due)
            elif kind == "manual_channel_pending" and latest_substantive.get(row["thread_id"]) == row["id"]:
                state = kind
                decision = one(c, "SELECT resolution FROM decisions WHERE message_id=? AND kind=? AND state='resolved'",
                               (row["id"], kind))
                if decision:
                    state, evidence = "handled", decision["resolution"] or "已由本人处理"
                else:
                    _put_decision(c, row, kind, "招聘方请通过既有微信好友申请。由本人确认是否已处理。")
            elif latest_substantive.get(row["thread_id"]) != row["id"]:
                state = "superseded"
                _suppress(c, row, "同会话已有更新的招聘来信")
            elif due and due < stamp():
                state = "expired"
                _suppress(c, row, "来信中的明确截止时间已过")
            elif row["baseline"] != 0 or _age_holds(row):
                state = "historical_review"
                if kind in {"interview", "assignment", "compensation", "availability", "offer", "sensitive"}:
                    _put_decision(c, row, kind, "历史招聘来信。核对是否仍有效后再决定。", due=due)
                _suppress_outbox_only(c, row, "基线或过期来信不自动回复")
            elif row["previous_state"] in {"reply_queued", "reply_draft", "decision_pending"}:
                queued = one(c, "SELECT state FROM outbox WHERE anchor_id=? AND kind='reply' ORDER BY id DESC LIMIT 1", (row["id"],))
                state = "handled" if queued and queued["state"] == "confirmed" else row["previous_state"]
            else:
                data = {"reply_to": row["reply_to"], "reply_to_unverified": row["reply_to_unverified"] or row["recipient"] != own
                        or not row["sender_authenticated"],
                        "possibly_truncated": row["truncated"]}
                thread = one(c, "SELECT * FROM threads WHERE id=?", (row["thread_id"],))
                harness.process_inbound(c, thread, row["id"], "email", row["external_id"], row["message_id"],
                                        row["sender"], row["subject"], new_text(row["body"]), refs, row["occurred_at"], kind, data)
                queued = one(c, "SELECT state FROM outbox WHERE anchor_id=? AND kind='reply' LIMIT 1", (row["id"],))
                state = ("reply_queued" if queued["state"] == "queued" else "reply_draft") if queued else "decision_pending"
        if kind in {"manual_channel_pending", "closed", "reject", "stop", "delivery_failed", "delivery_delay"} and not row["previous_state"]:
            _suppress_outbox_only(c, row, "已分流为非自动回复")
        audit(c, row, linked_job, history_id, state, evidence)
        add(state)
    return counts


def _suppress_outbox_only(c, row, reason):
    c.execute("UPDATE outbox SET state='stale',evidence=?,updated_at=? WHERE anchor_id=? AND state IN ('draft','queued')",
              (reason, stamp(), row["id"]))
