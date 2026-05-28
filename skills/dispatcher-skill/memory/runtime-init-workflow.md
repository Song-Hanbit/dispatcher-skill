# Runtime Init Workflow Memory

## Purpose

This topic is the durable install/init/start workflow for operating the dispatcher app after this repository is installed as the `dispatcher-skill` skill, usually under `<repo>/.agents/skills/dispatcher-skill` for project-local npx installs. Keep command detail here and keep root `SKILL.md` concise for user-facing routing.

This workflow is for operator or host-side runtime operation. Managers running under `codex exec` must not call tmux, Cloudflare tunnel commands, tunnel scripts, or `dispatcher_app.reboot request` directly.

## Source Of Truth

The operator helper is `scripts/run_dispatcher_tunnel.py`. It detects the dispatcher skill root by finding `dispatcher_app/server.py`; if `--repo` is omitted, it searches the current working directory, parents, and known nested roots such as `.agents/skills/dispatcher-skill` and `skills/dispatcher-skill`.

## Ordered Flow

Before trying a live runtime start on a packaged or copied tree, run the non-runtime smoke test:

```bash
python3 scripts/smoke_skill_package.py --repo .
```

It checks source imports, CLI help surfaces, `requirements.md`, tunnel `init-status`, context helper help, and skill metadata validation without starting tmux, cloudflared, server, dispatcher, foreground, restart, or reboot paths.

Before initialization, the operator should read `requirements.md`. It lists host prerequisites and the supported `cloudflared` choices.

Initialization has a mandatory memory bootstrap. The helper reads `memory/memory.md`, `memory/operator-onboarding.md`, `memory/runtime-init-workflow.md`, and `memory/context-compression/operator.md` before writing local state. If any required file is missing, init fails instead of guessing. The helper also runs the initialization compact checkpoint over `memory/` so durable memory rejects raw runtime logs, full JSON records, full transcripts, prompts, secrets, private keys, and live tunnel URLs.

First-install pre-lock exception: on a brand-new install, `data/`, runtime audit files, and `dispatcher_app/agents.json` may not exist yet. The operator may read required memory and `requirements.md`, run the non-runtime package smoke check, and run `init-status` before acquiring the runtime lock. Before `init` writes local state or starts services, acquire the lock from the dispatcher skill root with an explicit skill-local DB path:

```bash
python3 -m dispatcher_app.runtime_lock --db data/dispatcher.db acquire --owner-plane operator --owner-id operator --lease-seconds 900 --token-file data/operator_runtime_lock.token
```

If this fails with a read-only or sandbox write error, stop and fix the approved write path for the installed skill root. Do not retry by pointing `--db` at an unrelated location, because the runtime lock must protect the same skill-local runtime state that `init`, server, and dispatcher will use.

For deployment preparation, preview local-state cleanup before packaging:

```bash
python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>
```

`reset` is a dry run unless `--confirm-reset` is supplied. With confirmation it deletes only ignored skill-local runtime state and generated caches such as `data/`, `__pycache__/`, `.pytest_cache/`, and bytecode. It does not start runtime services and does not remove source, portable docs, or `bin/cloudflared`.

1. Check local initialization state before runtime work:

```bash
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
```

`init-status` reports whether required dispatcher files and ignored local settings exist. It may report `password_set` and `helper_token_set`, but it must not print the password or helper token.

`init-status` also reports `operator_memory_ready` and `memory_compact_ready` with file-level metadata only. It must not print memory contents, secrets, local env values, runtime logs, or tunnel URLs.

`init-status` includes a `next_init` object for uninitialized installs. When `cloudflared` is already available through PATH, `~/.local/bin`, ignored `data/bin`, or a configured path, `next_init` includes command templates that use the detected path. The command set includes the current detected install path, a from-skill-root command, a project-local npx form using `.agents/skills/dispatcher-skill`, and a source-repo form using `skills/dispatcher-skill`, so operators can copy the right shape without guessing. Password guidance stays value-free and lists `--password-file <password-file>` as the recommended path, plus `DISPATCHER_PASSWORD` and an interactive TTY prompt. The same object states the default port 8000, free fallback behavior, `--port 0`, and the `<surrounding-repo-name>-tunnel` session rule.

