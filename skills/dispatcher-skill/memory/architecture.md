# Architecture Memory

## Goal

This repository is the root `dispatcher-skill`: a local dispatcher app packaged as one Codex skill. `dispatcher_app/` is the app source/runtime code, and helper scripts under `scripts/` are integrated capabilities, not replacement roots. A user should be able to add tasks through a web UI and leave the session unattended while the system processes the queue.

## Target Shape

The intended architecture has two parallel planes:

```text
user-managed operations plane
  user
    -> operator
        -> repo, server process, tunnel, database, logs, UI debugging

dispatcher-managed task plane
  browser UI
    -> Python backend
        -> SQLite task store
        -> dispatcher loop
            -> manager agent
                -> worker agent(s)
```

The root skill and memory catalog orient Codex to the repository. Detailed operator onboarding lives in `memory/operator-onboarding.md`; helper script boundaries live in `memory/helper-scripts.md`.

The dispatcher should own state and control flow. Agents should propose plans or perform bounded work, but the deterministic backend should manage task state, leases, retries, approvals, and logs. `dispatcher_app/codex_runner.py` is the boundary between deterministic Python code and Codex CLI execution.

Runtime operations are mediated through `dispatcher_app/reboot.py`. Operator and host-side workflows may use `dispatcher_app.reboot request` directly, but managers running under `codex exec` do not call that CLI or append `data/reboot_requests.jsonl` themselves. When a manager needs a runtime restart, it completes implementation, docs or memory updates, and verification first, then returns a final-result `REBOOT_AFTER_TASK <command> <reason>` marker. The dispatcher strips that marker from the stored user-facing result, marks the task done, and appends the validated host-side reboot request. The host-side `reboot` tmux window watches `data/reboot_requests.jsonl` and restarts `server` and/or `dispatcher` while leaving the Cloudflare `tunnel` window alive.

Manager-triggered restart requests are also a queue barrier. After the dispatcher appends a validated manager-requested reboot record, it does not claim another pending task until the reboot watcher has processed that request and advanced `data/reboot_state.json`. This prevents a delayed server or dispatcher restart from landing in the middle of the next task.

The `operator` is outside the dispatcher task plane. It exists so the user can debug and evolve the dispatcher app itself while the dispatcher, manager, and workers focus on queued task execution.

Task concurrency is tied to manager sessions. Each dispatcher task thread should use one fixed manager for that task. While the app has one long-running manager session, `max_concurrent_tasks` should default to 1 to avoid mixing manager context across simultaneous tasks. Manager runtime state is stored in SQLite, separate from task status and separate from `agents.json` identity data.

## Agent Access Boundaries

The ignored per-repository `dispatcher_app/agents.json` stores known Codex agent handles, but access is constrained by role. `dispatcher_app/agent_registry.py` generates the default registry during initialization or first direct runtime use, and init records the operator handle when `--operator-key`, `DISPATCHER_OPERATOR_KEY`, or `CODEX_THREAD_ID` is available, so packaged skills do not ship another repo's handles. The dispatcher still claims queued tasks only for manager IDs by calling `codex exec` or `codex exec resume` through `codex_runner.py`. It passes a filtered `worker.*` catalog to the manager, but queued task dispatch remains manager-only.

Workers are now invoked through dispatcher-owned worker requests rather than Codex harness subagents. An active manager writes a bounded worker request with `python3 -m dispatcher_app.worker_client call`; the worker client sends the request to the dispatcher server API by default, the backend stores it in SQLite, and the dispatcher loop services that request by running `worker_runner.py` against the requested `worker.*` role. Direct SQLite worker-client access is reserved for explicit `--transport db` debugging or recovery fallback. This keeps worker execution visible to the dispatcher while still preventing the user or pending-task dispatcher from selecting workers directly. The user can view every agent conversation, but can directly chat only with the operator; manager interaction goes through the dispatcher, and worker interaction is read-only.

Worker delegation should be rolled out in phases. Until a dispatcher-owned worker can reliably receive instructions, change files, run verification, return results, and keep its conversation associated with the correct `worker.*` log, the manager must not be forced into delegate-only mode. During this transition the manager may still implement directly when necessary to keep the app moving, while worker prompts, session persistence, and audit paths are built and tested. Delegate-only enforcement should begin only after a real dispatcher-owned worker task path is smoke-tested end to end.

