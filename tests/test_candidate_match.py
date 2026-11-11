import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from job_harness.candidate_match import (
    ADVICE_SCHEMA, build_evidence, context_fingerprint, parse_advice,
    profile_overlap, reference_index, sanitize_evidence, validate_advice,
)
from job_harness.model import CodexModel
from job_harness.model_backends import HTTPModel


JOB = {"title": "对话产品经理", "location": "示例市", "salary": "15–25K",
       "description": "负责角色对话产品设计，梳理交互原型并进行回归评测。",
       "requirements": "需要角色对话项目经验，能够说明产品设计和测试取舍。",
       "url": "https://fictional.example/jobs/one", "recruiter": "unrelated contact"}
ASSESSMENT = {"state": "match", "score": 80, "scope": "ai", "hard": [], "unknown": ["招聘日期"],
              "strengths": ["对话、记忆与交互", "软件产品交付"]}


def context(**changes):
    return build_evidence(JOB, {"cities": "示例市", "directions": "对话", "api_key": "not a preference"},
                          facts=[{"id": 4, "key": "summary_dialogue", "value": "设计过角色对话项目，进行过交互原型测试。",
                                  "state": "verified", "source": "本人确认"}], assessment=ASSESSMENT, **changes)


def advice(**changes):
    result = {"summary": "角色对话项目与岗位有直接关联", "review": ["核实岗位的评测流程"], "fit": "related",
              "strengths": [{"text": "有已确认的角色对话原型经历，与JD所需方向相关。",
                             "references": ["candidate.fact.4", "job.requirements"]}],
              "gaps": [], "unknowns": []}
    result.update(changes)
    return result


class CandidateContextTests(unittest.TestCase):
    def test_only_confirmed_nonconflicting_facts_and_approved_reviewed_pdf_are_used(self):
        facts = [
            {"id": 1, "key": "skills", "value": "Python", "state": "verified", "source": "本人"},
            {"id": 2, "key": "skills", "value": "精通算法", "state": "pending", "source": "未核实"},
            {"id": 3, "key": "experience", "value": "量产机器人", "state": "pending", "source": "未核实"},
            {"id": 4, "key": "education", "value": "虚构专业硕士", "state": "confirmed", "source": "本人"},
            {"id": 5, "key": "history", "value": "A", "state": "verified", "source": "一份资料"},
            {"id": 6, "key": "history", "value": "B", "state": "verified", "source": "另一份资料"},
        ]
        pdfs = [{"resume_id": 1, "text": "已确认的项目", "status": "extracted", "state": "confirmed", "approved": 1},
                {"resume_id": 2, "text": "尚未核对的PDF", "status": "extracted", "state": "pending", "approved": 1},
                {"resume_id": 3, "text": "已停用附件", "status": "extracted", "state": "confirmed", "approved": 0}]
        evidence = build_evidence(JOB, {}, facts, profile={"summary": "未核实的摘要", "confirmed": False}, resume_texts=pdfs)
        refs = reference_index(evidence)
        self.assertEqual({key for key in refs if key.startswith("candidate.")}, {"candidate.fact.4", "candidate.resume.1"})
        self.assertNotIn("Python", json.dumps(evidence))
        self.assertNotIn("尚未", json.dumps(evidence, ensure_ascii=False))

    def test_identifying_contact_values_and_unrelated_fields_do_not_reach_prompt(self):
        facts = [{"id": 1, "key": "identity.name", "value": "样例同学", "state": "confirmed", "source": "本人"},
                 {"id": 2, "key": "summary_app", "value": "样例同学做过交互原型。邮箱person@fictional.example，手机13812345678。",
                  "state": "verified", "source": "/Users/fictional/resume.pdf"}]
        evidence = build_evidence(JOB, {"cities": "示例市", "api_key": "should-be-dropped"}, facts)
        text = json.dumps(evidence, ensure_ascii=False)
        for secret in ("样例同学", "person@fictional.example", "13812345678", "/Users/", "should-be-dropped", "fictional.example/jobs"):
            self.assertNotIn(secret, text)
        self.assertIn("交互原型", text)

    def test_confirmed_manual_transcription_is_usable_without_claiming_extraction(self):
        pdfs = [{"resume_id": 1, "text": "人工核对的角色对话项目", "status": "needs_manual", "state": "confirmed", "approved": 1},
                {"resume_id": 2, "text": "还没核对的项目", "status": "needs_manual", "state": "pending", "approved": 1}]
        refs = reference_index(build_evidence(JOB, {}, resume_texts=pdfs))
        self.assertEqual(refs["candidate.resume.1"], "人工核对的角色对话项目")
        self.assertNotIn("candidate.resume.2", refs)

    def test_labeled_secrets_and_instruction_fields_cannot_expand_context(self):
        evidence = context(profile={"summary": "交互项目。api_key=fictional-small-secret 密码:fictional-password", "confirmed": True})
        evidence["candidate"]["action"] = "enable automatic sending"
        bounded = sanitize_evidence(evidence)
        output = json.dumps(bounded, ensure_ascii=False)
        self.assertNotIn("fictional-small-secret", output)
        self.assertNotIn("fictional-password", output)
        self.assertNotIn("enable automatic", output)

    def test_context_is_bounded_idempotent_and_hash_tracks_relevant_changes(self):
        evidence = context(profile={"summary": "项目实践" * 9000, "confirmed": True})
        self.assertLess(len(json.dumps(evidence)), 64000)
        self.assertEqual(sanitize_evidence(evidence), evidence)
        self.assertEqual(context_fingerprint(evidence), context_fingerprint(sanitize_evidence(evidence)))
        self.assertNotEqual(context_fingerprint(evidence), context_fingerprint(context()))
        copy = dict(evidence, unrelated_private_message="not allowed")
        self.assertEqual(context_fingerprint(evidence), context_fingerprint(copy))

    def test_literal_overlap_has_citations_without_qualification_or_score_claims(self):
        result = profile_overlap(context())
        self.assertEqual(result["state"], "related")
        self.assertTrue(result["strengths"])
        self.assertNotIn("score", result)
        for entry in result["strengths"]:
            self.assertTrue(set(entry["references"]).issubset(reference_index(context())))
        missing = build_evidence(JOB, {}, profile={"summary": "没有确认", "confirmed": False})
        self.assertEqual(profile_overlap(missing)["state"], "missing")
        unrelated = build_evidence(JOB, {}, profile={"summary": "店铺陈列与仓库盘点", "confirmed": True})
        self.assertEqual(profile_overlap(unrelated)["state"], "unclear")


