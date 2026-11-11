# 安装与首次运行

[返回首页](../README.md) · [English](install.en.md)

安装完成后，程序仍需要你的求职条件、可用模型和招聘来源。可以先跑离线演示，也可以直接打开首次配置向导。两种数据请分开保存。

<a name="ai-install"></a>

## 交给编程助手的安装提示词

以下提示词适用于能操作本机终端的 Codex、Claude Code、Cursor Agent 等工具。它们帮你安装；运行时可以选择另一家模型服务。

```text
请在我的电脑上安装并验证 Job Search Harness。
仓库：https://github.com/milkrabbitt/job-search-harness

1. 阅读 README、docs/install.md、docs/models.md、docs/onboarding.md 和 job_harness/CHANNELS.md。检查实际操作系统、Python、SQLite、时区数据和端口，不覆盖已有安装或数据。
2. 克隆到新目录，创建独立 Python 虚拟环境。按操作系统使用 scripts/doctor.sh 或 scripts/doctor.ps1；将 PYTHON_BIN 指向该虚拟环境的 Python，用该解释器运行 -m pip install -r requirements.txt。缺时区数据时另外安装 tzdata，不修改系统的全局执行策略。
3. 在独立演示数据目录初始化 --demo（PowerShell 为 -Demo），启动服务并验证虚构岗位、申请记录、招聘消息和待决定事项。运行项目测试，演示期间关闭真实账号与外发。
4. 为真实使用选择仓库外的私有数据目录，设置 JOB_HARNESS_DATA_DIR；不带演示参数初始化并启动。打开 /setup，向我收集求职目标、可用模型和允许读取的招聘来源。再打开 /profile，让我上传 PDF、核对提取文字和确认真实经历，不替我猜测。
5. 向我说明 Codex CLI 登录、云 API 和本机 Ollama/LM Studio 的区别。模型名使用我账号实际可用的名称；本机模型需已加载。API Key 让我在本机向导输入或设置环境变量，不让我贴到聊天，也不写入源码、截图或日志。
6. 说明模型连接测试可能计费，按我选择的服务进行测试。检查结果与模型、端点一致；测试失败先排查，不把保存配置当成连接成功。不调用我没有提供或授权的真实账号。
7. 完成向导、登记招聘来源后开始扫描与匹配。由我在 /profile 决定是否同意将裁剪并遮蔽后的经历与 JD 发给所选模型。真实外发保持关闭，邮箱接通后再按我的明确授权启用自动申请和常规回复。
8. 告诉我实际通过和未通过的检查、本机地址、数据目录、启动和停止命令、调用上限及备份方式。说明电脑休眠后任务会暂停；没有配置来源时无法自动找岗位。
```

## 环境要求

- Python 3.9+，包含 `sqlite3`、`ssl`、`zoneinfo`；SQLite 3.24+。
- PDF 文字提取依赖 `pypdf==6.21.4`，通过所用虚拟环境安装 `requirements.txt`。缺少时仍可手动填写经历并使用其他核心功能，上传页面会提示需手动核对。
- IANA 时区数据包含 `Asia/Shanghai`。Windows 或精简系统缺少时，在所用虚拟环境安装 `tzdata`。
- macOS、Linux 或 Windows PowerShell。Windows 支持核心工作台与模型 API；126 邮箱、猎聘授权搜索的凭据接入仍依赖 macOS 钥匙串。
- 默认控制台端口 `8876`，仅本机访问。默认运行无需 Node.js、Docker 或前端构建。

跨系统自动化结果见 [CI](https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml)。CI 和模拟协议测试不能验证你自己的模型权限、服务余额、邮箱或招聘平台账号。

<a name="quickstart"></a>

## macOS / Linux

```sh
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
python3 -m venv .venv
export PYTHON_BIN="$PWD/.venv/bin/python"
"$PYTHON_BIN" -m pip install -r requirements.txt
./scripts/doctor.sh
```

也可以在已克隆目录运行 `./scripts/bootstrap.sh --demo --install-deps`，由脚本创建或复用本地 `.venv` 并安装依赖。已经设置 `PYTHON_BIN` 时使用指定解释器。

诊断报告缺少时区数据时，使用 `"$PYTHON_BIN" -m pip install tzdata`，然后重新诊断。

先体验演示：

```sh
./scripts/bootstrap.sh --demo
./scripts/serve.sh
```

真实使用前停止演示，再设置独立目录。下面路径可自行更改：

```sh
export JOB_HARNESS_DATA_DIR="$HOME/.local/share/job-search-harness"
./scripts/bootstrap.sh
./scripts/serve.sh
```

每次重开终端都需要使用相同的 `JOB_HARNESS_DATA_DIR` 和 `PYTHON_BIN`；它们决定使用哪个数据目录和 Python 环境。

## Windows PowerShell

```powershell
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
py -3 -m venv .venv
$env:PYTHON_BIN = "$PWD\.venv\Scripts\python.exe"
& $env:PYTHON_BIN -m pip install -r requirements.txt
.\scripts\doctor.ps1
```

