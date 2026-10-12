# Operations

Use `feishu-agent --config PATH COMMAND` when the configuration is not at its XDG default location. `setup` is non-overwriting. Data files are private (config/database 0600, state directory 0700). Credentials remain in the authenticated CLI storage; the background Codex runtime references existing local `auth.json` without distributing it.

The user service label hashes the profile/app/state directory. macOS uses a LaunchAgent; Linux uses systemd --user. CLI binary paths are discovered at setup; lark-cli's non-forwarding Node wrapper is resolved to its native executable. All process execution uses argv arrays without a shell. Stop never invokes a shared event-bus stop or kills unrelated consumers.

Status health distinguishes event connectivity, history errors, inference errors and heartbeat. `doctor` checks optional dependencies and actual group-history access; it does not infer event/send scope success. Configure Feishu event and include-bot permissions explicitly. Re-authenticate the same profile when history authorization expires; pending messages/cursors remain stored.

An ambiguous network write is `uncertain`. Inspect the source thread before reconciling it; do not force retry merely because the acknowledgement is missing. A known `failed` reply can be retried with the same stable idempotency key after resolving its cause. Blocked actions need correction, not blind retry.

Changing primary binding requires service restart. Changing bot/application/profile requires a new data directory; the database rejects cross-identity reuse. Changing groups does not authorize access to removed groups. Completion results must describe verified work, never a plan to do work.

Service upgrade: stop this instance, preserve its data directory, install the new package, start and inspect status. Keep the previous package/config for rollback. Database schema v0.1 has no destructive automatic migration. Runtime DB/config/log files must never be added to a release. No automatic log rotation/retention scheduler is included in v0.1; use the host's retention/rotation tooling.

Plugin upgrade: regenerate `dist/plugin` with `python scripts/package_plugin.py`, then remove/add only `feishu-agent-bridge@feishu-agent-bridge-local` through native Codex plugin commands and refresh the host. The generated stage is exclusively packaging output. Do not change the marketplace source to the repository root: native installation copies the selected directory, including ignored private files if they are present.

Model work consumes the user's configured Codex access. Hourly job and per-group send budgets retain backlog instead of launching unbounded inference. Large messages are processed in complete chunks; normal reading lag may increase under load. This local service cannot infer while the computer is powered off/asleep.
