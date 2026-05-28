# Helper Scripts Memory

## Boundary

This repository is one root dispatcher skill. Runtime and context helpers are part of this skill, not nested skills or replacement roots.

Keep routing/orientation in root `SKILL.md`, deterministic operations in `scripts/`, and durable packaging/runtime policy in memory.

## Helpers

| Helper | Trigger | Maintained paths | Intentional local state |
| --- | --- | --- | --- |
| Tunnel/runtime helper | Initialize, start, inspect, expose, restart, deployment-reset, or package-update the local dispatcher skill/runtime and Cloudflare Quick Tunnel. | `scripts/run_dispatcher_tunnel.py`, optional external/PATH/local-state `cloudflared`, and optional policy-managed `bin/cloudflared` only for profiles that intentionally ship it. | Reads/writes ignored `data/dispatcher.env`, `data/run-dispatcher-tunnel.json`, SQLite DBs, reboot state, and runtime logs during runtime/init paths; `init` can persist `DISPATCHER_CLOUDFLARED` and the local helper API token, and creates or updates a value-free Dispatcher Skill Operator Baseline block in the surrounding repo's `AGENTS.md`; `init-status` reports only whether password/helper token are set, suggests `~/.local/bin` install commands when needed, and prints secret-free next-init templates for detected npx/source/skill-root paths; `status` summarizes local HTTP health, expected runtime windows, URL presence, and safe restart candidates without printing the Quick Tunnel URL value; `install-cloudflared` defaults to ignored `data/bin/cloudflared`; `reset` can delete ignored `data/` and generated caches only when `--confirm-reset` is supplied; `update-skill` copies package files only and preserves ignored runtime state. |
| Context compact helper | Compress model context, append/read/ack handoffs, or run approved task-card/runtime-log purge workflows. | `scripts/context_compact.py`, `memory/context-compression/*.md`. | For approved purges, reads/writes ignored `data/dispatcher.db`, `data/agent_conversations/`, and `data/codex_runs/`; writes compressed handoff summaries only, not raw logs. |

The durable install/init/start workflow for `scripts/run_dispatcher_tunnel.py` lives in `memory/runtime-init-workflow.md`. Deployment reset behavior and packaging cleanup policy live in `memory/skill-packaging.md`.

## Package Boundary

The canonical include/exclude and binary policy lives in `memory/skill-packaging.md`. Helper scripts follow that package boundary.

Package helper scripts/docs needed to operate the dispatcher app. Do not package `data/`, generated caches, bytecode, temporary outputs, secrets, raw logs, full transcripts, prompts, stdout/stderr dumps, full JSONL records, or local tunnel URLs.

Cloudflared packaging profiles live in `memory/skill-packaging.md`. Exported packages should usually use the `server-external` path: omit `bin/cloudflared`, then use `--cloudflared ~/.local/bin/cloudflared`, another host-managed path, `DISPATCHER_CLOUDFLARED`, PATH, or ignored `data/bin/cloudflared` during host initialization.

## Safety

- Manager sessions under `codex exec` must not call tmux, tunnel scripts, Cloudflare commands, or `dispatcher_app.reboot request` directly.
- Operator or host-side workflows may use runtime helpers when explicitly doing runtime operations.
- Context compression and purge workflows must keep raw logs, full transcripts, prompts, secrets, and local tunnel URLs out of durable memory.
