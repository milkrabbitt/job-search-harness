<p align="center">
  <img src="docs/assets/banner.svg" alt="Job Search Harness — 公司有招聘系统。你，也该有求职系统。" width="100%">
</p>

<p align="center">
  <strong>岗位、简历、申请、招聘对话。终于有一个地方，把它们接起来。</strong><br>
  在自己的电脑上，把一次次零散的申请，变成可以持续推进的求职流程。
</p>

<p align="center">
  <a href="#quickstart"><strong>▶ 跑一次演示</strong></a>　·　
  <a href="#ai-install"><strong>✦ 让 AI 帮你安装</strong></a>　·　
  <a href="README.en.md">English</a>　·　
  <a href="https://github.com/milkrabbitt/job-search-harness/releases/tag/v0.1.0">v0.1.0 预览版</a>
</p>

<p align="center">
  <a href="https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml"><img src="https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-CDF568?style=flat-square&labelColor=171A16" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-CDF568?style=flat-square&labelColor=171A16" alt="Python 3.9 or newer">
</p>

## 别让下一份工作，散落在几十个标签页里。

看到一个岗位，先收藏。发出一份简历，另记一行。招聘方来了消息，再翻聊天找上下文。

**求职已经够费心了，进度不该还靠你硬记。**

Job Search Harness 是一个本地优先、开源的求职工作台。它把岗位来源、匹配依据、申请材料、招聘对话和待办留在一起。下次打开，知道自己从哪里继续。

| 这个机会值得投吗？ | 我到底发了什么？ | 下一步该做什么？ |
| :--- | :--- | :--- |
| 查看匹配理由、明确缺口和待核实项；合并重复岗位。 | 核对草稿、收件人和已批准的 PDF；回看发送证据。 | 找回招聘对话，把面试安排等事项放进待决定队列。 |

![真实控制台：虚构岗位、模拟申请和待决定事项](docs/assets/demo-overview.jpg)

<p align="center"><sub>真实运行界面 · 全部为虚构演示数据 · 回执来自本机模拟</sub></p>

<a name="quickstart"></a>

## 先跑起来，再决定是否用它求职。

**演示无需真实简历、邮箱授权或模型账号。** macOS / Linux，Python 3.9+；默认运行只用 Python 标准库，无需前端构建。还需 SQLite 3.24+ 与 IANA 时区数据，诊断脚本会检查。

```sh
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
./scripts/doctor.sh
./scripts/bootstrap.sh --demo
./scripts/serve.sh
```

