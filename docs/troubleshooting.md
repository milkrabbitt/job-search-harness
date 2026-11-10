# 运行诊断和故障排查 / Troubleshooting

## 先在独立目录体验

在克隆目录运行下面的命令。临时目录会保存虚构资料和演示记录，与日常求职资料分开。演示使用模拟渠道，无需登录、密钥或真实简历。

```sh
export JOB_HARNESS_DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/job-harness-demo.XXXXXX")"
./scripts/doctor.sh
./scripts/bootstrap.sh --demo
./scripts/serve.sh
```

打开 [本机控制台](http://127.0.0.1:8876/) 或启动时打印的地址。前台用 Ctrl+C 停止；关闭网页后，只要服务还在运行，后台任务就会继续。电脑休眠时进程会暂停，关机后进程会退出。电脑恢复或重新启动服务后，调度器会接续本地记录。

`doctor.sh` 检查 Python、SQLite、时区、必要文件、资料目录和回环端口。它只读本机配置，输出中省略账号及凭据。诊断阶段无需联网，也不会调用模型或检查招聘渠道。

## 启动和控制台

| 情况 | 处理 |
| --- | --- |
| 找不到 Python，或 SQLite 版本不符 | 安装 Python 3.9+（需链接 SQLite 3.24+），三个脚本共用同一个 `PYTHON_BIN`。运行 `./scripts/doctor.sh` 确认。 |
| 缺少 `Asia/Shanghai` 时区 | 优先安装操作系统的 IANA `tzdata`。使用自建虚拟环境时，也可用该解释器安装可选的 `tzdata` 包。 |
| Windows 找不到 `fcntl` | 在 WSL 中运行前台服务。macOS 后台服务和钥匙串依赖 macOS。 |
| 默认端口占用 | 如果已有控制台在运行，打开对应地址。若要启动另一份独立实例，在其资料目录的 `config.json` 中把 `port` 改为空闲的 1024–65535，再运行 `./scripts/serve.sh`。 |
| 页面能打开，按钮提示拒绝来自其他网页的操作 | 直接从浏览器地址栏打开服务打印的回环地址并重新载入页面。控制台动作使用本机来源校验和会话令牌。 |
| 已有执行器 | 同一资料目录或同一已配置邮箱只有一个发送执行器。退出此前的前台服务或停止对应的 LaunchAgent，然后从同一资料目录启动。 |
| 已暂停外发 | 「暂停全部外发」仍允许搜索和查收；「暂停后台任务」停止领取任务。「恢复后台任务」与「恢复发送」分开操作，可在概览查看下一次执行与等待原因。 |

正式资料目录建议放在源码之外。首次运行 `./scripts/bootstrap.sh`（省略 `--demo`）可初始化空白资料。此后启动、停止、配置和备份都使用同一个私有目录。macOS 登录桌面后，可以用 `python3 -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" install` 安装当前用户的 LaunchAgent。对应命令为 `status`、`stop` 和 `start`，均沿用相同的 `--data-dir`。

## 模型与招聘渠道

- **安装助手与运行时模型分别配置。** Codex、Claude Code 或 Cursor Agent 可以在安装时帮你执行命令；这些工具的会话不会自动给应用开通模型能力。当前运行时适配器可选用已在本机认证的 Codex CLI。要启用，在私有 `config.json` 设置 `model.enabled` 为 `true`，需要指定版本时把可执行文件的绝对路径写入 `model.executable`，重启后到「资料与设置 → 模型与用量」点「安排模型检查」。在这里可以设置模型名称、超时和调用上限；可先留空模型名称并采用 CLI 的默认设置。模型未启用时，岗位规则、台账和任务调度可照常使用。
- **网易邮箱和猎聘凭据留在本机。** 网易 126 的客户端授权码和猎聘候选人令牌使用 macOS「钥匙串访问」的通用密码条目。默认名称、账号和配置项见[渠道说明](../job_harness/CHANNELS.md)。把密钥直接填入本机钥匙串，避免粘贴到聊天、配置文件、命令参数或 Git。遇到钥匙串锁定时，在本机解锁，再从控制台重新检查对应渠道。
- **站内操作由浏览器完成。** BOSS 直聘、猎聘、鱼泡的站内提交和回复使用你登录的网页。Harness 负责登记待办、查重、发送前复核和回执记录。登录过期、验证码或平台限制由本人在原网页处理。
- **发送结果不明。** 邮件按原 Message-ID 核对已发送目录；站内申请或回复回到原平台核对回执或会话。确认平台结果后，在台账更新状态。只有明确未发送且授权仍有效的任务才可重试。

## 备份与恢复

```sh
python3 -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" backup
python3 -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" restore /path/to/private-backup.sqlite3
```

`backup` 会在资料目录的 `backups/` 创建 SQLite 快照。请另行备份私有配置和简历附件。恢复前停止对应的后台或前台服务。恢复会暂停任务和外发；重启后先检查最近的投递证据，再按需恢复。备份早于当前外发证据时，程序会拒绝恢复以保护去重记录。

## English quick help

- Set a fresh `JOB_HARNESS_DATA_DIR`, run `./scripts/doctor.sh`, then `./scripts/bootstrap.sh --demo` and `./scripts/serve.sh`. Open the loopback URL printed at startup. Use Ctrl+C to stop the foreground process.
- The doctor checks Python 3.9+, SQLite 3.24+, IANA timezone data, project files, local config, and loopback binding. It makes no external requests. If the console is already running on the selected port, open its URL directly.
- Installation agents run setup commands. Optional runtime review uses your locally authenticated Codex CLI and is configured separately. Store provider secrets in the macOS login keychain on your own device. [Channel details](../job_harness/CHANNELS.md)
- Keep the database, resumes, and local config in a private directory outside your Git checkout. Use `backup` to save a SQLite snapshot. Stop the service before `restore` and review recent submissions before re-enabling outbound activity.
