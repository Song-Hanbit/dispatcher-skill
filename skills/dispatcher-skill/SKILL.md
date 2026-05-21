---
name: dispatcher-skill
description: "Use when Codex is operating this repository as a local dispatcher app skill: initializing, running, or maintaining the queue runtime, working with operator/manager/dispatcher-owned worker workflows, using dispatcher_app commands, starting or inspecting the tunnel runtime, or compacting dispatcher context."
---

# Dispatcher Skill

Use this skill when this repository is installed or operated as one dispatcher app skill. Runtime and context helpers are part of this skill itself, not nested skills or replacement roots. In a project-local `npx skills` install, the skill root is normally `.agents/skills/dispatcher-skill/`; in this source repository, the payload root is `skills/dispatcher-skill/`. Run source/runtime commands from the detected skill root.

## Structure

- `dispatcher_app/`: Python app source, templates, static assets, schemas, agent registry, server, dispatcher loop, worker client/runner, reboot watcher, and runtime lock helper.
- `scripts/run_dispatcher_tunnel.py`: helper for local tmux runtime plus Cloudflare Quick Tunnel.
- `scripts/context_compact.py`: helper for compact handoffs and guarded task/runtime-log purges.
- `requirements.md`: operator-facing host and runtime prerequisites to check before initialization.
- `bin/cloudflared`: optional fallback Cloudflare tunnel binary for repo-local or exported-with-binary profiles; exported slim packages should omit it and use an external or local-state binary path.
- `memory/`: durable project memory. Read `memory/memory.md` first, then the specific topic needed.
- `data/`: ignored installation-local runtime state, including `dispatcher.env`, runtime config, SQLite DBs, logs, reboot state, and lock files. Do not package or copy it into memory.
- `dispatcher_app/agents.json`: ignored per-install agent handle registry generated during init or first direct runtime use.

## Initialization Trigger

When this skill is present in a new install and `init-status` reports uninitialized local state, enter the initialization sequence before runtime work:

1. Identify the skill root. From a repository where npx installed the skill, pass `--repo .agents/skills/dispatcher-skill`; from this source repository, pass `--repo skills/dispatcher-skill`; from the skill root itself, pass `--repo .`.
2. Review `requirements.md`, especially the host `cloudflared` choice. Prefer an external path through `--cloudflared`, a PATH install, or the operator-local installer target `~/.local/bin/cloudflared` over shipping the binary inside the skill.
3. Ask the user only for values that are not already available from command args, `DISPATCHER_PASSWORD`, `--password-file`, or existing `data/dispatcher.env`. At minimum, initialization needs a dispatcher password. A requested port is optional; the helper defaults to 8000 and automatically chooses a free fallback when the requested port is busy.
4. Run `python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared <path> --port <port>` from the skill root when the binary path is known, or the equivalent outer-root command with `--repo .agents/skills/dispatcher-skill` for project-local npx installs. Use `--no-start` only when local env/config should be written without starting tmux or Cloudflare.
5. During init, `dispatcher_app/agents.json` is created or validated and the operator Codex handle is recorded from `--operator-key`, `DISPATCHER_OPERATOR_KEY`, or `CODEX_THREAD_ID` when available. Keep the registry limited to `version`, `key_type`, and agent rows with `role_key`, `name`, `key`, and `key_status`; leave unknown handles as `null` with `key_status: "missing"` so the dispatcher can populate managers and workers later.
6. Determine the repository name by searching the directory hierarchy, not by guessing from the skill directory. If the skill root is `<repo>/.agents/skills/dispatcher-skill`, use the parent directory above `.agents/` as `<repo>`; if it is `<repo>/skills/dispatcher-skill`, use the parent directory above `skills/`; otherwise use the validated dispatcher root directory name.
7. Let the helper default the tmux session name unless the user explicitly overrides it. The default session name is `<repo>-tunnel`, where `<repo>` is the repository name found by that directory search, not the `dispatcher-skill` skill directory name. For example, this development repository uses `dispatcher-skill-tunnel`.
8. At the end of a successful init/start response, tell the user a no-`cd` command for checking the current Quick Tunnel URL by including both the helper script path and `--repo`, for example `python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py url --repo .agents/skills/dispatcher-skill --session <session>` from a project-local repository root or `python3 skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py url --repo skills/dispatcher-skill --session <session>` from this source repository root.

Do not print the password, generated public tunnel URL, or local env contents into chat, memory, or docs.

## Main Commands

Run the local web server only:

