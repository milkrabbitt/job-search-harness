"""Explicit candidate input, extraction review and narrow channel consent."""
import json

from .service import fingerprint, setting, set_setting
from .storage import fetch, one, stamp


def yes(value):
    return value in (True, "true", "on", "1")


def invalidate(service, c):
    for row in fetch(c,"""SELECT * FROM outbox WHERE kind='reply' AND state IN ('draft','queued')
                         AND attempts=0 AND provider_id IS NULL AND anchor_id IS NOT NULL"""):
        c.execute("""INSERT INTO decisions(thread_id,message_id,kind,question,draft,state,created_at,updated_at)
                     VALUES(?,?,'experience','经历变更，原回复需重新核对','','pending',?,?)
                     ON CONFLICT(message_id,kind) DO UPDATE SET question=excluded.question,state='pending',
                     resolution=NULL,updated_at=excluded.updated_at""",
                     (row["thread_id"],row["anchor_id"],stamp(),stamp()))
    c.execute("""UPDATE outbox SET state='stale',approved=0,approval_hash=NULL,evidence='经历或简历已更新，请重新生成申请',updated_at=?
                 WHERE kind IN ('application','reply') AND state IN ('draft','queued')""", (stamp(),))
    service.refresh_matches(c)
    from .experience_flow import schedule
    for row in fetch(c,"SELECT id FROM jobs WHERE status NOT IN ('closed','rejected','stopped') ORDER BY match_score DESC LIMIT 50"):
        schedule(service,c,row["id"])


def receive_upload(service, filename, content, fields):
    from .resume_upload import stage_upload
    with service.store.tx() as c:
        if service.config.get("demo") or setting(c, "demo_fixture", False):
            raise ValueError("离线演示不保存真实简历；请使用独立的资料目录。")
    scope = str(fields.get("scope") or "general")
    record = stage_upload(service.store.root, filename, content, str(fields.get("label") or ""), [scope],
                          yes(fields.get("approved")))
    with service.store.tx(True) as c:
        c.execute("""INSERT INTO resumes(label,path,sha256,modified_at,scope,approved) VALUES(?,?,?,?,?,?)
             ON CONFLICT(sha256) DO UPDATE SET label=excluded.label,scope=excluded.scope,approved=excluded.approved""",
             (record["label"],record["path"],record["sha256"],stamp(),",".join(record["scope"]),bool(record["approved"])))
        rid = one(c, "SELECT id FROM resumes WHERE sha256=?", (record["sha256"],))["id"]
        extracted = record["extraction"]
        previous = one(c, "SELECT text,state FROM resume_texts WHERE resume_id=?", (rid,))
        content = str(extracted.get("text") or "")[:24000]
        if len(str(extracted.get("text") or "")) > 24000:
            extracted["truncated"] = True
            extracted["detail"] += " 当前页面只保留前 24000 字用于核对。"
        confirmed = bool(previous and previous["state"] == "confirmed")
        if confirmed:
            content = previous["text"]
        c.execute("""INSERT INTO resume_texts VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(resume_id) DO UPDATE SET text=excluded.text,status=excluded.status,detail=excluded.detail,
              pages=excluded.pages,truncated=excluded.truncated,state=excluded.state,updated_at=excluded.updated_at""",
              (rid, content, extracted["status"], extracted["detail"],extracted.get("pages"),
               bool(extracted.get("truncated")), "confirmed" if confirmed else "pending",
               "resume:" + record["sha256"],stamp()))
        invalidate(service, c)
        service.event(c, "页面上传已保存为本机简历版本，提取内容等待本人核对", key="upload:"+record["sha256"])
    service.wake.set()
    return "PDF 已保存。请在下方核对提取文字；扫描件或无法提取的内容可以手动补充。"


def save(service, name, data):
    with service.store.tx(True) as c:
        if service.config.get("demo") or setting(c, "demo_fixture", False):
            raise ValueError("离线演示不能保存真实个人资料或启用外发。")
        if name == "save_candidate_profile":
            summary = str(data.get("summary") or "").strip()
            if not summary or len(summary) > 6000 or not yes(data.get("confirmed")):
                raise ValueError("请填写最多 6000 字的真实经历，并确认内容。")
            intro = str(data.get("intro") or "").strip()
            if len(intro)>600:
                raise ValueError("对外简介请控制在 600 字以内。")
            set_setting(c, "candidate_profile", {"summary":summary,"intro":intro,"confirmed":True,"source":"本人页面确认","updated_at":stamp()})
            prefs = service.preferences(c)
            prefs["profile_model_consent"] = yes(data.get("profile_model_consent"))
            set_setting(c, "preferences", prefs)
            invalidate(service, c)
            notice = "经历已保存。模型只会在你同意后接收用于匹配的裁剪内容。"
        elif name == "confirm_resume_text":
            rid = int(data.get("id") or 0)
            row = one(c, "SELECT resume_id FROM resume_texts WHERE resume_id=?", (rid,))
            text = str(data.get("text") or "").strip()
            if not row or not text or len(text) > 24000 or not yes(data.get("confirmed")):
                raise ValueError("请核对并确认最多 24000 字的简历文字；无法提取的部分可手动补充。")
            c.execute("UPDATE resume_texts SET text=?,state='confirmed',updated_at=? WHERE resume_id=?", (text,stamp(),rid))
            invalidate(service, c)
            notice = "此版本的经历文字已确认，可用于本机匹配。PDF 原件未改动。"
        elif name == "save_followup_authorization":
            enabled = yes(data.get("enabled"))
            if enabled:
                if not yes(data.get("scope_confirmed")):
                    raise ValueError("请确认自动处理范围，面试和薪资等承诺仍由你决定。")
                from .onboarding import required
                email = one(c, "SELECT status,paused FROM channels WHERE name='email'")
                if required(c) or not service.email_enabled() or not email or email["status"] != "ready" or email["paused"]:
                    raise ValueError("请先完成配置并核验邮箱收发；登录或平台限制不能跳过。")
            prefs = service.preferences(c)
            prefs["auto_send"] = enabled
            prefs["allow_follow_up"] = enabled and yes(data.get("allow_follow_up"))
            set_setting(c, "preferences", prefs)
            set_setting(c, "outbound_paused", not enabled)
            set_setting(c, "followup_authorization", {"email":enabled,"source":"本人页面授权","updated_at":stamp()})
            notice = "邮箱自动处理已授权；后台按规则查收、回复和保存记录。" if enabled else "自动外发已暂停，记录和查收保留。"
        else:
            raise ValueError("此资料操作不存在。")
        service.event(c, notice)
    service.wake.set()
    return notice


def snapshot(service, c):
    return {
        "profile": setting(c, "candidate_profile", {}),
        "resume_texts": fetch(c, """SELECT t.*,r.label,r.sha256,r.approved FROM resume_texts t
             JOIN resumes r ON r.id=t.resume_id ORDER BY t.updated_at DESC"""),
        "authorization": setting(c, "followup_authorization", {}),
    }
