# 渠道和运行依赖

[安装](../docs/install.md) · [首次配置](../docs/onboarding.md) · [模型接入](../docs/models.md)

基础运行需要 Python 3.9+、SQLite 3.24+，以及包含 `Asia/Shanghai` 的 IANA 时区数据。控制台目前为中文，调度时间沿用上海时区。Python 需提供 `sqlite3`、`ssl` 和 `zoneinfo`；文件锁在 POSIX 使用 `fcntl`，Windows 使用 `msvcrt`。默认运行只使用标准库；缺时区数据时可安装 `tzdata`。前端由 `console.py` 提供，无需 Node.js 构建或 CDN。

## 操作系统支持

| 能力 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| 本机控制台、首次配置、规则筛选、任务与记录 | 支持 | 支持 | 支持 |
| 云 API 和本机模型服务适配 | 支持；需本人测试服务 | 支持；需本人测试服务 | 支持；需本人测试服务 |
| 公开招聘来源读取 | 支持允许访问且解析器支持的来源 | 同左 | 同左 |
| 126 邮箱与猎聘授权搜索凭据 | macOS 钥匙串 | 未提供同等凭据接入 | 未提供同等凭据接入 |
| LaunchAgent、原生通知、桌面入口 | 支持 | 不支持；前台运行 | 不支持；前台运行 |

平台矩阵描述已实现的能力。自动化结果以 [GitHub Actions](https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml) 为准；测试不会替你登录真实供应商、邮箱或招聘网站。

macOS 后台安装使用 `launchctl`，通知使用 `osascript`，桌面入口使用 `osacompile`、`codesign` 和 `open`。Windows / Linux 默认使用 `serve` 前台运行，安装脚本不会自动注册开机服务。电脑休眠或关机时任务不会继续。

首次非演示安装从 `/setup` 填求职条件、选择并测试模型（或明确不使用模型）、登记来源，再核对并开始。来源保存只校验结构，首次扫描才检验网络和解析。自动扫描仅针对已经配置的来源，不代表能自主遍历全网。启动扫描与开启外发是两件事。

## 网易 126 邮箱

1. 在网易 126 网页设置启用 IMAP 和 SMTP，生成客户端授权码。
2. 在 macOS 的「钥匙串访问」创建通用密码条目。默认项目名称为 `job-harness-126-imap-smtp`，账号填本机配置的完整 126 邮箱地址，密码填客户端授权码。若本机配置设置了 `email.keychain_service`，使用该项目名称。也可用系统 `security add-generic-password` 创建条目，使用交互式密码输入。
3. 在控制台安排邮箱检查，核对 IMAP 收件箱、已发送目录及 SMTP 登录。首次读取建立历史基线。若 IMAP 无法唯一识别已发送目录，可设置 `email.sent_folder`。
4. 用完测试账号可关闭配置中的 `email.enabled`。授权码保留在钥匙串中。

协议端点为 `imap.126.com:993` 和 `smtp.126.com:465`，均验证 TLS 证书。读取按招聘主题、已知地址及线程头筛选，`email.history_days` 和 `email.scan_limit` 可调整检查范围。回复只有一个收件人并保留 `In-Reply-To` 和 `References`。SMTP 接受后会另外核验已发送目录。结果不明的发送等待同一 Message-ID 的证据。

## 公司官网

系统读取在控制台登记、经核验且允许访问的公开招聘来源。适配器支持 `JobPosting` 结构化页面、Greenhouse Job Board、Lever Postings 和源码中提供的特定官网解析器。采集遵守 robots 规则、核验 HTTPS、限制可访问主机。页面变化、限流或验证提示会在控制台显示。公司官网申请表由操作者在原网站提交，结果录入控制台。经核实的招聘邮箱由邮箱渠道发送。

## 猎聘

使用平台候选人授权取得自己的令牌。在 macOS 登录钥匙串创建通用密码条目，默认项目名称为 `job-harness-liepin-candidate`，账号为 `candidate`，密码填该令牌。可用 `channels.liepin.keychain_service` 指定其他项目名称。`channels.liepin.enabled` 可关闭授权搜索。

授权接口读取职位搜索结果，公开职位详情用于补充职责和要求。网页站内消息和申请由操作者处理。钥匙串锁定、授权失效或出现平台限制时，可在控制台查看相应状态。

## 已登录浏览器

BOSS 直聘、猎聘和鱼泡的站内动作使用操作者已登录的正常网页。智联的已核实岗位链接也可用于申请队列。Harness 存放原岗位链接、已批准附件、消息锚点、认领状态和实际回执。浏览器会话由浏览器管理，后台不会定时轮询站内聊天。

在控制台登记岗位与会话后安排动作。前台操作者核对原岗位、收件人和是否已提交，认领动作，提交前复查暂停状态、附件和最近来信，最后以原平台的证据登记结果。认领超时或提交结果不明时，先到原平台核对。遇到登录和验证要求，由操作者在原站完成。

命令包含 `browser-queue`、`browser-actions`、`browser-claim`、`browser-check`、`browser-finish` 和 `browser-reconcile`。运行 `python3 -B -m job_harness <命令> --help` 查看参数。若使用独立数据目录，`--data-dir` 放在子命令之前。

## 模型接入

当前适配 Codex CLI、OpenAI、Anthropic、Gemini、DeepSeek、Qwen、Moonshot、Zhipu、MiniMax、Ollama、LM Studio 和 OpenAI-compatible。每个 API 模型名由用户根据自己的账号或本机服务填写；没有默认承诺可用的模型。详见[供应商矩阵、端点、限额与密钥说明](../docs/models.md)。

- Codex CLI 使用本机已认证的可执行文件；可在设置中指定路径。CLI 参数兼容性和实际账号额度需本机连接测试。
- 云端适配器发送有限的结构化匹配特征；不发送完整简历、招聘聊天或原始 JD。连接测试使用合成输入，仍可能收费。
- Ollama / LM Studio 由用户先启动并加载模型，工作台只连接其回环 API。明文 HTTP 仅用于回环；外部端点必须 HTTPS，接口不跟随重定向。
- API Key 可从环境变量读取，或保存在私有数据目录 `private/model-keys/` 的未加密文件中。它们与 **126 / 猎聘使用的 macOS 钥匙串是不同存储方式**，不会随数据库备份恢复。
- 基础规则筛选、离线演示和模拟测试不需要真实模型账号。模型复核只提供建议，不能代替外发授权或用户对面试、薪资与 Offer 的决定。

适配与协议测试不等于全部真实供应商端到端验证。首次使用、切换模型或凭据后，均需由使用者在本机完成连接测试。