如果没有 `py` 启动器但 `python` 指向 Python 3.9+，使用 `python -m venv .venv`。诊断报告缺少时区数据时，在此环境运行 `& $env:PYTHON_BIN -m pip install tzdata`。

先体验演示：

```powershell
.\scripts\bootstrap.ps1 -Demo
.\scripts\serve.ps1
```

真实使用前停止演示，再设置独立目录：

```powershell
$env:JOB_HARNESS_DATA_DIR = Join-Path $env:LOCALAPPDATA "JobSearchHarness"
.\scripts\bootstrap.ps1
.\scripts\serve.ps1
```

也可运行 `.\scripts\bootstrap.ps1 -Demo -InstallTimezone -InstallDependencies`，使用私有虚拟环境安装时区与 PDF 依赖后初始化演示。

若 PowerShell 阻止脚本，不必全局关闭执行策略。已经配置好上述 Python 环境后，可以直接运行同一个 Python 入口：

```powershell
$env:JOB_HARNESS_DATA_DIR = Join-Path $env:LOCALAPPDATA "JobSearchHarness"
& $env:PYTHON_BIN -B -m job_harness --data-dir $env:JOB_HARNESS_DATA_DIR init --empty
& $env:PYTHON_BIN -B -m job_harness --data-dir $env:JOB_HARNESS_DATA_DIR serve
```

如设备管理同时限制 Python，请使用单位允许的安装方式。

## 完成首次配置

打开终端显示的本机地址，进入 `/setup`。向导顺序为：

1. 求职条件：目标方向、城市或远程偏好，可选薪资底线和个人简介。
2. 模型：选择服务和实际模型名；在本机提供凭据，测试连接。也可明确选择不调用模型。
3. 来源：登记公开招聘来源。保存时检查 URL 格式；实际可访问性、内容解析和岗位数量由首次扫描确认。
4. 核对后开始：检查条件、模型与来源，再启动扫描。重复点击开始不会重复创建同一轮启动任务。

开始扫描不会打开真实外发。进度可在控制台查看；失败的来源和模型调用需要排查，不能把“任务已安排”当成已有结果。

服务运行在当前终端。关网页后仍会运行；按 **Ctrl+C** 停止。macOS 另外提供 LaunchAgent，见[渠道说明](../job_harness/CHANNELS.md)。Windows / Linux 的默认安装不会自动注册开机服务。

<a name="private-profile"></a>

## 登记真实资料和简历

推荐打开 `/profile`，用文件选择框上传不超过 20 MB 的 PDF，填写版本名称与适用岗位，并确认是否批准作为申请附件。程序保存本机私有副本和文件指纹，并尝试提取文字。在「核对简历文字」检查并修正结果，再确认该版本的文字可用于匹配。上传和批准附件完成后，经历文字仍需要你确认。

在同一页补充最多 6000 字的真实经历摘要，可另填最多 600 字的对外简介。通过 `profile_model_consent` 选择是否允许将处理后的经历与 JD 发送给所选模型。扫描件、加密 PDF、提取依赖缺失或解析失败时，页面明确提示手动补充。旧版 `/setup` 绝对路径登记方式仍可用于附件管理。 [完整操作说明](candidate-guide.md)

简历不是开始扫描的必填项。准备正式申请时，需要匹配适用范围的已批准简历、核实过的事实简介及已核实的收件人。网盘链接不能代替本机文件路径。

### 高级用法：批量导入私有 JSON

需要多条经历、常用回答或多个简历版本时，可使用下面的导入方式；首次安装不必先做这一步。

1. 在仓库外保存一份私有 `profile.json`，参考[虚构资料示例](../examples/profile.example.json)。只填写能确认的经历、常用回答与公开作品链接。
2. 在 `resumes` 中为批准使用的 PDF 登记 `path`、`label`、`scope`（`general`（通用）、`ai`、`robotics` 或 `finance`）、`sha256` 和 `approved: true`。JSON 导入校验文件与哈希；需要提取和确认 PDF 经历文字时，在 `/profile` 使用上传流程。
3. macOS / Linux 可用 `shasum -a 256` 或 `sha256sum`；Windows 可用 `(Get-FileHash -Algorithm SHA256 'C:\path\resume.pdf').Hash.ToLower()` 计算哈希。
4. 停止当前实例后导入到同一私有数据目录，再启动并核对资料。

macOS / Linux：

```sh
"$PYTHON_BIN" -B -m job_harness --data-dir "$JOB_HARNESS_DATA_DIR" init --inheritance /path/to/private/profile.json
./scripts/serve.sh
```

Windows：

```powershell
& $env:PYTHON_BIN -B -m job_harness --data-dir $env:JOB_HARNESS_DATA_DIR init --inheritance C:\path\private\profile.json
.\scripts\serve.ps1
```

不要把真实 `profile.json`、PDF、数据库或密钥提交到 Git。模型本地密钥保存在数据目录 `private/model-keys/`，不加密，也不进入数据库备份。详见[模型凭据](models.md#credentials)和[备份与卸载](onboarding.md#maintenance)。
