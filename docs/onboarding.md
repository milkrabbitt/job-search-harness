# 首次配置与日常使用

[返回首页](../README.md) · [安装](install.md) · [模型接入](models.md)

安装只是准备好工作台。要开始真实工作，还需要你自己的求职目标、一个可用模型或明确选择规则筛选，以及允许读取的招聘来源。默认不会启用真实发送。

## 首次打开 `/setup`

### 1. 填求职条件

填写岗位方向、城市或远程偏好，可选最低薪资和自己确认的简介。新用户按自选关键词、地区、薪资和排除项筛选；分数是机会排序依据，不是录用概率，也不是对真实技能的完整评估。

旧版的中文 AI 应用、产品、机器人、金融产品规则仍可作为兼容预设保留。导入旧数据时先核对偏好，不要把示例条件当成自己的条件。

### 2. 选择模型并测试

选择供应商，填实际可用的模型名与 API Base。API Key 在本机输入或通过环境变量提供；Codex CLI 使用本机认证；本机模型服务需已运行并加载模型。也可以明确选择**不调用模型**，继续使用规则筛选。

连接测试使用合成特征，云 API 可能收费。测试通过才说明当前配置能返回期望格式；换模型、URL 或凭据后需要重新测试。[详细矩阵与费用边界](models.md)

### 3. 登记招聘来源

输入允许访问的公开招聘页面或支持的数据源 URL，补充公司等必要信息。只添加自己核实过的来源。

保存时检查 URL 结构；**首次扫描才检查网络访问、页面内容与解析结果**。保存成功不等于已找到岗位。需要登录、验证码、动态渲染或不受支持的页面可能无法自动读取，工作台会保留错误供你排查。

没有启用的来源或可用渠道时无法开始自动发现。手动登记一条岗位可以供你跟进，但不等于系统获得全网搜索能力。

### 上传简历并核对经历

打开 `/profile` 的「我的经历与简历」，从文件选择框上传不超过 20 MB 的 PDF，登记版本和适用岗位。附件批准只允许这份 PDF 用于申请。提取结果先显示在「核对简历文字」，修正后勾选确认，才能作为候选人经历用于匹配。扫描件、加密文件、缺少提取依赖和无法可靠解析时，会提示手动核对或补充。

在同一页填写并确认最多 6000 字的真实经历摘要，可选填写最多 600 字的对外简介。没有 PDF 也能先填写经历或扫描岗位。准备申请前再补齐已批准的附件与核实的招聘联系人。 [资料上传与确认说明](candidate-guide.md)

### 决定模型可以使用哪些经历

勾选 `profile_model_consent`，表示允许所选模型接收已确认经历和 JD 的裁剪、遮蔽后文字。PDF 原文件保存在本机，模型请求仍可能包含真实工作和项目内容。未勾选时按本机规则筛选。

启用经历匹配后，模型的匹配点、缺口和未知项引用本次提供的证据。自动申请等待与当前经历、JD 对应的 `related` 复核。硬性排除持续生效。修改经历或 JD 后需要重新判断，旧申请草稿会要求重新核对。 [模型数据范围](models.md)

### 4. 核对并开始工作

开始前检查求职条件、当前模型测试结果（或已明确不使用模型）以及来源配置。通过后安排扫描与后续匹配；重复点击开始不会重复排入同一轮启动任务。

**真实外发保持关闭。** 启动任务、连接模型、测试来源都不是发送邮件或代你接受 Offer 的授权。

## 从哪里看结果

- **运行概览**：任务是否运行、渠道错误、扫描进度及外发状态。
- **岗位**：来源、薪资区间、规则依据、已确认经历与 JD 的关联、模型引用、缺口和待核实项。没有岗位时先看来源错误。
- **薪资对比**：选择 2 至 6 个岗位，查看原文、计薪周期及明示人民币月薪与薪数的算术对比。缺失信息保留待确认。 [口径说明](compensation.md)
- **草稿与发送**：收件人、正文、批准的 PDF、发送状态及回执。准备好草稿不代表已经发送。
- **招聘消息 / 待我决定**：已同步或登记的会话、面试安排与需要你拍板的事项。
- **我的经历与简历**：上传并确认 PDF 文字、补充经历、设置模型数据同意和邮箱自动处理授权。
- **资料与设置**：核实偏好、资料和模型配置；修改后检查是否需重新测试。

## 启用邮箱自动申请与跟进

先完成首次配置，在 macOS 配置并核验网易 126，确认邮箱已经启用、可发送且未暂停。然后在 `/profile` 勾选自动处理范围并保存，允许系统使用已批准材料进行符合规则的邮件申请和常规回复。需要时另行勾选无回复后的单次跟进，按设置的工作日间隔执行。

常规自动回复限于明确的简历或公开作品请求、已有事实答案的简短问题，以及匹配规则覆盖的自我介绍、兴趣和流程问答。自我介绍使用你确认的最多 600 字对外简介。面试时间、作业、薪资、Offer、到岗、搬迁和敏感资料等进入「待我决定」，有此类待定事项的会话会停止自动常规回复。拒绝、要求停止联系或已指定人工跟进的会话按原范围停止外联。

