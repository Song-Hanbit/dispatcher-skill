# Project Memory

Use this directory as the portable dispatcher skill knowledge base. `dispatcher_app/` is the app source/runtime code, and helper scripts under `scripts/` are integrated capabilities. Read this index first, then open only the topic file needed for the current task.

## Topics

- `architecture.md`: root skill boundary, dispatcher-manager-worker model, source-of-truth decisions, and app boundaries.
- `agents.md`: agent keys, display names, ownership, and registry rules.
- `session.md`: short portable runtime state, recent changes, verification summary, and open follow-ups.
- `dispatcher-app.md`: current Python server behavior, SQLite state, UI, auth, initialization memory bootstrap, and verification flow.
- `operator-onboarding.md`: ordered operator audit, runtime lock, handoff, task-plane boundary, and restart safety procedure.
- `helper-scripts.md`: integrated helper script trigger conditions, maintained paths, and package/local state boundaries.
- `runtime-init-workflow.md`: operator/host-side requirements, install, init-status, init, start, foreground, password, port, cloudflared path, and tmux session workflow.
- `skill-packaging.md`: repo-as-skill packaging boundary, include/exclude policy, helper script treatment, and deferred packaging work.
- `release-checklist.md`: concise operator checklist for package boundary, secret/runtime cleanup, smoke checks, cloudflared profile, migration/init, and rollback.
- `cloudflare-tunnel.md`: mobile access, Cloudflare Quick Tunnel, fixed URL tradeoffs, and the tunnel helper.
- `context-compression/`: compressed handoffs and purge summaries for agent context transfer. These files must not contain raw logs, full transcripts, prompts, secrets, or full JSON records.

## Maintenance Rules

- Keep root `SKILL.md` short and link durable details here.
- Keep `memory/` portable and universal for installed dispatcher skill copies. Development-repository and migration notes belong outside the payload in the operator container's ignored migration memory.
- Do not store secrets, Cloudflare tokens, API keys, or private URLs in memory files.
- Update `session.md` after substantial work, server or tunnel changes, or before a long final response.
- Update the relevant memory file when architecture, runtime behavior, or operating procedure changes.
- After required runtime audit and mutex preconditions, process any nonempty unread handoff addressed to the current agent before other substantive work.
- When receiving any handoff, compress the useful durable facts into the relevant `memory/*.md` topic file before acknowledging it. Update this index when the handoff creates a new topic, renames a topic, or changes what a topic covers.
- Handoff cataloging keeps decisions, current state, verification, unresolved risks, and next actions. Do not copy raw logs, full transcripts, prompts, secrets, or non-actionable detail into memory.