When `cloudflared` is missing or unconfigured, `init-status` also prints copy-ready commands for a server-local `~/.local/bin/cloudflared` install and a matching `init --cloudflared ~/.local/bin/cloudflared` command.

2. Initialize local state when needed:

```bash
python3 scripts/run_dispatcher_tunnel.py init --repo <repo> --cloudflared <path> --port <port>
```

`init` verifies the repo root, loads the required operator initialization memory, runs the memory compact checkpoint, resolves host/port/session choices, creates or validates the ignored per-repository `dispatcher_app/agents.json`, records the operator Codex handle from `--operator-key`, `DISPATCHER_OPERATOR_KEY`, or `CODEX_THREAD_ID` when available, writes ignored local settings, and starts the runtime plus Quick Tunnel unless `--no-start` is supplied. The written local files are `dispatcher_app/agents.json`, `data/dispatcher.env`, and `data/run-dispatcher-tunnel.json`. `data/dispatcher.env` includes a per-install `DISPATCHER_HELPER_TOKEN` for manager helper API calls; `data/run-dispatcher-tunnel.json` records only whether that token is set.

After `init` creates the default `dispatcher_app/agents.json`, complete any known missing local agent fields before relying on the runtime. Keep the file limited to `version`, `key_type`, and agent rows with `role_key`, `name`, `key`, and `key_status`; leave unknown Codex handles as `null` with `key_status: "missing"` so the dispatcher can populate them on first use. Manager keys are recorded after manager Codex startup, and dispatcher-owned worker keys are recorded as soon as the worker Codex session emits `thread.started`. Do not add secrets, provider tokens, passwords, tunnel URLs, policy text, or lifecycle notes to `agents.json`.

During initialization, the helper automatically ensures the surrounding repository's `AGENTS.md` contains a durable `Dispatcher Skill Operator Baseline` block. It finds the surrounding repository root for both `.agents/skills/dispatcher-skill` and `skills/dispatcher-skill` installs, creates `AGENTS.md` if absent, replaces an existing marked or heading-based baseline idempotently, or appends the block without overwriting unrelated repository guidance. The block preserves the always-on operator rules from `SKILL.md`: operator/task-plane boundary, runtime audit, raw-log and secret copying prohibitions, global runtime lock acquire/heartbeat/release discipline, handoff processing order, manager restart-marker boundary, and local-secret handling. The generated block must stay value-free: no passwords, tokens, tunnel URLs, raw logs, or runtime state.

At the end of a successful init/start response, include a no-`cd` command for post-init verification. Prefer a command that includes both the helper script path and `--repo`, such as this project-local repository-root form:

```bash
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py status --repo .agents/skills/dispatcher-skill --session <session>
```

`status` reports the local server HTTP response, expected tmux window presence for `server`, `dispatcher`, `reboot`, and `tunnel`, and whether a Quick Tunnel URL was detected in the tunnel pane. It does not print the Quick Tunnel URL value. If a component is missing or unhealthy, it suggests the safe host-side candidate (`restart-server`, `restart-dispatcher`, `restart-reboot`, or a tunnel restart that may issue a new URL) while preserving the manager boundary: managers do not call tmux, Cloudflare, tunnel helpers, or restarts directly.

Use the explicit `url` command only when the operator needs the current Quick Tunnel URL value:

```bash
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py url --repo .agents/skills/dispatcher-skill --session <session>
```

Do not store the generated URL itself in memory or durable docs.

Password input choices are, in priority order: `--password`, `--password-file`, `DISPATCHER_PASSWORD`, an existing `data/dispatcher.env`, then an interactive prompt when a TTY is available. Prefer `--password-file`, `DISPATCHER_PASSWORD`, or prompt for real secrets. Do not print or copy passwords into memory.

Helper-token input choices are `DISPATCHER_HELPER_TOKEN`, an existing `data/dispatcher.env`, or an auto-generated per-install token during `init`. Runtime `start`, `restart-*`, and `foreground` backfill a missing helper token into an existing env file before launching server/dispatcher processes, so manager subprocesses inherit the same local API credential. Do not print or copy helper token values into memory or tracked docs.

