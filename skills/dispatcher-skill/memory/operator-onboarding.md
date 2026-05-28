# Operator Onboarding Memory

## Role Boundary

The operator is the user-managed maintenance role for repository edits, runtime inspection, verification, and app evolution. It is outside the dispatcher-managed task plane.

The dispatcher task plane runs queued user tasks through managers. Managers may queue dispatcher-owned workers with `dispatcher_app.worker_client`, but the Python dispatcher still dispatches queued user tasks only to managers.

## Ordered Operator Procedure

1. Start every operator task with the runtime audit file listing:

```bash
find data/codex_runs data/agent_conversations -type f -printf '%T@ %TY-%Tm-%Td %TH:%TM %p\n' 2>/dev/null | sort -n | tail -n 20
```

If these paths do not exist in a new install, treat that as "no runtime audit state yet" and continue with the first-install preflight below.

2. If relevant audit records changed, inspect only the latest relevant manager result and conversation tail needed for the task. Treat `data/codex_runs/` and `data/agent_conversations/` as runtime audit logs. Never copy raw audit content, full transcripts, prompts, secrets, stdout/stderr dumps, local tunnel URLs, or full JSONL records into memory.

3. Before substantive inspection, decisions, edits, tests, or runtime state changes, acquire the global runtime mutex from the dispatcher skill root:

```bash
python3 -m dispatcher_app.runtime_lock --db data/dispatcher.db acquire --owner-plane operator --owner-id operator --lease-seconds 900 --token-file data/operator_runtime_lock.token
```

The only normal pre-lock operations are the runtime audit and checking whether the mutex is already owned. First-install exceptions are reading required skill memory and `requirements.md`, running non-runtime `init-status` or package smoke checks, and diagnosing whether the skill-local `data/` directory can be written. If `data/` and `dispatcher_app/agents.json` do not exist yet, still acquire the lock before `init` writes state or starts runtime services. If lock acquisition fails because the skill root or `data/` is read-only under the current sandbox, stop and request the approved write path instead of creating an ad-hoc DB elsewhere.

If the acquire command reports task-plane ownership, wait or report that the dispatcher task plane is active.

4. After audit and lock acquisition, process any nonempty unread handoff addressed to the current agent before other substantive work. Read the handoff, catalog useful durable facts into the relevant `memory/*.md` topic file, update `memory/memory.md` if the catalog changes, then acknowledge/purge the handoff. Do not copy raw logs, full transcripts, prompts, secrets, or non-actionable detail.

5. For long operator work, heartbeat only if this operator acquired the lock:

```bash
python3 -m dispatcher_app.runtime_lock heartbeat --lease-seconds 900 --token-file data/operator_runtime_lock.token
```

Release only the lock this operator acquired:

```bash
python3 -m dispatcher_app.runtime_lock release --token-file data/operator_runtime_lock.token
```

## Task-Plane Boundary

- The Python dispatcher dispatches queued user tasks only to managers.
- Managers may invoke dispatcher-owned workers only through `dispatcher_app.worker_client`; do not use Codex subagents for worker execution in this flow.
- Worker requests are mutexed to the active manager task: the source task must be `in_progress`, the manager runtime row must be busy for that task, and the global runtime lock must still belong to that manager/task pair.
- Dispatcher-managed manager tasks acquire and release the global runtime mutex automatically before claiming pending work.

## Restart Safety

- Managers under `codex exec` must not call tmux, tunnel scripts, Cloudflare commands, or `dispatcher_app.reboot request` directly.
- Manager tasks request runtime restarts by adding a final-result marker only after implementation, docs or memory updates, and verification are complete:

```text
REBOOT_AFTER_TASK restart-dispatcher <short reason>
```

- The dispatcher strips the marker from the stored user-facing result, marks the task done, then appends the validated host-side reboot request.
- Operator or host-side workflows may use tunnel runtime helpers directly when explicitly performing runtime operations.
