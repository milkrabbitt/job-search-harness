"""Candidate evidence and current-review gates; model advice cannot grant authority."""
import json

from . import rules
from .service import fingerprint, setting, set_setting
from .storage import fetch, one, stamp


def evidence(service, c, job, assessment=None):
    from .candidate_match import build_evidence
    return build_evidence(
        job, service.preferences(c),
        facts=fetch(c, "SELECT * FROM facts ORDER BY id"),
        profile=setting(c, "candidate_profile", {}),
        resume_texts=fetch(c, """SELECT t.*,r.label,r.sha256,r.approved FROM resume_texts t
                            JOIN resumes r ON r.id=t.resume_id WHERE t.state='confirmed' AND r.approved=1"""),
        assessment=assessment,
    )


def model_input(service, c, job):
    prefs = service.preferences(c)
    if not prefs.get("profile_model_consent"):
        return None
    from .onboarding import effective_model_config
    try:
        config = effective_model_config(service, c)
    except ValueError:
        return None
    if not config.get("enabled") or config.get("provider") == "none":
        return None
    result = evidence(service,c,job)
    if not result.get("candidate",{}).get("items"):
        return None
    return result


def review_hash(service, c, context):
    from .candidate_match import context_fingerprint
    from .onboarding import config_hash, effective_model_config
    return fingerprint(context_fingerprint(context), config_hash(service,effective_model_config(service,c)))


def decorate(service, c, job, assessment):
    from .candidate_match import context_fingerprint, profile_overlap, reference_index
    context = evidence(service,c,job,assessment)
    overlap = profile_overlap(context)
    assessment["candidate_overlap"] = overlap
    current = model_input(service,c,job)
    if current is None:
        return assessment
    digest = review_hash(service,c,current)
    review = one(c, "SELECT * FROM candidate_reviews WHERE job_id=? AND context_hash=?", (job.get("id"),digest))
    result = json.loads(review["result_json"]) if review and review["state"]=="done" else {}
    advice = result.get("advice",{})
    assessment["candidate_review"] = {"state":review["state"] if review else "pending", "advice":advice,
                                     "model":review["model"] if review else "", "context_hash":digest,
                                     "detail":result.get("detail",""),"references":reference_index(current)}
    if advice.get("fit") != "related":
        assessment["candidate_gate"] = ("经历与岗位关联不足，暂不自动申请" if advice.get("fit")=="not_related"
                                        else "经历匹配等待复核，暂不自动申请")
    return assessment


def schedule(service,c,jid):
    from .candidate_match import context_fingerprint
    job = one(c, "SELECT * FROM jobs WHERE id=?", (jid,))
    if not job or not job["description"] or not job["requirements"]:
        return
    if rules.assess(job,service.preferences(c),[])["hard"]:
        return
    context = model_input(service,c,job)
    if context is None:
        return
    digest = review_hash(service,c,context)
    if not one(c,"SELECT job_id FROM candidate_reviews WHERE job_id=? AND context_hash=? AND state='done'",(jid,digest)):
        service.enqueue(c,"model_probe","model","candidate:"+str(jid)+":"+digest,payload={"job_id":jid,"candidate_hash":digest})