Cloudflared input choices are, in priority order: `--cloudflared`, persisted `DISPATCHER_CLOUDFLARED`, ignored local `data/bin/cloudflared`, PATH `cloudflared`, then optional package-local `bin/cloudflared` when a profile intentionally ships it. `init` persists a resolved `--cloudflared` or installer path into `data/dispatcher.env`.

If the operator wants the helper to download the binary without adding it to the package payload:

```bash
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo <repo> --cloudflared-install-dir ~/.local/bin
python3 scripts/run_dispatcher_tunnel.py init --repo <repo> --cloudflared ~/.local/bin/cloudflared --port <port>
```

`install-cloudflared` defaults to ignored `data/bin/cloudflared` when no install directory is supplied; `~/.local/bin` is the recommended server-local path outside the skill checkout. Use another `--cloudflared-install-dir <dir>` only for a host-approved external directory.

3. Start normally after settings exist:

```bash
python3 scripts/run_dispatcher_tunnel.py start --repo <repo>
```

`start` reads defaults from `data/dispatcher.env` when present. Explicit command-line values still override stored host, port, DB, Python, session, and cloudflared settings. Use `--replace` only when recreating the tmux session and issuing a new Quick Tunnel URL is intended.

4. Run without tmux for one-shot foreground operation:

```bash
python3 scripts/run_dispatcher_tunnel.py foreground --repo <repo> --port <port>
```

`foreground` starts the server, dispatcher, and tunnel under the current process and exits when interrupted; it is the no-tmux path.

## Choices

- Skill root: use `--repo .agents/skills/dispatcher-skill` from a project-local npx install's repository root, `--repo skills/dispatcher-skill` in this source repository, or `--repo .` from the skill root itself. The helper validates `dispatcher_app/server.py`, `dispatcher_app/dispatcher.py`, and `dispatcher_app/reboot.py`.
- Port: default is `8000`; the helper first checks occupied ports with `ss -H -tuln` and then verifies candidates with bind probes. If the requested port is busy, it chooses a free fallback unless `--strict-port` is supplied. Use `--port 0` to always choose a random free port. `init --no-start` requires a concrete port, not `--port 0`.
- Session: determine the repository name by searching the directory hierarchy instead of guessing from the skill directory name. If the skill root is `<repo>/.agents/skills/dispatcher-skill`, use the parent directory above `.agents/` as `<repo>`; if it is `<repo>/skills/dispatcher-skill`, use the parent directory above `skills/`; otherwise use the validated dispatcher root directory name. `init` generates `<repo>-tunnel` when no `--session` or stored session exists. For example, this development repository uses `dispatcher-skill-tunnel`. Other commands use the stored env setting, an explicit `--session`, or the same `<repo>-tunnel` default. The helper addresses tmux sessions with exact `=session` targets internally so `repo-tunnel` and `repo-tunnel-old` style names do not collide through tmux prefix matching.
- Password/env: the dispatcher server receives `DISPATCHER_PASSWORD` and `DISPATCHER_HELPER_TOKEN` from the ignored local env file after init. The dispatcher process inherits the same env so manager-run `worker_client` can use the server API by default. `data/dispatcher.env` is installation-local state, not package payload.
- Cloudflared: prefer `--cloudflared ~/.local/bin/cloudflared` for a server-local user install, another host-managed binary path, PATH for system installs, or ignored `data/bin/cloudflared` for repo-local throwaway installs. Do not make a large binary part of exported skill payload unless the packaging profile intentionally includes a vetted binary.

## Safety And Packaging

`data/` is ignored local state created per install/bootstrap/runtime. Do not package it and do not copy runtime DB contents, raw logs, full transcripts, prompts, stdout/stderr dumps, secrets, passwords, or local tunnel URLs into memory.

Normal `start` must not download executables silently. If `cloudflared` is missing, the operator must provide it explicitly through PATH, `--cloudflared`, `DISPATCHER_CLOUDFLARED`, `install-cloudflared`, or `--install-cloudflared`.
