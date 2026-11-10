"""Durable job workflow. All outbound actions pass the same transactional gate."""
import csv
import hashlib
import io
import json
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import channels, mail_workflow, rules
from .browser_actions import BrowserActions
from .storage import FOLLOWUP_EMPLOYER_MATCH, TZ, fetch, manual_followup, now, one, shift, stamp

CHANNELS = ("official", "boss", "liepin", "yupao", "email")
DEFAULTS = {
    "reply_minutes": 30, "scan_times": "10:00,15:30", "send_start": "09:30", "send_end": "18:30",
    "daily_limit": 10, "follow_up_business_days": 5, "auto_send": False, "cities": "",
    "directions": "", "salary_floor": "",
    "target_scope": "", "email_only_new": True, "allow_follow_up": False,
    "allow_remote": False, "job_expiry_days": 3, "allow_unknown_recruiting": False, "send_anytime": False
}
STATE_VALUES = {"confirmed", "failed", "unknown"}
REVIEW_KINDS = {
    "sensitive": "异常或敏感资料请求", "offer": "Offer／合同", "interview": "面试／改期",
    "assignment": "作业／笔试", "compensation": "薪资", "availability": "地点、到岗或出差",
    "experience": "经历问题需核实", "review": "招聘消息需判断", "resume": "附件请求待确认",
    "portfolio": "作品请求待确认",
}
DEFAULT_CHANNEL_DETAIL = {
    "official": "公开招聘页；仅发现岗位，附件邮件由网易适配器独立发送",
    "boss": "仅前台人工接管；后台没有站内私聊接口",
    "liepin": "候选人官方搜索需单独授权；站内聊天与指定 PDF 投递由浏览器接管",
    "yupao": "仅前台人工接管；后台没有站内私聊接口",
    "email": "126 网页登录与协议授权分开核验",
}

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def setting(c, key, default=None):
    row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default

def set_setting(c, key, value):
    c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, dump(value)))

def evidence_text(value):
    if isinstance(value, (dict, list)):
        return dump(value)[:5000]
    return str(value or "")[:5000]

def fingerprint(*parts):
    return hashlib.sha256(dump(parts).encode()).hexdigest()

def normalize_when(value):
    if not value:
        return None
    try:
        value = str(value)
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)).isoformat()
    except ValueError:
        return None

def job_link(link):
    link = str(link or "").strip()
    parsed = urlsplit(link)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("来源必须是可核验的 HTTPS 页面")
    return link

MANUAL_FOLLOWUP = "已建立联系，人工跟进"
RECRUITING_QUESTION = "想请问贵司目前是否还有这个岗位或相关机会。"
RECRUITING_QUESTION_REQUIRED = "当前在招状态未确认，申请正文需询问是否还有相关机会"


