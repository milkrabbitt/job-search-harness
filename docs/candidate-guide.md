# 上传简历、确认经历与自动跟进

[首次配置](onboarding.md) · [模型接入](models.md) · [薪资对比](compensation.md)

完成 `/setup` 的求职条件、模型选择和来源配置后，打开 `/profile` 的「我的经历与简历」。这页集中管理申请附件、用于匹配的经历文字和邮箱自动处理授权。

## 上传并确认 PDF

1. 在「上传简历」使用文件选择框选择 PDF，填写版本名称与适用岗位。最大 20 MB，每次一份。
2. 核对附件内容后，决定是否勾选允许用于求职申请。上传会保存私有副本与文件指纹。
3. 点击「上传并读取 PDF」，查看「核对简历文字」中的结果。
4. 修正提取遗漏、乱码和项目状态，勾选确认，再保存此版本的经历文字。

附件批准与文字确认各有用途。批准允许选用该 PDF 作为附件，确认后的文字才进入经历匹配。对提取文字的编辑保存在本机资料中，PDF 本身保持原样。

文字提取使用安装环境中的 `pypdf==6.21.4`。缺少依赖时，用运行服务的同一虚拟环境执行 `python -m pip install -r requirements.txt`。其他功能及手动经历填写仍可继续使用。

| 页面提示 | 下一步 |
| --- | --- |
| 已提取 PDF 文字 | 逐项核对后确认 |
| 未提取到文字，可能是扫描件或图片 PDF | 手动补充，或上传带文字层的 PDF |
| PDF 已加密 | 上传未加密版本，或手动填写经历 |
| 缺少提取依赖、超时或无法可靠解析 | 按提示修复依赖、重新导出，或手动补充 |
| 仅提取部分文字 | 检查截断位置并补充需要用于匹配的内容 |

当前提取最多处理前 20 页，页面最多保留 24,000 字供核对。提取失败与不完整状态会显示具体提示。扫描件采用手动补充流程。

## 补充实际经历

在「补充经历和偏好重点」填写教育、工作、项目、技能与个人实际贡献，最多 6000 字。区分已实现、已上线和仍在规划的功能，核对后勾选真实经历确认。

可另写最多 600 字的对外沟通简介，用于规则覆盖的申请介绍或自我介绍回复。简介与详细经历分别保存，便于你控制发送给招聘方的表述。

## 决定是否使用模型复核

`profile_model_consent` 控制经历匹配的数据同意。勾选且模型启用后，Harness 将已确认的经历、已批准简历中确认的文字和已核实事实，与岗位 JD 及求职偏好组成有长度限制的请求。

请求会按规则遮蔽常见联系方式、凭据、链接和本机路径。处理后的内容仍可能包含真实工作与项目细节，发送目标是你在「模型接入」选择的服务。原 PDF 保存于本机私有数据目录。未勾选时，按本机规则进行岗位筛选。

模型结果应引用本次输入中的候选人证据与 JD 字段，列出关联、缺口和未知项。启用此模式后，自动申请等待与当前内容对应的相关复核。待复核、失败、不明确或不相关的结果保留待处理，岗位硬性排除持续生效。修改经历或岗位内容会要求重新判断，关联申请草稿也会要求重新核对。

## 授权邮箱自动处理

先完成首次配置并接通网易 126。邮箱需要已启用、核验为可发送且未暂停，才能在这页保存自动处理授权。

可授权的范围包括使用已批准附件申请符合规则的岗位，以及在可信招聘邮件线程中回答规则覆盖的常规问题。例如明确索要简历或公开作品、可由核实事实回答的简短问题，以及自我介绍、兴趣和招聘流程问答。自我介绍使用你确认的对外简介。

无回复后的主动跟进另有勾选项，按设置的工作日间隔最多一次。面试、作业、薪资、Offer、到岗、搬迁和敏感资料等进入「待我决定」，存在此类未处理事项的会话暂停自动常规回复。已拒绝、要求停止联系或指定人工跟进的记录继续约束后续动作。

BOSS、猎聘与鱼泡的站内消息及投递在已登录的原平台完成。Harness 保存待办和核实记录。已同步或已登记的对话在「招聘消息」查看，发送凭据在「草稿与发送」查看。

## English

Use `/profile` after setup to upload a PDF up to 20 MB. Review the extracted text before confirming it for matching. Approving the PDF as an attachment is a separate step. Scanned, encrypted, incomplete or unreadable PDFs receive a manual-review message. Manual experience entry remains available when extraction is unavailable.

Confirm an experience summary of up to 6000 characters and optionally a public introduction of up to 600 characters. The `profile_model_consent` checkbox allows the configured model to receive bounded, redacted experience and job text. The source PDF stays local. This matching mode waits for a current related review before an automatic application and retains all hard exclusions.

With a verified, enabled and send-ready 126 mailbox, authorize eligible applications and narrow routine replies. A separate option allows one follow-up. Pending interview, salary, offer and other important decisions pause automatic routine replies for that thread. Platform submissions and chats use the logged-in browser.
