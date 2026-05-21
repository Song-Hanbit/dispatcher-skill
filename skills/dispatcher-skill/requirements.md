# Dispatcher Skill Requirements

Review this file before running `init`, `start`, or `foreground` in a fresh install.

## Required Runtime

- Linux or another POSIX-like host with Python 3.11+.
- Python standard library support for `sqlite3`, `http.server`, `subprocess`, `threading`, and `urllib`.
- `codex` CLI installed and authenticated for manager and worker execution.
- `tmux` for `start`, `restart-*`, `status`, and `stop`. Use `foreground` only for a one-shot no-tmux run.
- Network egress to Cloudflare when Quick Tunnel is used.

No Python package install is required for the dispatcher app itself; it uses the Python standard library.

## Cloudflared Binary

Prefer keeping `cloudflared` outside the skill package so exported skill artifacts stay small. The helper resolves the binary in this order:

1. `--cloudflared /path/to/cloudflared`, or the persisted `DISPATCHER_CLOUDFLARED` value from `data/dispatcher.env`.
2. `data/bin/cloudflared`, created by the explicit installer path and ignored by Git.
3. `cloudflared` on `PATH`.
4. Optional `bin/cloudflared` when a repo-local or exported-with-binary package intentionally includes it.

Recommended server setup:

```bash
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared ~/.local/bin/cloudflared --port 8000
```

`init-status` prints copy-ready commands for a server-local `~/.local/bin/cloudflared` install.

If the operator wants the helper to download the Linux binary, keep it out of package payload by installing into the operator's user bin directory:

```bash
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo . --cloudflared-install-dir ~/.local/bin
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared ~/.local/bin/cloudflared --port 8000
```

For a repo-local throwaway install, the helper can still use ignored local state:

```bash
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo .
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared data/bin/cloudflared --port 8000
```

`install-cloudflared` defaults to `data/bin/cloudflared`; `~/.local/bin` is the recommended server-local path when the operator wants the binary outside the skill checkout.

## Local State

- `data/dispatcher.env` stores local runtime settings, including `DISPATCHER_CLOUDFLARED` when a binary path is chosen during `init`.
- `data/` is ignored installation-local state. Do not package it.
- Do not copy passwords, tokens, local tunnel URLs, runtime DBs, logs, prompts, stdout/stderr dumps, full transcripts, or full JSONL records into docs or memory.
