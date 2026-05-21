# Agent Registry Memory

Agent identities are stored in `dispatcher_app/agents.json`. Keep JSON limited to agent identity fields; keep policy and descriptions in this Markdown file.

For operator cognition flow, start from the root `dispatcher-skill` entrypoint and `memory/memory.md`, then follow `memory/operator-onboarding.md`: runtime audit, runtime mutex acquisition, addressed handoff processing, and only then substantive work.

## Registry Rules

- `key` means the Codex handle for that agent, such as a session ID or harness-provided subagent ID. It is nullable until the agent exists.
- `role_key` is the stable dispatcher identifier for the agent role.
- Use lowercase dotted `role_key` values for dispatcher-managed agents, such as `manager.default` or `worker.default`.
- Use a plain lowercase `role_key` for global roles outside the task plane, such as `operator`.
- Do not store model provider tokens, OpenAI keys, Cloudflare tokens, passwords, or other secrets here.
- If an agent role is renamed, keep the old `role_key` until existing tasks and logs no longer reference it.
- `dispatcher_app/agents.json` should contain only `version`, `key_type`, and agent rows with `role_key`, `name`, `key`, and `key_status`.
- Access rules, purpose text, lifecycle notes, and UI behavior belong in this file, not in JSON.

## Access Policy

- Dispatcher can dispatch queued tasks only to manager agent IDs through `codex_runner.py`.
- Manager can invoke worker roles only by queueing dispatcher-owned worker requests with `dispatcher_app.worker_client`.
- Dispatcher may build a filtered `worker.*` catalog payload and prompt instructions for the manager, and may execute worker requests that were queued by the active manager for the active task. It must not dispatch pending user tasks directly to workers.
- User can view conversations for operator, manager, and worker agents.
- User can chat directly only with `operator`.
- User can talk to manager agents only through the dispatcher.
- User cannot directly chat with worker agents.

## Manager Work Records

Manager work history is stored as ignored runtime state, not copied into durable memory as raw logs.

- `data/codex_runs/task-*.json`: final structured manager decision/result files written by `codex_runner.py`.
- `data/agent_conversations/manager.default.jsonl`: append-only manager prompt, Codex stream event, and final response transcript.

Every operator task should first check these locations for recent changes, then acquire the global runtime mutex before inspecting, deciding, editing, testing, or changing runtime state. Treat the files as the manager audit log and read only the relevant latest records needed for the task. When these runtime logs need cleanup, use the context compact helper's guarded runtime-log purge so only compressed metadata is written to handoff memory before regular log files are deleted.

## Context Compression Handoffs

Operator and manager agents can use the `scripts/context_compact.py` integrated helper to exchange compressed Markdown handoffs under `memory/context-compression/`. Each agent role has one handoff file. The sender appends when the receiver has not acknowledged prior context. After the required safety preconditions, including operator runtime audit and mutex acquisition, a receiver with nonempty unread handoff entries addressed to it must process those entries before other substantive work. When the receiver reads any handoff, it must compress the useful durable facts into the relevant `memory/*.md` topic file, update `memory/memory.md` if the catalog changes, and only then run the helper's acknowledge command to purge that sender's handoff file back to its header. Raw logs, full transcripts, prompts, secrets, and non-actionable detail stay out of durable memory.

The same helper has guarded purge commands for approved cleanup: `purge-tasks` snapshots current UI cards into an agent handoff file and then clears `tasks` and `events` from SQLite, while `purge-runtime-logs` summarizes `data/agent_conversations/` and `data/codex_runs/` metadata into a handoff and then deletes only regular files under those log directories. Both confirmed purges refuse to run while a manager or task is active, and runtime-log summaries must not copy raw logs, stdout/stderr, prompts, secrets, or full JSON records into memory.

## Worker Policy

- Worker `role_key` values use `worker.<slug>`.
- Managers should call workers through `python3 -m dispatcher_app.worker_client call`, not Codex subagent tools.
- The worker client can create a request only while the manager owns the current `in_progress` task and the task-plane runtime mutex.
- The dispatcher loop runs pending worker requests through `dispatcher_app/worker_runner.py` and stores dispatcher-owned worker Codex session handles in `dispatcher_app/agents.json`.
- `key_status=dispatcher_ready` means the worker key is a dispatcher-owned Codex session that `worker_runner.py` can resume. Older `ready` worker keys may be legacy Codex harness subagent handles and are not resumed by `worker_runner.py`.
- Worker requests are serialized per manager task: only one `pending` or `running` worker request is allowed at a time.
- `dispatcher_app/agents.json` is the worker catalog given to managers when they need to select or reuse workers.
- Managers choose dispatcher-owned worker display names when initializing or renaming a worker request by passing `--worker-name` to `dispatcher_app.worker_client`; the dispatcher persists that display name in `agents.json`.
- Worker reuse must avoid conflicting active tasks.

## Current Agents

| Role Key | Name | Codex Handle | Plane | Managed By | User Contact | Status | Purpose |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `operator` | Operator | set | operations | user | direct | ready | Maintains, debugs, restarts, inspects, and evolves the dispatcher app itself. |
| `manager.default` | Manager | set | task | dispatcher | via dispatcher | ready | Interprets queued tasks and decomposes broad work into bounded worker actions. |
| `worker.default` | Worker | set | task | manager | read only | ready | Executes one bounded task action and returns a result, error, or approval request. The manager may assign a human display name on worker initialization. |

## Dispatcher Use

The dispatcher should record manager `role_key` in task events, leases, and logs. It may pass a filtered worker catalog to the manager, but pending task dispatch remains manager-only. Worker execution happens only after the active manager queues a worker request for the active task. Human-facing UI can display `name`.

Manager calls use `codex --ask-for-approval never --disable plugins exec --sandbox workspace-write --cd <workspace-root>` when no manager key exists and the same `exec` options before `resume <SESSION_ID>` after a key is recorded. The task-plane approval policy is non-interactive, but the sandbox must be `workspace-write` so managers can edit anywhere in the active repository working tree and queue dispatcher-owned worker requests through SQLite. When this skill is nested at `skills/dispatcher-skill/`, the Codex workspace root is the surrounding repository root; helper commands still run from the dispatcher skill root, so manager prompts include `cd skills/dispatcher-skill && python3 -m dispatcher_app...` command forms. The manager task path disables Codex plugins because dispatcher task handling does not need ChatGPT plugin discovery; operator remains responsible for skill/plugin/tool updates. The first `thread.started.thread_id` observed from Codex JSONL output becomes the manager `key`.

Each dispatcher task thread should keep one fixed manager for that task. With only `manager.default` active, keep dispatcher concurrency at 1 so simultaneous tasks do not share and scramble one manager session context.

Manager working state is not `key_status`. `key_status` describes whether the Codex handle exists or looks usable. Runtime working state lives in SQLite `manager_runtime` with statuses such as `idle` and `busy`, plus `current_task_id` and `heartbeat_at`. Dispatcher assignment requires an idle manager and atomically marks that manager busy with the claimed task.

Dispatcher-owned worker sessions may be created or resumed by `worker_runner.py`. Reuse requires a compatible worker, `key_status=dispatcher_ready`, and no conflicting active worker request.
