# 模型接入 / Model connections

[返回首页](../README.md) · [安装](install.md) · [首次配置](onboarding.md)

在本机 `/setup` 选择供应商、填写模型名和端点，然后测试。模型复核只总结有限的结构化匹配特征，提供复核建议；岗位筛选、发送授权和任务状态仍由工作台管理。

## 支持矩阵

下表是当前适配器的默认设置。**适配已实现、模拟协议可测试，不代表每个真实账号、模型或地区均已验证。** 模型名不预填：请用服务后台可用的名称，或本机已加载的名称。第一次在自己的电脑上连接成功后再启用。

| 向导选项 / provider | 协议与默认 API Base | 凭据方式 |
| --- | --- | --- |
| 不调用模型 / `none` | 仅运行规则筛选，无外部模型请求 | 无 |
| Codex CLI / `codex_cli` | 本机已安装且已认证的 CLI；需要兼容当前调用参数 | CLI 官方登录流程 |
| OpenAI / `openai` | Chat Completions；`https://api.openai.com/v1` | `OPENAI_API_KEY` 或本机密钥文件 |
| Anthropic / `anthropic` | Messages；`https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` 或本机密钥文件 |
| Gemini / `gemini` | Chat Completions 兼容接口；`https://generativelanguage.googleapis.com/v1beta/openai` | `GEMINI_API_KEY` 或本机密钥文件 |
| DeepSeek / `deepseek` | Chat Completions；`https://api.deepseek.com` | `DEEPSEEK_API_KEY` 或本机密钥文件 |
| Qwen / `qwen` | Chat Completions；`https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` 或本机密钥文件 |
| Moonshot / `moonshot` | Chat Completions；`https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` 或本机密钥文件 |
| Zhipu / `zhipu` | Chat Completions；`https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` 或本机密钥文件 |
| MiniMax / `minimax` | Chat Completions 兼容接口；`https://api.minimax.io/v1` | `MINIMAX_API_KEY` 或本机密钥文件 |
| Ollama / `ollama` | 本机兼容接口；`http://127.0.0.1:11434/v1` | 本机未启用认证时无需 Key；可选 `OLLAMA_API_KEY` |
| LM Studio / `lmstudio` | 本机兼容接口；`http://127.0.0.1:1234/v1` | 本机未启用认证时无需 Key；可选 `LM_STUDIO_API_KEY` |
| OpenAI-compatible / `openai_compatible` | 由你填写兼容的 API Base，使用 Chat Completions | `JOB_HARNESS_MODEL_API_KEY` 或本机密钥文件 |

Qwen 默认是中国内地端点，Moonshot 默认 `.cn`，MiniMax 默认 `.io`。其他地区的账号需填写与该账号匹配的端点；不要将某地区的 Key 和另一区域端点混用。兼容服务的能力有差异，并非任何模型都支持同样的输出参数；测试失败时检查供应商实际接口说明。

MiniMax 的推理可能占用输出预算；上限太低时可能无法返回完整结构化答案。此时安全报错，不会自动提高上限或重试。

## 会员、API 和本机模型不是一回事

- **Codex CLI** 使用你在这台电脑上的认证与可用额度。能打开聊天产品不代表本机 CLI 已配置成功。
- **云 API** 使用自己的 API Key、模型权限和 API 余额。聊天会员不会自动转换成另一家的 API 额度。
- **Ollama / LM Studio** 需要先安装服务、下载并加载模型，再启动兼容 API。Harness 只连接该服务，不安装或下载模型。
- **安装助手** 可以是 Codex、Claude Code 或 Cursor Agent；这不限制 Harness 的运行时模型选择。

## 连接测试与数据范围

模型测试发送合成的匹配特征并检查结构化回答，云端调用可能收费。它验证当时的凭据、模型、URL 和输出格式，不证明招聘来源可读取，也不保证供应商之后永不限流。

真实复核发送有限字段：匹配状态、分数、岗位分类、缺口数量、未知项数量以及固定列表中的优势标签；不把完整简历、原始招聘聊天或整份 JD 作为模型请求。云端接入仍会将这些字段传给你配置的模型服务。

更换供应商、模型名、API Base 或凭据后，重新测试。账号无权限、余额不足、模型不存在、超时或输出格式不符合要求时，查看本机结果；不要把保存成功当成连接成功。

## 端点规则

