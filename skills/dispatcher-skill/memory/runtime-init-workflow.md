# Runtime Init Workflow Memory

## Purpose

This topic is the durable install/init/start workflow for operating the dispatcher app after this repository is installed as the root `dispatcher-skill`. Keep command detail here and keep root `SKILL.md` concise for user-facing routing.

This workflow is for operator or host-side runtime operation. Managers running under `codex exec` must not call tmux, Cloudflare tunnel commands, tunnel scripts, or `dispatcher_app.reboot request` directly.

## Source Of Truth

The operator helper is `scripts/run_dispatcher_tunnel.py`. It detects a repo root by finding `dispatcher_app/server.py`; if `--repo` is omitted, it searches from the current working directory through its parents.

## Ordered Flow

Before trying a live runtime start on a packaged or copied tree, run the non-runtime smoke test:

```bash
python3 scripts/smoke_skill_package.py --repo .
```

It checks source imports, CLI help surfaces, `requirements.md`, tunnel `init-status`, context helper help, and skill metadata validation without starting tmux, cloudflared, server, dispatcher, foreground, restart, or reboot paths.

Before initialization, the operator should read `requirements.md`. It lists host prerequisites and the supported `cloudflared` choices.

For deployment preparation, preview local-state cleanup before packaging:

```bash
python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>
```

`reset` is a dry run unless `--confirm-reset` is supplied. With confirmation it deletes only ignored skill-local runtime state and generated caches such as `data/`, `__pycache__/`, `.pytest_cache/`, and bytecode. It does not start runtime services and does not remove source, portable docs, or `bin/cloudflared`.

1. Check local initialization state before runtime work:

```bash
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
```

`init-status` reports whether required dispatcher files and ignored local settings exist. It may report `password_set`, but it must not print the password.

When `cloudflared` is missing or unconfigured, `init-status` also prints copy-ready commands for a server-local `~/.local/bin/cloudflared` install and a matching `init --cloudflared ~/.local/bin/cloudflared` command.

2. Initialize local state when needed:

```bash
python3 scripts/run_dispatcher_tunnel.py init --repo <repo> --cloudflared <path> --port <port>
```

`init` verifies the repo root, resolves host/port/session choices, writes ignored local settings, and starts the runtime plus Quick Tunnel unless `--no-start` is supplied. The written local files are `data/dispatcher.env` and `data/run-dispatcher-tunnel.json`.

At the end of a successful init/start response, include the command for checking the current Quick Tunnel URL, such as:

```bash
python3 scripts/run_dispatcher_tunnel.py url --session <session>
```

Do not store the generated URL itself in memory or durable docs.

Password input choices are, in priority order: `--password`, `--password-file`, `DISPATCHER_PASSWORD`, an existing `data/dispatcher.env`, then an interactive prompt when a TTY is available. Prefer `--password-file`, `DISPATCHER_PASSWORD`, or prompt for real secrets. Do not print or copy passwords into memory.

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

- Repo: use the current working directory unless the user provides `--repo`; the helper validates `dispatcher_app/server.py`, `dispatcher_app/dispatcher.py`, and `dispatcher_app/reboot.py`.
- Port: default is `8000`; if the requested port is busy, the helper chooses a free fallback unless `--strict-port` is supplied. Use `--port 0` to always choose a random free port. `init --no-start` requires a concrete port, not `--port 0`.
- Session: `init` generates the surrounding repository name when no `--session` or stored session exists. If the skill root is `<repo>/skills/dispatcher-skill`, the session name is `<repo>`'s directory name, not `dispatcher-skill`. Other commands use the stored env setting, an explicit `--session`, or the same repository-name default. The helper addresses tmux sessions with exact `=session` targets internally so `repo` and `repo-old` style names do not collide through tmux prefix matching.
- Password/env: the dispatcher server receives `DISPATCHER_PASSWORD` from the ignored local env file after init. `data/dispatcher.env` is installation-local state, not package payload.
- Cloudflared: prefer `--cloudflared ~/.local/bin/cloudflared` for a server-local user install, another host-managed binary path, PATH for system installs, or ignored `data/bin/cloudflared` for repo-local throwaway installs. Do not make a large binary part of exported skill payload unless the packaging profile intentionally includes a vetted binary.

## Safety And Packaging

`data/` is ignored local state created per install/bootstrap/runtime. Do not package it and do not copy runtime DB contents, raw logs, full transcripts, prompts, stdout/stderr dumps, secrets, passwords, or local tunnel URLs into memory.

Normal `start` must not download executables silently. If `cloudflared` is missing, the operator must provide it explicitly through PATH, `--cloudflared`, `DISPATCHER_CLOUDFLARED`, `install-cloudflared`, or `--install-cloudflared`.