## Roles

- Operator: user-managed maintenance agent for code edits, server restarts, tunnel checks, database inspection, UI debugging, and verification. It is not a queue worker.
- Dispatcher: watches pending tasks, claims work, calls managers, manages status transitions, leases, retries, and event logging.
- Manager: decomposes broad tasks into bounded worker actions, owns worker selection, and invokes workers by queueing dispatcher-owned worker requests when needed.
- Worker: handles one scoped task and returns a result, patch, error, or approval request.

## State Model

SQLite is the source of truth for the dispatcher app. JSONL is used only as append-only agent conversation/audit logs under `data/agent_conversations/`; SQLite remains authoritative for querying status, locking work, and coordinating UI plus dispatcher writes.

Core task states are `inbox`, `pending`, `in_progress`, `needs_approval`, `done`, `failed`, and `canceled`.

Manager runtime states live in the `manager_runtime` table. The dispatcher may assign a task only when the selected manager is `idle`; claiming a task sets that manager to `busy` with `current_task_id`, and completion, approval pause, failure, cancellation, or lease recovery releases the manager back to `idle`. Stale `busy` rows that point to non-`in_progress` tasks are reclaimed before new assignment.

Operator/task-plane mutual exclusion uses a SQLite-backed global execution mutex, not a Python `threading.Lock`, because the server, dispatcher, reboot watcher, operator, and Codex executions are separate processes. The mutex covers every operator task after its initial audit/lock-status check, plus dispatcher-managed manager/worker execution. UI reads and task creation remain outside the mutex so the user can still inspect the system and enqueue work while another plane is active.

The mutex is stored in the `runtime_locks` table with stable resource name `global_execution`, owner plane (`operator` or `task`), owner id, optional task id, fencing token, heartbeat, lease expiry, and updated timestamp. Acquisition uses `BEGIN IMMEDIATE`, and heartbeat/release check the fencing token so a stale owner cannot renew or release a newer lock.

Dispatcher processing acquires the global execution mutex before claiming a pending task for a manager. If the mutex is held by operator work, the dispatcher leaves pending tasks unclaimed and tries again on the next poll. Once a task-plane lock is acquired, the dispatcher keeps it through manager execution and any manager-requested worker execution, heartbeating it alongside the task lease and releasing it when the task reaches `done`, `failed`, `needs_approval`, or `canceled`.

Before acquiring the task-plane mutex for a new pending task, the dispatcher also checks for unprocessed reboot requests. Any record in `data/reboot_requests.jsonl` beyond the watcher offset in `data/reboot_state.json` pauses new task claims until the reboot watcher handles it. A task-owned runtime lock whose referenced task is missing or no longer `in_progress` is treated as stale and reclaimed before pending-task claims, so a manager released to idle after a terminal task cannot leave the queue blocked behind that stale lock.

Manager-worker mutual exclusion is enforced through SQLite. A worker request can be created only for an `in_progress` task whose manager runtime row is `busy` for that same task and whose global runtime lock is still owned by that manager/task pair. Only one `pending` or `running` worker request is allowed for a manager task at a time. The dispatcher services pending worker requests only while the task, manager runtime, and task-plane lock still match.

Every operator task should acquire the same mutex before starting substantive work through `python3 -m dispatcher_app.runtime_lock acquire --owner-plane operator --owner-id operator --token-file data/operator_runtime_lock.token`. If a manager task owns the mutex, operator work should wait or report that the task plane is active. The UI header surfaces the current mutex owner through `/api/runtime-lock`, for example `Lock: operator operator` or `Lock: task manager.default task #id`, and Agent status marks the Operator card busy while an active operator-plane lock is held.

## Safety Principles

- Long-running sessions need idempotent task handling.
- In-progress work should have leases or heartbeats so dead workers can be recovered.
- Risky actions such as deletion, deployment, payment, production changes, or external API usage should pause for user approval.
- Worker scope should remain narrow and verifiable.