- 外部服务必须使用 **HTTPS** 并通过系统证书校验。
- 明文 HTTP 只允许回环地址，如 `127.0.0.1`、`localhost` 或 `[::1]`；Ollama / LM Studio 本机预设仅连接回环服务。
- 填 API Base，不要再追加 `/chat/completions` 或 `/messages`；适配器会添加对应路径。
- 不跟随重定向，不自动重试或切换供应商。失败后先查证，再决定是否重试，避免重复计费或将凭据发往意外端点。
- 自定义 HTTPS 端点应由你确认可信；启用 API Key 时，该端点会收到凭据。

<a name="credentials"></a>

## API Key 保存在何处

两种方式任选其一：

1. **环境变量。** 使用上表名称，或在设置中指定 `api_key_env`。启动服务的进程必须能读取该变量；修改后重启服务。环境变量非空时优先于本机文件。
2. **本机向导输入。** 保存为私有数据目录下的 `private/model-keys/<provider>.key`。POSIX 下目录权限为 `0700`，文件权限为 `0600`；Windows 使用当前用户目录的访问控制。

**这些文件是未加密的本地凭据文件，不是系统钥匙串或加密保险箱。** 使用自己控制的用户目录与磁盘保护，不要放进共享目录、Git 仓库、云同步盘或问题附件。Key 不写入 SQLite、普通配置快照或模型响应日志。数据库备份不包含它们；恢复到其他电脑需重新提供 Key。

如果更新本机文件后仍使用旧 Key，检查同名环境变量是否覆盖了文件。删除本机 Key 不会撤销供应商那边的凭据，也不会清空外部环境变量；如需彻底停用，在供应商后台撤销，并清理本机配置。

## 调用限制与费用

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `max_calls` | `3` | 每日调用上限，范围 `0–1000`；`0` 禁止调用。连接测试和失败的调用尝试也计入上限。 |
| `timeout` | `75` | 单次超时秒数，范围 `5–180`。 |
| `max_output_tokens` | `1024` | HTTP API 请求的输出 token 上限，范围 `64–4096`；Codex CLI 不由此字段限制。 |
| `cost_limit` | 空字符串 | 当前无法可靠计价；非空值会阻止调用，不会假装已执行金额预算。 |

超时限制作用于请求与响应读取，本机网络或 DNS 故障仍可能影响等待时间。API token 统计在服务提供时记录，不代表实际账单。计费价格、缓存、推理 token、折扣和订阅额度由供应商决定。需要硬金额上限时，另用供应商后台的可用额度与预算控制；不要把 Harness 的调用次数当成金额保证。

## English quick guide

Choose a provider in `/setup`, enter an exact model name available to your account or loaded local service, and test before starting. The table lists implemented adapters and their default bases, not a promise of live access to every model or region. Provider tests in this repository use simulated responses; your own account needs a real connection test.

Cloud APIs need API credentials and may incur charges. A chat subscription does not automatically provide API credits. Local Ollama/LM Studio must already be running with a model loaded. Codex CLI needs its own successful local authentication.

Only limited structured match features are sent for review, not full résumés or raw recruiter threads. Remote endpoints require HTTPS; plain HTTP is loopback-only. The client does not follow redirects, automatically retry or fail over. Retest after changing provider, model, endpoint or credentials.

Environment keys take precedence over unencrypted files under `<data-dir>/private/model-keys/`. POSIX permissions are restricted; Windows relies on your user's directory permissions. Keys are excluded from SQLite backups. The HTTP output cap does not cap Codex CLI output. A nonempty currency budget blocks calls because reliable monetary accounting is not implemented.

## 官方接口说明

[OpenAI](https://developers.openai.com/api/reference/resources/chat) · [Anthropic](https://platform.claude.com/docs/en/api/messages/create) · [Gemini](https://ai.google.dev/gemini-api/docs/openai) · [DeepSeek](https://api-docs.deepseek.com/api/create-chat-completion/) · [Qwen 地区端点](https://www.alibabacloud.com/help/en/model-studio/base-url) · [Kimi / Moonshot](https://www.kimi.com/help/kimi-api/api-troubleshooting) · [MiniMax](https://platform.minimax.io/docs/api-reference/text-openai-api) · [Ollama](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx) · [LM Studio](https://lmstudio.ai/docs/developer/openai-compat)

各家模型名、权限、地区和计费以自己的账号与官方文档为准。工作台默认端点不代表订阅、Coding Plan 或全部产品入口互通。
