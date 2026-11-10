# Job Search Harness

一个在自己电脑上运行的求职工作台。先填你想找的岗位、城市和薪资要求，登记自己的经历和可以发送的简历 PDF。Harness 按你的条件筛岗位，用你核实过的资料准备申请，再把发过的简历、招聘方的回复和面试待办记在一起。

[运行离线演示](#quickstart) · [让编程助手帮我安装](#ai-install) · [English](README.en.md) · [v0.1.0 预览版](https://github.com/milkrabbitt/job-search-harness/releases/tag/v0.1.0)

## 实际用起来是什么样

1. **先填自己的资料和求职条件。** 在本机登记个人经历、常用回答、公开作品链接和已批准的 PDF；再设置岗位方向、城市、可接受的最低薪资和排除项。简历通过[私有资料文件](examples/profile.example.json)登记，目前不会从 PDF 自动提取经历。
2. **找岗位，先看为什么匹配。** Harness 检查你配置的公开招聘来源，也支持经授权的猎聘职位搜索和手动补录 JD。结果会列出匹配点、不符合的条件和还需要问清的地方，并合并能确认重复的岗位。当前规则主要覆盖中文 AI 应用、产品、机器人和金融产品岗位；可选的 Codex CLI 只复核部分匹配信息。
3. **准备申请，把发出的材料记下来。** 核实招聘邮箱后，可以用已批准的简历准备申请草稿。接通网易 126 并开启授权后，可按规则发送。每次申请都可回看公司、岗位、收件人、正文、简历版本和可查到的发送凭据。发送结果不明时先核验，避免重复发送。
4. **收到招聘回复后，知道该谁接手。** 对已同步或登记的消息，可以按岗位查看往来内容和时间。授权后的网易 126 邮箱可自动处理部分明确的请求，例如发送已批准的简历、提供公开作品链接，或回答已有核实资料支持的简单问题。面试邀约会进入「待我决定」，由你确认时间；薪资、到岗和 Offer 也由你决定。

![招聘消息页：同一会话里可以看到发出的申请和招聘方的面试邀约](docs/assets/demo-inbox.jpg)

*实际控制台截图，岗位、邮件和面试邀请均为虚构演示数据。*

**怎么看不同岗位的薪资？** 「岗位」页显示招聘信息里的薪资区间；填了底线后，可筛掉公开区间上限低于底线的岗位。投递台账可以导出 CSV，方便自己对照有记录的岗位。未公开的薪资留待核实，目前没有自动比较 Offer 总包的功能。

## 各渠道能做什么

| 渠道 | 当前支持 |
| --- | --- |
| 公司官网 | 检查已登记且可访问的公开招聘来源。官网申请表由你在原站提交。 |
| 网易 126 邮箱 | 用 macOS 钥匙串授权后，可按设置收发、处理部分明确的请求并核验发送结果。 |
| 猎聘 | 可用候选人授权搜索职位、补查公开详情。站内聊天和提交由你在浏览器完成。 |
| BOSS 直聘、鱼泡 | 工作台可登记待办、查重和保存回执；站内投递与回复仍由你在已登录的浏览器完成。 |

<a name="quickstart"></a>

## 运行离线演示

需要 macOS 或 Linux、Python 3.9+、SQLite 3.24+ 和 IANA 时区数据。演示无需邮箱、真实简历或模型账号，界面目前为中文。

```sh
git clone https://github.com/milkrabbitt/job-search-harness.git
cd job-search-harness
./scripts/doctor.sh
./scripts/bootstrap.sh --demo
./scripts/serve.sh
```

在浏览器打开终端显示的本机地址，默认是 `http://127.0.0.1:8876/`。终端按 **Ctrl+C** 停止。演示不会向任何招聘方发送消息。

<a name="ai-install"></a>

## 让编程助手帮我安装

[复制安装提示词](docs/install.md#ai-install)，可交给 Codex、Claude Code 或 Cursor Agent。它们负责安装和验证；运行时模型是另一个可选设置，目前使用在本机登录的 Codex CLI。

使用真实资料时，选择一个和演示分开的私有数据目录；简历、申请记录和凭据不要放进 Git。可参考[如何登记真实资料和简历](docs/install.md#private-profile)、[账号配置](examples/config.example.json)、[资料导入示例](examples/profile.example.json)、[渠道接入](job_harness/CHANNELS.md)和[故障排查与备份](docs/troubleshooting.md)。

<a name="tests"></a>

## 测试

```sh
python3 -B -m unittest discover -s tests -v
```

测试使用临时数据库、虚构资料和模拟发送，覆盖匹配、查重、草稿失效、附件校验及发送结果核验。查看 [GitHub Actions](https://github.com/milkrabbitt/job-search-harness/actions/workflows/tests.yml)。项目采用 [MIT 许可证](LICENSE)；反馈问题时请使用虚构数据。
