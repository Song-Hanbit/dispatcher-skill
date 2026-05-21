# Repository Guidelines

## Root Purpose

This repository root is now the operator container for a migrated dispatcher skill. The shippable skill payload lives under `skills/dispatcher-skill/`.

Dispatcher-managed managers operate on this whole repository working tree for non-runtime task work, not only the skill payload directory. Keep shippable dispatcher source and docs under `skills/dispatcher-skill/`, and keep development-container notes under `skill-migration-memory/`.

Keep the root narrow:

- `skills/dispatcher-skill/`: dispatcher skill source, integrated helper scripts, optional local binaries, and final reference docs.
- `skill-migration-memory/`: ignored local migration notes and archives only. Runtime env/config belongs inside the skill's ignored `data/` directory.
- `.gitignore`: root ignore policy.
- `AGENTS.md`: this operator-facing instruction file.

Do not add dispatcher app source, runtime DBs, logs, package docs, or generated files back at the root.

## Working In The Skill

For source edits, validation, and package smoke checks, work from the skill payload root:

```bash
cd skills/dispatcher-skill
```

Useful non-runtime checks from that directory:

- `rg --files --hidden`: list skill payload files.
- `python3 scripts/smoke_skill_package.py --repo .`: run source/help/init-status smoke checks without starting live services.
- `python3 -m py_compile dispatcher_app/*.py`: compile dispatcher app modules.
- `python3 scripts/run_dispatcher_tunnel.py init-status --repo .`: inspect skill-local init state without starting tmux or Cloudflare.

Runtime start, restart, tmux, Cloudflare tunnel, and reboot watcher operations are operator/host-side actions. Do not run them unless the user explicitly asks for runtime work.

## Migration Memory

During this migration, put temporary plans, local decisions, and status notes in `skill-migration-memory/current.md`.

Future notes about this development repository, the outer operator container, migration state, package preparation, or repo-local runtime observations also belong in `skill-migration-memory/current.md`, not in `skills/dispatcher-skill/memory/`.

Do not copy secrets, passwords, tokens, public or local tunnel URLs, raw logs, prompts, stdout/stderr dumps, full transcripts, full JSON records, SQLite databases, or runtime state into tracked docs or skill payload.

Before final packaging, distill durable facts from `skill-migration-memory/` into `skills/dispatcher-skill/SKILL.md` or focused reference docs that are meant to ship. `skill-migration-memory/` itself is not package payload and must not hold active dispatcher runtime config.

## Safety

The previous root dispatcher runtime may have been stopped or removed during migration. Do not assume root `data/`, root `dispatcher_app/`, or root `memory/` exists.

If a future task creates a live runtime inside `skills/dispatcher-skill/`, follow that skill's own operator procedure for audit, runtime lock, handoff processing, and restart safety from inside the skill payload root.

Make surgical changes. Preserve the root boundary above unless the user explicitly changes the migration target.
