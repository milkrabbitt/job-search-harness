"""Deterministic gates. External prose is evidence, never a command."""
import re
import unicodedata
from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import urlsplit

HARD = {
    "明确销售或获客": r"^(?:(?:海外|大客户|渠道|电话|网络|电商)?销售(?:代表|专员|顾问|经理|主管|总监|负责人)?|商务拓展|客户开发)(?:\b|[/（(\s]|$)|承担.{0,10}(销售指标|销售额|营收指标)|完成.{0,10}(销售任务|销售业绩|签单指标)|客户开拓|电话销售|渠道拓展|开户拉新|贷款中介|保险销售|地推获客|底薪.{0,8}纯提成",
    "明确算法、训练或底层研发": r"(负责|主导|核心.{0,4})(大模型.{0,8}(预训练|训练)|强化学习|推理加速|SLAM算法|导航算法|运动控制算法|VLA.{0,5}训练)|算法笔试必|独立算法笔试",
    "机器人本体与底层研发": r"^(?:高级|资深|初级)?(?:机械结构|嵌入式|电控|电机控制|运动控制|导航算法|操作算法|SLAM|机器人硬件)(?:工程师|研发|算法|专家|设计师)|(?:主导|负责|独立).{0,14}(整机量产|机器人本体硬件|电控系统设计|嵌入式驱动|底盘导航算法)",
    "明确强独立编码": r"(必须|明确要求).{0,20}独立.{0,12}(编码|手写|编程)|无.{0,4}AI辅助.{0,12}(编程|笔试)|算法题.{0,10}(必考|必须)|必须.{0,10}(现场|手写).{0,10}(算法|代码)",
    "明确不接受的工作制": r"\b996\b|每周单休|明确单休|仅单休|强制待命|7[×x*]24小时待命",
    "限制行业或发薪": r"NSFW|成人媒体|博彩|纯USDT|仅USDT",
}
QUALIFICATIONS = {
    "毕业年份／身份限制": r"仅限.{0,8}(应届|在读|毕业)|(?:202[5-9]|20\d{2})届.{0,8}(校招|毕业生|全日制)|202[5-9]届|校园招聘|在校生|在读学生",
    "必须具备资深同岗经历": r"(?:[五六七八九十]|[5-9]|10)\s*年(?:以上|及以上|\+).{0,14}(经验|经历)|(?:至少|必须).{0,8}[3-9]\s*年.{0,14}(量产|机器人|同岗|产品|开发)",
    "明确相关理工专业": r"(?:须为|必须|专业要求[:：]?)[^。\n]{0,30}(理工|计算机|机械|自动化)|(?:全日制|统招).{0,18}(?:理工科相关专业|计算机相关专业)(?!优先)",
}
DIRECTION = {
    "具身与机器人": r"具身|机器人|智能硬件|智能玩具|AI玩具|智能宠物|智能穿戴",
    "对话、记忆与交互": r"对话|陪伴|角色|记忆|语音交互|交互|多轮",
    "AI 应用或 Agent": r"Agent|智能体|工作流|API|AI\s*(?:应用|产品)|人工智能(?:应用|产品)|原型",
    "软件产品交付": r"产品设计|需求分析|产品经理|回归|原型|测试|配套\s*App|软件产品",
    "金融产品或分析": r"金融科技|交易系统|业务分析|证券|期货|债券",
}
BLOCKING = ("confirmed", "contacted", "rejected", "closed", "failed", "unknown", "pending", "stopped", "inquiry", "manual_followup")
RECRUITING = frozenset({"open", "active", "recruiting"})
RECRUITING_ENDED = frozenset({"closed", "rejected", "stopped", "expired"})
UNKNOWN_RECRUITING = "当前在招状态"
PLATFORM_DOMAINS = ("zhipin.com", "liepin.com", "yupao.com", "zhaopin.com", "jobs.feishu.cn",
                    "mokahr.com", "zhiye.com", "nowcoder.com", "126.com", "163.com")

def norm(text):
    return "".join(re.findall(r"[\w\u4e00-\u9fff]+", unicodedata.normalize("NFKC", str(text or "")).casefold())).replace("_", "")

def title_key(text):
    text = re.sub(r"[（(][A-Z]*\d{4,}[）)]", "", str(text or ""), flags=re.I)
    return norm(re.sub(r"^[【\[]?(热招|急招)[】\]]?", "", text))

def host(url):
    try:
        value = urlsplit(url or "").hostname or ""
    except ValueError:
        return ""
    return value.lower().removeprefix("www.")

def company_domain(domain):
    value = host("https://" + str(domain or "").removeprefix("https://"))
    if not value or any(value == h or value.endswith("." + h) for h in PLATFORM_DOMAINS):
        return ""
    return value

def address(value):
    value = parseaddr(str(value or ""))[1].lower()
    if re.fullmatch(r"[\w.!#$%&'*+/=?^\x60{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", value, flags=re.ASCII):
        return value
    return ""

