"""Narrow, fact-backed replies; unknown questions and commitments need a person."""
import re

from .service import setting

INTRO = re.compile(r"(?:您好[，, ]*)?(?:(?:请|可以|能否|方便)(?:简单|简要)?(?:介绍|说明)(?:一下)?(?:自己|你的经历|您的经历|个人经历|相关经历)|(?:请|可以|能否|方便)(?:做|来)(?:个|一个)?(?:简单的?)?自我介绍)[吗么？?。！! ]*\Z")
INTEREST = re.compile(r"(?:您好[，, ]*)?(?:您|你)?(?:对这个岗位|对该岗位|对我们这个岗位)?(?:感兴趣|有兴趣|想进一步了解)[吗么？?。！! ]*\Z")
PROCESS = re.compile(r"(?:您好[，, ]*)?(?:您|你)?(?:对岗位|对这个岗位)?(?:有什么疑问|还有什么想了解的)[吗么？?。！! ]*\Z")


def reply(c, text, match):
    if match.get("hard") or match.get("candidate_gate"):
        return None
    profile = setting(c,"candidate_profile",{})
    intro = str(profile.get("intro") or "").strip() if profile.get("confirmed") is True else ""
    if INTRO.fullmatch(text.strip()) and intro:
        return "您好，" + intro + "希望进一步了解这个岗位的核心职责和后续沟通流程。"
    if INTEREST.fullmatch(text.strip()) and match.get("state")=="match":
        return "您好，感兴趣。想进一步了解这个岗位的核心职责、实际工作地点和后续面试流程。"
    if PROCESS.fullmatch(text.strip()):
        return "您好，想了解岗位的核心职责、实际工作地点和后续面试流程，谢谢。"
    return None
