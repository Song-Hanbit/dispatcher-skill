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
- `VERSION`: current dispatcher skill version.
- `CHANGELOG.md`: version history and bump policy.
- `bin/cloudflared`: optional fallback Cloudflare tunnel binary for repo-local or exported-with-binary profiles; exported slim packages should omit it and use an external or local-state binary path.
- `memory/`: durable project memory. Read `memory/memory.md` first, then the specific topic needed.
- `data/`: ignored installation-local runtime state, including `dispatcher.env`, runtime config, SQLite DBs, logs, reboot state, and lock files. Do not package or copy it into memory.
- `dispatcher_app/agents.json`: ignored per-install agent handle registry generated during init or first direct runtime use.

## Initialization Trigger

When this skill is present in a new install and `init-status` reports uninitialized local state, enter the initialization sequence before runtime work:

1. Identify the skill root. From a repository where npx installed the skill, pass `--repo .agents/skills/dispatcher-skill`; from this source repository, pass `--repo skills/dispatcher-skill`; from the skill root itself, pass `--repo .`.
2. Load required operator memory once before init: `memory/memory.md`, `memory/operator-onboarding.md`, `memory/runtime-init-workflow.md`, and `memory/context-compression/operator.md`. The `init` helper enforces this read and then runs the memory compact checkpoint.
3. Review `requirements.md`, especially the host `cloudflared` choice. Prefer an external path through `--cloudflared`, a PATH install, or the operator-local installer target `~/.local/bin/cloudflared` over shipping the binary inside the skill.
4. Ask the user only for values that are not already available from command args, `DISPATCHER_PASSWORD`, `--password-file`, or existing `data/dispatcher.env`. At minimum, initialization needs a dispatcher password. A requested port is optional; the helper defaults to 8000, checks occupied ports with `ss -H -tuln`, and automatically chooses a free fallback when the requested port is busy.
5. Run `python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared <path> --port <port>` from the skill root when the binary path is known, or the equivalent outer-root command with `--repo .agents/skills/dispatcher-skill` for project-local npx installs. Use `--no-start` only when local env/config should be written without starting tmux or Cloudflare.
6. During init, `dispatcher_app/agents.json` is created or validated and the operator Codex handle is recorded from `--operator-key`, `DISPATCHER_OPERATOR_KEY`, or `CODEX_THREAD_ID` when available. Keep the registry limited to `version`, `key_type`, and agent rows with `role_key`, `name`, `key`, and `key_status`; leave unknown handles as `null` with `key_status: "missing"` so the dispatcher can populate managers and workers later.
7. During init, the helper ensures the surrounding repository's `AGENTS.md` contains a durable `Dispatcher Skill Operator Baseline` block so the rules survive operator/user and dispatcher/manager conversation drift. It creates the file if it is missing, updates the existing baseline block if present, or appends the block without overwriting unrelated repository guidance. The block must state:
   - The operator is outside the dispatcher task plane and owns maintenance, runtime inspection, verification, and app evolution.
   - Start operator work with runtime audit, inspect only relevant recent audit records, and never copy raw logs, full transcripts, prompts, secrets, stdout/stderr dumps, local or public tunnel URLs, full JSONL records, DB contents, or runtime state into chat, memory, or docs.
   - Before substantive inspection, decisions, edits, tests, or runtime state changes, acquire the global runtime lock from the skill root with `--db data/dispatcher.db`; if the task plane owns it, wait or report active task-plane work. On first install, reading required memory/requirements and running non-runtime `init-status` or smoke checks are allowed before this lock so the operator can identify uninitialized or unwritable local state. Heartbeat only locks this operator acquired and release only by this operator's token.
   - After audit and lock acquisition, process unread handoffs addressed to the current agent before other substantive work, cataloging only durable facts before ack/purge.
   - Managers must not call tmux, Cloudflare, tunnel scripts, or `dispatcher_app.reboot request` directly; runtime restarts from manager work use a final `REBOOT_AFTER_TASK ...` marker after implementation, docs or memory updates, and verification.
   - Passwords, helper tokens, lock tokens, and tunnel URLs are local runtime secrets and must not be printed or copied into tracked docs or durable memory.
8. Determine the repository name by searching the directory hierarchy, not by guessing from the skill directory. If the skill root is `<repo>/.agents/skills/dispatcher-skill`, use the parent directory above `.agents/` as `<repo>`; if it is `<repo>/skills/dispatcher-skill`, use the parent directory above `skills/`; otherwise use the validated dispatcher root directory name.
9. Let the helper default the tmux session name unless the user explicitly overrides it. The default session name is `<repo>-tunnel`, where `<repo>` is the repository name found by that directory search, not the `dispatcher-skill` skill directory name. For example, this development repository uses `dispatcher-skill-tunnel`.
10. At the end of a successful init/start response, tell the user a no-`cd` `status` command for post-init verification by including both the helper script path and `--repo`. `status` reports local HTTP health, expected tmux window presence, and whether a Quick Tunnel URL was detected without printing the URL value. If the user explicitly needs the URL, provide the separate `url` command, for example `python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py url --repo .agents/skills/dispatcher-skill --session <session>` from a project-local repository root or `python3 skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py url --repo skills/dispatcher-skill --session <session>` from this source repository root.

Do not print the password, generated public tunnel URL, or local env contents into chat, memory, or docs.

