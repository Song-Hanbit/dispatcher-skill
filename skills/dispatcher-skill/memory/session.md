# Session Memory

This file keeps compact, portable working context for installed dispatcher skill copies. Durable behavior belongs in the topic files listed by `memory/memory.md`.

## Current Runtime

- The skill has no required active runtime at rest. Local initialization and runtime state are created inside ignored `data/`.
- Runtime helper state lives in `data/dispatcher.env` and `data/run-dispatcher-tunnel.json`; do not copy their contents into memory.
- Quick Tunnel URLs are intentionally not stored here. When a tunnel is running, retrieve the current URL with a no-`cd` command that includes the helper script path and `--repo`, such as `python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py url --repo .agents/skills/dispatcher-skill --session <session>` from a project-local repository root.
- Runtime audit records live under ignored `data/codex_runs/` and `data/agent_conversations/`. Treat them as audit logs; do not copy raw log lines, prompts, secrets, stdout/stderr dumps, full transcripts, or full JSONL records into memory.

## Recent Portable Changes

- Root `SKILL.md` describes this repository as one dispatcher app skill with integrated runtime and context helpers.
- Dispatcher-managed manager and worker Codex runs use the surrounding repository root as their workspace when the skill is nested at `.agents/skills/dispatcher-skill/` or `skills/dispatcher-skill/`, while helper commands remain anchored in the dispatcher skill root.
- Dispatcher-owned worker display names are no longer hardcoded to a personal name; managers can provide a name with `worker_client --worker-name`, and the dispatcher persists it in `agents.json`.
- `memory/skill-packaging.md` defines the package boundary, runtime-state exclusion policy, helper script treatment, cloudflared packaging profiles, and deferred packaging follow-ups.
- Package exports use selected portable memory docs only; development decision logs such as `memory/decisions.md` are excluded and any durable facts must be distilled into focused memory docs first.
- `requirements.md` is the operator-facing pre-init checklist for Python, Codex, tmux, network, and cloudflared binary choices.
- `memory/operator-onboarding.md` defines the operator audit, runtime lock, handoff, task-plane, and restart-safety procedure.
- `memory/helper-scripts.md` describes `scripts/run_dispatcher_tunnel.py` and `scripts/context_compact.py` as integrated helper capabilities.
- `memory/runtime-init-workflow.md` documents the operator/host-side `init-status` -> `init` -> `start` or `foreground` runtime flow.
- `scripts/smoke_skill_package.py` provides a non-runtime smoke test for packaged or copied skill trees without starting live services.
- `scripts/run_dispatcher_tunnel.py reset --repo <candidate>` provides a deployment-prep dry run by default; with `--confirm-reset` it deletes only ignored skill-local runtime state and generated caches, not source docs or `bin/cloudflared`.
- Cloudflared should normally be outside exported packages: use `--cloudflared ~/.local/bin/cloudflared`, persisted `DISPATCHER_CLOUDFLARED`, PATH, or ignored `data/bin/cloudflared`; `init-status` prints copy-ready `~/.local/bin` install/init commands; `bin/cloudflared` is only for repo-local or intentionally vetted binary profiles.
- `memory/release-checklist.md` tracks package include/exclude, secret/runtime cleanup, smoke checks, cloudflared profile, migration/init, reject/pause, and rollback criteria.
- `dispatcher_app/memory_bootstrap.py` enforces operator init memory loading, manager prompt memory bootstrap checklists, and a memory compact checkpoint that rejects raw runtime records, secrets, private keys, and live tunnel URLs in durable memory.
- The web UI Directory section reports the operator workspace root, including project-local npx installs under `.agents/skills/dispatcher-skill/`, while runtime state remains under the skill's ignored `data/`.
- UI panel defaults are viewport-aware: compact viewports start with every panel collapsed, while larger viewports open Activity, Agent status, New task, and Queue by default and keep Directory plus task cards collapsed.
- Tmux session defaults use `<repo>-tunnel`, where `<repo>` is found by searching the directory hierarchy. For nested installs at `<repo>/.agents/skills/dispatcher-skill`, use the parent directory above `.agents/`; for `<repo>/skills/dispatcher-skill`, use the parent directory above `skills/`, not the `dispatcher-skill` directory name. This development repository's default is `dispatcher-skill-tunnel`.
- Init/start summaries end with a no-`cd` command for checking the current Quick Tunnel URL; memory and durable docs still omit the generated URL itself.
- Manager and dispatcher-owned worker calls explicitly set non-interactive approval policy plus `--sandbox workspace-write --cd <repo>` so task-plane agents can edit the skill repo, while worker request creation now goes through the dispatcher helper API by default instead of requiring manager-side DB writes.
- Inbox has selected/all bulk queue controls backed by `POST /api/tasks/bulk-queue`; the server only moves current Inbox rows to Pending and reports skipped stale or non-Inbox selections safely.
- Pending cards can now be moved back to Inbox; the action is accepted only while the task is still `pending`, clears `queued_at`, and removes the card from dispatcher claim order until queued again.
- Done and Closed task cards now have an in-card continuation form that records a `user_continuation` note and sends the same task back to Pending; in-progress steering remains a separate Activity-only flow for currently running tasks.
- Worker request creation and lookup now have helper-token protected server API endpoints, and `worker_client call` uses them by default. Direct SQLite access remains available only through explicit `--transport db` debugging or recovery fallback.
- Runtime init writes a per-install helper API token only to ignored local env, reports only `helper_token_set`, and passes the same env to server, dispatcher, and manager subprocesses so nested skill installs can use worker API requests without manager-side DB writes.
- The dispatcher now reclaims stale task-owned runtime locks whose task is no longer `in_progress` before worker servicing or pending-task claims, covering the failure mode where manager runtime is idle but an unexpired stale task lock blocks the next Pending card.

## Verification Summary

- Python code changes should be checked with `python3 -m py_compile dispatcher_app/*.py` when dispatcher modules are touched.
- Frontend changes should be checked with `node --check dispatcher_app/static/app.js` when JavaScript is touched.
- Package-boundary changes should be checked with `python3 scripts/smoke_skill_package.py --repo .`.
- Tunnel helper init-state checks can use `python3 scripts/run_dispatcher_tunnel.py init-status --repo .`; this is status-only and must not start tmux or Cloudflare.
- Documentation-only changes are checked with focused `rg` searches for stale policy wording and catalog consistency.

## Open Follow-ups

- Move the UI password out of source code into environment or local config.
- Add automated tests under `tests/` for store transitions, dispatcher behavior, runtime locks, worker requests, approval flow, and reboot barriers.
- Smoke test a real Codex manager task and a real long-running dispatcher-owned worker request through a live dispatcher only when pending task side effects are acceptable.
- Validate the global execution mutex in real operator-versus-task-plane operation, then turn the helper checks into automated coverage.
- For high-assurance cloudflared packaging, add pinned version and checksum verification or ship a separately vetted binary.

## Update Rule

Keep this file portable. Non-portable development notes, migration state, and package-preparation scratch notes belong outside the skill payload in ignored migration memory. Context handoffs and approved purge summaries stay under `memory/context-compression/` and must remain free of raw logs, full transcripts, prompts, secrets, and full JSON records.
