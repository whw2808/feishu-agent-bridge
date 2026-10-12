# Feishu Agent Bridge

[中文说明](README.zh-CN.md)

A local, open-source collaboration plugin for Codex. An independent background agent reads Feishu groups and answers coordination requests while your primary coding conversation keeps working.

- Live **real @ mentions** from people and other bots.
- Periodic collection and reading of **all new messages in configured groups**, including unmentioned messages.
- Durable SQLite inbox, independent background inference, truthful progress feedback, validated replies and development handoffs.
- Portable Agent Plugins package, local stdio MCP, CLI and macOS/Linux user services.

The original mention-to-primary-session prototype is not used for routine messages. Only optional necessary handoffs enter the primary session, at its next turn boundary.

## Install from source

Requires Python 3.11+, Codex CLI 0.162.x, an authenticated lark-cli profile and a Feishu application bot in the selected groups. This repository is release-ready source; no PyPI or public plugin-directory publication is implied.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[agent,mcp]'
# Activate the environment so feishu-agent is discoverable by the plugin host:
source .venv/bin/activate
```

`agent` installs the Python Codex SDK; `mcp` installs the local plugin server. The base CLI/state tools have no third-party dependencies. The background runtime uses the explicitly configured Codex executable and requires compatible 0.162.x behavior.

## Feishu access

Use your own Feishu app, bot and lark-cli profile. Configure the `im.message.receive_v1` event. People-only mention access does not cover other bots; select the include-bot mention capability when collaborating with bots. History collection requires permission to read the selected group's messages; replies use the **bot identity**, not user sending. Group-member read access resolves peer bot app IDs into real application-scoped mention IDs. Setup neither creates groups nor sends as a user.

Reuse lark-cli's setup/authentication. Do not put App Secret/access tokens into this repository or `config.json`. See the [official message event requirements](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive) and [history API](https://open.feishu.cn/document/server-docs/im-v1/message/list).

```sh
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

IDs are placeholders; resolve them within your own application's profile. `setup` refuses to overwrite an existing config. It starts reading at activation time; add `--since-ms EPOCH_MILLISECONDS` to deliberately backfill earlier history. Add further groups to the configuration's `groups` array. Defaults follow XDG directories: `~/.config/feishu-agent-bridge/config.json` and `~/.local/state/feishu-agent-bridge/<profile>/`.

The process is foreground with `feishu-agent run`, or a user service with `start`. `stop` manages only this instance; it never stops the shared lark-cli event bus. macOS keeps running while the user is logged in and the machine is awake. Linux user-service login/lifetime follows the user's systemd setup. Offline periods are recovered through history after restart.

## Install the Codex plugin

Install the Python package first so `feishu-agent` is available on the host's PATH. Add this repository as a local marketplace:

```sh
codex plugin marketplace add /absolute/path/to/feishu-agent-bridge
```

Refresh/restart the desktop app and install **Feishu Agent Bridge** from the local marketplace. The plugin's root `plugin.json`, `mcp.json` and `skills/` follow the portable Agent Plugins layout. It does not depend on lifecycle hooks. You can also register `feishu-agent mcp` as a local stdio MCP server directly.

With a custom config location, set the MCP server's args to `["--config", "/absolute/path/to/config.json", "mcp"]`. The daemon must be started separately; plugin installation is not a service installer. Public plugin-directory submission is a separate workflow with remote HTTPS MCP/hook restrictions; see [OpenAI packaging docs](https://developers.openai.com/plugins/build/plugins).

## Background and primary responsibilities

The worker uses an isolated Codex runtime configuration, a separate thread and read-only sandbox, with shell, plugins, apps and external mutation tools disabled. It gets explicit project documentation/brief and group data, not the primary conversation transcript. Only the deterministic sender can send group replies.

- Ordinary messages are read into group summaries, without unsolicited replies.
- Addressed questions are answered in the original thread.
- Slow addressed jobs receive one receipt based on actual processing state.
- Code changes/owner decisions become durable inbox tasks. They do not grant repository ownership, file leases or deployment permission.
- Closed tests and acknowledgements do not keep bots replying to one another.

```sh
feishu-agent summaries
feishu-agent inbox
feishu-agent claim TASK_ID --owner PRIMARY_SESSION_ID
# After completing and verifying the authorized work:
feishu-agent complete TASK_ID --owner PRIMARY_SESSION_ID --result 'Verified result'
```

The service sends one completion reply. The same operations are MCP tools (`pending_tasks`, `claim_task`, `complete_task`). Primary-session automatic handoff is opt-in:

```sh
feishu-agent bind PRIMARY_SESSION_ID --auto-handoff
feishu-agent stop
feishu-agent start
```

This uses native `codex queue`; it never interrupts/steers the active primary turn. A queued handoff is not completed work. Without auto-handoff, inspect tasks through MCP/CLI.

## Reliability and configuration

See [architecture](docs/architecture.md) and [operations](docs/operations.md). The service records collection/read/outbox/task states separately. Message edits remain pending until their new version is read. Long content is read in complete bounded chunks. Attachments retain metadata; their contents are not automatically downloaded or claimed inspected. Unsupported message types remain visible as unsupported.

External writes can have ambiguous acceptance. Interrupted/timeout writes become `uncertain` and are **not automatically resent**. Known `failed` replies may be explicitly retried with `feishu-agent retry REPLY_ID` after resolving their cause. This is idempotent local processing, not a promise of exactly-once delivery over a network.

Defaults: history 30s, ordinary reading 60s, receipt 10s, model timeout 120s, model jobs 60/hour, message batch 16000 characters, sends 6/minute/group, replies 4/topic. These are configurable. A slow/failed model does not stop the collector. Status exposes lag/errors and pending work; budgets retain messages for later processing.

## Develop and validate

```sh
.venv/bin/python -m pip install '.[mcp,dev]'
.venv/bin/python scripts/check.py
.venv/bin/python -m build
```

Tests use real SQLite, CLI subprocesses and stdio MCP; model/network dependencies are controlled fakes. Live inference/events/sends are separate authorized acceptance checks, recorded independently. CI runs without credentials. See [validation](docs/validation.md) for the evidence and platform limits of this version.

MIT licensed. Runtime databases, local config, auth references, group history and logs are excluded from distribution.