class CandidateAdviceTests(unittest.TestCase):
    def test_related_requires_citations_to_existing_candidate_and_jd_evidence(self):
        self.assertEqual(validate_advice(advice(), context())["fit"], "related")
        malformed = [advice(fit="probability"), advice(strengths=[]),
                     advice(strengths=[{"text": "虚构能力", "references": ["candidate.fact.999", "job.requirements"]}]),
                     advice(strengths=[{"text": "只有JD", "references": ["job.requirements"]}]),
                     dict(advice(), action="send"), {"summary": "looks fine", "review": []}]
        for result in malformed:
            with self.subTest(result=result), self.assertRaises(ValueError):
                validate_advice(result, context())

    def test_probe_compatibility_does_not_create_unsupported_candidate_fit(self):
        self.assertEqual(parse_advice('{"summary":"连接正常","review":[]}', None)["summary"], "连接正常")
        self.assertEqual(validate_advice(advice(fit="unclear", strengths=[]), None)["fit"], "unclear")
        with self.assertRaises(ValueError):
            validate_advice(advice(), None)

    def test_secret_is_redacted_before_output_truncation(self):
        secret = "fictional-provider-key-" + "a" * 400
        result = validate_advice(advice(summary=secret), context(), secret)
        self.assertNotIn(secret[:30], json.dumps(result))

    def test_cli_receives_actual_candidate_and_jd_and_same_structured_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex"
            executable.write_text("fixture executable")
            def complete(args, **kwargs):
                schema_path = Path(args[args.index("--output-schema") + 1])
                self.assertEqual(json.loads(schema_path.read_text()), ADVICE_SCHEMA)
                Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(advice()), encoding="utf-8")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            model = CodexModel({"model": {"executable": str(executable)}})
            with patch("job_harness.model.subprocess.run", side_effect=complete) as run:
                result = model.evaluate({"name": "fixture", "timeout": 5}, context())
            prompt = run.call_args.kwargs["input"]
            self.assertIn("角色对话项目", prompt)
            self.assertIn("job.requirements", prompt)
            self.assertEqual(result["advice"]["fit"], "related")
            self.assertEqual(result["state"], "done")

    def test_http_protocols_share_candidate_context_and_validate_model_references(self):
        for provider in ("openai_compatible", "anthropic"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                config = {"provider": provider, "name": "fixture", "base_url": "https://model.fictional.example/v1"}
                model = HTTPModel(config, Path(directory))
                content = json.dumps(advice())
                response = ({"content": [{"type": "text", "text": content}], "usage": {}} if provider == "anthropic" else
                            {"choices": [{"message": {"content": content}}], "usage": {}})
                opener = Mock()
                opener.open.return_value = io.BytesIO(json.dumps(response).encode())
                with patch("job_harness.model_backends.read_model_key", return_value="fixture-key"), \
                        patch("job_harness.model_backends.build_opener", return_value=opener):
                    result = model.evaluate({}, context())
                payload = json.loads(opener.open.call_args.args[0].data)
                prompt = payload["messages"][-1]["content"]
                self.assertIn("角色对话项目", prompt)
                self.assertIn(JOB["requirements"], prompt)
                self.assertNotIn("fixture-key", prompt)
                self.assertEqual(result["state"], "done")
                self.assertEqual(result["advice"]["fit"], "related")
