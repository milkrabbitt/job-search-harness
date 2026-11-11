"""Candidate-facing profile and explicit automation settings."""
from html import escape


def render(console, query):
    from .console import _field, _check, _details, _text
    data, form = console.data, console.form
    candidate = data.get("candidate", {})
    profile = candidate.get("profile", {})
    demo = bool(data.get("demo"))
    scope = '<label>适用岗位<select name="scope">' + ''.join(
        f'<option value="{key}">{title}</option>' for key,title in (
            ("general","通用"),("ai","AI 应用 / 产品"),("robotics","机器人 / 具身智能"),("finance","金融"))) + '</select></label>'
    upload = form("resume_upload", "上传并读取 PDF",
        '<div class="form-grid"><label>选择简历 PDF<input name="resume" type="file" accept=".pdf,application/pdf" required></label>'
        + _field("label","版本名称（选填）") + scope + '</div>'
        + _check("approved","我已核对这份 PDF，允许用于求职申请（此步不会发送）",False)
        + '<p class="muted">最大 20 MB，保存在本机私有目录。提取文字后先由你核对。扫描件不会编造识别结果，可手动补充。</p>',
        disabled=demo)
    upload = upload.replace('<form method="post"', '<form enctype="multipart/form-data" method="post"')
    intro = form("save_candidate_profile","保存经历与匹配授权",
        _field("summary","教育、工作、项目、技能和希望强调的贡献",profile.get("summary",""),"textarea",True)
        + _field("intro","对外沟通简介（选填，最多 600 字；用于申请或自我介绍）",profile.get("intro",""),"textarea")
        + _check("confirmed","我确认这里描述的是实际经历，未实现的内容已注明",False,required=True)
        + _check("profile_model_consent","允许将确认后的经历文字和岗位 JD 的裁剪内容发送给我选定的模型，用于匹配",data.get("preferences",{}).get("profile_model_consent"))
        + '<p class="muted">联系方式会在匹配输入中遮蔽。未勾选时只做本机规则筛选；模型不会读取本机其他文件，也不能修改外发权限。</p>',
        disabled=demo)
    records=[]
    for row in candidate.get("resume_texts",[]):
        status = "已确认，可用于匹配" if row.get("state")=="confirmed" else "等待你核对"
        body = form("confirm_resume_text","确认此版本的经历文字",
            _field("text","从此 PDF 提取的文字（可修正，PDF 原文件保持不变）",row.get("text",""),"textarea",True)
            + _check("confirmed","我已核对这些文字，可将它们用于岗位匹配",False,required=True),
            id=row["resume_id"],disabled=demo)
        records.append('<article class="panel"><h3>'+escape(str(row["label"]))+'</h3><p>'+status+'</p>'
                       '<p class="muted">'+escape(str(row.get("detail") or ""))+'</p>'+body+'</article>')
    prefs = data.get("preferences",{})
    auth = form("save_followup_authorization","保存邮箱自动跟进授权",
        _check("enabled","对已核验且可发送的邮箱启用自动申请和常规回复",prefs.get("auto_send"))
        + _check("allow_follow_up","未回复时按设置的工作日间隔最多跟进一次",prefs.get("allow_follow_up"))
        + _check("scope_confirmed","我确认自动处理范围：已批准材料和有依据的常规答复；面试、薪资、Offer、到岗与搬迁由我确认",False)
        + '<p class="muted">仅作用于已接通的邮箱。平台没有授权发送接口时保留浏览器待办，验证码和身份验证由本人处理。'
          '所有已同步或登记的来往消息在「招聘消息」查看；暂停入口始终保留。</p>',
        disabled=demo)
    return ('<h1>我的经历与简历</h1><p class="muted">先填求职条件，再上传简历或补充经历。'
            '已确认的资料用于匹配，外发有单独授权。</p><p><a href="/setup#setup-profile">填写岗位、城市和薪资条件</a>'
            ' · <a href="/jobs">查看匹配岗位</a> · <a href="/threads">查看沟通记录</a></p>'
            '<section class="panel section"><h2>上传简历</h2>'+upload+'</section>'
            '<section class="section stack"><h2>核对简历文字</h2>'+(''.join(records) or '<p>还没有通过页面上传的 PDF。</p>')+'</section>'
            '<section class="panel section"><h2>补充经历和偏好重点</h2>'+intro+'</section>'
            '<section class="panel section"><h2>授权后自动跟进</h2>'+auth+'</section>')
