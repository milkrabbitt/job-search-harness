# 安装与首次运行

[返回首页](../README.md) · [English](install.en.md)

<a name="ai-install"></a>

## 让 AI 帮我安装

复制下面这段，交给 Codex、Claude Code 或 Cursor Agent。你用来安装项目的编程助手，和项目运行时可选的模型，是两个独立角色。体验演示只需要本机 Python。运行时模型复核岗位时，使用已由你在本机登录的 Codex CLI。

```text
请在我的电脑上安装并验证 Job Search Harness。
仓库 https://github.com/milkrabbitt/job-search-harness

1. 克隆到一个新目录，阅读 README 和 job_harness/CHANNELS.md。
2. 运行 ./scripts/doctor.sh，检查 Python、SQLite、时区数据和可用端口。用 python3 -m venv .venv 创建隔离环境，把 PYTHON_BIN 指向 .venv/bin/python。按 requirements.txt 核对依赖，仅在缺少时区数据时按文档安装。
3. 运行 ./scripts/bootstrap.sh --demo，再用 ./scripts/serve.sh 启动演示，打开它显示的本机地址。先用虚构数据检查岗位匹配、申请草稿和模拟发送证据、招聘消息与待决定事项。
4. 用 .venv/bin/python -B -m unittest discover -s tests -v 运行测试。演示和测试期间保持真实账号与外发关闭。
5. 如果我希望使用运行时模型，检查本机是否已有经我本人登录的 Codex CLI。向我说明这一步是可选的。等我本机完成认证后，在独立的非演示数据目录中将 config.json 的 model.enabled 设为 true，必要时设置 model.executable，重启该实例。然后在控制台“资料与设置”配置实际可用的模型、调用上限与超时，并安排模型检查。
6. 把实际验证结果、本机地址、数据目录、启动与停止方法告诉我。不要读取或复用已有求职数据。密钥与授权码由我在本机的系统凭据管理器或官方登录流程中配置，禁止粘贴到聊天、写入代码或提交到 Git。
```

<a name="quickstart"></a>

## 立即体验

需要 macOS 或 Linux、Python 3.9+、SQLite 3.24+ 和包含 `Asia/Shanghai` 的 IANA 时区数据。界面当前为中文。默认运行只使用 Python 标准库。

```sh
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
./scripts/doctor.sh
./scripts/bootstrap.sh --demo
./scripts/serve.sh
```

打开 [本地控制台](http://127.0.0.1:8876/)。服务运行在当前终端，关掉网页后仍在运行，在终端按 **Ctrl+C** 停止。默认数据放在仓库下独立的 `.job-harness-dev/`。要切换数据目录，给初始化和启动脚本设置同一个 `JOB_HARNESS_DATA_DIR`。遇到端口冲突或缺少依赖，可看 [故障排查](troubleshooting.md)。

