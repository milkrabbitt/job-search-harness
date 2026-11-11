# Job Search Harness

![Job Search Harness — Make your next move clear.](docs/assets/hero.svg)

## 先看清匹配，再决定投递。

**把你的经历和真实 JD 放在一起，看清匹配点、缺口和依据；把招聘回复与下一步待办，收进自己的求职工作台。**

同时聊着几家公司时，最容易忘的往往是：为什么投这份岗位、发过哪版简历、对方说到哪了。Job Search Harness 帮你把这些线索接起来，在自己的电脑上运行，模型由你选择。

[**先看效果 →**](docs/quick-tour.md) · [**复制安装提示词 →**](#ai-install) · [无需账号的演示](#quickstart) · [支持哪些模型](#models) · [English](README.en.md)

[![Tests](https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml/badge.svg)](https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml) · [MIT 开源](LICENSE) · [v0.3.0 预览版](https://github.com/milkrabbitt/job-search-harness/releases/tag/v0.3.0)

## 三件值得交给它的事

| 你想弄清楚什么 | 工作台怎么帮你 |
| --- | --- |
| **这份岗位，和我的经历到底哪里对得上？** | 上传 PDF、确认经历，授权所选模型对照实际 JD。匹配点附来源引用，缺口和待核实项一起列出。 |
| **这几份工作的薪资，能放在一起看吗？** | 选 2–6 个岗位，对照原始报价、币种、周期和明示薪数。有明确人民币月薪与薪数才折算年额；面议就保留待确认。 |
| **招聘方回了什么，接下来该我做什么？** | 已同步或登记的消息按岗位回看。接通并授权网易 126 后，可按规则处理常规申请与回复；面试、谈薪、Offer 进入「待我决定」。邮箱接入目前需要 macOS 钥匙串。 |

![招聘消息实际界面：同一岗位下回看已发申请和面试邀请；全部为虚构演示](docs/assets/demo-inbox.jpg)

*实际软件截图，使用虚构岗位、消息与模拟回执。不是实际求职成果；BOSS、猎聘和鱼泡站内操作仍在已登录浏览器完成。*

[看简历上传、薪资对比和完整流程 →](docs/quick-tour.md)

<a name="ai-install"></a>

## 不想手动安装？把这段交给你的编程助手

适用于能操作你电脑终端的 Codex、Claude Code、Cursor Agent。**帮你安装的助手，和工作台运行时使用的模型，可以分开选。**

```text
请帮我安装并跑通 Job Search Harness：
https://github.com/milkrabbitt/job-search-harness

先阅读 README 和 docs/install.md 中的完整安装提示词，按实际系统操作。
检查已有安装与数据，在新目录、独立 Python 环境中安装，先跑虚构数据演示。
验证页面可打开、岗位和招聘消息可查看；不要接真实账号或发送消息。
再为正式使用建立独立私有数据目录，打开 /setup，向我收集求职条件、
可用模型和允许读取的来源；打开 /profile，让我上传并确认简历经历。
密钥由我在本机填写。连接测试通过后再启用扫描；真实外发另行由我授权。
最后给我实际验证结果、本机地址、启动与停止方式；遇到缺失信息不要猜。
```

[完整安装提示词与排错步骤](docs/install.md#ai-install) · [模型接入说明](docs/models.md)

<a name="quickstart"></a>

## 先跑演示：不用简历、不用 API Key、不用邮箱账号

准备 Git、Python 3.9+、SQLite 3.24+ 与 IANA 时区数据。安装依赖需要联网；演示流程本身使用虚构数据与模拟发送，不调用真实模型或邮箱。无需 Node.js、Docker 或前端构建。

请使用新目录；已有安装可按[安装说明](docs/install.md)另设演示数据目录。

<details open>
<summary><strong>macOS / Linux</strong></summary>

```sh
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
./scripts/bootstrap.sh --demo --install-deps
./scripts/serve.sh
```

</details>

<details>
<summary><strong>Windows PowerShell</strong></summary>

```powershell
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
.\scripts\bootstrap.ps1 -Demo -InstallTimezone -InstallDependencies
.\scripts\serve.ps1
```

</details>

打开终端显示的本机地址，默认为 **http://127.0.0.1:8876**。依次看「岗位」「招聘消息」「草稿与发送」「待我决定」。PDF 读取依赖 pypdf；安装失败或时区缺失可按[故障排查](docs/troubleshooting.md)处理。

<a name="start"></a>

## 开始自己的求职流程

停止演示，为真实资料设置仓库外的 `JOB_HARNESS_DATA_DIR`，初始化时去掉 `--demo` / `-Demo`。初始化和每次启动都使用同一个数据目录。[按系统配置独立目录](docs/install.md)

1. **填条件：** `/setup` 设置方向、城市、薪资底线和排除项。
2. **接模型和来源：** 选择自己的模型、测试连接，登记允许读取的招聘来源。也可先用本机规则筛选。
3. **确认经历：** `/profile` 上传 PDF，核对文字、补充贡献，决定是否授权模型匹配。
4. **开始工作：** 通过检查后启动扫描与匹配；在岗位和消息页查看进度。外发需独立授权，关键决定留给你。

控制台当前为中文。进程需要保持运行，电脑休眠或关机会暂停任务；**Ctrl+C** 停止服务。没有配置来源时不会自动发现岗位。[首次配置与日常操作](docs/onboarding.md)

<a name="models"></a>

## 接你自己的模型

| 接入方式 | 可以选择什么 | 需要准备什么 |
| --- | --- | --- |
| 已登录的本机 CLI | Codex CLI | 已认证且调用参数兼容的 CLI、实际可用的模型 |
| 云端 API | OpenAI、Anthropic、Gemini、DeepSeek、Qwen、Moonshot、Zhipu、MiniMax | 对应 API 凭据、可用模型名与地区端点；聊天会员不等于 API 余额 |
| 本机模型服务 | Ollama、LM Studio | 已启动服务并加载模型；安装工作台不会自动下载模型 |
| 兼容接口 | OpenAI-compatible | 兼容端点、模型名及所需凭据 |

这些是**已实现的适配方式**，有模拟协议测试；实际账号与端点需要在你电脑上通过连接测试。云端调用可能计费，可设置每日调用次数与超时。[完整模型矩阵与配置](docs/models.md)

## 数据、渠道与控制权

- **数据保存在本机，模型由你选择。** 授权经历匹配后，所选模型会收到裁剪、遮蔽后的经历与 JD；云模型因此会收到这些内容。原 PDF 留在本机。遮蔽不等于内容完全匿名。[简历与匹配](docs/candidate-guide.md)
- **渠道能力写清楚。** 官网读取已登记的公开来源；网易 126、猎聘授权搜索的凭据接入目前依赖 macOS 钥匙串。BOSS、猎聘站内聊天、鱼泡由操作者在已登录浏览器完成动作并保留记录。[渠道说明](job_harness/CHANNELS.md)
- **扫描与发送分开控制。** 面试、薪资、到岗和 Offer 等进入待决定队列。可以暂停任务，也可以单独暂停外发。
- **私有文件需要自己保管。** 本机文件不加密，不要提交简历、邮箱记录或密钥；数据库备份不含模型密钥，原简历需另行保管。金额预算尚非实时账单控制器，详见[模型说明](docs/models.md)与[备份维护](docs/onboarding.md#maintenance)。

<a name="tests"></a>

## 开源，也欢迎一起把它做好

如果你也想让求职进度更清楚，**点一个 Star，方便下次找回来**。试用后欢迎告诉我们：哪一步卡住了、哪一项还需要自己记。报告问题请用虚构数据，移除简历、招聘私信和密钥。

[反馈问题](https://github.com/milkrabbitt/job-search-harness/issues) · [版本记录](https://github.com/milkrabbitt/job-search-harness/releases) · [宣传文案与口播](docs/launch/zh-CN.md) · [真机演示录制提示词](docs/video-demo-prompt.md)

项目采用 [MIT 许可证](LICENSE)。自动化覆盖核心流程、模型协议、恢复逻辑和虚构数据浏览器流程；跨系统结果以 [GitHub Actions](https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml) 为准。真实模型账号、邮件送达与招聘平台登录需在用户环境验证。

<details>
<summary>本地运行测试</summary>

```sh
.venv/bin/python -B -m unittest discover -s tests -v
```

以上使用默认安装的虚拟环境。Windows 使用 `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`；自选环境请使用其 Python。

</details>
