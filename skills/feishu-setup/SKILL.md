---
name: feishu-setup
description: Install, configure, bind or troubleshoot Feishu Agent Bridge's local background service and plugin for explicitly selected Feishu groups.
---

Read the repository README for installation and permission requirements. The Python package supplies `feishu-agent`; the plugin exposes its local stdio MCP interface. Installing the plugin alone does not start a service.

Reuse the user's existing lark-cli profile. Configure explicit groups, this application's bot open ID/app ID and a project directory with `setup`; credentials stay in lark-cli. Never copy tokens into the plugin or source repository. Choose the initial history boundary explicitly when backfilling.

Run `doctor` before `start`. History-read capability alone does not prove real bot/user @ event or bot-send capability. Verify the required channel with a live authorized test. Missing include-bot permissions must be explained as a capability failure, not silently ignored.

Use `bind` only for the explicitly selected primary Codex session. Automatic primary handoff is opt-in. Routine messages are handled by the independent background thread. A binding update requires a service restart.

`stop` controls only this plugin's service. Do not stop the shared lark event bus or other consumers. Uncertain external writes require reconciliation; the `retry` command accepts only known failures.
