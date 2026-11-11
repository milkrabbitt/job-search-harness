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

### 可选：登记简介和简历

在向导中填写自己确认的简介；有现成简历时，可填写本机 PDF 绝对路径、版本名与适用范围，并勾选批准使用。程序接受最大 20 MB 的 PDF、保存私有副本和文件指纹，保留原文件。这一步不发送材料，也不自动提取 PDF 里的经历。

没有简历也能先扫描岗位。正式准备申请前，再补齐匹配范围的已批准 PDF、核实的简介与招聘联系人。[资料登记说明](install.md#private-profile)

### 4. 核对并开始工作

开始前检查求职条件、当前模型测试结果（或已明确不使用模型）以及来源配置。通过后安排扫描与后续匹配；重复点击开始不会重复排入同一轮启动任务。

**真实外发保持关闭。** 启动任务、连接模型、测试来源都不是发送邮件或代你接受 Offer 的授权。

## 从哪里看结果

- **运行概览**：任务是否运行、渠道错误、扫描进度及外发状态。
- **岗位**：来源、薪资区间、匹配依据、缺口和待核实项。没有岗位时先看来源错误，不要只反复启动。
- **草稿与发送**：收件人、正文、批准的 PDF、发送状态及回执。准备好草稿不代表已经发送。
- **招聘消息 / 待我决定**：已同步或登记的会话、面试安排与需要你拍板的事项。
- **资料与设置**：核实偏好、资料和模型配置；修改后检查是否需重新测试。

进程需要持续运行。关网页后任务可继续；终端停止、电脑休眠、关机或断网会影响执行。恢复后先查看错误和未确认的发送结果，不要盲目重发。

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

Open `/setup`: enter goals, choose and test a provider (or explicitly choose no model), register a recruiting source, then review and start. Source URL checks at save time do not prove network access or parsing; the first scan provides that evidence. Starting scans leaves outbound sending disabled.

Keep the process running. Pause tasks and pause outbound sending separately. Revisiting `/setup` is non-destructive. Saving goals or model settings pauses work and outbound sending; retest changed models/keys, then review and start again. Use a new empty data directory for a clean reset rather than overwriting real data.

Database backups exclude model keys, original PDFs, local model downloads and CLI authentication. Stop before restoring, then verify credentials and sending evidence. Before uninstalling, stop any background service, preserve the data you want, and remove private files only when you decide. Revoking a provider key is separate from deleting its local copy.
