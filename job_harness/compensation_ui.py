"""GET-only salary comparison view. ``render(console, query)`` returns HTML.

The root console supplies its existing snapshot under ``console.data['jobs']``.
Select 2–6 existing IDs with repeated ``job`` query parameters, for example
``/salary?job=1&job=2``. Rendering never changes facts or triggers external work.
"""

import re

from .compensation import PERIOD_LABELS, format_range, parse_salary


def render(console, query):
    from .console import _source, _table, _text

    jobs = {str(job["id"]): job for job in console.data.get("jobs", []) if job.get("id") is not None}
    requested = query.get("job", [])
    if isinstance(requested, str):
        requested = [requested]
    selected = list(dict.fromkeys(str(value) for value in requested))
    valid = [key for key in selected if re.fullmatch(r"[1-9][0-9]{0,12}", key) and key in jobs]
    error = ""
    if len(valid) != len(selected):
        error = "部分岗位已移除或编号无效，请重新选择。"
    elif (selected or query.get("compare")) and not 2 <= len(valid) <= 6:
        error = "请选择 2 至 6 个岗位进行对比。"

    selection_rows = []
    for key, job in jobs.items():
        checked = " checked" if key in valid else ""
        label = "选择 " + str(job.get("company") or "公司待确认") + " · " + str(job.get("title") or "岗位待确认")
        checkbox = (f'<input type="checkbox" name="job" value="{_text(key)}"'
                    f' aria-label="{_text(label)}"{checked}>')
        selection_rows.append([checkbox, _text(job.get("company")), _text(job.get("title")),
                               _text(job.get("location")), _text(job.get("salary"), "待确认")])
    selector = ('<form method="get" action="/salary"><input type="hidden" name="compare" value="1">'
                '<fieldset><legend>选择 2 至 6 个岗位</legend>'
                + _table(("选择", "公司", "岗位", "地点", "薪资原文"), selection_rows,
                         "暂无岗位，请先在岗位页保存职位。")
                + '<div class="actions"><button>查看对比</button><a href="/salary">清除选择</a>'
                  '<a href="/jobs">返回岗位</a></div></fieldset></form>')
    compared = ""
    if not error and 2 <= len(valid) <= 6:
        chosen = [jobs[key] for key in valid]
        salaries = [parse_salary(job.get("salary")) for job in chosen]
        header = ["对比项"] + [str(job.get("company") or "公司待确认") + " · "
                                + str(job.get("title") or "岗位待确认") for job in chosen]
        rows = [
            ["地点"] + [_text(job.get("location")) for job in chosen],
            ["薪资原文"] + [_text(salary.raw, "待确认") for salary in salaries],
            ["币种"] + [_text({"unknown": "待确认", "mixed": "多种币种"}.get(salary.currency, salary.currency))
                       for salary in salaries],
            ["计薪周期"] + [_text(PERIOD_LABELS[salary.period]) for salary in salaries],
            ["税前 / 税后"] + [_text(salary.tax_basis) for salary in salaries],
            ["明示人民币月薪"] + [_text(format_range(salary.monthly_min, salary.monthly_max)
                                           + (" 元/月" if salary.monthly_min is not None else ""))
                                  for salary in salaries],
            ["明示发薪月数"] + [_text(format_range(salary.months_min, salary.months_max)
                                         + (" 薪" if salary.months_min is not None else ""))
                                for salary in salaries],
            ["按明示月薪与薪数折算的年额"] + [_text(format_range(salary.annual_min, salary.annual_max)
                                                        + (" 元/年" if salary.annual_min is not None else ""))
                                               for salary in salaries],
            ["待核对项"] + [_text("；".join(salary.issues), "无额外金额换算") for salary in salaries],
            ["岗位来源"] + [_source(job.get("url"), "查看原岗位") for job in chosen],
        ]
        compared = '<section class="section"><h2>薪资信息对比</h2>' + _table(header, rows) + '</section>'
    notice = f'<p class="notice error" role="alert">{_text(error)}</p>' if error else ""
    return ('<h1>薪资对比</h1><p class="muted">选择岗位并查看各自公开的薪资口径。'
            '只有明示人民币月薪及发薪月数时，才计算对应年额区间。'
            '区间下限乘薪数下限，上限乘薪数上限。奖金、股权和税额按招聘方另行说明核对。</p>'
            + notice + selector + compared)