打开 **[http://127.0.0.1:8876/](http://127.0.0.1:8876/)**。按 **Ctrl+C** 停止。演示数据保存在 `.job-harness-dev/`。

<a name="ai-install"></a>

### 不想研究安装？把这段交给你的编程助手。

```text
请在我的电脑上安装并验证 Job Search Harness：
https://github.com/milkrabbitt/job-search-harness

先阅读 README.md、docs/install.md 和 job_harness/CHANNELS.md。
检查系统是否支持，使用独立目录和 Python 虚拟环境，先启动离线演示。
验证岗位匹配、申请记录、招聘消息和待决定事项，运行项目测试。
保持真实账号与外发关闭，不读取我已有的求职资料。
完成后给我本机入口，以及启动、停止和数据保存位置。
若我需要真实渠道或模型，再说明支持范围，并引导我在本机完成认证；不要让我把密钥发到聊天中。
```

适用于有终端操作能力的 Codex、Claude Code、Cursor Agent 等编程助手。**帮助安装的助手，与运行时模型是两回事**：当前可选的模型复核通过本机已登录的 Codex CLI 完成。

[完整安装说明 →](docs/install.md) · [渠道与系统要求 →](job_harness/CHANNELS.md) · [安装遇到问题 →](docs/troubleshooting.md)

## 把“投过了”变成一条能追溯的记录。

### 01 / 先看清机会，再花时间申请

同一岗位出现在多个渠道，不必当成多个新机会。保留原始来源，把匹配点、明确缺口和招聘方没说清的条件放在一起。**分数用于排序，判断有据可查。**

![真实岗位页：虚构 JD 的匹配理由、缺口和待核实项](docs/assets/demo-jobs.jpg)

### 02 / 每一份申请，都能回头查

申请正文、收件人、批准过的简历版本与发送证据对应保存。邮件结果不明时，先按原 Message-ID 核验；站内提交由你在原平台完成，再登记回执。**“准备好了”和“真的发出去了”，分别记录。**

![真实申请页：虚构申请与模拟确认凭据](docs/assets/demo-outbox.jpg)

### 03 / 下次打开，接着往前走

招聘方上次说了什么？这个邀约还等你确认吗？在同一个工作台查看会话、关联岗位和需要你拍板的事项。**把记忆留给系统，把选择留给自己。**

![真实消息页：虚构招聘对话及上下文](docs/assets/demo-inbox.jpg)

## 为什么是 Harness？

一次聊天结束后，求职还在继续。Harness 用持久化记录、任务状态和核验步骤，把下一次行动接上来。

- **本地运行，资料可带走。** Python + SQLite，本机控制台，支持备份与恢复。
- **进度能暂停，结果能核对。** 后台任务与外发分开控制，处理重复岗位、过期草稿和不明确的发送结果。
- **AI 有明确职责。** 可选模型复核匹配特征；授权和发送决定仍由工作台处理。
- **源码可以改。** MIT 开源，按自己的求职习惯继续扩展。

## 从演示切换到自己的资料

给真实资料选一个独立、私有的数据目录，初始化时省略 `--demo`。在「资料与设置」填写偏好，添加经过核实的公开招聘来源，并管理个人事实和已批准的简历版本。配置和导入格式见 [账号配置示例](examples/config.example.json) 与 [虚构资料示例](examples/profile.example.json)。

自动匹配预设目前聚焦中文 JD 的 AI 应用、产品、机器人与金融产品；可设置城市、方向词、薪资、排除项和外发规则。坐班备注与工作类型供人工参考。其他行业仍可补录和跟进。

| 渠道 | 当前能力 |
| --- | --- |
| 公司官网 | 读取已登记且可访问的公开招聘来源。官网申请表由你在原站提交。 |
| 网易 126 邮箱 | 配置 macOS 钥匙串后，可检查招聘邮件、准备线程回复，并按授权发送、核验邮件。 |
| 猎聘 | 配置候选人授权后，可搜索职位并补查公开详情。站内聊天与提交走浏览器接管。 |
| BOSS 直聘、鱼泡 | 在工作台登记待办、检查重复并记录回执。由你在已登录的浏览器完成站内动作。 |
| 可选模型 | 由本机 Codex CLI 复核有限的匹配特征。排序规则、授权和发送决定由工作台处理。 |

渠道开通、系统要求和故障处理见 [渠道与依赖说明](job_harness/CHANNELS.md)。通过外部账号发送会向收件方传送你的材料。真实外发应在核对资料、授权和账号后启用。

<a name="troubleshooting"></a>

## 故障排查

[完整诊断、后台管理与备份恢复说明](docs/troubleshooting.md)。

- **端口已占用。** 如果该端口上已经运行了同一个实例，直接打开控制台。要启动另一个实例，请使用独立的数据目录，并在该目录的 `config.json` 中设置空闲端口。
- **时区数据缺失。** 安装系统的 `tzdata`，或在所用的 Python 虚拟环境中运行 `python -m pip install tzdata`。
- **渠道未授权或访问受限。** 在「运行概览」查看渠道状态与错误。需要登录或验证时，在原网站完成，再按 [渠道与依赖说明](job_harness/CHANNELS.md) 检查本机配置。

## 运行、隐私与备份

- **本机运行。** 控制台仅监听回环地址。macOS 可以用项目的 `install` 子命令安装当前用户的 LaunchAgent，用 `start`、`status`、`stop` 管理。Linux 可运行前台服务。电脑休眠或关机时后台任务也会暂停。
- **运行任务和外发分别控制。** 在「运行概览」暂停全部外发时，搜索与查收可继续。暂停后台任务会停掉调度。
- **真实资料由你保管。** SQLite 数据、简历与备份请放在源码目录之外。系统钥匙串保存真实渠道凭据。项目不会自动带入其他安装实例的数据。
- **可选模型调用。** 开启 Codex 复核后，有限的结构化岗位匹配特征会交给其模型服务。本机工作台不等于本地模型推理。
- **备份和恢复。** 在控制台创建备份，或运行 `python3 -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" backup`。停止该实例后，使用同一数据目录运行 `restore /path/to/backup.sqlite3`。恢复后先核对发送记录，再恢复外发。

<a name="tests"></a>

## 测试

当前套件包含 **75 项测试**，使用临时数据库、虚构数据和模拟渠道。它覆盖匹配、查重、草稿失效、附件校验、暂停、发送结果核验与恢复。

```sh
python3 -B -m unittest discover -s tests -v
```

## 让求职者，也有自己的好工具。

**如果你也希望求职工具更多站在求职者这一边，给它一个 Star。**

先跑一次演示，再告诉我们：哪一步最该替你省下来。欢迎 [反馈体验](https://github.com/milkrabbitt/job-search-harness/issues)、改进文档，或贡献代码。提交问题时请使用虚构数据。

[↑ 回到演示入口](#quickstart) · [复制安装提示词](#ai-install)

按 [MIT 许可证](LICENSE) 开源。

