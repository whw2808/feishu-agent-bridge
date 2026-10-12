# Architecture

`adapters/lark.py` owns async CLI invocations and event consumers. `collector.py` ingests real-time events and scans incremental history. `store.py` is the canonical scoped durable state. `worker.py` owns reading, chunk offsets and model outcomes; `adapters/codex.py` launches an isolated runtime/thread. `delivery.py` validates and sends durable replies. `runtime.py` supervises independent tasks. `cli.py` and `mcp_server.py` perform prompt database operations.

Collection is separate from inference. Only the service singleton recovers interrupted jobs; CLI/MCP readers do not reset live jobs. History cursors advance only after complete pagination. Scope/identity checks happen at ingestion, inference action validation and original-message readback before send. Versions prevent an edit arriving during inference from being marked read by an older result.

Model actions cannot choose a different group, source message or arbitrary recipient. Unknown actions are suppressed and visible in health. Ordinary messages are observed without replies. Closed conversations and per-topic/rate/repeated-content limits stop common bot loops. The primary session is optional and explicitly bound; only handoffs enter its native queue.

SQLite states: message `pending -> reading -> read`, with partial chunks returning to pending; self/unsupported messages are separate. Replies `pending -> sending -> sent/failed/uncertain/blocked`. Inbox `pending -> claimed -> done`; native handoff delivery is independent of work completion. External uncertain acceptance needs operator reconciliation.

Local runtime configuration has no plugin/MCP/shell tools and references existing Codex authentication through a private runtime directory. Small explicit README/AGENTS snippets and the configured project brief supply project context; no primary transcript is copied. The background agent cannot perform project changes or direct group sends.