Dispatcher-managed manager prompts also include a mandatory initialization memory checkpoint. The dispatcher reads `memory/memory.md`, `memory/agents.md`, `memory/dispatcher-app.md`, and the manager's `memory/context-compression/<role>.md` before building the prompt, and the manager must perform a compact-equivalent memory cleanup before returning the final JSON.

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

`worker_client` uses the dispatcher server API by default. Runtime init supplies the local API address and helper token through ignored local env; use `--transport db` only for explicit debugging or recovery fallback.

Default worker request flow: `worker_client` reads `DISPATCHER_API_URL` or `DISPATCHER_HOST`/`DISPATCHER_PORT` plus `DISPATCHER_HELPER_TOKEN` from CLI flags, process env, or `data/dispatcher.env`, posts the request to `/api/worker-requests`, then polls `/api/worker-requests/<id>` until the dispatcher-run worker finishes. The server performs the manager/task/runtime-lock ownership checks and writes `worker_requests`; normal manager sandboxes do not need direct write access to `data/dispatcher.db`.

Worker API troubleshooting: a missing helper token means local runtime env was not initialized/backfilled or not inherited by the manager process; refresh it through the operator-side init/start/restart/foreground helper flow without copying token values. `Dispatcher server is not reachable` means the server is down or the host/port settings are wrong. `Worker API HTTP 401` means the helper token seen by the server and client do not match, so restart the affected runtime processes from the same ignored env. Ownership or active-worker errors mean the current manager does not own the in-progress task/runtime lock or another worker request is already pending/running.

Manager-suggested Inbox todo candidates:

```bash
python3 -m dispatcher_app.task_client suggest --task-id <task_id> --manager-role-key manager.default --items-file <items.json>
```

Operator runtime lock expectation before substantive operator work:

```bash
python3 -m dispatcher_app.runtime_lock --db data/dispatcher.db acquire --owner-plane operator --owner-id operator --lease-seconds 900 --token-file data/operator_runtime_lock.token
```

Initialize or inspect local runtime state:

```bash
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py status --repo .
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared /path/to/cloudflared --port 8000
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo . --cloudflared-install-dir ~/.local/bin
```

`init-status` prints a `next_init` block with secret-free init command templates for the current detected install, plus explicit project-local npx and source-repo path forms. When `cloudflared` is already available through PATH, `~/.local/bin`, ignored `data/bin`, or a configured path, those templates use the detected binary path; otherwise it also prints copy-ready commands for installing `cloudflared` into `~/.local/bin` and initializing with that path. Password choices are shown as `--password-file <password-file>`, `DISPATCHER_PASSWORD`, or TTY prompt without printing a value. The same block documents the default port 8000, free-port fallback, `--port 0`, and `<repo>-tunnel` default session rule. `install-cloudflared` defaults to ignored `data/bin/cloudflared` when no install directory is supplied, but `--cloudflared-install-dir ~/.local/bin` is the recommended server-local path outside the skill checkout. `init` persists a chosen binary path to `data/dispatcher.env` as `DISPATCHER_CLOUDFLARED`.

`status` is the post-init verification helper. It returns a structured summary of the local server HTTP response, expected tmux windows (`server`, `dispatcher`, `reboot`, `tunnel`), whether a Quick Tunnel URL is present, and safe restart candidates such as `restart-server`, `restart-dispatcher`, or `restart-reboot` when a component is missing. It does not print the Quick Tunnel URL value; use the explicit `url` command only when that value is needed.

Prepare a candidate tree for deployment without starting runtime services:

```bash
python3 scripts/run_dispatcher_tunnel.py reset --repo .
python3 scripts/run_dispatcher_tunnel.py reset --repo . --confirm-reset
```

`reset` removes only ignored local runtime state and generated caches, and is a dry run unless `--confirm-reset` is supplied.

Update an installed skill payload without moving runtime state:

1. Keep roots distinct. In this source repository, the candidate payload is `skills/dispatcher-skill/`; in a project-local npx install, the live installed skill is normally `.agents/skills/dispatcher-skill/`; from inside either skill root, helper commands use `--repo .`.
2. Prepare and validate the candidate away from the live runtime directory. Run `python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>` as a dry run, then run `python3 scripts/smoke_skill_package.py --repo <candidate>`, or use the integrated update dry run:

```bash
python3 scripts/run_dispatcher_tunnel.py update-skill --repo <installed-skill-root> --source <source-payload-root> --candidate <candidate-root>
```

3. The `update-skill` helper defaults to dry-run, compares package files, reports preserved runtime state, compares `VERSION`/`CHANGELOG.md`, and runs the package smoke check. Add `--confirm` only when the planned package-file changes should be applied.
4. Replace only packaged source/docs/scripts/static assets in the installed skill root. Do not copy, package, overwrite, or delete install-local `data/`, `dispatcher_app/agents.json`, `dispatcher.env`, SQLite DB/WAL files, logs, lock/token files, local tunnel URLs, raw logs, prompts, stdout/stderr dumps, full JSON records, or secrets.
5. After copying, run `init-status` against the installed skill root. If changed code requires runtime reload, operators restart the affected process host-side; manager-run tasks request it only through the final `REBOOT_AFTER_TASK restart-server` or `REBOOT_AFTER_TASK restart-dispatcher` marker after verification.

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
