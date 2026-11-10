# 渠道和运行依赖

基础运行需要 CPython 3.9 或更新版本、SQLite 3.24 或更新版本，以及包含 `Asia/Shanghai` 的 IANA 时区数据库。Python 的 `sqlite3`、`ssl`、`zoneinfo` 和 `fcntl` 模块随常见的 macOS／Linux 构建提供。HTTPS 和邮箱连接使用 Python 可见的系统信任库。前端的 HTML、CSS、JavaScript 都由 `console.py` 提供，浏览器直接访问本机控制台即可。没有 Node.js 构建步骤或 CDN 依赖。

macOS 的后台安装使用系统 `launchctl` 生成并加载当前用户的 LaunchAgent。通知依赖 `osascript` 和当前用户的通知权限。桌面入口由 `osacompile` 生成，使用 `codesign` 签名，打开控制台使用 `open`。这些组件随 macOS 提供。Linux 等 POSIX 环境使用 `serve` 前台启动，原生 Windows 不支持 `fcntl` 文件锁。实际邮件收发和猎聘授权搜索使用 macOS 登录钥匙串。

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

## 可选模型

模型复核可调用已经在本机登录的 Codex CLI。程序按 `model.executable`、PATH 中的 `codex`、macOS ChatGPT 应用的内置路径依次寻找可执行文件。接入需要支持源码所用参数的 CLI 版本。模型调用仅接收有限的岗位匹配特征，外发判断留在本地。基础调度和单元测试不依赖 Codex CLI。
