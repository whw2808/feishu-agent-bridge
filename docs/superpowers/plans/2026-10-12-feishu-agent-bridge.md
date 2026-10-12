# Feishu Agent Bridge Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. Steps use checkbox syntax for tracking.

**Goal:** Ship an installable local plugin with asynchronous group monitoring and a separate collaboration agent.

**Architecture:** Feishu collector -> SQLite inbox -> independent Codex worker -> validated reply/handoff outbox. CLI and MCP read durable state promptly; the primary thread is used only for optional necessary handoff.

**Tech Stack:** Python 3.11+, asyncio, sqlite3, lark-cli adapter, openai-codex optional extra, MCP optional extra, setuptools, unittest, ruff.

**Spec:** ../specs/2026-10-12-feishu-agent-bridge.md

## Global Constraints

- No changes to knok-agent; develop on codex/initial-plugin in an independent repository.
- All distributed paths/identities configurable; Python >=3.11; secrets remain in existing CLI credential storage.
- Only configured groups; independent Codex background thread; no primary steer/interrupt or project writes.
- History 30s, normal reading 60s, long-job acknowledgement 10s; all configurable and positive.
- Every external write has durable intent and stable idempotency key; ambiguous acceptance is not automatically repeated.
- Only bootstrap this plugin's service; no shared event-bus stop. Publication is outside this local implementation.

## Review Focus

- Identity spoofing/cross-profile open IDs must not route replies to an unrelated group or primary session.
- A model timeout or malicious message must not block collecting ordinary messages or obtain send/project-write capabilities.
- Model claims of task completion or unauthorized recipients must be rejected by central output validation.
- Mid-page failures and restarted sending processes must preserve pending/uncertain state without silent loss.
- Very large messages, edits and bot acknowledgements must not cause hidden truncation, starvation or reply loops.

## Task 1: Private configuration and durable inbox

**Files:** src/feishu_agent_bridge/config.py, models.py, store.py; tests/test_state.py.
**Interfaces:** Config.load(path), Message.from_event/from_history, Store.ingest(message), pending_messages(group,limit), mark_read(ids), enqueue_reply(...), tasks()/claim_task()/complete_task().
- [x] Write failing behavior tests for configured scope, ordinary-message collection, real mention, own identity, duplicates/edits, interrupted reads/sends and cross-identity database reuse.
- [x] Run unittest tests.test_state; verify assertions fail on stub behavior, not import failure.
- [x] Implement private config validation and transactional SQLite state.
- [x] Run whole suite; record structured result and commit.

## Task 2: Feishu collection and external delivery

**Files:** adapters/lark.py, collector.py, delivery.py; tests/test_transport.py.
**Interfaces:** async Lark.call(args)/history(group,scan)/events(queue)/reply(...); Collector.scan_group(); Sender.dispatch(); independent tasks consume Store from Task 1.
- [x] Write RED tests for multi-page normal messages, timeout/expired token, safe argv, correct true mention resolution, own-process termination and ambiguous send recovery.
- [x] Implement incremental cursors, async process wrapper and deterministic sender. Use native lark binary and bounded timeouts.
- [x] Run suite, ledger results and commit.

## Task 3: Background worker and handoff

**Files:** adapters/codex.py, worker.py, policy.py, runtime.py; tests/test_worker.py.
**Interfaces:** async Agent.read(messages,summary,brief)->Decision; Worker.tick(); Runtime.run(); optional PrimaryQueue.deliver(task); Store jobs/replies/tasks remain authoritative.
- [x] Write RED tests proving blocked inference leaves collector active, normal messages never send by default, addressable requests reply or become inbox tasks, acknowledgements occur once, hostile decisions are rejected and bot closure stops loops.
- [x] Implement independent SDK thread, structured validation, budgets, safe acknowledgement, bounded message batching and primary-task lifecycle.
- [x] Run suite; record evidence and commit.

## Task 4: Installable CLI, MCP and plugin

**Files:** cli.py, mcp_server.py, service.py, pyproject.toml, plugin.json, mcp.json, skills/*/SKILL.md, .agents/plugins/marketplace.json, README.md, docs/, LICENSE, .github/workflows/ci.yml; tests/test_cli.py.
**Interfaces:** feishu-agent setup/run/start/stop/status/doctor/inbox/complete/bind/mcp; MCP status, summaries, pending_tasks, claim_task, complete_task, bind_session.
- [x] Write failing subprocess/MCP tests for installation, absent optional dependencies, stdin protocol, configuration privacy, portable manifests, task completion idempotency and service scope.
- [x] Implement CLI/service/MCP, author narrowly scoped skills and bilingual release documentation.
- [x] Build wheel/sdist and plugin archive; install in clean venv and smoke-test; run suite/lint; commit.

## Task 5: Review and real acceptance

**Files:** docs/validation.md and ignored .artifacts/ reports only; runtime files outside repository.
- [x] Validate independently with a fresh code reviewer; fix Important findings with RED->GREEN tests.
- [x] Verify actual Feishu history/events and a separate Codex worker without writing project files or queuing routine messages to primary.
- [x] Cut over only this user's old bridge after acceptance; preserve its baseline/processed receipts and rollback command.
- [x] Verify service shutdown/restart and plugin/MCP installed in a new session; record exact local platform evidence and limits.
- [x] Commit clean delivery and provide source/install/validation paths. No public publication claim.