class Harness:
    def __init__(self, store, config, adapters, model=None, notifier=None):
        self.store, self.config, self.adapters, self.model = store, config, adapters, model
        self.notifier = notifier
        self.wake = threading.Event()
        self.shutdown = threading.Event()
        self.last_heartbeat = None
        self.browser_actions = BrowserActions(self)
        with self.store.tx(True) as c:
            for name in CHANNELS:
                status = "ready" if name == "official" else "unverified" if name == "email" else "manual_handoff"
                c.execute("INSERT OR IGNORE INTO channels(name,status,detail,updated_at) VALUES(?,?,?,?)",
                          (name, status, DEFAULT_CHANNEL_DETAIL[name], stamp()))

    def preferences(self, c):
        return {**DEFAULTS, **setting(c, "preferences", {})}

    def event(self, c, message, channel=None, level="info", key=None):
        c.execute("INSERT OR IGNORE INTO events(created_at,level,channel,message,event_key) VALUES(?,?,?,?,?)",
                  (stamp(), level, channel, str(message)[:1000], key))

    def enqueue(self, c, kind, channel, key, due=None, payload=None):
        c.execute("INSERT OR IGNORE INTO tasks(kind,channel,dedupe_key,payload,state,due_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (kind, channel, key, dump(payload or {}), "queued", due or stamp(), stamp()))

    def step(self, c, task_id, stage, outcome, detail, job_id=None, outbox_id=None):
        if task_id:
            c.execute("""INSERT INTO task_steps(task_id,stage,outcome,detail,job_id,outbox_id,at)
                         VALUES(?,?,?,?,?,?,?)""", (task_id, stage, outcome, evidence_text(detail), job_id, outbox_id, stamp()))

    def stage_mail_evidence(self, manifest):
        records = manifest.get("records", [])
        if not isinstance(records, list) or len(records) > 200:
            raise ValueError("基线证据格式无效")
        with self.store.tx(True) as c:
            for row in records:
                message = one(c, "SELECT message_id,direction FROM messages WHERE channel='email' AND external_id=?", (row["external_id"],))
                linked = one(c, "SELECT job_id,company FROM history WHERE id=?", (row["history_id"],))
                disposition = row["disposition"]
                if (not message or message["message_id"] != row.get("message_id") or not linked
                        or linked["job_id"] != row["job_id"]
                        or disposition not in {"historical_sent", "delivery_failed", "delivery_delay", "manual_channel_pending"}
                        or (disposition == "historical_sent") != (message["direction"] == "outbound")):
                    raise ValueError("基线邮件标识、方向或台账关联不一致")
                c.execute("""INSERT INTO mail_baseline_evidence VALUES(?,?,?,?,?,?)
                             ON CONFLICT(external_id) DO UPDATE SET job_id=excluded.job_id,history_id=excluded.history_id,
                             state=excluded.state,company=excluded.company,evidence=excluded.evidence""",
                          (row["external_id"], row["job_id"], row["history_id"], disposition,
                           linked["company"], str(row["evidence"])[:500]))
            self.enqueue(c, "mail_audit", "email", "mail-audit:" + fingerprint(records))
        self.wake.set()
        return len(records)

    def recover(self):
        with self.store.tx(True) as c:
            for row in fetch(c, "SELECT id,channel FROM outbox WHERE state='sending'"):
                c.execute("UPDATE outbox SET state='unknown',updated_at=?,evidence=? WHERE id=?",
                          (stamp(), "服务重启前未记账；先核验，不重发", row["id"]))
                self.enqueue(c, "reconcile", row["channel"], "recover:" + str(row["id"]), payload={"id": row["id"]})
            c.execute("""UPDATE tasks SET state='stopped',cancelled=1,detail='旧周期已过，等待下一轮'
                WHERE state='queued' AND kind IN ('replies','scan') AND due_at<?""", (shift(-24 * 3600),))
            c.execute("UPDATE tasks SET state=CASE WHEN cancelled=1 THEN 'stopped' ELSE 'queued' END,updated_at=? WHERE state='running'", (stamp(),))
            c.execute("UPDATE model_runs SET state='failed',detail='进程退出，模型调用结果未记账' WHERE state='running'")
            self.event(c, "后台启动，恢复任务与未核实发送")
            self.enqueue(c, "probe", "email", "probe:email:" + now().strftime("%Y%m%d%H"))
            if not one(c, "SELECT id FROM tasks WHERE dedupe_key LIKE 'bootstrap:%'"):
                self.enqueue(c, "scan", "official", "bootstrap:first")
            if self.adapters.get("liepin") and not one(c, "SELECT id FROM tasks WHERE dedupe_key='liepin:official-connectivity'"):
                self.enqueue(c, "probe", "liepin", "liepin:official-connectivity")

    def employer(self, c, company, channel, domain=None, employer_external_id=None, source=None):
        key = rules.norm(company)
        domain = rules.company_domain(domain)
        if not domain and employer_external_id:
            ident = "channel:" + channel + ":" + str(employer_external_id)
        elif domain:
            ident = "domain:" + domain + ":" + key
        else:
            ident = "unresolved:" + fingerprint(channel, key, source or "")
        c.execute("INSERT OR IGNORE INTO employers(name,name_key,domain,identity) VALUES(?,?,?,?)", (company, key, domain, ident))
        return c.execute("SELECT id FROM employers WHERE identity=?", (ident,)).fetchone()[0]

    def upsert_job(self, c, data):
        channel = str(data.get("channel") or "official")
        url = str(data.get("url") or "")
        external_id = str(data.get("external_id") or url or fingerprint(data.get("company"), data.get("title"), data.get("location")))
        company = str(data.get("company") or "未核公司").strip()[:160]
        title = str(data.get("title") or "未核岗位").strip()[:180]
        alias = one(c, "SELECT job_id FROM job_aliases WHERE channel=? AND external_id=?", (channel, external_id))
        domain = rules.company_domain(data.get("domain") or (rules.host(url) if channel == "official" else ""))
        eid = self.employer(c, company, channel, domain, data.get("employer_external_id"), data.get("employer_source") or url)
        tk = rules.title_key(title)
        location = str(data.get("location") or "").strip()[:180]
        desc = str(data.get("description") or "")[:24000]
        req = str(data.get("requirements") or "")[:24000]
        status = str(data.get("status") or "unknown")
        jd = {"title": title, "company": company, "location": location, "salary": data.get("salary"),
              "description": desc, "requirements": req, "status": status, "published_at": data.get("published_at")}
        assessment = rules.assess(jd, self.preferences(c), [r["pattern"] for r in fetch(c, "SELECT pattern FROM exclusions")])
        match_json = dump(assessment)
        jid = alias["job_id"] if alias else None
        if not jid and domain and location:
            for candidate in fetch(c, "SELECT * FROM jobs WHERE employer_id=? AND title_key=?", (eid, tk)):
                left = rules.norm(location)
                right = rules.norm(candidate["location"])
                same_place = left == right or (len(min(left, right, key=len)) >= 2 and (left in right or right in left))
                if same_place and rules.similar(rules.norm(desc + req), rules.norm(candidate["description"] + candidate["requirements"])) >= .42:
                    jid = candidate["id"]
                    break
        if jid:
            if not data.get("_history"):
                old = one(c, "SELECT * FROM jobs WHERE id=?", (jid,))
                status_regression = old["status"] in rules.RECRUITING_ENDED and status not in rules.RECRUITING_ENDED
                partial_regression = status == "partial" and old["description"]
                if not (status_regression or partial_regression) and (len(desc + req) or status in rules.RECRUITING_ENDED):
                    c.execute("""UPDATE jobs SET location=COALESCE(NULLIF(?,''),location),
                         salary=COALESCE(NULLIF(?,''),salary),description=COALESCE(NULLIF(?,''),description),
                         requirements=COALESCE(NULLIF(?,''),requirements),status=?,published_at=COALESCE(?,published_at),
                         observed_at=?,match_json=?,match_score=? WHERE id=?""",
                         (location, data.get("salary"), desc, req, status, data.get("published_at"), stamp(),
                          match_json, assessment["score"], jid))
                    if old["status"] == "open" and (rules.norm(old["description"]) != rules.norm(desc)
                            or rules.norm(old["requirements"]) != rules.norm(req) or rules.norm(old["location"]) != rules.norm(location or old["location"])):
                        c.execute("""UPDATE outbox SET state='stale',evidence='公开 JD 有变更，须重新核对申请',updated_at=?
                                     WHERE job_id=? AND kind='application' AND state IN ('draft','queued')""", (stamp(), jid))
        else:
            jid = c.execute("""INSERT INTO jobs(employer_id,company,title,title_key,location,salary,description,requirements,
                   channel,url,domain,status,published_at,observed_at,created_at,match_json,match_score)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (eid, company, title, tk, location, data.get("salary"), desc, req, channel, url, domain, status,
                    data.get("published_at"), stamp(), stamp(), match_json, assessment["score"])).lastrowid
        c.execute("INSERT OR IGNORE INTO job_aliases VALUES(?,?,?,?)", (channel, external_id, jid, url))
        recipient_source = str(data.get("recipient_source") or "").strip()
        if channel == "official" and recipient_source and data.get("recipient") and company != "未核公司":
            self.authorize_recipient(c, jid, data["recipient"], recipient_source)
        if status in rules.RECRUITING_ENDED:
            c.execute("UPDATE outbox SET state='stale',updated_at=?,evidence='岗位关闭或已停止联系' WHERE job_id=? AND state IN ('draft','queued')",
                      (stamp(), jid))
        return jid

    def authorize_recipient(self, c, job_id, recipient, source):
        recipient = rules.address(recipient)
        if not recipient or not source:
            raise ValueError("需要有效邮箱和明确的招聘证据")
        job = one(c, "SELECT employer_id,company FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise ValueError("岗位不存在")
        c.execute("INSERT OR IGNORE INTO contacts(employer_id,channel,address,source,verified_at) VALUES(?,?,?,?,?)",
                  (job["employer_id"], "email", recipient, source[:1200], stamp()))
        contact = c.execute("SELECT id FROM contacts WHERE employer_id=? AND channel='email' AND address=?",
                            (job["employer_id"], recipient)).fetchone()[0]
        c.execute("INSERT INTO job_contacts VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET contact_id=excluded.contact_id",
                  (job_id, contact))

    def ingest_legacy(self, data):
        counts = {}
        with self.store.tx(True) as c:
            if not setting(c, "profile_initialized"):
                set_setting(c, "preferences", data.get("preferences", {}))
                set_setting(c, "profile_initialized", True)
            for row in data.get("facts", []):
                val = row["value"] if isinstance(row["value"], str) else dump(row["value"])
                c.execute("INSERT OR IGNORE INTO facts(key,value,source,updated_at,state) VALUES(?,?,?,?,?)",
                          (row["key"], val, row["source"], row.get("updated_at") or stamp(), row.get("state") or "verified"))
            for row in data.get("resumes", []):
                scopes = ",".join(row["scope"]) if isinstance(row["scope"], list) else row["scope"]
                c.execute("INSERT OR IGNORE INTO resumes(label,path,sha256,modified_at,scope,approved) VALUES(?,?,?,?,?,?)",
                          (row["label"], row["path"], row["sha256"], row.get("modified_at"), scopes, bool(row.get("approved"))))
            for row in data.get("sources", []):
                self.add_source(c, row)
            for row in data.get("history", []):
                company, title, channel = row.get("company"), row.get("title"), row.get("channel", "official")
                if not company or not title:
                    continue
                sig = fingerprint(channel, company, title, row.get("url"), row.get("external_id"), row.get("status"), row.get("source"))
                if one(c, "SELECT id FROM history WHERE fingerprint=?", (sig,)):
                    continue
                job_id = self.upsert_job(c, {**row, "status": "unknown", "_history": True})
                c.execute("""INSERT INTO history(job_id,channel,company,title,status,source,evidence,occurred_at,recipient,fingerprint)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""", (job_id, channel, company, title, row.get("status", "review"),
                   row.get("source") or "历史台账", evidence_text(row.get("evidence")), row.get("occurred_at"),
                   rules.address(row.get("recipient")), sig))
            for row in data.get("messages", []):
                alias = one(c, "SELECT job_id FROM job_aliases WHERE channel=? AND (external_id=? OR url=?)",
                            (row["channel"], str(row.get("job_external_id") or row.get("external_id") or ""), row.get("url")))
                duplicate = one(c, """SELECT m.id,t.id AS thread_id FROM messages m JOIN threads t ON t.id=m.thread_id
                    WHERE t.channel=? AND t.external_id=? AND t.external_id LIKE 'legacy:%' AND m.message_id IS NULL
                    AND m.body=? AND m.direction=? AND m.occurred_at=? ORDER BY m.id DESC LIMIT 1""",
                    (row["channel"], row.get("thread_external_id"), row.get("body"), row.get("direction"),
                     normalize_when(row.get("occurred_at"))))
                if duplicate:
                    c.execute("UPDATE messages SET subject=? WHERE id=?", (row.get("subject") or "", duplicate["id"]))
                    if alias:
                        c.execute("UPDATE threads SET job_id=COALESCE(job_id,?) WHERE id=?", (alias["job_id"], duplicate["thread_id"]))
                    continue
                self.ingest_message(c, {**row, "_history": True, "job_id": alias["job_id"] if alias else row.get("job_id")})
            for table in ("facts", "resumes", "sources", "history"):
                counts[table] = c.execute("SELECT count(*) FROM " + table).fetchone()[0]
            self.event(c, "已导入并保留原始台账： " + dump(counts), key="legacy:" + fingerprint(counts))
        return counts

    def add_source(self, c, row):
        url = job_link(row["url"])
        channel = row.get("channel") or "official"
        if channel != "official":
            raise ValueError("后台发现源只接收公开官方页面")
        c.execute("""INSERT INTO sources(channel,url,company,domain,location,recipient,recipient_source)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
                    company=COALESCE(sources.company,excluded.company), domain=COALESCE(sources.domain,excluded.domain),
                    location=COALESCE(sources.location,excluded.location)""", (channel, url, row.get("company"),
                    row.get("domain"), row.get("location"), row.get("recipient"), row.get("recipient_source")))
        return c.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()[0]

    def duplicate_reason(self, c, job, *, platform_channel="", browser_work=False):
        if manual_followup(c, job["id"]):
            return MANUAL_FOLLOWUP + "，公司外联已暂停"
        marks = ",".join("?" for _ in rules.BLOCKING)
        if one(c, f"""SELECT id FROM history WHERE job_id=? AND status IN ({marks})
                   AND NOT (channel=? AND status IN ('contacted','inquiry'))""",
               (job["id"], *rules.BLOCKING, platform_channel)):
            return "本岗位已有历史记录"
        if not browser_work and one(c, """SELECT id FROM browser_actions WHERE job_id=?
                   AND kind IN ('application','attachment','contact')
                   AND state IN ('queued','sending','unknown','attachment_requested')""", (job["id"],)):
            return "本岗位已有浏览器外发动作，须先核对结果"
        aliases = fetch(c, f"""SELECT j.company,j.domain,j.location,j.title FROM jobs j WHERE j.id<>? AND j.id IN (
                   SELECT job_id FROM history WHERE status IN ({marks})
                   UNION SELECT job_id FROM browser_actions WHERE kind IN ('application','attachment','contact')
                       AND state IN ('queued','sending','unknown','attachment_requested')
                   UNION SELECT job_id FROM outbox WHERE kind='application'
                       AND state IN ('queued','sending','unknown','confirmed')
                   )""", (job["id"], *rules.BLOCKING))
        company = rules.norm(job["company"])
        for a in aliases:
            if rules.title_key(a["title"]) != job["title_key"] or rules.norm(a["company"]) != company:
                continue
            if job["domain"] and a["domain"] and job["domain"] != a["domain"]:
                continue
            if a["location"] and job["location"] and not (a["location"] in job["location"] or job["location"] in a["location"] or
                                                          any(city in a["location"] and city in job["location"]
                                                              for city in rules.configured_cities(self.preferences(c)))):
                continue
            return "与另一来源的历史岗位可能相同，等待人工核对"
        other = one(c, """SELECT id FROM outbox WHERE job_id<>? AND kind='application' AND state IN ('queued','sending','unknown','confirmed')
               AND job_id IN (SELECT id FROM jobs WHERE employer_id=?)
               AND updated_at>=? LIMIT 1""", (job["id"], job["employer_id"], shift(-5 * 86400)))
        return "同一公司近期已有申请，等待人工核对" if other else ""

    def job_review_block(self, c, jid):
        review = one(c, "SELECT note FROM job_reviews WHERE job_id=? AND state IN ('hold','excluded')", (jid,))
        return "完整 JD 人工核对 · " + review["note"] if review else ""

    def save_job_review(self, c, jid, state, source, note):
        job = one(c, "SELECT * FROM jobs WHERE id=?", (jid,))
        if not job:
            raise ValueError("岗位不存在")
        note = str(note or "").strip()
        if state not in {"hold", "excluded", "cleared"} or not note or len(note) > 1500:
            raise ValueError("请填写实际核对结果和依据")
        c.execute("""INSERT INTO job_reviews VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE
                     SET state=excluded.state,source=excluded.source,note=excluded.note,updated_at=excluded.updated_at""",
                  (jid, state, job_link(source), note, stamp()))
        if state != "cleared":
            c.execute("""UPDATE outbox SET state='stale',evidence=?,updated_at=? WHERE job_id=? AND kind='application'
                         AND state IN ('queued','draft')""", ("完整 JD 人工核对：" + note, stamp(), jid))
        prefs = self.preferences(c)
        job["status"] = self.recruitment_status(job, prefs)
        match = self.job_match(c, job, prefs)
        c.execute("UPDATE jobs SET match_json=?,match_score=? WHERE id=?", (dump(match), match["score"], jid))

    def select_resume(self, c, scope):
        for row in fetch(c, "SELECT * FROM resumes WHERE approved=1 ORDER BY modified_at DESC,id DESC"):
            if scope in re.split(r"[,，/\s]+", row["scope"]):
                return row
        return None

    def verified_fact(self, c, key):
        rows = fetch(c, "SELECT value,state FROM facts WHERE key=? ORDER BY updated_at DESC", (key,))
        if any(r["state"] in {"conflict", "pending", "待确认"} for r in rows):
            return None
        values = {r["value"] for r in rows if r["state"] in {"verified", "confirmed", "已核实"}}
        return next(iter(values)) if len(values) == 1 else None

    @staticmethod
    def recruitment_status(job, prefs):
        if job["status"] in rules.RECRUITING and job["observed_at"] < shift(-int(prefs["job_expiry_days"]) * 86400):
            return "unknown"
        return job["status"]

    @staticmethod
    def job_match(c, job, prefs):
        exclusions = [r["pattern"] for r in fetch(c, "SELECT pattern FROM exclusions")]
        reviewed = one(c, "SELECT state FROM job_reviews WHERE job_id=?", (job["id"],))
        return rules.assess(job, prefs, exclusions, review_cleared=bool(reviewed and reviewed["state"] == "cleared"))

    def template(self, c, job, match):
        if match["scope"] == "finance":
            fact = self.verified_fact(c, "summary_finance")
            portfolio = None
        elif "对话、记忆与交互" in match["strengths"] or match["scope"] == "robotics":
            fact = self.verified_fact(c, "summary_dialogue")
            portfolio = None
        else:
            fact = self.verified_fact(c, "summary_app")
            portfolio = self.verified_fact(c, "portfolio_public")
        if not fact:
            return None
        job_text = " ".join(str(job.get(key) or "") for key in ("title", "description", "requirements"))
        focus = "产品需求和应用交付"
        for pattern, label in ((r"评测.{0,15}日志.{0,15}人工复核", "从智能体原型、API 接入到评测和人工复核"),
                               (r"多轮|陪伴|记忆|角色", "角色交互和对话体验"),
                               (r"语音|交互|原型", "交互原型和体验验证"),
                               (r"评测|质量|回归", "评测设计和迭代验证"),
                               (r"工作流|Agent|智能体|API", "AI 应用工作流和实际交付"),
                               (r"交易系统|风控|金融科技", "交易场景和产品需求")):
            if re.search(pattern, job_text, re.I):
                focus = label
                break
        links = ("\n相关作品：" + portfolio) if portfolio and re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", portfolio) else ""
        next_step = "希望有机会进一步沟通。" if job["status"] in rules.RECRUITING else RECRUITING_QUESTION
        return ("您好，我想申请贵司的" + job["title"] + "。" + fact
                + "看到岗位涉及" + focus + "。" + next_step + "附件是我的求职简历，谢谢。" + links)

    def approval_hash(self, row):
        parts = (row["channel"], row["kind"], row["job_id"], row["thread_id"], row["thread_revision"],
                 row["to_address"], row["subject"], row["body"], row["resume_id"], row["attachment_sha"], row["in_reply_to"])
        if row.get("attachment_filename"):
            parts += (row["attachment_filename"],)
        return fingerprint(*parts)

    def create_outbox(self, c, *, job_id=None, thread=None, anchor=None, kind, recipient, subject, body, resume=None,
                      auto=False, channel="email", in_reply_to=None, refs=None, due=None, origin_task_id=None):
        if not rules.address(recipient) or manual_followup(c, job_id or (thread and thread.get("job_id"))):
            return None
        ts = stamp()
        identifier = "<job-harness." + uuid.uuid4().hex + "@126.com>"
        cursor = c.execute("""INSERT OR IGNORE INTO outbox(job_id,thread_id,anchor_id,thread_revision,channel,kind,to_address,
            subject,body,resume_id,attachment_sha,message_id,in_reply_to,refs,state,approved,policy_generated,created_at,updated_at,due_at,origin_task_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, thread["id"] if thread else None, anchor["id"] if anchor else None,
             thread["revision"] if thread else None, channel, kind, rules.address(recipient), subject, body,
             resume["id"] if resume else None, resume["sha256"] if resume else None, identifier,
             in_reply_to, dump(refs or []), "queued" if auto else "draft", int(auto), 1, ts, ts, due or ts, origin_task_id))
        if cursor.rowcount != 1:
            return None
        oid = cursor.lastrowid
        row = one(c, "SELECT * FROM outbox WHERE id=?", (oid,))
        c.execute("UPDATE outbox SET approval_hash=? WHERE id=?", (self.approval_hash(row) if auto else None, oid))
        return oid

    def prepare(self, c, jid, manual=False, origin_task_id=None):
        job = one(c, "SELECT * FROM jobs WHERE id=?", (jid,))
        if not job:
            raise ValueError("岗位不存在")
        reviewed = self.job_review_block(c, jid)
        if reviewed:
            self.step(c, origin_task_id, "人工复核", "held", reviewed, jid)
            if manual:
                raise ValueError(reviewed)
            return None
        dupe = self.duplicate_reason(c, job)
        if dupe:
            self.step(c, origin_task_id, "去重", "held", dupe, jid)
            if manual:
                raise ValueError(dupe)
            return None
        contact = one(c, """SELECT ct.* FROM contacts ct JOIN job_contacts jc ON ct.id=jc.contact_id
                            WHERE jc.job_id=? AND ct.employer_id=?""", (jid, job["employer_id"]))
        prefs = self.preferences(c)
        job["status"] = self.recruitment_status(job, prefs)
        match = self.job_match(c, job, prefs)
        resume = self.select_resume(c, match["scope"])
        body = self.template(c, job, match) if contact and resume else None
        self.step(c, origin_task_id, "匹配", match["state"],
                  {"匹配点": match["strengths"], "缺口": match["gaps"], "未知项": match["unknown"], "硬条件": match["hard"]}, jid)
        if not contact:
            reason = "缺少已核实的招聘收件人"
        elif match["hard"]:
            reason = "岗位不符合硬性条件"
        elif rules.UNKNOWN_RECRUITING in match["unknown"] and not prefs["allow_unknown_recruiting"]:
            reason = "当前在招状态未确认，投递偏好尚未允许"
        elif not body or not (match["state"] == "match" or (manual and rules.UNKNOWN_RECRUITING not in match["unknown"])):
            reason = "附件、资料或自动匹配门槛尚未满足"
        else:
            reason = ""
        if reason:
            self.step(c, origin_task_id, "申请", "held", reason, jid)
            if manual:
                raise ValueError(reason)
            return None
        auto = not manual and prefs["auto_send"]
        oid = self.create_outbox(c, job_id=jid, kind="application", recipient=contact["address"],
                                 subject="应聘 " + job["title"],
                                 body=body, resume=resume, auto=auto,
                                 origin_task_id=origin_task_id)
        if not oid and prefs["allow_unknown_recruiting"]:
            previous = one(c, """SELECT id FROM outbox WHERE job_id=? AND kind='application' AND state='stale'
                        AND attempts=0 AND provider_id IS NULL AND policy_generated=1
                        AND evidence IN ('招聘状态未能在有效期内核实','当前在招状态未确认，投递偏好尚未允许',?)""",
                           (jid, RECRUITING_QUESTION_REQUIRED))
            if previous:
                oid = previous["id"]
                ts = stamp()
                c.execute("""UPDATE outbox SET to_address=?,subject=?,body=?,resume_id=?,attachment_sha=?,state=?,approved=?,
                             approval_hash=NULL,evidence=NULL,created_at=?,due_at=?,updated_at=?,origin_task_id=COALESCE(?,origin_task_id)
                             WHERE id=?""",
                          (contact["address"], "应聘 " + job["title"], body, resume["id"], resume["sha256"], "queued" if auto else "draft",
                           int(auto), ts, ts, ts, origin_task_id, oid))
                if auto:
                    row = one(c, "SELECT * FROM outbox WHERE id=?", (oid,))
                    c.execute("UPDATE outbox SET approval_hash=? WHERE id=?", (self.approval_hash(row), oid))
        self.step(c, origin_task_id, "申请", ("queued" if auto else "draft") if oid else "held",
                  {"简历": resume["label"], "SHA-256": resume["sha256"], "生成": bool(oid)}, jid, oid)
        return oid

    def reconsider_unknown_recruiting(self, c, prefs):
        candidates = fetch(c, """SELECT j.id FROM jobs j JOIN job_contacts jc ON j.id=jc.job_id
                                  WHERE j.status NOT IN ('closed','rejected','stopped','expired')
                                  AND (j.status NOT IN ('open','active','recruiting') OR j.observed_at<?)
                                  ORDER BY j.match_score DESC,j.id""",
                           (shift(-int(prefs["job_expiry_days"]) * 86400),))
        return sum(bool(self.prepare(c, row["id"])) for row in candidates)

    def trusted_thread(self, c, thread, sender):
        if thread["job_id"]:
            return one(c, """SELECT ct.id FROM job_contacts jc JOIN contacts ct ON jc.contact_id=ct.id
                             WHERE jc.job_id=? AND ct.address=?""", (thread["job_id"], sender))
        return None

    def ingest_message(self, c, data):
        channel = data["channel"]
        external = str(data.get("external_id") or data.get("message_id") or fingerprint(data))
        ext_thread = str(data.get("thread_external_id") or data.get("thread_id") or external)
        msgid = str(data.get("message_id") or "") or None
        if one(c, "SELECT id FROM messages WHERE channel=? AND (external_id=? OR message_id=?)", (channel, external, msgid)):
            return False
        sender = rules.address(data.get("sender"))
        direction = data.get("direction", "inbound")
        if direction in {"incoming", "received", "in"}:
            direction = "inbound"
        if direction in {"outgoing", "sent", "out"}:
            direction = "outbound"
        raw_refs = data.get("references") or []
        refs = re.findall(r"<[^<>\s]+>", raw_refs) if isinstance(raw_refs, str) else list(raw_refs)
        ts = normalize_when(data.get("occurred_at")) or stamp()
        linked = data.get("job_id")
        if not linked and refs:
            for ref in reversed(refs):
                correlated = one(c, "SELECT job_id,thread_id FROM outbox WHERE message_id=? AND state IN ('confirmed','unknown')", (ref,))
                if correlated:
                    linked = correlated["job_id"]
                    if correlated["thread_id"]:
                        t = one(c, "SELECT external_id FROM threads WHERE id=?", (correlated["thread_id"],))
                        if t:
                            ext_thread = t["external_id"]
                    break
                previous = one(c, """SELECT t.job_id,t.external_id FROM messages m JOIN threads t ON t.id=m.thread_id
                                      WHERE m.channel=? AND m.message_id=?""", (channel, ref))
                if previous:
                    ext_thread = previous["external_id"]
                    linked = previous["job_id"]
                    break
        if not linked and channel == "email" and direction == "outbound":
            recipient = rules.address(data.get("recipient"))
            normalized_subject = rules.norm(data.get("subject"))
            if recipient and normalized_subject:
                hits = {row["job_id"] for row in fetch(c, """SELECT DISTINCT job_id,title FROM history
                    WHERE channel='email' AND recipient=? AND status='confirmed' AND job_id IS NOT NULL""", (recipient,))
                    if len(rules.title_key(row["title"])) >= 5 and rules.title_key(row["title"]) in normalized_subject}
                if len(hits) == 1:
                    linked = next(iter(hits))
        subject, body = str(data.get("subject") or "")[:300], str(data.get("body") or "")[:30000]
        latest_body = re.split(r"(?m)^(?:>+|On .+ wrote:|[-_ ]{3,}.*(?:Original Message|原始邮件)|发件人[：:])", body, maxsplit=1)[0]
        if direction == "outbound":
            classification = "sent"
        elif str(data.get("delivery_status") or "").startswith("failed "):
            classification = "bounce"
        elif data.get("delivery_status") or str(data.get("auto_submitted") or "").lower() not in {"", "no"}:
            classification = "auto"
        else:
            classification = rules.classify(subject, latest_body)
        c.execute("""INSERT INTO threads(channel,external_id,company,subject,job_id,updated_at) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(channel,external_id) DO UPDATE SET job_id=COALESCE(threads.job_id,excluded.job_id),
                         updated_at=max(threads.updated_at,excluded.updated_at)""",
                  (channel, ext_thread, str(data.get("company") or ""), subject, linked, ts))
        thread = one(c, "SELECT * FROM threads WHERE channel=? AND external_id=?", (channel, ext_thread))
        mid = c.execute("""INSERT INTO messages(channel,thread_id,external_id,direction,sender,subject,body,occurred_at,message_id,refs,classification)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (channel, thread["id"], external, direction, sender, subject, body, ts, msgid, dump(refs), classification)).lastrowid
        if direction != "inbound" or data.get("_history"):
            return True
        return self.process_inbound(c, thread, mid, channel, external, msgid, sender, subject, latest_body, refs, ts, classification, data)

    def process_inbound(self, c, thread, mid, channel, external, msgid, sender, subject, latest_body, refs, ts, classification, data):
        c.execute("UPDATE threads SET revision=revision+1 WHERE id=?", (thread["id"],))
        c.execute("""UPDATE outbox SET state='stale',updated_at=?,evidence='收到新消息，旧草稿作废'
                     WHERE thread_id=? AND state IN ('draft','queued')""", (stamp(), thread["id"]))
        thread["revision"] += 1
        if classification == "bounce":
            for ref in refs:
                c.execute("UPDATE outbox SET evidence=COALESCE(evidence,'') || ?,updated_at=? WHERE message_id=?",
                          ("；收到退信，非收件成功", stamp(), ref))
            self.event(c, "收到退信，需要核对收件人和原申请", channel, "warning", "bounce:" + external)
            return True
        if classification in {"stop", "reject"}:
            c.execute("UPDATE threads SET stopped=1 WHERE id=?", (thread["id"],))
            if thread["job_id"] and self.trusted_thread(c, thread, sender):
                j = one(c, "SELECT company,title FROM jobs WHERE id=?", (thread["job_id"],))
                c.execute("""INSERT OR IGNORE INTO history(job_id,channel,company,title,status,source,evidence,occurred_at,fingerprint)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (thread["job_id"], channel, j["company"], j["title"],
                    "stopped" if classification == "stop" else "rejected", "招聘方消息", subject, ts, fingerprint(channel, external)))
            self.event(c, "拒绝或停止联系，已暂停该会话外发", channel, "info", "stop:" + external)
            return True
        if classification == "auto":
            return True
        auto_reply = (channel == "email" and classification in {"resume", "portfolio", "experience"} and msgid and
                      self.trusted_thread(c, thread, sender) and not thread["stopped"] and not manual_followup(c, thread["job_id"])
                      and not data.get("possibly_truncated") and not data.get("reply_to_unverified")
                      and (not data.get("reply_to") or rules.address(data.get("reply_to")) == sender) and not
                      re.search(r"全部|多人|转发|第三方|私有|原件|银行|身份证|验证|安装|下载|微信|电话|最新一版", latest_body))
        if auto_reply:
            job = one(c, "SELECT * FROM jobs WHERE id=?", (thread["job_id"],))
            match = rules.assess(job, self.preferences(c), [])
            portfolio = self.verified_fact(c, "portfolio_public")
            resume = self.select_resume(c, match["scope"])
            prior_pdf = one(c, "SELECT id FROM outbox WHERE thread_id=? AND resume_id IS NOT NULL AND state IN ('confirmed','unknown','sending')",
                            (thread["id"],))
            past_pdf = one(c, "SELECT id FROM history WHERE job_id=? AND status='confirmed'", (thread["job_id"],))
            reply, attachment = None, None
            if (classification == "resume" and resume and not prior_pdf and not past_pdf and not match["hard"]
                    and rules.explicit_request(latest_body, "resume")):
                reply, attachment = "您好，收到。随信附上我的求职简历，感谢您的联系。", resume
            elif (classification == "portfolio" and portfolio and portfolio.startswith("https://")
                  and rules.explicit_request(latest_body, "portfolio")):
                reply = "您好，感谢您的联系。这里是我的公开作品链接 " + portfolio + " 。欢迎交流。"
            elif classification == "experience" and len(latest_body.strip()) <= 160:
                for expression, key in rules.FACT_QUESTIONS:
                    if re.fullmatch(expression, latest_body.strip(), flags=re.I):
                        answer = self.verified_fact(c, key)
                        if answer:
                            reply = "您好，" + answer + "谢谢。"
                        break
            if reply:
                self.create_outbox(c, job_id=thread["job_id"], thread=thread, anchor={"id": mid}, kind="reply",
                                   recipient=sender, subject=subject if re.match(r"^Re:", subject, re.I) else "Re: " + subject,
                                   body=reply, resume=attachment,
                                   auto=self.preferences(c)["auto_send"], in_reply_to=msgid, refs=refs + [msgid])
                return True
        label = REVIEW_KINDS.get(classification, "招聘消息需判断")
        due = self.extract_deadline(latest_body) if classification in {"assignment", "interview", "offer"} else None
        c.execute("""INSERT OR IGNORE INTO decisions(thread_id,message_id,kind,question,draft,state,due_at,created_at,updated_at)
                     VALUES(?,?,?,?,?,'pending',?,?,?)""",
                  (thread["id"], mid, classification, label, "" if classification == "sensitive" else
                   "您好，收到您的消息。我核实后再回复，谢谢。", due, stamp(), stamp()))
        level = "important" if classification in {"interview", "offer", "assignment", "sensitive"} else "info"
        self.event(c, "待决定 · " + label, channel, level, "decision:" + channel + ":" + external)
        return True

    @staticmethod
    def extract_deadline(body):
        m = re.search(r"(?:截止|最晚|前提交)[^\n。]{0,18}?(?:(20\d{2})[-/年])?(\d{1,2})[-/月](\d{1,2})(?:日)?(?:\s+(\d{1,2}):(\d{2}))?", body)
        if not m:
            return None
        try:
            return datetime(int(m[1] or now().year), int(m[2]), int(m[3]), int(m[4] or 23),
                            int(m[5] or 59), tzinfo=TZ).isoformat()
        except ValueError:
            return None

    def channel_update(self, c, channel, status, detail):
        if channel == "email" and status == "configuration_required":
            self.event(c, "网易后台需配置 · " + str(detail), channel, "important", "email:configuration-required")
        c.execute("UPDATE channels SET status=?,detail=?,updated_at=? WHERE name=?",
                  (status, str(detail)[:800], stamp(), channel))
        if status in {"login_required", "platform_limit", "page_changed"} and channel != "official":
            c.execute("UPDATE channels SET paused=1 WHERE name=?", (channel,))
            self.event(c, "需要人工接管 · " + str(detail), channel, "important", "blocked:" + channel + ":" + status)

    def send_window(self, c):
        prefs = self.preferences(c)
        current = now()
        return self.outbound_enabled(c) and (prefs["send_anytime"] or (
            rules.is_business_day(current) and prefs["send_start"] <= current.strftime("%H:%M") < prefs["send_end"]
        ))

    @staticmethod
    def outbound_enabled(c):
        return not any(setting(c, key, False) for key in ("demo_fixture", "outbound_paused", "stopped"))

    @staticmethod
    def send_once_authorization(c):
        authorization = setting(c, "send_once") or {}
        if authorization.get("expires_at", "") > stamp() and authorization.get("stop_generation") == setting(c, "stop_generation", 0):
            return authorization
        return {}

    def authorize_send_once(self, outbox_id):
        """Let the scheduler claim one already-approved application outside its usual window."""
        with self.store.tx(True) as c:
            if not self.outbound_enabled(c):
                raise ValueError("后台调度或外发已暂停")
            item = one(c, "SELECT * FROM outbox WHERE id=?", (outbox_id,))
            if not item or item["channel"] != "email" or item["kind"] != "application":
                raise ValueError("仅能对指定的邮箱申请授权一次发送")
            if item["state"] != "queued" or item["attempts"] != 0:
                raise ValueError("申请须已批准、仍待发送且从未尝试。已提交或结果不明的申请须核验原记录")
            if one(c, """SELECT m.id FROM messages m JOIN threads t ON t.id=m.thread_id
                          LEFT JOIN mail_metadata md ON md.message_id=m.id
                          WHERE m.channel='email' AND m.direction='outbound'
                          AND (m.message_id=? OR (t.job_id=? AND lower(md.recipient)=?)) LIMIT 1""",
                   (item["message_id"], item["job_id"], item["to_address"].lower())):
                raise ValueError("本地同步记录已有这个岗位与收件人的外发，须核对原邮件")
            ch = one(c, "SELECT * FROM channels WHERE name='email'")
            if not ch or ch["paused"] or ch["status"] != "ready":
                raise ValueError("邮箱渠道已暂停或尚未就绪")
            error = self.validate_outbound(c, item)
            if error:
                raise ValueError(error)
            previous = self.send_once_authorization(c)
            if previous:
                if previous["id"] == outbox_id and previous["approval_hash"] == item["approval_hash"]:
                    return f"邮件 #{outbox_id} 已在等待本次发送"
                raise ValueError("另有一次性发送授权尚未处理")
            set_setting(c, "send_once", {"id": outbox_id, "approval_hash": item["approval_hash"],
                        "expires_at": shift(900), "stop_generation": setting(c, "stop_generation", 0)})
            self.event(c, f"邮件 #{outbox_id} 获得一次性发信窗口授权，等待发送核验", "email")
        self.wake.set()
        return f"邮件 #{outbox_id} 已安排一次发送，等待后台核验"

    @staticmethod
    def next_send_window(prefs, due_at):
        current = now()
        due = normalize_when(due_at)
        day = max(current, datetime.fromisoformat(due)) if due else current
        if prefs["send_anytime"]:
            return day.isoformat()
        for offset in range(8):
            date = (day + timedelta(days=offset)).date()
            if date.weekday() >= 5:
                continue
            opening = datetime.fromisoformat(date.isoformat() + "T" + prefs["send_start"]).replace(tzinfo=TZ)
            closing = datetime.fromisoformat(date.isoformat() + "T" + prefs["send_end"]).replace(tzinfo=TZ)
            if day < closing:
                return max(day, opening).isoformat()
        return None

    def validate_outbound(self, c, item):
        if not item["approved"] or item["approval_hash"] != self.approval_hash(item):
            return "发送内容、附件或对象与批准快照不一致"
        if not rules.address(item["to_address"]) or not item["subject"] or not item["body"]:
            return "缺少合法收件人、主题或正文"
        if any(ch in item["subject"] for ch in "\r\n") or len(item["subject"]) > 240 or len(item["body"]) > 6000:
            return "邮件字段长度或格式不正确"
        if re.search(r"\b\d{17}[\dXx]\b|银行卡号|私有仓库|身份证照片|验证码|银行账号", item["body"]):
            return "正文可能含敏感信息"
        prefs = self.preferences(c)
        if item["created_at"] < shift(-int(prefs["job_expiry_days"]) * 86400):
            return "草稿过期，需要重新核实岗位和上下文"
        job = one(c, "SELECT * FROM jobs WHERE id=?", (item["job_id"],)) if item["job_id"] else None
        if job and manual_followup(c, job["id"]):
            return MANUAL_FOLLOWUP + "，公司外联已暂停"
        if not job or not one(c, """SELECT ct.id FROM job_contacts jc JOIN contacts ct ON jc.contact_id=ct.id
                  WHERE jc.job_id=? AND ct.address=? AND ct.employer_id=?""",
                  (item["job_id"], item["to_address"], job["employer_id"])):
            return "收件人与岗位及招聘证据不一致"
        job["status"] = self.recruitment_status(job, prefs)
        match = self.job_match(c, job, prefs)
        if match["hard"] and item["kind"] in {"application", "followup"}:
            return "岗位不再符合硬性条件"
        if item["kind"] == "followup" and job["status"] not in rules.RECRUITING:
            return "跟进前须重新核实岗位仍在招聘"
        if item["kind"] == "application":
            unknown_recruiting = rules.UNKNOWN_RECRUITING in match["unknown"]
            if unknown_recruiting and not prefs["allow_unknown_recruiting"]:
                return "当前在招状态未确认，投递偏好尚未允许"
            reviewed = self.job_review_block(c, job["id"])
            if reviewed:
                return reviewed
            dupe = self.duplicate_reason(c, job)
            if dupe:
                return dupe
            if (item["policy_generated"] or unknown_recruiting) and match["state"] != "match":
                return "自动投递的匹配条件已变更"
            if unknown_recruiting and not re.search(r"(?:是否|还有|还在|仍在).{0,16}(?:招聘|机会|开放|招募)", item["body"]):
                return RECRUITING_QUESTION_REQUIRED
            if not item["resume_id"] or not item["policy_generated"] and job["title"] not in item["subject"]:
                return "申请缺少岗位标题或简历"
        if item["thread_id"]:
            thread = one(c, "SELECT * FROM threads WHERE id=?", (item["thread_id"],))
            anchor = one(c, "SELECT * FROM messages WHERE id=? AND thread_id=?", (item["anchor_id"], thread["id"])) if thread else None
            if (not thread or thread["stopped"] or thread["revision"] != item["thread_revision"] or not anchor
                    or not anchor["message_id"] or anchor["message_id"] != item["in_reply_to"]):
                return "会话已停止、新消息使草稿失效，或回复锚点不符"
            if item["kind"] == "reply" and (anchor["sender"] != item["to_address"] or anchor["direction"] != "inbound"):
                return "回复对象与原始发件人不一致"
            if item["kind"] == "followup" and (anchor["direction"] != "outbound" or thread["revision"] != 0):
                return "已有新回复或跟进上下文不完整"
        if item["resume_id"]:
            expected = self.select_resume(c, match["scope"])
            if not expected or item["resume_id"] != expected["id"] or item["attachment_sha"] != expected["sha256"]:
                return "简历版本与岗位方向或批准的附件不一致"
        return ""

    def _claim_send(self):
        if self.shutdown.is_set():
            return None
        with self.store.tx(True) as c:
            if not self.outbound_enabled(c):
                return None
            normal_window = self.send_window(c)
            send_once = self.send_once_authorization(c)
            if not normal_window and not send_once:
                return None
            prefs = self.preferences(c)
            today = now().date().isoformat()
            if one(c, "SELECT id FROM outbox WHERE state='sending' LIMIT 1"):
                return None
            if not prefs["send_anytime"] and one(c, """SELECT id FROM outbox WHERE state IN ('confirmed','sending','unknown')
                         AND updated_at>=? LIMIT 1""", (shift(-120),)):
                return None
            daily = c.execute("""SELECT count(*) FROM outbox WHERE kind='application'
                             AND state IN ('confirmed','sending','unknown') AND substr(updated_at,1,10)=?""", (today,)).fetchone()[0]
            immediate_id = send_once.get("id")
            at_daily_limit = not prefs["send_anytime"] and daily >= int(prefs["daily_limit"])
            email_ready = setting(c, "provider_send_after:email", "") <= stamp()
            for item in fetch(c, """SELECT o.* FROM outbox o JOIN channels ch ON ch.name=o.channel
                                    WHERE o.state='queued' AND o.due_at<=? AND o.attempts<3
                                    AND ch.paused=0 AND ch.status='ready'
                                    AND (? OR o.id=?)
                                    AND (NOT ? OR o.kind<>'application')
                                    AND (? OR o.channel<>'email')
                                    ORDER BY CASE WHEN o.id=? THEN -1 WHEN o.kind='reply' THEN 0
                                                  WHEN o.kind='application' THEN 1 ELSE 2 END,
                                    o.due_at,o.id LIMIT 30""", (stamp(), normal_window, immediate_id, at_daily_limit, email_ready, immediate_id)):
                authorized = (item["id"] == immediate_id and item["approval_hash"] == send_once.get("approval_hash")
                              and item["channel"] == "email" and item["kind"] == "application" and item["attempts"] == 0)
                if not normal_window and not authorized:
                    continue
                error = self.validate_outbound(c, item)
                if error:
                    if authorized:
                        set_setting(c, "send_once", None)
                    c.execute("UPDATE outbox SET state='stale',evidence=?,updated_at=? WHERE id=?", (error, stamp(), item["id"]))
                    self.event(c, "发送校验拦截 · " + error, item["channel"], "warning")
                    continue
                item["stop_generation"] = setting(c, "stop_generation", 0)
                c.execute("UPDATE outbox SET state='sending',attempts=attempts+1,stop_generation=?,updated_at=? WHERE id=?",
                          (item["stop_generation"], stamp(), item["id"]))
                if authorized:
                    set_setting(c, "send_once", None)
                    item["send_once"] = send_once
                return item
        return None

    def before_submit(self, item):
        if self.shutdown.is_set():
            return False
        with self.store.tx() as c:
            row = one(c, "SELECT * FROM outbox WHERE id=?", (item["id"],))
            ch = one(c, "SELECT * FROM channels WHERE name=?", (item["channel"],))
            generation = setting(c, "stop_generation", 0)
            if (not row or row["state"] != "sending" or not ch or ch["paused"] or ch["status"] != "ready"
                    or row["stop_generation"] != generation or not self.outbound_enabled(c)):
                return False
            if row["channel"] == "email" and setting(c, "provider_send_after:email", "") > stamp():
                return False
            authorization = item.get("send_once") or {}
            authorized = (authorization.get("expires_at", "") > stamp() and authorization.get("id") == row["id"]
                          and authorization.get("approval_hash") == row["approval_hash"]
                          and authorization.get("stop_generation") == generation and row["attempts"] == 1)
            return bool((self.send_window(c) or authorized) and not self.validate_outbound(c, row))

    def item_for_adapter(self, item):
        return {**item, "recipient": item["to_address"], "references": json.loads(item["refs"] or "[]"),
                "_before_submit": lambda: self.before_submit(item)}

    def verify_live_source(self, item):
        from .channels import ChannelUnavailable
        if item["kind"] != "application":
            return True
        with self.store.tx() as c:
            job = one(c, "SELECT * FROM jobs WHERE id=?", (item["job_id"],))
            source = one(c, "SELECT * FROM sources WHERE url=?", (job["url"],))
        if not source or rules.host(source["url"]) != channels.FUXIAN_HOST:
            return True
        detail = "发送前未能重新核实官网岗位或指定招聘邮箱，未提交"
        if source["enabled"]:
            try:
                rows = self.adapters["official"].discover([{**source, "kind": "fuxian"}])
                for row in rows:
                    if (row["status"] == "open" and row["url"] == job["url"] and row.get("recipient") == item["to_address"]
                            and all(rules.norm(row[field]) == rules.norm(job[field])
                                    for field in ("company", "title", "location", "description", "requirements"))):
                        return True
            except ChannelUnavailable as exc:
                detail = exc.detail + " 岗位和地址未重新核实，未提交。"
                if exc.code == "network" and item["attempts"] < 2:
                    with self.store.tx(True) as c:
                        changed = c.execute("UPDATE outbox SET state='queued',evidence=?,due_at=?,updated_at=? WHERE id=? AND state='sending'",
                                            (detail, shift(120 * (item["attempts"] + 1)), stamp(), item["id"]))
                        if changed.rowcount:
                            self.step(c, item["origin_task_id"], "官网发送前核验", "held", detail, item["job_id"], item["id"])
                    return False
        self.finish_send(item, {"state": "failed", "evidence": detail})
        return False

    def send_one(self):
        from .channels import ChannelUnavailable
        item = self._claim_send()
        if not item:
            return False
        with self.store.tx() as c:
            resume = one(c, "SELECT * FROM resumes WHERE id=?", (item["resume_id"],)) if item["resume_id"] else None
        attachment = None
        if resume:
            path = Path(resume["path"])
            try:
                if path.suffix.lower() != ".pdf" or path.stat().st_size > 20_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != item["attachment_sha"]:
                    raise ValueError("文件不同")
                attachment = {"path": str(path), "sha256": item["attachment_sha"],
                              "filename": item.get("attachment_filename") or path.name}
            except (OSError, ValueError):
                self.finish_send(item, {"state": "failed", "evidence": "本地附件不存在、超限或校验值不一致，未提交"})
                return True
        try:
            if not self.verify_live_source(item):
                return True
            if item["thread_id"] and item["channel"] == "email" and hasattr(self.adapters["email"], "read_messages"):
                self.check_replies("email")
                if not self.before_submit(item):
                    self.finish_send(item, {"state": "failed", "evidence": "最新会话或暂停状态变化，未提交"})
                    return True
            result = self.adapters[item["channel"]].send(self.item_for_adapter(item), attachment)
        except ChannelUnavailable as exc:
            with self.store.tx(True) as c:
                if exc.code != "dedup_required":
                    self.channel_update(c, item["channel"], exc.code, exc.detail)
                if exc.code == "network" and item["channel"] == "email" and self.preferences(c)["send_anytime"]:
                    self.enqueue(c, "probe", "email", f"send-recovery:{item['id']}:{item['attempts']}",
                                 due=shift(120 * (item["attempts"] + 1)))
            result = {"state": "failed", "retryable": exc.code in {"network", "login_required", "configuration_required"},
                      "retry_after": 120 if exc.code == "network" else None,
                      "evidence": exc.detail + " 未进入正文提交。"}
        except Exception:
            result = {"state": "unknown", "evidence": "适配器在发送中断开，无法确认结果，先核验。"}
        self.finish_send(item, result)
        return True

    def finish_send(self, item, result, reconciliation=False):
        state = result.get("state")
        if state not in STATE_VALUES:
            state = "unknown"
        evidence = evidence_text(result.get("evidence")) or "适配器未返回可核验证据"
        if state == "confirmed" and not result.get("evidence"):
            state = "unknown"
        with self.store.tx(True) as c:
            current = one(c, "SELECT * FROM outbox WHERE id=?", (item["id"],))
            if not current or current["state"] not in {"sending", "unknown"}:
                return
            if reconciliation and current["state"] != "unknown":
                return
            if current["state"] == "unknown" and state == "failed":
                state = "unknown"
            retryable = int(state == "failed" and bool(result.get("retryable")))
            provider_wait = result.get("channel_retry_after")
            if item["channel"] == "email" and provider_wait:
                after = max(setting(c, "provider_send_after:email", ""), shift(provider_wait))
                set_setting(c, "provider_send_after:email", after)
            retry_delay = result.get("retry_after")
            auto_retry = (retryable and retry_delay and current["attempts"] < 3 and self.preferences(c)["send_anytime"])
            saved_state = "queued" if auto_retry else state
            due = shift(retry_delay * current["attempts"]) if auto_retry else current["due_at"]
            c.execute("""UPDATE outbox SET state=?,evidence=?,provider_id=COALESCE(?,provider_id),updated_at=?,retryable=?,due_at=?
                         WHERE id=?""", (saved_state, evidence, result.get("provider_id"), stamp(), retryable, due, item["id"]))
            self.step(c, current["origin_task_id"], "发送证据核验" if reconciliation else "发送结果",
                      state, evidence, current["job_id"], current["id"])
            if state == "unknown" and not reconciliation:
                self.enqueue(c, "reconcile", item["channel"], "unknown:" + str(item["id"]), due=shift(45), payload={"id": item["id"]})
            if state == "confirmed":
                if item["channel"] == "email" and not reconciliation:
                    self.enqueue(c, "verify_sent", "email", "verify-sent:" + str(item["id"]),
                                 due=shift(30), payload={"id": item["id"]})
                job = one(c, "SELECT company,title FROM jobs WHERE id=?", (item["job_id"],))
                if item["kind"] == "application":
                    c.execute("""INSERT OR IGNORE INTO history(job_id,channel,company,title,status,source,evidence,occurred_at,recipient,fingerprint)
                                 VALUES(?,?,?,?,?,?,?,?,?,?)""", (item["job_id"], item["channel"], job["company"], job["title"],
                                 "confirmed", "Harness 发送", evidence, stamp(), item["to_address"], item["message_id"]))
                email = self.config.get("email", {}).get("account")
                thread = one(c, "SELECT external_id FROM threads WHERE id=?", (item["thread_id"],)) if item["thread_id"] else None
                self.ingest_message(c, {"channel": item["channel"], "external_id": item["message_id"], "direction": "outbound",
                    "thread_external_id": thread["external_id"] if thread else item["message_id"], "message_id": item["message_id"],
                    "company": job["company"], "job_id": item["job_id"], "sender": email, "subject": item["subject"], "body": item["body"],
                    "references": json.loads(item["refs"] or "[]"), "occurred_at": stamp()})
            state_label = "等待重试" if auto_retry else {"unknown": "结果不明", "failed": "失败", "confirmed": "已核实"}[state]
            self.event(c, "邮件 #" + str(item["id"]) + " · " + state_label
                       + " · " + evidence, item["channel"], "warning" if state != "confirmed" else "info")

    def reconcile(self, oid):
        with self.store.tx() as c:
            item = one(c, "SELECT * FROM outbox WHERE id=? AND state='unknown'", (oid,))
        if not item:
            return "没有待核验的结果不明发送"
        result = self.adapters[item["channel"]].reconcile(self.item_for_adapter(item))
        self.finish_send(item, result, reconciliation=True)
        return "核对结果 · " + evidence_text(result.get("evidence"))

    def verify_sent(self, oid, task_id):
        with self.store.tx() as c:
            item = one(c, "SELECT * FROM outbox WHERE id=? AND state='confirmed' AND channel='email'", (oid,))
        if not item:
            return "没有待核验的 SMTP 已接受邮件"
        result = self.adapters["email"].reconcile(self.item_for_adapter(item))
        detail = evidence_text(result.get("evidence"))
        with self.store.tx(True) as c:
            matched = result.get("state") == "confirmed" and bool(detail)
            if matched:
                evidence = item["evidence"] or ""
                evidence = evidence if detail in evidence else (evidence + " " + detail).strip()
                c.execute("UPDATE outbox SET evidence=? WHERE id=? AND state='confirmed'", (evidence, oid))
                c.execute("UPDATE history SET evidence=? WHERE fingerprint=? AND status='confirmed'",
                          (evidence, item["message_id"]))
            else:
                detail = "服务端已接受，已发送目录尚未核实。不重发。"
            self.step(c, task_id, "已发送目录核验", "confirmed" if matched else "review", detail, item["job_id"], oid)
        return detail

    def followups(self, c):
        prefs = self.preferences(c)
        if not prefs["allow_follow_up"]:
            return
        rows = fetch(c, """SELECT o.*,t.id AS follow_thread FROM outbox o JOIN threads t ON t.external_id=o.message_id
            WHERE o.kind='application' AND o.state='confirmed' AND t.channel=o.channel AND t.revision=0 AND t.stopped=0
            AND o.updated_at>=? AND o.evidence NOT LIKE '%退信%'""", (shift(-18 * 86400),))
        for row in rows:
            since = datetime.fromisoformat(row["updated_at"])
            due = rules.business_days_after(since, int(prefs["follow_up_business_days"]))
            if now() < due or now() > due + timedelta(days=3):
                continue
            if one(c, "SELECT id FROM decisions WHERE thread_id=? AND state='pending'", (row["follow_thread"],)):
                continue
            if manual_followup(c, row["job_id"]):
                continue
            thread = one(c, "SELECT * FROM threads WHERE id=?", (row["follow_thread"],))
            anchor = one(c, "SELECT id,message_id FROM messages WHERE thread_id=? AND message_id=?", (thread["id"], row["message_id"]))
            if not anchor:
                continue
            self.create_outbox(c, job_id=row["job_id"], thread=thread, anchor=anchor, kind="followup", recipient=row["to_address"],
                subject="Re: " + row["subject"], body="您好，想跟进一下此前发给您的申请。若岗位仍在招聘，我很愿意进一步交流。谢谢。",
                auto=prefs["auto_send"], in_reply_to=anchor["message_id"], refs=[anchor["message_id"]])

    def schedule(self):
        ts = now()
        with self.store.tx(True) as c:
            if setting(c, "stopped", False):
                return
            prefs = self.preferences(c)
            email = one(c, "SELECT * FROM channels WHERE name='email'")
            interval = max(10, int(prefs["reply_minutes"]))
            previous_read = normalize_when(setting(c, "read_baseline:email"))
            due_read = datetime.fromisoformat(previous_read) + timedelta(minutes=interval) if previous_read else ts
            active_read = one(c, """SELECT due_at,state FROM tasks WHERE channel='email'
                    AND kind IN ('replies','probe') AND state IN ('queued','running') AND cancelled=0
                    ORDER BY state='running' DESC,due_at LIMIT 1""")
            next_probe = ts.replace(minute=0, second=0) + timedelta(hours=1)
            next_email = None
            if not email["paused"]:
                next_email = active_read["due_at"] if active_read and active_read["state"] == "queued" else (
                    max(ts, due_read).isoformat() if email["status"] in {"ready","read_only"} else next_probe.isoformat())
            c.execute("UPDATE channels SET next_check=? WHERE name='email'", (next_email,))
            if not email["paused"] and not active_read:
                if email["status"] in {"ready", "read_only"}:
                    if ts >= due_read:
                        self.enqueue(c, "replies", "email", "replies:" + (previous_read or "baseline"))
                else:
                    self.enqueue(c, "probe", "email", "probe:email:" + ts.strftime("%Y%m%d%H"))
            times = [t.strip() for t in prefs["scan_times"].split(",")]
            future = [(ts + timedelta(days=d)).strftime("%Y-%m-%d") + "T" + t + ":00+08:00"
                      for d in range(8) if prefs["send_anytime"] or rules.is_business_day(ts + timedelta(days=d)) for t in times]
            upcoming = min((x for x in future if x > stamp()), default=None)
            c.execute("UPDATE channels SET next_check=? WHERE name='official'", (upcoming,))
            last_slot = max((x for x in times if x <= ts.strftime("%H:%M")), default=None)
            official = one(c, "SELECT paused FROM channels WHERE name='official'")
            liepin = one(c, "SELECT status,paused FROM channels WHERE name='liepin'")
            if liepin and liepin["status"] == "read_only" and not liepin["paused"]:
                c.execute("UPDATE channels SET next_check=? WHERE name='liepin'", (upcoming,))
            if last_slot and (prefs["send_anytime"] or rules.is_business_day(ts)):
                scheduled = datetime.fromisoformat(ts.date().isoformat() + "T" + last_slot).replace(tzinfo=TZ)
                if ts - scheduled < timedelta(minutes=75):
                    if not official["paused"]:
                        self.enqueue(c, "scan", "official", "scan:" + scheduled.isoformat())
                    if self.adapters.get("liepin") and liepin["status"] == "read_only" and not liepin["paused"]:
                        self.enqueue(c, "platform_search", "liepin", "liepin:search:" + scheduled.isoformat())
            if email["status"] == "ready" and not email["paused"]:
                self.followups(c)
            for row in fetch(c, """SELECT id,kind FROM decisions WHERE state='pending' AND due_at IS NOT NULL
                                  AND due_at BETWEEN ? AND ?""", (stamp(), shift(86400))):
                self.event(c, "待决定事项将在 24 小时内截止", level="important", key="deadline:" + str(row["id"]))

    def scan(self, payload=None):
        payload = payload or {}
        with self.store.tx() as c:
            if payload.get("source_id"):
                sources = fetch(c, "SELECT * FROM sources WHERE enabled=1 AND id=?", (payload["source_id"],))
            else:
                sources = fetch(c, "SELECT * FROM sources WHERE enabled=1 ORDER BY id")
        if not payload.get("source_id"):
            with self.store.tx(True) as c:
                for src in sources:
                    self.enqueue(c, "scan", "official", "source:" + str(src["id"]) + ":" + str(payload.get("batch") or stamp()),
                                 payload={"source_id": src["id"]})
            return "已排入 " + str(len(sources)) + " 个官方来源检查"
        if not sources:
            return "没有启用的官方来源"
        src = sources[0]
        host = rules.host(src["url"])
        src["kind"] = {"unitree.com": "unitree", "fengkanai.com": "peakvision", channels.FUXIAN_HOST: "fuxian",
                       "boards-api.greenhouse.io": "greenhouse", "api.lever.co": "lever"}.get(host, "schema")
        adapter = self.adapters["official"]
        try:
            found = adapter.discover([src])
        except Exception as exc:
            detail = getattr(exc, "detail", "公开来源读取失败")
            with self.store.tx(True) as c:
                suspend = getattr(exc, "code", "") in {"page_changed", "platform_limit", "configuration_required"}
                c.execute("UPDATE sources SET last_checked=?,last_status=?,enabled=CASE WHEN ? THEN 0 ELSE enabled END WHERE id=?",
                          (stamp(), ("暂停 · " if suspend else "") + detail, suspend, src["id"]))
            raise
        with self.store.tx(True) as c:
            count = draft_count = 0
            for data in found:
                jid = self.upsert_job(c, data)
                count += 1
                if self.prepare(c, jid, origin_task_id=payload.get("task_id")):
                    draft_count += 1
                elif data.get("recipient") and data.get("requirements"):
                    job = one(c, "SELECT match_json FROM jobs WHERE id=?", (jid,))
                    assessment = json.loads(job["match_json"])
                    if assessment["state"] == "review" and assessment["score"] >= 60:
                        self.enqueue(c, "model_probe", "model", "review-job:" + str(jid) + ":" + now().date().isoformat(),
                                     payload={"job_id": jid})
            result = "读取 " + str(count) + " 条 · 生成 " + str(draft_count) + " 项候选外发"
            self.step(c, payload.get("task_id"), "官网读取", "done", result)
            c.execute("UPDATE sources SET last_checked=?,last_status=? WHERE id=?", (stamp(), result, src["id"]))
            self.channel_update(c, "official", "ready", DEFAULT_CHANNEL_DETAIL["official"])
        return (src["company"] or host) + " · " + result

    def check_replies(self, channel, task_id=None):
        with self.store.tx() as c:
            addresses = [x["address"] for x in fetch(c, """SELECT address FROM contacts WHERE channel=?
                UNION SELECT recipient FROM history WHERE channel=? AND recipient IS NOT NULL AND recipient<>''""", (channel, channel))]
            ids = [x["message_id"] for x in fetch(c, "SELECT message_id FROM outbox WHERE channel=? AND state IN ('confirmed','unknown')", (channel,))]
            baseline = setting(c, "read_baseline:" + channel)
            cursor = setting(c, "mail_cursor:" + channel, {})
        adapter = self.adapters[channel]
        read_options = {"known_contacts": addresses, "known_message_ids": ids}
        if getattr(adapter, "uses_mail_cursor", False):
            read_options["cursor"] = cursor
        result = adapter.read_messages(**read_options)
        count = 0
        with self.store.tx(True) as c:
            for data in sorted(result, key=lambda x: (x.get("occurred_at") or "", x.get("direction") != "outbound")):
                timestamp = normalize_when(data.get("occurred_at"))
                if channel != "email":
                    count += self.ingest_message(c, {**data, "_history": True})
                    continue
                external = data.get("external_id") or data.get("message_id") or fingerprint({**data, "_history": True})
                existing = one(c, "SELECT id FROM messages WHERE channel='email' AND (external_id=? OR message_id=?)",
                               (external, data.get("message_id")))
                count += self.ingest_message(c, {**data, "_history": True})
                message = existing or one(c, "SELECT id FROM messages WHERE channel='email' AND (external_id=? OR message_id=?)",
                                          (external, data.get("message_id")))
                if message:
                    historical = bool(existing) or not baseline or not timestamp or timestamp < shift(-72 * 3600)
                    mail_workflow.persist_metadata(c, message, data, historical, normalize_when)
            set_setting(c, "read_baseline:" + channel, stamp())
            completed = getattr(adapter, "completed_cursor", None)
            if isinstance(completed, dict) and completed:
                set_setting(c, "mail_cursor:" + channel, {**cursor, **completed})
            if channel == "email" and one(c, "SELECT name FROM channels WHERE name='email' AND status='network'"):
                self.channel_update(c, "email", "read_only", "IMAP 读取已恢复，等待重新核验 SMTP 登录")
                self.enqueue(c, "probe", "email", "recovered-smtp:" + str(task_id or stamp()))
            self.step(c, task_id, "收件箱与已发送目录", "done", {"核对": len(result), "新增": count})
            outcomes = mail_workflow.reconcile(self, c) if channel == "email" else {}
            self.step(c, task_id, "关联与分流", "done", outcomes)
        return ("核对 " + str(len(result)) + " 条相关邮件，新增入库 " + str(count)
                + ("；首次读取建立基线" if not baseline else "") + ("；已核验状态 " + str(sum(outcomes.values())) + " 条" if outcomes else ""))

    def platform_results(self, c, rows, task_id):
        for data in rows:
            jid = self.upsert_job(c, data)
            match = one(c, "SELECT match_json FROM jobs WHERE id=?", (jid,))
            self.step(c, task_id, "猎聘岗位匹配", "review", json.loads(match["match_json"]), jid)
            self.enqueue_liepin_detail(c, jid, data["external_id"], task_id)
        self.step(c, task_id, "猎聘官方岗位搜索", "done", {"返回": len(rows), "平台外发": 0})

    def enqueue_liepin_detail(self, c, jid, external_id, parent):
        from .liepin_detail import detail_url
        alias = one(c, "SELECT url FROM job_aliases WHERE job_id=? AND channel='liepin' AND external_id=?", (jid, external_id))
        if not alias:
            return False
        try:
            detail_url(external_id, alias["url"])
        except ValueError:
            return False
        key = "liepin:detail:" + now().date().isoformat() + ":" + external_id
        if one(c, "SELECT id FROM tasks WHERE dedupe_key=?", (key,)):
            return False
        ordinal = c.execute("SELECT count(*) FROM tasks WHERE kind='liepin_detail' AND state='queued'").fetchone()[0]
        self.enqueue(c, "liepin_detail", "liepin", key, due=shift(2 * min(ordinal, 30)),
                     payload={"job_id": jid, "external_id": external_id, "source_task_id": parent})
        return True

    def stage_liepin_details(self, source_task_id):
        with self.store.tx(True) as c:
            if not one(c, "SELECT id FROM tasks WHERE id=? AND channel='liepin' AND kind IN ('probe','platform_search') AND state='done'",
                       (source_task_id,)):
                raise ValueError("请选择已完成的猎聘搜索任务编号")
            rows = fetch(c, """SELECT DISTINCT j.job_id, a.external_id FROM task_steps j
                            JOIN job_aliases a ON a.job_id=j.job_id AND a.channel='liepin'
                            WHERE j.task_id=? AND j.stage='猎聘岗位匹配' ORDER BY j.id LIMIT 80""", (source_task_id,))
            count = sum(self.enqueue_liepin_detail(c, row["job_id"], row["external_id"], source_task_id) for row in rows)
        self.wake.set()
        return count

    def read_liepin_detail(self, payload, task_id):
        jid, external_id = payload["job_id"], payload["external_id"]
        with self.store.tx() as c:
            row = one(c, """SELECT j.*, a.url AS listing_url FROM jobs j JOIN job_aliases a ON j.id=a.job_id
                WHERE j.id=? AND a.channel='liepin' AND a.external_id=?""", (jid, external_id))
        if not row:
            raise ValueError("没有可与公开页面核对的猎聘岗位")
        detail = self.adapters["liepin"].read_job(external_id, row["listing_url"], row["title"])
        with self.store.tx(True) as c:
            live = one(c, "SELECT cancelled FROM tasks WHERE id=?", (task_id,))
            if not live or live["cancelled"] or setting(c, "stopped", False) or self.shutdown.is_set():
                return "详情任务已停止"
            merged = {**row, **detail, "location": detail["location"] or row["location"],
                      "salary": detail["salary"] or row["salary"], "status": "unknown" if detail["status"] == "partial" else detail["status"]}
            current = one(c, "SELECT status FROM jobs WHERE id=?", (jid,))
            if current["status"] in rules.RECRUITING_ENDED:
                merged["status"] = current["status"]
            match = rules.assess(merged, self.preferences(c), [r["pattern"] for r in fetch(c, "SELECT pattern FROM exclusions")])
            c.execute("""UPDATE jobs SET description=?,requirements=?,location=?,salary=?,status=?,url=?,observed_at=?,match_json=?,match_score=?
                         WHERE id=? AND channel='liepin'""", (merged["description"], merged["requirements"], merged["location"],
                         merged["salary"], merged["status"], detail["url"], stamp(), dump(match), match["score"], jid))
            self.step(c, task_id, "完整猎聘详情", "done", {"来源": detail["url"], "状态": merged["status"],
                      "职责字数": len(merged["description"]), "要求字数": len(merged["requirements"])}, jid)
            self.prepare(c, jid, origin_task_id=task_id)
        return "已核对公开猎聘职位详情及匹配条件"

    def record_platform_message(self, c, data):
        from .platform_handoff import normalize_platform_message
        msg = normalize_platform_message(data)
        jid = msg["job_id"]
        job = one(c, "SELECT * FROM jobs WHERE id=?", (jid,))
        if not job:
            raise ValueError("聊天证据的公开岗位编号与所选岗位不一致")
        self.browser_actions.verify_job_url(c, job, msg["source_url"])
        if not self.ingest_message(c, {**msg, "company": job["company"], "_history": True}):
            return "这条平台消息已有记录"
        stored = one(c, "SELECT * FROM messages WHERE channel=? AND external_id=?", (msg["channel"], msg["external_id"]))
        c.execute("INSERT INTO platform_message_sources VALUES(?,?,?,?)",
                  (stored["id"], msg["source_url"], msg["platform_job_id"], stamp()))
        if msg["direction"] == "inbound":
            kind = mail_workflow.classify(msg["subject"], msg["body"])
            c.execute("UPDATE messages SET classification=? WHERE id=?", (kind, stored["id"]))
            if kind in {"interview", "assignment", "offer", "availability", "compensation", "sensitive", "manual_channel_pending"}:
                mail_workflow._put_decision(c, stored, kind, "平台消息待核对。请在原平台处理。",
                                            due=self.extract_deadline(msg["body"]))
        return "已按岗位编号登记平台聊天证据"

    def task_one(self):
        from .channels import ChannelUnavailable
        with self.store.tx(True) as c:
            if setting(c, "stopped", False):
                return False
            row = one(c, """SELECT t.* FROM tasks t LEFT JOIN channels ch ON ch.name=t.channel
                WHERE t.state='queued' AND t.cancelled=0 AND t.due_at<=? AND (COALESCE(ch.paused,0)=0 OR t.kind IN ('probe','reconcile','verify_sent','mail_audit'))
                ORDER BY CASE t.kind WHEN 'reconcile' THEN 0 WHEN 'mail_audit' THEN 1 WHEN 'replies' THEN 2 WHEN 'probe' THEN 3 WHEN 'model_probe' THEN 4 ELSE 5 END,
                t.due_at,t.id LIMIT 1""", (stamp(),))
            if not row:
                return False
            c.execute("UPDATE tasks SET state='running',attempts=attempts+1,updated_at=? WHERE id=?", (stamp(), row["id"]))
        detail, state, due = "", "done", row["due_at"]
        try:
            payload = json.loads(row["payload"])
            kind, channel = row["kind"], row["channel"]
            if kind == "scan":
                detail = self.scan({**payload, "batch": row["dedupe_key"], "task_id": row["id"]})
            elif kind == "probe":
                result = self.adapters[channel].probe()
                detail = result["detail"]
                provider_status = status = result["status"]
                if status != "ready" and result.get("capabilities", {}).get("read"):
                    status = "read_only"
                with self.store.tx(True) as c:
                    self.channel_update(c, channel, status, detail)
                    if channel == "email" and provider_status == "smtp_rate_limited":
                        resume_at = max(setting(c, "provider_send_after:email", ""), shift(1800))
                        set_setting(c, "provider_send_after:email", resume_at)
                        self.enqueue(c, "probe", "email", "recovered-smtp:" + resume_at, due=resume_at)
                if channel == "email" and provider_status == "network" and row["attempts"] < 2:
                    state, due = "queued", shift(120 * (row["attempts"] + 1))
                if channel == "email" and status in {"ready", "read_only"} and not row["dedupe_key"].startswith("recovered-smtp:"):
                    with self.store.tx(True) as c:
                        self.enqueue(c, "replies", channel, "probe-read:" + str(row["id"]))
                if channel == "liepin" and result.get("capabilities", {}).get("discover"):
                    with self.store.tx(True) as c:
                        self.platform_results(c, result.get("jobs", []), row["id"])
            elif kind == "replies":
                detail = self.check_replies(channel, row["id"])
            elif kind == "mail_audit":
                with self.store.tx(True) as c:
                    outcomes = mail_workflow.reconcile(self, c)
                    self.step(c, row["id"], "已入库邮件重核", "done", outcomes)
                detail = "已重核 " + str(sum(outcomes.values())) + " 条邮件；历史来信走证据分流，不自动外发"
            elif kind == "platform_search":
                found = self.adapters[channel].discover()
                with self.store.tx(True) as c:
                    self.platform_results(c, found, row["id"])
                detail = "官方猎聘搜索，返回 " + str(len(found)) + " 个岗位；站内外联由人工接管"
            elif kind == "liepin_detail":
                detail = self.read_liepin_detail(payload, row["id"])
            elif kind == "reconcile":
                detail = self.reconcile(payload["id"])
                with self.store.tx() as c:
                    if (self.preferences(c)["send_anytime"] and row["attempts"] < 2
                            and one(c, "SELECT id FROM outbox WHERE id=? AND state='unknown'", (payload["id"],))):
                        state, due = "queued", shift(120 * (row["attempts"] + 1))
            elif kind == "verify_sent":
                detail = self.verify_sent(payload["id"], row["id"])
            elif kind == "model_probe":
                model_state, detail = self.invoke_model_probe(payload.get("job_id"))
                if model_state == "failed":
                    state = "failed"
                    if "超时" in detail and row["attempts"] < 2:
                        state, due = "queued", shift(120 * (row["attempts"] + 1))
            else:
                state, detail = "failed", "未识别的后台任务"
        except ChannelUnavailable as exc:
            detail, state = exc.detail, "failed"
            with self.store.tx(True) as c:
                if row["channel"] != "official":
                    self.channel_update(c, row["channel"], exc.code, detail)
            if exc.code == "network" and row["attempts"] < 2:
                state, due = "queued", shift(120 * (row["attempts"] + 1))
        except ValueError as exc:
            detail, state = (str(exc) if row["kind"] == "liepin_detail" else "任务数据未通过校验"), "failed"
        except Exception as exc:
            detail, state = "任务错误 " + type(exc).__name__, "failed"
        with self.store.tx(True) as c:
            live = one(c, "SELECT cancelled FROM tasks WHERE id=?", (row["id"],))
            if live["cancelled"]:
                state = "stopped"
            c.execute("UPDATE tasks SET state=?,detail=?,due_at=?,updated_at=? WHERE id=?",
                      (state, detail, due, stamp(), row["id"]))
            self.event(c, f'后台任务 #{row["id"]} · {detail}', row["channel"], "warning" if state == "failed" else "info")
        return True

    def notify_one(self):
        if not self.notifier:
            return
        with self.store.tx(True) as c:
            row = one(c, "SELECT * FROM events WHERE level='important' AND notified=0 ORDER BY id LIMIT 1")
            if not row:
                return
            c.execute("UPDATE events SET notified=1 WHERE id=?", (row["id"],))
        try:
            if self.notifier("求职 Harness", row["message"]) is False:
                raise RuntimeError("local notification failed")
        except (OSError, RuntimeError):
            with self.store.tx(True) as c:
                self.event(c, "系统通知未能送达，事项已保留在控制台", level="warning", key="notification-error")

    def run(self):
        self.recover()
        next_schedule = ""
        while not self.shutdown.is_set():
            self.last_heartbeat = stamp()
            try:
                if stamp() >= next_schedule:
                    self.schedule()
                    next_schedule = shift(20)
                sent = self.send_one()
                if self.task_one() or sent:
                    continue
                self.notify_one()
            except Exception as exc:
                with self.store.tx(True) as c:
                    self.event(c, "调度错误 " + type(exc).__name__, level="warning", key="scheduler:" + now().strftime("%Y%m%d%H%M"))
            self.wake.wait(3)
            self.wake.clear()

    def invoke_model_probe(self, job_id=None):
        with self.store.tx(True) as c:
            config = {**{"name": "", "max_calls": 3, "timeout": 75, "cost_limit": ""},
                      **setting(c, "model_config", {})}
            used = c.execute("SELECT count(*) FROM model_runs WHERE substr(created_at,1,10)=?", (now().date().isoformat(),)).fetchone()[0]
            if not self.model or used >= config["max_calls"] or config["cost_limit"] not in ("", None):
                return "held", "模型已禁用、达到调用上限，或真实费用不可用而启用了费用上限"
            job = one(c, "SELECT match_json FROM jobs WHERE id=?", (job_id,)) if job_id else None
            info = json.loads(job["match_json"]) if job else None
            mid = c.execute("INSERT INTO model_runs(created_at,model,state) VALUES(?,?,'running')",
                            (stamp(), config["name"] or "CLI 默认")).lastrowid
        result = self.model.evaluate(config, info)
        with self.store.tx(True) as c:
            c.execute("""UPDATE model_runs SET model=?,state=?,detail=?,input_tokens=?,output_tokens=? WHERE id=?""",
                      (result["model"], result["state"], result["detail"], result.get("input_tokens"),
                       result.get("output_tokens"), mid))
            if job_id and result["state"] == "done":
                current = one(c, "SELECT match_json FROM jobs WHERE id=?", (job_id,))
                if current and json.loads(current["match_json"]) == info:
                    info["model_advice"] = result["detail"]
                    c.execute("UPDATE jobs SET match_json=? WHERE id=?", (dump(info), job_id))
        return result["state"], result["detail"]

    def set_employer_followup(self, c, data):
        job_id, decision_id = int(data.get("job_id") or 0), int(data.get("decision_id") or 0)
        resolution = str(data.get("resolution") or "").strip()
        job = one(c, "SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job or not job["employer_id"] or not resolution or len(resolution) > 2000:
            raise ValueError("请选择已核实的公司岗位，并填写建立联系的依据")
        if decision_id and not one(c, """SELECT d.id FROM decisions d JOIN threads t ON t.id=d.thread_id
                WHERE d.id=? AND t.job_id=? AND d.kind='manual_channel_pending' AND d.state IN ('pending','resolved')""",
                (decision_id, job_id)):
            raise ValueError("该待办不属于所选岗位的人工联系")

        ts = stamp()
        c.execute("""INSERT INTO employer_followups(employer_id,evidence,updated_at) VALUES(?,?,?)
                     ON CONFLICT(employer_id) DO UPDATE SET evidence=excluded.evidence,
                     updated_at=excluded.updated_at,released_at=NULL""", (job["employer_id"], resolution, ts))
        c.execute("""INSERT OR IGNORE INTO history(job_id,channel,company,title,status,source,evidence,occurred_at,fingerprint)
                     VALUES(?,?,?,?,'manual_followup','本人确认',?,?,?)""",
                  (job_id, job["channel"], job["company"], job["title"], resolution, ts,
                   fingerprint(job_id, "manual_followup", resolution)))
        affected = f"""SELECT j.id FROM jobs j JOIN employers candidate ON candidate.id=j.employer_id
            JOIN employers held ON held.id=? WHERE {FOLLOWUP_EMPLOYER_MATCH}"""
        c.execute(f"""UPDATE outbox SET state='stale',approved=0,approval_hash=NULL,evidence=?,updated_at=?
                     WHERE state IN ('draft','queued') AND job_id IN ({affected})""",
                  (MANUAL_FOLLOWUP + "，公司外联已暂停", ts, job["employer_id"]))
        if decision_id:
            c.execute("""UPDATE decisions SET state='resolved',resolution=?,updated_at=?
                         WHERE id=? AND state='pending'""", (resolution, ts, decision_id))
            c.execute("""UPDATE mail_audits SET state='manual_followup',evidence=?,updated_at=?
                         WHERE message_id=(SELECT message_id FROM decisions WHERE id=?)""",
                      (resolution, ts, decision_id))
        return job["company"] + " · " + MANUAL_FOLLOWUP

    def action(self, name, data):
        data = dict(data)
        notice = "操作已保存"
        if name == "backup":
            return "已创建备份 " + self.store.backup().name
        with self.store.tx(True) as c:
            channel = data.get("channel", "")
            if name in {"pause_channel", "resume_channel", "scan"} and channel not in CHANNELS:
                raise ValueError("未知渠道")
            if name in {"pause_all", "resume_all", "stop", "run"}:
                if name in {"pause_all", "resume_all"}:
                    set_setting(c, "outbound_paused", name == "pause_all")
                if name in {"stop", "run"}:
                    set_setting(c, "stopped", name == "stop")
                if name == "stop":
                    set_setting(c, "stop_generation", setting(c, "stop_generation", 0) + 1)
                    c.execute("UPDATE tasks SET cancelled=1 WHERE state='running'")
                    self.browser_actions.stop(c)
                    for row in fetch(c, "SELECT id,channel FROM outbox WHERE state='sending'"):
                        c.execute("UPDATE outbox SET state='unknown',evidence='停止时无法确认，先核验',updated_at=? WHERE id=?",
                                  (stamp(), row["id"]))
                        self.enqueue(c, "reconcile", row["channel"], "stop:" + str(row["id"]) + ":" + stamp(), payload={"id": row["id"]})
                notice = {"pause_all": "已暂停申请和邮件外发，搜索、查收和核验可继续", "resume_all": "已恢复按发送规则处理申请和邮件",
                          "stop": "后台任务已暂停。已开始外发的邮件会进入结果核验", "run": "后台任务已恢复，外发仍按其发送开关和窗口执行"}[name]
            elif name in {"pause_channel", "resume_channel"}:
                c.execute("UPDATE channels SET paused=?,updated_at=? WHERE name=?", (name == "pause_channel", stamp(), channel))
                if name == "resume_channel":
                    self.enqueue(c, "probe", channel, "manual:probe:" + uuid.uuid4().hex)
            elif name == "scan":
                kind = "scan" if channel == "official" else "probe"
                self.enqueue(c, kind, channel, "manual:" + uuid.uuid4().hex)
                notice = "已安排后台检查"
            elif name == "record_platform_message":
                notice = self.record_platform_message(c, data)
            elif name == "browser_queue":
                action_id = self.browser_actions.queue(c, data)
                notice = f"已登记浏览器动作 #{action_id}，等待前台执行"
            elif name == "employer_manual_followup":
                notice = self.set_employer_followup(c, data)
            elif name == "employer_resume_outreach":
                employer_id = int(data.get("employer_id") or 0)
                changed = c.execute("UPDATE employer_followups SET released_at=? WHERE employer_id=? AND released_at IS NULL",
                                    (stamp(), employer_id)).rowcount
                if not changed:
                    raise ValueError("没有可恢复的公司外联暂停")
                notice = "已恢复该公司的外联规则，已有联系仍按历史记录去重"
            elif name in {"retry", "retry_send"}:
                table = "tasks" if name == "retry" else "outbox"
                oid = int(data.get("id") or 0)
                row = one(c, "SELECT * FROM " + table + " WHERE id=? AND state='failed' AND attempts<3", (oid,))
                if not row or (name == "retry_send" and not row["retryable"]):
                    raise ValueError("此项不可重试。外发结果不明须先核验")
                if name == "retry_send" and manual_followup(c, row["job_id"]):
                    raise ValueError(MANUAL_FOLLOWUP + "，公司外联已暂停")
                c.execute("UPDATE " + table + " SET state='queued',due_at=?,updated_at=? WHERE id=?", (stamp(), stamp(), oid))
            elif name == "reconcile":
                oid = int(data.get("id") or 0)
                item = one(c, "SELECT * FROM outbox WHERE id=? AND state='unknown'", (oid,))
                if not item:
                    raise ValueError("没有待核验的结果不明发送")
                self.enqueue(c, "reconcile", item["channel"], "manual:" + uuid.uuid4().hex, payload={"id": oid})
                notice = "已安排核对，不会重发"
            elif name in {"edit_draft", "approve"}:
                oid = int(data.get("id") or 0)
                row = one(c, "SELECT * FROM outbox WHERE id=? AND state='draft'", (oid,))
                if not row:
                    raise ValueError("草稿已失效、已经在队列或不存在")
                if name == "edit_draft":
                    subject, body = str(data.get("subject", row["subject"])).strip(), str(data.get("body", "")).strip()
                    if not subject or not body or len(body) > 6000 or len(subject) > 240:
                        raise ValueError("请填写有效主题和正文")
                    filename = str(data.get("attachment_filename", row["attachment_filename"]) or "").strip()
                    if filename and (row["kind"] != "application" or not row["resume_id"] or len(filename.encode("utf-8")) > 240
                                     or not filename.lower().endswith(".pdf") or re.search(r"[/\\\x00-\x1f\x7f]", filename)):
                        raise ValueError("附件名称须为有效的 PDF 文件名")
                    c.execute("""UPDATE outbox SET subject=?,body=?,attachment_filename=?,approved=0,approval_hash=NULL,
                                 policy_generated=0,updated_at=? WHERE id=?""", (subject, body, filename or None, stamp(), oid))
                else:
                    row["approved"] = 1
                    row["approval_hash"] = self.approval_hash(row)
                    error = self.validate_outbound(c, row)
                    if error:
                        raise ValueError(error)
                    c.execute("UPDATE outbox SET approved=1,approval_hash=?,state='queued',updated_at=? WHERE id=?",
                              (row["approval_hash"], stamp(), oid))
                    notice = "已批准并排队，外发前仍会核对渠道、时间和会话"
            elif name == "decide":
                oid = int(data.get("id") or 0)
                resolution = str(data.get("resolution") or "").strip()
                if not resolution or len(resolution) > 3000:
                    raise ValueError("请填写决定或需要发送的正文")
                decision = one(c, "SELECT * FROM decisions WHERE id=? AND state='pending'", (oid,))
                if not decision:
                    raise ValueError("该事项不存在或已处理")
                c.execute("UPDATE decisions SET state='resolved',resolution=?,updated_at=? WHERE id=?", (resolution, stamp(), oid))
                thread = one(c, "SELECT * FROM threads WHERE id=?", (decision["thread_id"],))
                anchor = one(c, "SELECT * FROM messages WHERE id=?", (decision["message_id"],))
                latest = one(c, "SELECT id FROM messages WHERE thread_id=? AND direction='inbound' ORDER BY id DESC LIMIT 1", (thread["id"],))
                notice = "已保存决定"
                if (decision["kind"] not in {"sensitive","manual_channel_pending","delivery_failed","delivery_delay"}
                        and thread["channel"] == "email" and not thread["stopped"] and latest
                        and latest["id"] == anchor["id"] and anchor["message_id"] and self.trusted_thread(c, thread, anchor["sender"])):
                    new = self.create_outbox(c, job_id=thread["job_id"], thread=thread, anchor=anchor, kind="reply",
                            recipient=anchor["sender"], subject=anchor["subject"] if re.match(r"^Re:", anchor["subject"], re.I)
                            else "Re: " + anchor["subject"], body=resolution, in_reply_to=anchor["message_id"],
                            refs=json.loads(anchor["refs"]) + [anchor["message_id"]])
                    if new:
                        notice = "已保存决定并生成待批准的回复草稿"
            elif name == "preferences":
                prefs = self.preferences(c)
                previously_allowed = prefs["allow_unknown_recruiting"]
                was_sending_anytime = prefs["send_anytime"]
                for key in ("target_scope", "cities", "directions", "work_arrangement", "work_types", "salary_floor"):
                    if key in data:
                        prefs[key] = str(data[key]).strip()[:400]
                if prefs["salary_floor"] and not re.fullmatch(r"\d{1,6}", str(prefs["salary_floor"])):
                    raise ValueError("薪资底线请填每月人民币数字，或留空")
                for key, minimum, maximum in (("reply_minutes", 10, 240), ("daily_limit", 1, 10), ("follow_up_business_days", 5, 30)):
                    if key in data:
                        number = int(data[key])
                        if not minimum <= number <= maximum:
                            raise ValueError("节奏设置超出安全范围")
                        prefs[key] = number
                valid_time = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]"
                for key in ("send_start", "send_end"):
                    if key in data:
                        if not re.fullmatch(valid_time, data[key]):
                            raise ValueError("时间须为 HH:MM")
                        prefs[key] = data[key]
                if prefs["send_start"] >= prefs["send_end"]:
                    raise ValueError("外发结束时间须晚于开始")
                if "scan_times" in data:
                    parts = [s.strip() for s in data["scan_times"].replace("，", ",").split(",")]
                    if not 1 <= len(parts) <= 4 or any(not re.fullmatch(valid_time, s) for s in parts):
                        raise ValueError("请输入 1–4 个 HH:MM")
                    prefs["scan_times"] = ",".join(sorted(set(parts)))
                for key in ("auto_send", "email_only_new", "allow_follow_up", "allow_remote", "require_robotics",
                            "allow_unknown_recruiting", "send_anytime"):
                    if key in data:
                        prefs[key] = str(data[key]).lower() in {"true", "1", "on", "yes"}
                set_setting(c, "preferences", prefs)
                if not prefs["auto_send"]:
                    c.execute("""UPDATE outbox SET state='draft',approved=0,approval_hash=NULL,updated_at=?
                              WHERE state='queued' AND policy_generated=1""", (stamp(),))
                self.refresh_matches(c)
                if prefs["allow_unknown_recruiting"] and not previously_allowed:
                    count = self.reconsider_unknown_recruiting(c, prefs)
                    notice = f"已保存求职偏好并复核已登记渠道，新增 {count} 份申请"
                if prefs["send_anytime"] and not was_sending_anytime:
                    self.enqueue(c, "scan", "official", "anytime:start:" + stamp())
                    liepin = one(c, "SELECT status,paused FROM channels WHERE name='liepin'")
                    if self.adapters.get("liepin") and liepin["status"] == "read_only" and not liepin["paused"]:
                        self.enqueue(c, "platform_search", "liepin", "anytime:liepin:" + stamp())
                    notice = "已启用随时发送并安排新一轮找岗"
            elif name in {"add_exclusion", "remove_exclusion"}:
                pattern = str(data.get("pattern", "")).strip()[:150]
                if not pattern:
                    raise ValueError("排除项不能为空")
                if name == "add_exclusion":
                    c.execute("INSERT OR REPLACE INTO exclusions VALUES(?,?,?)", (pattern, str(data.get("note") or "")[:400], stamp()))
                else:
                    c.execute("DELETE FROM exclusions WHERE pattern=?", (pattern,))
                self.refresh_matches(c)
            elif name == "edit_fact":
                row = one(c, "SELECT key FROM facts WHERE id=?", (int(data.get("id") or 0),))
                value, source = str(data.get("value") or "").strip(), str(data.get("source") or "").strip()
                if not row or not value or not source:
                    raise ValueError("事实内容与核实来源不能为空")
                c.execute("UPDATE facts SET value=?,source=?,updated_at=?,state='verified' WHERE id=?",
                          (value[:3000], source[:400], stamp(), int(data["id"])))
            elif name == "add_job":
                if not data.get("company") or not data.get("title") or data.get("channel") not in CHANNELS:
                    raise ValueError("需要公司、职位和渠道")
                status = data.get("status") or "unknown"
                if status not in {"unknown", "open", "closed"}:
                    raise ValueError("请选择在招、状态待确认或已关闭")
                jid = self.upsert_job(c, {**data, "url": job_link(data["url"]), "status": status})
                notice = f"已保存岗位 #{jid}，先核对匹配和联系人"
            elif name == "authorize_recipient":
                jid = int(data.get("job_id") or 0)
                self.authorize_recipient(c, jid, data.get("address"), str(data.get("source") or "").strip())
                notice = "已核对收件人"
                try:
                    draft = self.prepare(c, jid, manual=True)
                    if draft:
                        notice += "并生成待批准的申请草稿"
                except ValueError as exc:
                    notice += "。未生成草稿 · " + str(exc)
            elif name in {"add_source", "resume_source", "pause_source"}:
                if name == "add_source":
                    source_id = self.add_source(c, data)
                    notice = "已保存公开来源并安排检查"
                else:
                    source_id = int(data.get("id") or 0)
                    if not one(c, "SELECT id FROM sources WHERE id=?", (source_id,)):
                        raise ValueError("没有这个公开来源")
                    c.execute("UPDATE sources SET enabled=? WHERE id=?", (name == "resume_source", source_id))
                    notice = "已恢复此公开来源" if name == "resume_source" else "已暂停此公开来源"
                if name != "pause_source":
                    self.enqueue(c, "scan", "official", "source-manual:" + uuid.uuid4().hex, payload={"source_id": source_id})
            elif name == "record_job_review":
                self.save_job_review(c, int(data.get("job_id") or 0), data.get("state"), data.get("source"), data.get("note"))
                notice = "已保存完整 JD 复核结果"
            elif name == "record_manual":
                jid = int(data.get("job_id") or 0)
                outcome, evidence = data.get("state"), str(data.get("evidence") or "").strip()
                job = one(c, "SELECT * FROM jobs WHERE id=?", (jid,))
                if not job or outcome not in {"contacted", "confirmed", "unknown", "rejected", "closed", "stopped"} or not evidence:
                    raise ValueError("须选择状态并填写真实平台证据")
                c.execute("""INSERT OR IGNORE INTO history(job_id,channel,company,title,status,source,evidence,occurred_at,fingerprint)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (jid, job["channel"], job["company"], job["title"], outcome,
                        "控制台人工核验", evidence[:2000], stamp(), fingerprint(jid, outcome, evidence)))
                c.execute("""UPDATE outbox SET state='stale',evidence='已有人工记录，停止自动外联',updated_at=?
                             WHERE job_id=? AND state IN ('draft','queued')""", (stamp(), jid))
                if outcome in {"rejected", "closed", "stopped"}:
                    c.execute("UPDATE threads SET stopped=1 WHERE job_id=?", (jid,))
                notice = "已保存人工核验结果并阻止重复外联"
            elif name in {"set_model", "model_probe", "model_match"}:
                if name == "set_model":
                    model_name = str(data.get("model_name") or "").strip()
                    calls, timeout = int(data.get("max_calls") or 0), int(data.get("timeout") or 75)
                    cost = str(data.get("cost_limit") or "").strip()
                    if ((model_name and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", model_name))
                            or not 0 <= calls <= 30 or not 20 <= timeout <= 180
                            or (cost and not re.fullmatch(r"\d+(\.\d{1,4})?", cost))):
                        raise ValueError("模型设置不合法")
                    set_setting(c, "model_config", {"name": model_name, "max_calls": calls, "timeout": timeout, "cost_limit": cost})
                jid = int(data.get("job_id") or 0) if name == "model_match" else None
                self.enqueue(c, "model_probe", "model", "model:" + uuid.uuid4().hex, payload={"job_id": jid})
                notice = "已安排 CLI 模型调用"
            else:
                raise ValueError("此操作不存在")
            self.event(c, "控制台 · " + notice, channel if channel in CHANNELS else None)
        self.wake.set()
        return notice

    def refresh_matches(self, c):
        prefs = self.preferences(c)
        for job in fetch(c, "SELECT * FROM jobs"):
            job["status"] = self.recruitment_status(job, prefs)
            match = self.job_match(c, job, prefs)
            c.execute("UPDATE jobs SET match_json=?,match_score=? WHERE id=?", (dump(match), match["score"], job["id"]))

    def describe_send_queue(self, c, outbox, prefs, stopped, paused, channels):
        current = stamp()
        authorized = self.send_once_authorization(c)
        provider_wait = setting(c, "provider_send_after:email", "")
        recent = one(c, """SELECT MAX(updated_at) AS last FROM outbox
                           WHERE state IN ('confirmed','sending','unknown') AND updated_at>=?""", (shift(-120),))
        cool_until = (datetime.fromisoformat(recent["last"]) + timedelta(seconds=120)).isoformat() if recent["last"] and not prefs["send_anytime"] else current
        daily = c.execute("""SELECT count(*) FROM outbox WHERE kind='application' AND state IN ('confirmed','sending','unknown')
                             AND substr(updated_at,1,10)=?""", (now().date().isoformat(),)).fetchone()[0]
        channels_by_name = {ch["name"]: ch for ch in channels}
        for item in outbox:
            state = item["state"]
            if state == "confirmed":
                continue
            if state != "queued":
                reasons = {"draft": "等待确认正文并批准发送", "sending": "正在复核岗位或等待邮件服务端响应",
                           "unknown": "提交结果待核验，系统会检查已发送目录", "failed": "发送失败，查看具体证据",
                           "stale": "草稿已失效，需要重新核对"}
                item["waiting_reason"] = item["evidence"] or reasons.get(state, "等待处理")
                continue
            channel = channels_by_name.get(item["channel"], {})
            if stopped:
                reason = "后台任务已暂停，恢复后继续"
            elif paused:
                reason = "外发已暂停，恢复发送后继续"
            elif channel.get("paused"):
                reason = "发送渠道已暂停。" + channel.get("detail", "")
            elif channel.get("status") != "ready":
                reason = "发送渠道尚未就绪。" + channel.get("detail", "")
            elif item["attempts"] >= 3:
                reason = "已达到尝试上限，需核对发送证据"
            elif error := self.validate_outbound(c, item):
                reason = error
            else:
                provider_blocked = item["channel"] == "email" and provider_wait > current
                due = max(item["due_at"] or current, cool_until, provider_wait if provider_blocked else "")
                once = authorized.get("id") == item["id"] and authorized.get("approval_hash") == item["approval_hash"]
                capped = not prefs["send_anytime"] and item["kind"] == "application" and daily >= int(prefs["daily_limit"])
                if capped:
                    tomorrow = (now() + timedelta(days=1)).date()
                    due = max(due, datetime.combine(tomorrow, datetime.min.time(), tzinfo=TZ).isoformat())
                expected = max(due, current) if once and not capped and due < authorized["expires_at"] else self.next_send_window(prefs, due)
                item["expected_send_at"] = expected
                if provider_blocked:
                    reason = "邮箱服务要求退避，等待恢复发送"
                elif capped:
                    reason = "已达到今天的申请额度，等待下一个发送窗口"
                elif expected and expected > due:
                    reason = "等待工作日发送窗口"
                elif item["retryable"] and due > current:
                    reason = "等待网络或邮件服务重试。" + (item["evidence"] or "")
                elif due > current:
                    reason = "等待发送间隔结束或到达排队时间"
                else:
                    reason = "等待后台复核并认领"
            item["waiting_reason"] = reason

    @staticmethod
    def next_task_time(c, channels, stopped):
        if stopped:
            return None
        queued = one(c, """SELECT MIN(t.due_at) AS due FROM tasks t LEFT JOIN channels ch ON ch.name=t.channel
                            WHERE t.state='queued' AND t.cancelled=0
                            AND (COALESCE(ch.paused,0)=0 OR t.kind IN ('probe','reconcile','verify_sent','mail_audit'))""")
        candidates = [ch["next_check"] for ch in channels if not ch["paused"] and ch["next_check"]]
        if queued["due"]:
            candidates.append(queued["due"])
        return max(stamp(), min(candidates)) if candidates else None

    def snapshot(self):
        today = now().date().isoformat()
        with self.store.tx() as c:
            jobs = fetch(c, """SELECT j.*,(SELECT external_id FROM job_aliases WHERE job_id=j.id ORDER BY rowid LIMIT 1) AS external_id
                               FROM jobs j ORDER BY CASE WHEN status IN ('open','active','recruiting') THEN 0 ELSE 1 END,
                               match_score DESC,created_at DESC LIMIT 700""")
            for job in jobs:
                job["manual_followup"] = manual_followup(c, job["id"])
                job["review"] = one(c, "SELECT * FROM job_reviews WHERE job_id=?", (job["id"],))
            threads = fetch(c, "SELECT * FROM threads ORDER BY updated_at DESC LIMIT 100")
            for thread in threads:
                thread["manual_followup"] = manual_followup(c, thread["job_id"])
                thread["messages"] = fetch(c, """SELECT m.*,a.state audit_state,a.evidence audit_evidence,a.history_id,p.source_url,
                        CASE WHEN a.state='unrelated' THEN 1 ELSE 0 END hide_body
                        FROM messages m LEFT JOIN mail_audits a ON m.id=a.message_id
                        LEFT JOIN platform_message_sources p ON m.id=p.message_id
                        WHERE m.thread_id=? ORDER BY m.occurred_at,m.id""", (thread["id"],))
            model_conf = {**{"name": "", "max_calls": 3, "timeout": 75, "cost_limit": ""}, **setting(c, "model_config", {})}
            model_last = one(c, "SELECT * FROM model_runs ORDER BY id DESC LIMIT 1")
            calls = c.execute("SELECT count(*) FROM model_runs WHERE substr(created_at,1,10)=?", (today,)).fetchone()[0]
            config = self.preferences(c)
            state = "stopped" if setting(c, "stopped", False) else "running"
            channels = fetch(c, "SELECT * FROM channels ORDER BY rowid")
            paused = setting(c, "outbound_paused", False)
            outbox = fetch(c, """SELECT o.*,j.company,j.title,
                     CASE WHEN o.state='stale' OR (t.revision IS NOT NULL AND t.revision<>o.thread_revision) THEN 0 ELSE 1 END draft_valid
                     FROM outbox o LEFT JOIN jobs j ON j.id=o.job_id LEFT JOIN threads t ON t.id=o.thread_id
                     ORDER BY o.id DESC LIMIT 250""")
            self.describe_send_queue(c, outbox, config, state == "stopped", paused, channels)
            next_send = min((item["expected_send_at"] for item in outbox if item.get("expected_send_at")), default=None)
            return {
                "demo": self.config.get("demo") is True or setting(c, "demo_fixture", False),
                "state": {"state": state, "status": state, "heartbeat": self.last_heartbeat,
                          "paused_all": paused, "next_send_at": next_send,
                          "next_task_at": self.next_task_time(c, channels, state == "stopped"),
                          "live_armed": state == "running" and self.outbound_enabled(c)
                          and any(x["name"] == "email" and x["status"] == "ready" and not x["paused"] for x in channels)},
                "stats": {"found_today": sum(1 for j in jobs if j["created_at"].startswith(today) and j["status"] != "unknown"),
                          "screened_today": sum(1 for j in jobs if j["observed_at"].startswith(today) and j["status"] != "unknown"),
                          "sent_today": c.execute("""SELECT count(*) FROM history WHERE status='confirmed' AND source='Harness 发送'
                                                 AND substr(occurred_at,1,10)=?""", (today,)).fetchone()[0],
                          "decisions_open": c.execute("SELECT count(*) FROM decisions WHERE state='pending'").fetchone()[0],
                          "queued_applications": c.execute("SELECT count(*) FROM outbox WHERE kind='application' AND state='queued'").fetchone()[0]},
                "channels": channels, "jobs": jobs, "threads": threads,
                "outbox": outbox, "browser_actions": self.browser_actions.list(c),
                "decisions": fetch(c, """SELECT d.*,t.job_id FROM decisions d LEFT JOIN threads t ON t.id=d.thread_id
                        ORDER BY d.state='pending' DESC,d.due_at IS NULL,d.due_at,d.id DESC LIMIT 200"""),
                "facts": fetch(c, "SELECT * FROM facts ORDER BY key"), "resumes": fetch(c, "SELECT * FROM resumes ORDER BY modified_at DESC"),
                "exclusions": fetch(c, "SELECT * FROM exclusions ORDER BY pattern"), "preferences": config,
                "sources": fetch(c, "SELECT * FROM sources ORDER BY id"),
                "history": fetch(c, "SELECT * FROM history ORDER BY occurred_at DESC,id DESC LIMIT 500"),
                "tasks": fetch(c, "SELECT * FROM tasks ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,id DESC LIMIT 90"),
                "task_steps": fetch(c, "SELECT * FROM task_steps ORDER BY id DESC LIMIT 650"),
                "events": fetch(c, "SELECT * FROM events ORDER BY id DESC LIMIT 180"),
                "model": {**model_conf, "adapter": "Codex CLI", "configured_name": model_conf["name"],
                          "name": model_conf["name"] or (model_last["model"] if model_last else "CLI 默认，待核验"),
                          "status": model_last["state"] if model_last else "unverified", "calls_today": calls,
                          "cost": "未知", "detail": model_last["detail"] if model_last else "未运行实际调用",
                          "input_tokens": model_last["input_tokens"] if model_last else None,
                          "output_tokens": model_last["output_tokens"] if model_last else None},
            }

    def export_csv(self):
        result = io.StringIO()
        writer = csv.writer(result)
        writer.writerow(["渠道", "公司", "岗位", "地点", "薪资", "职位链接", "状态", "时间（上海）", "来源或执行器",
                         "实际收件人", "主题", "正文", "简历", "简历 SHA-256", "Message-ID", "证据"])
        with self.store.tx() as c:
            entries = c.execute("""SELECT h.channel,h.company,h.title,j.location,j.salary,j.url,h.status,h.occurred_at,
                       h.source,h.recipient,NULL,NULL,NULL,NULL,NULL,h.evidence FROM history h LEFT JOIN jobs j ON j.id=h.job_id
                       WHERE h.source<>'Harness 发送'
                       UNION ALL
                       SELECT o.channel,j.company,j.title,j.location,j.salary,j.url,o.state,o.updated_at,
                       'Harness',o.to_address,o.subject,o.body,r.label,o.attachment_sha,o.message_id,o.evidence FROM outbox o
                       LEFT JOIN jobs j ON j.id=o.job_id LEFT JOIN resumes r ON r.id=o.resume_id
                       ORDER BY 8 DESC""").fetchall()
        for entry in entries:
            writer.writerow([(("'" if str(v or "").lstrip().startswith(("=", "+", "-", "@")) else "") + str(v or "")) for v in entry])
        return ("\ufeff" + result.getvalue()).encode("utf-8")
