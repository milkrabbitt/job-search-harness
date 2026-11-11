"""The actual durable worker gates applications on current candidate evidence."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from job_harness.demo import synthetic_pdf
from job_harness.service import Harness, set_setting
from job_harness.storage import Store, fetch


ROLE = {
    "channel":"official","company":"纸桥实验室（虚构）","title":"AI 对话产品经理",
    "location":"示例市","salary":"人民币 20–30K/月","status":"open",
    "external_id":"fictional-dialogue","url":"https://paper-bridge.example/careers/dialogue",
    "domain":"paper-bridge.example","recipient":"jobs@paper-bridge.example",
    "recipient_source":"https://paper-bridge.example/careers",
    "description":"负责角色对话交互原型的产品设计与体验评测，梳理需求，针对测试结果迭代应用。",
    "requirements":"接受个人项目，希望有角色对话应用、原型设计和回归测试的真实实践。",
}


class Mail:
    def __init__(self): self.calls=[]
    def send(self,item,attachment):
        assert item["_before_submit"]()
        self.calls.append(item)
        return {"state":"confirmed","evidence":"隔离测试回执，没有网络外发","provider_id":"fixture-receipt"}
    def read_messages(self,**options): return []


class MatchModel:
    def __init__(self,fit="related",hook=None,malformed=False):
        self.fit,self.hook,self.malformed,self.calls=fit,hook,malformed,[]
    def evaluate(self,config,evidence=None):
        self.calls.append(evidence)
        if self.hook:self.hook()
        text="确认经历包含角色对话应用，与 JD 的交互职责有关。"
        return {"state":"done","model":"fixture-model","detail":text,
            "advice":{"summary":text,"review":[],"fit":self.fit,
                "strengths":[{"text":text,"references":["candidate.profile.summary","job.requirements"]}] if self.fit=="related" else [],
                "gaps":[],"unknowns":[{"text":"需进一步核对","references":["job.requirements"]}] if self.fit!="related" else []}
            if not self.malformed else {"summary":"无引用","review":[]}}


class CandidateSchedulerTests(TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.mail=Mail()
        self.model=MatchModel()
        self.h=Harness(Store(self.root/"private"),{"email":{"account":"candidate@fixture.example"},
                       "model":{"enabled":True,"max_calls":50}},{"email":self.mail},model=self.model)
        pdf=self.root/"fictional.pdf"
        pdf.write_bytes(synthetic_pdf())
        self.h.ingest_legacy({
            "preferences":{"cities":"示例市","auto_send":True,"send_anytime":True},
            "resumes":[{"path":str(pdf),"label":"虚构简历","sha256":hashlib.sha256(pdf.read_bytes()).hexdigest(),
                        "scope":["ai","robotics"],"approved":True}],
        })
        self.save_profile()
        with self.h.store.tx(True) as c:
            self.h.channel_update(c,"email","ready","隔离邮箱")
            set_setting(c,"outbound_paused",False)

    def save_profile(self,**change):
        self.h.action("save_candidate_profile",{
            "summary":"我设计过角色对话应用，做过交互原型和测试。",
            "intro":"我完成过角色对话应用的产品设计与交互测试。",
            "confirmed":"on","profile_model_consent":"on",**change})

    def rows(self,sql):
        with self.h.store.tx() as c:return fetch(c,sql)

    def discover(self,**change):
        with self.h.store.tx(True) as c:
            jid=self.h.upsert_job(c,{**ROLE,**change})
            self.assertIsNone(self.h.prepare(c,jid))
        return jid

    def test_worker_uses_real_profile_before_preparing_and_sends_only_once(self):
        jid=self.discover()
        self.assertFalse(self.rows("SELECT * FROM outbox"))
        self.assertTrue(self.h.task_one())
        self.assertIn("角色对话应用",self.model.calls[0]["candidate"]["items"][0]["text"])
        self.assertEqual(self.model.calls[0]["job"]["requirements"],ROLE["requirements"])
        self.assertEqual([r["state"] for r in self.rows("SELECT * FROM outbox")],["queued"])
        self.assertTrue(self.h.send_one())
        self.assertFalse(self.h.send_one())
        self.assertEqual(len(self.mail.calls),1)
        self.assertEqual(self.rows("SELECT state FROM outbox")[0]["state"],"confirmed")
        self.assertEqual(self.rows("SELECT status FROM history")[0]["status"],"confirmed")
        self.assertIn("角色对话",self.mail.calls[0]["body"])
        self.assertTrue(self.h.snapshot()["candidate"]["profile"]["confirmed"])

    def test_unrelated_or_uncited_advice_never_unlocks_application(self):
        for fit,invalid in (("not_related",False),("related",True)):
            with self.subTest(fit=fit,invalid=invalid):
                self.model.fit,self.model.malformed=fit,invalid
                self.discover(external_id=fit+str(invalid),url=ROLE["url"]+fit+str(invalid),
                              title=ROLE["title"]+" "+fit+str(invalid))
                self.assertTrue(self.h.task_one())
                self.assertFalse(self.rows("SELECT * FROM outbox"))
        self.assertIn("failed",[r["state"] for r in self.rows("SELECT * FROM candidate_reviews")])

    def test_new_profile_invalidates_in_flight_model_result(self):
        self.discover()
        self.model.hook=lambda:self.save_profile(summary="我做过餐饮供应链整理，尚未做对话项目。")
        self.h.task_one()
        self.assertFalse(self.rows("SELECT * FROM candidate_reviews"))
        self.assertFalse(self.rows("SELECT * FROM outbox"))

    def test_hard_exclusion_remains_blocking_after_model_related_result(self):
        jid=self.discover(description=ROLE["description"]+"主要负责电话销售，承担销售业绩指标。")
        self.h.invoke_model_probe(jid)
        self.assertFalse(self.rows("SELECT * FROM outbox"))
        self.assertFalse(self.mail.calls)

    def test_routine_email_progresses_then_interview_hands_thread_to_person(self):
        jid=self.discover()
        self.h.task_one()
        self.h.send_one()
        sent=self.rows("SELECT * FROM outbox")[0]
        def incoming(key,body):
            with self.h.store.tx(True) as c:
                return self.h.ingest_message(c,{
                    "channel":"email","external_id":key,"message_id":"<"+key+"@paper-bridge.example>",
                    "thread_external_id":sent["message_id"],"job_id":jid,
                    "sender":ROLE["recipient"],"company":ROLE["company"],
                    "subject":"Re: 对话产品岗位", "body":body,"references":[sent["message_id"]]})
        incoming("routine-interest","对这个岗位感兴趣吗？")
        replies=self.rows("SELECT * FROM outbox WHERE kind='reply'")
        self.assertEqual(len(replies),1)
        self.assertIn("面试流程",replies[0]["body"])
        incoming("interview-invite","想邀请您下周二面试，请确认具体时间。")
        self.assertEqual(self.rows("SELECT kind FROM decisions WHERE state='pending'")[0]["kind"],"interview")
        incoming("routine-after-invite","请简单介绍一下自己？")
        self.assertEqual(len(self.rows("SELECT * FROM outbox WHERE kind='reply'")),1)
        self.assertFalse(self.h.send_one())
        self.assertEqual(len(self.mail.calls),1)