BOSS、猎聘、鱼泡的站内动作仍在已登录浏览器中完成，工作台保存队列和核实后的记录。验证码与身份验证由本人处理。 [渠道操作范围](../job_harness/CHANNELS.md)

进程需要持续运行。关网页后任务可继续；终端停止、电脑休眠、关机或断网会影响执行。恢复后先查看错误和未确认的发送结果。

<a name="maintenance"></a>

## 暂停、重配、恢复与卸载

**暂停外发**：保留扫描和查收任务，暂停对外发送。**暂停后台任务**：停止后台调度。两者是不同开关。恢复前检查账号、当前模型配置、来源和外发范围；首次配置未完成时先回到向导。

**重配**：可以重新打开 `/setup`，不会删除旧数据。保存求职条件或模型配置会暂停后台与外发；模型或密钥改变后旧的连接测试失效，仅修改求职条件可保留仍有效的模型测试。核对并再次开始后恢复任务，外发仍需单独启用。不要为了换模型重新运行演示或清空数据库。模型、端点或 Key 改变后重新测试。需要完全从头体验时，停止服务，选择一个新的空数据目录；旧目录保留作回退，不能直接覆盖已有数据。

**备份**：在控制台使用备份功能，或在命令行指定同一数据目录执行 `backup`。SQLite 备份保存数据库，不包含模型密钥文件、原始 PDF 或外部环境变量；这些要另外安全保管。不要将含个人信息的数据库作为公开问题附件。

macOS / Linux 示例：

```sh
python3 -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" backup
```

Windows 示例：

```powershell
& $env:PYTHON_BIN -B -m job_harness --data-dir $env:JOB_HARNESS_DATA_DIR backup
```

**恢复**：停止目标实例，再用相同 `--data-dir` 执行 `restore /path/to/backup.sqlite3`。恢复后核对资料、简历原文件、凭据、模型测试和发送记录，再恢复任务与外发。

**升级**：先停止服务并备份，更新源码，运行诊断和测试，再使用原私有数据目录启动。不要把 `config.example.json` 覆盖到已有真实配置上。

**卸载**：当前没有统一的一键卸载命令。前台服务按 Ctrl+C 停止；若曾在 macOS 安装 LaunchAgent，可在同一数据目录执行 `python3 -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" desktop-stop` 持久停用对应服务，再按自己的安装记录移除该实例的启动项和桌面入口。不要批量删除其他程序的 LaunchAgent。源码目录、虚拟环境和私有数据目录分开处理：先保留需要的资料与备份，再由你决定是否删除私有数据及模型密钥。删除本地 Key 不会撤销供应商凭据；不用的 Key 需在服务后台撤销。

## 常见卡点

| 现象 | 先检查 |
| --- | --- |
| 保存模型后仍不能开始 | 当前配置是否真的通过连接测试；检查模型名、URL、权限、余额、超时及每日调用上限。 |
| 更新 Key 后仍报旧账号错误 | 环境变量可能优先覆盖本机文件；重启时使用了哪个环境和数据目录。 |
| 开始后没有岗位 | 是否有启用的来源；首次扫描是否遇到网络、robots 限制、页面解析或空结果。 |
| 填了金额预算却不调用 | 当前金额计价未知，非空预算会阻止调用；使用调用次数限制，并在供应商后台控制消费。 |
| Windows 能启动但邮箱不能用 | 核心支持不等于渠道完整支持；126 与猎聘凭据仍依赖 macOS 钥匙串。 |
| 关闭窗口后任务没继续 | 浏览器窗口可关，服务进程必须保持；休眠和关机不会继续执行。 |
| 换电脑后缺模型和简历 | 数据库备份不包含密钥、原始 PDF、模型下载和本机 CLI 认证。 |

## English summary

Open `/setup` to enter goals, choose and test a provider (or explicitly choose no model), register sources, then review and start. The first scan checks actual access and parsing. Starting scans leaves outbound sending disabled.

Use `/profile` to upload a PDF up to 20 MB, review its extracted text, and confirm your experience. An optional public introduction is limited to 600 characters. Grant `profile_model_consent` if you want the configured model to receive bounded, redacted experience and JD text. The PDF stays local. In this mode, applications wait for a current related review, and hard rules still apply. Use `/salary` to compare 2–6 tracked roles while preserving unknown pay details.

With an enabled, verified, send-ready 126 mailbox, explicitly authorize automatic applications and narrow routine replies in `/profile`. A separate option allows one follow-up after the configured business-day interval. Pending interview, salary, offer and other important decisions block automatic routine replies in that thread. Platform chat and submission continue in your logged-in browser.

Keep the process running. Pause tasks and pause outbound sending separately. Revisiting `/setup` is non-destructive. Saving goals or model settings pauses work and outbound sending; retest changed models/keys, then review and start again. Use a new empty data directory for a clean reset rather than overwriting real data.

Database backups exclude model keys, original PDFs, local model downloads and CLI authentication. Stop before restoring, then verify credentials and sending evidence. Before uninstalling, stop any background service, preserve the data you want, and remove private files only when you decide. Revoking a provider key is separate from deleting its local copy.