```bash
python3 -m dispatcher_app.server --host 127.0.0.1 --port 8000
```

Run the dispatcher loop:

```bash
python3 -m dispatcher_app.dispatcher
```

Manager-invoked dispatcher-owned worker request shape:

```bash
python3 -m dispatcher_app.worker_client call --task-id <task_id> --manager-role-key manager.default --worker-role-key worker.default --worker-name <name> --prompt-file <path>
```

Manager-suggested Inbox todo candidates:

```bash
python3 -m dispatcher_app.task_client suggest --task-id <task_id> --manager-role-key manager.default --items-file <items.json>
```

Operator runtime lock expectation before substantive operator work:

```bash
python3 -m dispatcher_app.runtime_lock acquire --owner-plane operator --owner-id operator --lease-seconds 900 --token-file data/operator_runtime_lock.token
```

Initialize or inspect local runtime state:

```bash
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared /path/to/cloudflared --port 8000
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo . --cloudflared-install-dir ~/.local/bin
```

`init-status` prints copy-ready commands for installing `cloudflared` into `~/.local/bin` and initializing with that path. `install-cloudflared` defaults to ignored `data/bin/cloudflared` when no install directory is supplied, but `--cloudflared-install-dir ~/.local/bin` is the recommended server-local path outside the skill checkout. `init` persists a chosen binary path to `data/dispatcher.env` as `DISPATCHER_CLOUDFLARED`.

Prepare a candidate tree for deployment without starting runtime services:

```bash
python3 scripts/run_dispatcher_tunnel.py reset --repo .
python3 scripts/run_dispatcher_tunnel.py reset --repo . --confirm-reset
```

`reset` removes only ignored local runtime state and generated caches, and is a dry run unless `--confirm-reset` is supplied.

For tunnel/runtime operations, use `scripts/run_dispatcher_tunnel.py`; read `memory/runtime-init-workflow.md` for the install/init/start flow. For context handoffs and approved purges, use `scripts/context_compact.py`. From a repository root, pass `--repo .agents/skills/dispatcher-skill` for project-local npx installs, pass `--repo skills/dispatcher-skill` in this source repository, or change directory into the detected skill root first.

## Roles

- Operator: user-managed maintenance role for repo edits, runtime inspection, verification, and app evolution. It is outside the dispatcher task plane; read `memory/operator-onboarding.md` for the ordered audit, mutex, handoff, and restart-safety procedure.
- Dispatcher: deterministic Python runtime that owns queue state, task claims, locks, retries, approvals, events, and manager/worker process boundaries.
- Manager: dispatcher-invoked Codex role that interprets queued user tasks, may suggest Inbox follow-ups, and may queue bounded worker requests.
- Dispatcher-owned worker: manager-invoked role run through `dispatcher_app.worker_client` and `worker_runner.py` for one bounded request.

## Runtime State

Package source and docs, not local state. `dispatcher_app/` is shipped as source/runtime code, but each install creates its own local runtime state after bootstrap.

Exclude `data/`, generated caches, local DBs, local logs, `dispatcher_app/agents.json`, lock/token files, secrets, local tunnel URLs, raw transcripts, prompts, stdout/stderr dumps, full JSONL records, and unvetted large binaries from package payloads and durable memory.

See `memory/skill-packaging.md` for the durable package include/exclude and binary policy.

## Safety

- Do not store secrets, raw logs, full transcripts, prompts, local tunnel URLs, or full JSONL records in `memory/`.
- Manager sessions running under `codex exec` must not call tmux, Cloudflare tunnel commands, tunnel scripts, or `dispatcher_app.reboot request` directly.
- If a manager-run task needs a runtime restart, finish implementation, docs or memory updates, and verification first, then add a final-result marker:

```text
REBOOT_AFTER_TASK restart-dispatcher <short reason>
```

The dispatcher strips that marker from the stored user-facing result, marks the task done, and appends the validated host-side reboot request.

## Integrated Helpers

The helper capabilities are integrated into this root dispatcher skill:

- Use `scripts/run_dispatcher_tunnel.py` when starting, inspecting, exposing, or packaging the local tunnel runtime.
- Use `scripts/run_dispatcher_tunnel.py reset --repo .` before deployment packaging to preview ignored runtime/cache cleanup, and add `--confirm-reset` only when deletion is intended.
- Use `scripts/context_compact.py` when exchanging compact handoffs or purging approved task/runtime-log state.

Do not recreate nested dispatcher helper skills inside this skill. See `memory/helper-scripts.md` for durable helper boundaries and maintained paths.
