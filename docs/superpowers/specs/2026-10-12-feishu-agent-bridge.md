# Feishu Agent Bridge v0.1

Approved brief: the user requested an open-source plugin that monitors real mentions, periodically reads all unprocessed group messages, handles collaboration independently of the primary development conversation, and gives timely, truthful feedback. On 2026-10-12 the user approved the proposed daemon + plugin design and instructed development to begin.

## Delivery

An independent Python 3.11+ package, portable Agent Plugins manifest, local stdio MCP server, two focused skills, macOS LaunchAgent / Linux systemd-user integration, MIT license, bilingual quick start, and CI. Codex is the first host. No changes to knok-agent source or its task claims. GitHub/PyPI public publication is separate from preparing the release.

## Collection and state

Only explicitly configured groups and application/profile identities are accessible. Subscribe to im.message.receive_v1 and also query group history every 30 seconds. Store all new group messages, including ordinary messages and bot messages when permitted. Validate and normalize identity, timestamps, true mentions and message types; never interpret a display name or @all as a bot mention. Persist message version updates; self messages are recorded but do not generate agent jobs. Unsupported message bodies remain visible as unsupported, never falsely reported read. Store attachment metadata; no automatic bulk download.

Initial activation starts at its configured timestamp, optionally selected by --since. Each group has an independent complete history cursor plus persistent in-progress page descriptor. Only a complete scan advances the complete cursor; invalid page tokens restart from the last complete cursor with overlap. Retries are bounded by a scheduled backoff, not immediate loops. A message collected in real time and later found in history is one record.

Separate message collection, reading, response outbox and primary-session handoff state. SQLite WAL transactions enforce job ownership and idempotent state transitions. Recover interrupted read jobs; uncertain external writes are retained for operator reconciliation rather than blindly resent. No exactly-once network claim.

## Background collaboration

The collector, background worker and sender run independent async tasks; model inference cannot block event receipt/history polling. A Codex SDK adapter owns its own thread; it never resumes/steers the primary thread. Configure read-only sandbox and isolated runtime configuration without external mutation tools. Worker input contains bounded complete message batches, prior group summary and project brief; large bodies are chunked or left pending rather than silently truncated. Read normal messages every 60 seconds and prioritize real mentions/replies to the bot. The worker returns validated structured decisions: observe, reply, handoff. It has no direct Feishu send API; a deterministic sender validates each proposed action.

Normal messages may update summaries; only explicit mentions or replies to the bot can cause unsolicited replies/handoffs by default. Pure confirmations/thanks/closed tests do not cause acknowledgement loops. Model output never grants task/file authority. Codex model is configurable, otherwise the configured default is used.

## Feedback and actions

If an addressed job remains unfinished after 10 seconds, persist one acknowledgement describing actual queued/processing state. Fast replies need no separate acknowledgement. Final replies refer to original message and optionally true-mention the original sender, resolved within this application's identity scope. Group/app identity, source message and send budget are revalidated centrally. Limit response count per thread, response rate per group, repeated content, input/output size and model jobs per hour.

Code changes or user decisions become durable primary-session inbox tasks. Default handoff is visible through MCP/CLI; optional native codex queue adapter sends a concise task at the next turn boundary without interrupt/steer. Delivery to primary is not task completion. Primary can claim a task and report result through MCP; duplicate completions do not send duplicate replies. Host queue acceptance timeouts are uncertain.

## Operations and packaging

No hardcoded personal paths, group IDs, app IDs or session IDs in distributed files. Runtime configuration and history are private local files. Discover binaries from PATH; resolve the native lark-cli binary behind its Node launcher for graceful termination. Use existing lark-cli authentication; inspect required capability failures without requesting unrelated scopes. Never stop the shared event bus or unrelated consumers. SIGTERM shuts down only owned processes. Configuration updates with a different identity cannot reuse the same state database silently.

MCP tools expose status, group summaries, pending tasks, claim, completion and session binding. They finish promptly from the database and do not wait for model inference. Local service installation is explicit. Plugin installation alone does not assume a lifecycle hook runs. Root plugin.json and mcp.json use portable schemas; a repository marketplace supports local distribution. Public directory submission restrictions are documented, not claimed complete.

## Acceptance

1. Incoming bot/user real mention is ingested and processed while the primary turn continues unchanged; no primary message for an ordinary reply.
2. Ordinary unmentioned messages are collected and read, but do not trigger default group chatter.
3. Recovery after a mid-page error, restarts and duplicates has no lost messages/duplicate sends; incomplete work stays visible.
4. Two bots exchanging confirmations terminate rather than ping-pong.
5. Feedback reflects actual durable state; permissions/model/network failures are exposed and never marked successful.
6. Fresh wheel installation can start the CLI/MCP, install a local plugin, and run tests without personal configuration.
7. Real Feishu history/event and independent Codex inference are checked on this machine; Linux/other-platform status is reported from actual evidence only.