def similar(left, right):
    a = {left[i:i+3] for i in range(len(left)-2)}
    b = {right[i:i+3] for i in range(len(right)-2)}
    return len(a & b) / len(a | b) if a and b else 0

def configured_cities(prefs):
    return [city.strip() for city in re.split(r"[,，、\n]+", str(prefs.get("cities") or "")) if city.strip()]

def assess(job, prefs, exclusions, *, review_cleared=False):
    role = job.get("title") or ""
    jd = "\n".join(str(job.get(k) or "") for k in ("title", "description", "requirements"))
    place = job.get("location") or ""
    hard = [name for name, regex in HARD.items() if re.search(regex, jd, re.I)]
    hard += [name for name, regex in QUALIFICATIONS.items() if re.search(regex, jd, re.I)]
    exclusion_fields = " ".join((job.get("company") or "", role, jd))
    hard += ["排除名单 " + word for word in exclusions if word.casefold() in exclusion_fields.casefold()]
    cities = configured_cities(prefs)
    local_match = any(city in place for city in cities)
    remote_match = prefs.get("allow_remote") and "远程" in place
    if place and cities and not (local_match or remote_match):
        hard.append("工作地点超出当前范围")
    if job.get("status") in RECRUITING_ENDED:
        hard.append("岗位关闭或已停止联系")
    strengths = [name for name, regex in DIRECTION.items() if re.search(regex, jd, re.I)]
    unknown = []
    for key, label in [("location", "实际工作地点"), ("salary", "薪资"), ("requirements", "完整任职要求"),
                       ("description", "具体工作职责"), ("published_at", "发布时间")]:
        if not job.get(key):
            unknown.append(label)
    if "远程" in place and not re.search(r"(工作时间|时区|在线时间|\d{1,2}:\d{2})", jd):
        unknown.append("远程实际工作时间")
    if re.search(r"解决方案|售前|客户成功|咨询顾问|实施|交付|\bFDE\b", role, re.I) and not re.search(
            r"不(?:涉及|承担|负责).{0,8}(销售|获客|续费)|无.{0,5}(销售|获客|续费).{0,3}(指标|考核)", jd):
        unknown.append("是否承担销售、续费或获客指标")
    if re.search(r"高级|资深|负责人|总监", role):
        unknown.append("资深级别是否接受实际项目替代")
    if not re.search(r"项目.{0,6}(可|优先|不限)|欢迎转行|个人项目|作品.{0,5}(优先|均可)|经验不限|应届亦可|不限经验", jd):
        unknown.append("是否接受项目背景或转行")
    recruiting_unknown = job.get("status") not in RECRUITING | RECRUITING_ENDED
    if recruiting_unknown:
        unknown.append(UNKNOWN_RECRUITING)
    if "出差" in jd and not re.search(r"(无需|不需要|无)出差", jd):
        unknown.append("每月出差次数、单次天数及地区")
    if "驻场" in jd and not re.search(r"(无需|不需要|无)驻场", jd):
        unknown.append("驻场频率及地点")
    if re.search(r"量产|ROS2?|SLAM|VLA|真机部署|运动控制", jd, re.I) and "具身与机器人" in strengths:
        unknown.append("是否把未核实的机器人量产、部署或底层经验作为必要条件")
    scope = str(prefs.get("target_scope") or "")
    require_robotics = prefs.get("require_robotics", "具身" in scope or "机器人" in scope)
    if require_robotics and "具身与机器人" not in strengths:
        unknown.append("具身、机器人或相关硬件应用关联")
    current_target = str(prefs.get("directions") or "")
    target_keys = [x.strip() for x in re.split(r"[,，、\n]+", current_target) if x.strip()]
    if target_keys and not any(norm(key) and norm(key) in norm(jd) for key in target_keys):
        unknown.append("目标方向匹配")
    salary_floor = str(prefs.get("salary_floor") or "").strip()
    if salary_floor:
        salary = re.search(r"(\d+(?:\.\d+)?)\s*(?:[-–~至]\s*(\d+(?:\.\d+)?))?\s*([kK千万])", str(job.get("salary") or ""))
        if salary:
            high = float(salary[2] or salary[1]) * (10000 if salary[3] == "万" else 1000)
            if high < float(salary_floor):
                hard.append("公开薪资上限低于本人确认的底线")
        else:
            unknown.append("薪资能否满足本人明确条件")
    evidence = len(norm(job.get("description"))) >= 24 and len(norm(job.get("requirements"))) >= 12
    priority = 0 if hard else min(90, 20 * len(strengths) + (10 if evidence else 0))
    direction = "robotics" if "具身与机器人" in strengths else ("ai" if re.search(r"AI|Agent|机器人|智能体", jd, re.I) or "金融产品或分析" not in strengths else "finance")
    nonblocking = {"发布时间", "薪资"}
    if prefs.get("allow_unknown_recruiting"):
        nonblocking.add(UNKNOWN_RECRUITING)
        if recruiting_unknown and review_cleared:
            nonblocking.add("是否接受项目背景或转行")
    auto = not hard and evidence and len(strengths) >= 2 and not set(unknown) - nonblocking and (local_match or remote_match)
    gaps = []
    if re.search(r"(?:[2-5二三四五])\s*年.{0,18}(经验|经历)", jd):
        gaps.append("同岗年限需单独核实，个人项目或其他行业经验不能自动折算")
    if re.search(r"ROS|真机部署|运动控制|导航|量产", jd, re.I):
        gaps.append("核对是否要求实际机器人底层、部署或量产经验")
    if re.search(r"Python|API|数据库|编程", jd, re.I):
        gaps.append("核对候选人的开发实践及岗位的独立编码考核要求")
    reason = "与真实项目关联 " + "、".join(strengths) if strengths else "岗位职责尚未找到足够的直接项目关联"
    if recruiting_unknown and prefs.get("allow_unknown_recruiting"):
        reason += "。在招状态待确认，符合条件并核实招聘渠道后可先申请，正文询问是否还有相关机会"
    return {"hard": hard, "strengths": strengths, "unknown": list(dict.fromkeys(unknown)), "gaps": gaps,
            "score": priority, "scope": direction, "state": "excluded" if hard else ("match" if auto else "review"),
            "reason": reason + "。排序分不代表录用概率。"}

