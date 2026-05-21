# Decision Memory

## Accepted

- Use a web UI for the dispatcher app because the dispatcher is a long-running server process and the user may check it from different devices.
- Use Python for the backend and dispatcher loop.
- Use SQLite as the source of truth for tasks and events.
- Keep the first UI simple: task form, queue lanes, task events, approval/retry/cancel actions.
- Use a single-user password gate for initial app access control.
- Use Cloudflare Quick Tunnel for temporary mobile/public access.
- Keep skill instructions in root `SKILL.md`, detailed portable project memory in `memory/`, and deterministic operational helpers in `scripts/`. Operator-container instructions and migration notes stay outside the shippable skill payload.
- Name the user-managed debugging and maintenance agent `operator`; keep it outside the dispatcher-managed manager/worker task plane.
- Store all known Codex agent handles in the ignored per-repository `dispatcher_app/agents.json`, while limiting queued task dispatch to managers and worker invocation to active manager-authored worker requests.
- Keep `agents.json` as a generated local agent handle registry, but have managers invoke workers through `dispatcher_app.worker_client` so dispatcher-owned worker runs can be tracked in SQLite and Activity.
- Run the web server, dispatcher loop, reboot watcher, and tunnel as separate processes; tmux should keep `server`, `dispatcher`, `reboot`, and `tunnel` windows alive.
- Managers must not control tmux directly or run `dispatcher_app.reboot request` from inside `codex exec`. Runtime restarts requested by managers use final-result `REBOOT_AFTER_TASK <command> <reason>` markers; after marking the task done, the dispatcher strips the marker from the stored result and appends the validated host-side reboot request for the `reboot` tmux window to apply.
- Keep queued task dispatch manager-only. The dispatcher may execute worker requests only after the active manager queues one for the active task; it must not assign pending user tasks directly to workers.
- Operator/task-plane mutual exclusion uses a SQLite-backed global execution mutex between operator work and dispatcher-managed manager/worker execution, with lease heartbeat, fencing-token release checks, dispatcher claim gating, operator CLI helpers, and UI owner display.

## Deferred

- Finish smoke testing dispatcher-owned worker execution end to end, then tighten manager direct-implementation policy.
- Move the password from code to an environment variable or local config.
- Add automated tests for store transitions, auth, and dispatcher behavior.
- Add a named Cloudflare Tunnel with a fixed domain if stable URLs become necessary.
- Add SSE or WebSocket updates only if polling becomes insufficient.
- Expand global execution mutex tests into automated `tests/` coverage, including stale lock recovery and operator/task contention.

## Rejected For Initial App

- Tailscale inside the current container: package installation worked, but `systemd` was unavailable and `tailscaled` did not run as a service.
- JSONL as primary state store: useful for audit logs, but SQLite is simpler for locking, querying, and UI state.
