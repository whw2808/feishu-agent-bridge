# Feishu Agent Bridge

[English](README.md)

让 Agent 在后台阅读飞书群并处理协作，开发主对话继续工作。首版支持 Codex，独立于 knok-agent。

- 实时接收真人和其他机器人的真实 @。
- 每 30 秒补读已配置群的全部新消息，每分钟阅读未处理消息；不限于 @。
- 独立后台会话推理，SQLite 保存消息、阅读状态、回复和开发待办。
- 长任务反馈真实状态；普通消息不主动插话，纯确认不反复互相 @。
- 提供 CLI、MCP、标准插件包以及 macOS/Linux 用户服务。

## 安装与接入

需要 Python 3.11+、Codex CLI 0.162.x、已登录的 lark-cli profile，以及加入目标群的飞书应用机器人。当前提供源码与本地插件分发，不表示已经发布 PyPI 或上架官方目录。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[agent,mcp]'
source .venv/bin/activate
feishu-agent setup \
  --profile YOUR_LARK_PROFILE \
  --chat-id oc_YOUR_GROUP \
  --bot-open-id ou_YOUR_BOT \
  --bot-app-id cli_YOUR_APP \
  --project-dir /absolute/path/to/project
feishu-agent doctor
feishu-agent start
feishu-agent status
```

示例 ID 需要替换成自己应用下的真实 ID。机器人收取其他机器人 @ 需要 include-bot 权限；历史读取和机器人发送也需相应权限。用户发送消息、用户建群权限不是本插件的前提。凭据复用 lark-cli，不放入源码或配置文件。

默认从启用时刻开始阅读；通过 `--since-ms` 指定回溯起点。配置文件默认位于 `~/.config/feishu-agent-bridge/config.json`，消息数据库位于 `~/.local/state/feishu-agent-bridge/<profile>/`。配置可增加多个群，所有访问仅限配置群。

```sh
codex plugin marketplace add /absolute/path/to/feishu-agent-bridge
```

刷新或重启桌面应用，在本地插件来源中安装 Feishu Agent Bridge。Python 包须先安装且 `feishu-agent` 在宿主 PATH 中；插件本身不会自动安装常驻服务，也不依赖生命周期 hooks。自定义配置路径时，MCP 参数设置为 `["--config", "/absolute/path/to/config.json", "mcp"]`。

## 开发主对话如何处理待办

后台可以回答协作问题、更新摘要。需要修改代码或让任务 owner 作决定时，写入待办，不直接修改项目。

```sh
feishu-agent inbox
feishu-agent claim TASK_ID --owner PRIMARY_SESSION_ID
feishu-agent complete TASK_ID --owner PRIMARY_SESSION_ID --result '已完成的工作与验证结果'
```

也可通过插件 MCP 查询、认领和回写。此处认领仅是插件待办，不能替代仓库的 claim/lease/PR 流程。重复回写不会重复发送完成消息。

需要自动交接到主对话时可启用：

```sh
feishu-agent bind PRIMARY_SESSION_ID --auto-handoff
feishu-agent stop
feishu-agent start
```

必要待办通过 `codex queue` 在后续回合处理；不会 steer 或 interrupt 当前回合。普通回复始终由后台会话处理。

## 运行与恢复

`run` 前台运行，`start` 启动用户常驻服务，`stop` 仅停止本实例，`doctor` 检查能力，`summaries` 查看群摘要。电脑休眠期间不推理，恢复后补读历史。Windows 用户服务留到后续版本。

“已采集、已阅读、已回复、已转交”分开保存。大消息按完整分片阅读，编辑版本重新阅读；附件只保存元数据，不假装已检查内容。网络超时导致发送结果不明确时保留 `uncertain`，不盲目重发。已知失败可修复原因后执行 `retry REPLY_ID`。

所有消息和日志默认仅保留在本机；需要定期按自己的数据保留规则备份/清理。当前版本尚未提供自动保留期限清理。源码不包含个人账户、群信息、凭据或本地运行数据。

开发验证、配置默认值和兼容边界见 [英文 README](README.md)、[运维说明](docs/operations.md) 和 [验证记录](docs/validation.md)。MIT 开源。