def classify(subject, body):
    text = (subject or "") + "\n" + (body or "")
    checks = (
        ("stop", r"请勿.{0,6}(联系|投递)|不要再.{0,5}(联系|发)|取消订阅|停止联系"),
        ("bounce", r"Undeliver(?:ed|able)|Delivery Status Notification|Mail Delivery Subsystem|退信|未能投递"),
        ("reject", r"暂不匹配|很遗憾|无法推进|暂不考虑|不合适|招聘已结束|已招满|未通过"),
        ("sensitive", r"身份证|银行卡|银行信息|支付|缴费|转账|押金|下载.{0,12}(软件|安装)|忽略.{0,12}(系统|之前).{0,8}(指令|规则)|修改.{0,10}(权限|配置)|私有仓库|原始聊天"),
        ("offer", r"\bOffer\b|录用通知|入职合同|竞业|知识产权|劳动合同"),
        ("interview", r"面试|改期|预约时间|视频交流|邀约"),
        ("assignment", r"作业|笔试|考核题|测试题|截止时间"),
        ("compensation", r"薪资|期望.{0,5}(薪|工资)|目前收入|底薪|薪酬|薪水|待遇"),
        ("availability", r"到岗|入职日期|最快.{0,4}(入职|开始)|出差|驻场|搬迁|搬到|是否接受.{0,8}(上海|北京|深圳)|目前在哪"),
        ("auto", r"自动回复|已收到.{0,8}(申请|简历)|感谢投递|系统通知|out of office"),
        ("resume", r"简历|CV\b|résumé"),
        ("portfolio", r"作品集|作品链接|GitHub|项目链接|项目演示"),
        ("experience", r"请.{0,5}(介绍|说明)|做过吗|有.{0,12}经验吗|工作年限|从事过|学历|项目|经历"),
    )
    for kind, pattern in checks:
        if re.search(pattern, text, flags=re.I):
            return kind
    return "review"

def is_business_day(dt):
    return dt.weekday() < 5

def business_days_after(dt, days):
    while days > 0:
        dt += timedelta(days=1)
        if is_business_day(dt):
            days -= 1
    return dt

FACT_QUESTIONS = (
    (r"(?:您好[，,！! ]*)?(?:方便|可以|能否|请)(?:简单|简要)?介绍(?:一下)?(?:你的|您的)?角色对话(?:项目|经历)?[吗么？?。 ]*", "summary_dialogue"),
    (r"(?:您好[，,！! ]*)?(?:方便|可以|能否|请)(?:简单|简要)?介绍(?:一下)?(?:你的|您的)?(?:软件产品|AI应用)(?:项目|经历)?[吗么？?。 ]*", "summary_app"),
    (r"(?:您好[，,！! ]*)?(?:方便|可以|能否|请)(?:简单|简要)?介绍(?:一下)?(?:你的|您的)?(?:债券交易|证券)(?:工作|项目|经历)?[吗么？?。 ]*", "summary_finance"),
)

def explicit_request(text, kind):
    name = r"简历|CV|r[eé]sum[eé]" if kind == "resume" else r"作品集|作品链接|项目链接|GitHub|portfolio"
    pattern = r"(?:请|能否|方便|可以|麻烦)?[^。！？\n]{0,12}(?:发|提供|补充|分享|附上|send|share|provide)[^。！？\n]{0,18}(?:" + name + ")"
    return bool(re.search(pattern, text, flags=re.I))
