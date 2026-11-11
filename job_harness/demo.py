"""Seed the real workflow with fictional records and an in-process mailbox."""
import hashlib
import json
from pathlib import Path

from .service import Harness, set_setting, setting
from .storage import Store, one, stamp


def role(slug, company, title, description):
    base = f"https://{slug}.example/careers"
    return {
        "channel": "official", "external_id": slug, "url": base + "/" + slug,
        "domain": f"{slug}.example", "company": company, "title": title,
        "location": "示例市", "salary": "18–26K", "status": "open",
        "description": description,
        "requirements": "接受个人项目，重视产品设计、AI 应用实践和测试验证。希望能解释产品取舍。",
        "recipient": f"careers@{slug}.example", "recipient_source": base,
    }


ROLES = (
    role("paper-orbit", "纸环实验室（虚构）", "对话产品经理",
         "负责 AI 角色对话与语音交互产品的需求梳理、原型设计和体验评测，根据测试结果迭代。"),
    role("moon-workshop", "月栈工坊（虚构）", "陪伴机器人交互产品",
         "负责桌面陪伴机器人的 AI 语音交互和配套 App 产品设计，验证交互原型并迭代。"),
    role("cloud-lantern", "云盏系统（虚构）", "AI 应用产品",
         "负责 AI 工作流应用的产品设计、需求和原型评测，整理反馈并推进功能迭代。"),
)


def synthetic_pdf():
    stream = b"BT /F1 20 Tf 60 740 Td (Fictional resume - Job Search Harness demo) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(pdf)


class OfflineMailbox:
    """No SMTP, browser or other outbound I/O."""
    def send(self, item, attachment):
        if not (item["recipient"].endswith(".example") and attachment and item["_before_submit"]()):
            raise ValueError("演示只接受虚构收件人与已核对附件")
        return {
            "state": "confirmed", "evidence": "离线模拟回执：仅本地记录，没有连接邮箱或发送邮件",
            "provider_id": "demo-receipt-001",
        }


class OfflineRuntime:
    """Even changing UI controls cannot open a real provider connection."""
    def probe(self):
        return {"status": "disabled", "detail": "离线演示 · 不连接外部渠道", "capabilities": {}}

    def discover(self, _sources):
        return []

    def read_messages(self, **_evidence):
        return []

    def reconcile(self, _item):
        return {"state": "unknown", "evidence": "离线演示不查询外部回执"}


def seed(root, config):
    store = Store(root)
    with store.tx() as c:
        if setting(c, "demo_fixture"):
            return False
    mailbox = OfflineMailbox()
    harness = Harness(store, {**config, "email": {"account": "candidate@harness.example"}}, {"email": mailbox})
    resume = root / "resumes" / "fictional-resume.pdf"
    resume.parent.mkdir(exist_ok=True)
    resume.write_bytes(synthetic_pdf())
    harness.ingest_legacy({
        "preferences": {"cities": "示例市", "target_scope": "虚构的 AI 交互与应用岗位",
                        "directions": "对话,交互,工作流", "send_anytime": True, "auto_send": True},
        "facts": [
            {"key": "summary_dialogue", "value": "我设计过角色对话原型，做过多轮交互评测并按结果迭代。",
             "source": "离线演示 · 虚构经历"},
            {"key": "summary_app", "value": "我从需求到评测做过一款工作流工具原型，并根据反馈迭代。",
             "source": "离线演示 · 虚构经历"},
        ],
        "resumes": [{"label": "演示 PDF · 虚构", "path": str(resume), "scope": ["ai", "robotics"],
                     "sha256": hashlib.sha256(resume.read_bytes()).hexdigest(), "approved": True, "modified_at": stamp()}],
    })
    with store.tx(True) as c:
        ids = [harness.upsert_job(c, row) for row in ROLES]
        alias = {**ROLES[1], "channel": "liepin", "external_id": "demo-cross-listing",
                 "url": "https://jobs.harness.example/demo-cross-listing"}
        if harness.upsert_job(c, alias) != ids[1]:
            raise RuntimeError("演示的跨渠道去重未通过")
        harness.channel_update(c, "email", "ready", "仅供离线模拟，不连接邮箱")
        existing = one(c, "SELECT * FROM outbox WHERE job_id=? AND kind='application'", (ids[0],))
        if not existing and not harness.prepare(c, ids[0]):
            raise RuntimeError("无法创建离线演示申请")
    harness.send_one()
    with store.tx(True) as c:
        set_setting(c,"candidate_profile",{
            "summary":"虚构演示：设计过角色对话原型，完成交互测试并根据反馈迭代。",
            "intro":"虚构演示：做过对话产品设计与测试。",
            "confirmed":True,"source":"虚构演示数据","updated_at":stamp()})
        for jid, salary in zip(ids, ("人民币 18–26K/月 · 13薪", "人民币 20–28K/月", "")):
            c.execute("UPDATE jobs SET salary=? WHERE id=?",(salary,jid))

        confirmed = one(c, "SELECT * FROM outbox WHERE job_id=? AND state='confirmed'", (ids[0],))
        if not confirmed or confirmed["provider_id"] != "demo-receipt-001":
            raise RuntimeError("未取得离线模拟回执")
        if not one(c, "SELECT id FROM outbox WHERE job_id=?", (ids[1],)):
            harness.prepare(c, ids[1], manual=True)
        harness.ingest_message(c, {
            "channel": "email", "external_id": "demo-interview-inbound",
            "thread_external_id": confirmed["message_id"], "job_id": ids[0],
            "message_id": "<demo-interview@paper-orbit.example>", "references": [confirmed["message_id"]],
            "company": ROLES[0]["company"], "sender": ROLES[0]["recipient"],
            "subject": "Re: 对话产品经理 · 面试邀请（虚构）",
            "body": "您好，想邀请您参加 30 分钟的线上面试。下周二下午或周三上午，您哪个时间方便？",
        })
        c.execute("UPDATE tasks SET state='stopped',cancelled=1 WHERE kind='verify_sent' AND state='queued'")
        set_setting(c, "preferences", {**harness.preferences(c), "auto_send": False, "send_anytime": False})
        set_setting(c, "outbound_paused", True)
        set_setting(c, "demo_fixture", True)
        harness.channel_update(c, "email", "disabled", "离线演示 · 不连接邮箱")
        harness.event(c, "离线演示就绪：已生成虚构岗位、草稿、模拟回执和待决定事项。", key="demo-ready")
    return True


def initialize(root, example_config, load_config, runtime_locks, write_private):
    root = Path(root)
    config_path = root / "config.json"
    with runtime_locks(root, ""):
        if config_path.exists():
            config = load_config(root)
            if config.get("demo") is not True:
                raise ValueError("该目录已有非演示配置。请通过 JOB_HARNESS_DATA_DIR 选择独立目录。")
        elif (root / "harness.sqlite3").exists():
            raise ValueError("该目录已有数据库。请通过 JOB_HARNESS_DATA_DIR 选择独立目录。")
        else:
            config = {**example_config, "demo": True}
            write_private(config_path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode())
        seed(root, config)
    print("离线演示已就绪。资料全为虚构，不会发邮件。")
