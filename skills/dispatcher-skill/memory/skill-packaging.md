# Skill Packaging Memory

## Boundary

This repository is a root Codex skill for running a local dispatcher app. The top-level skill boundary should provide:

- A root `SKILL.md` entrypoint that explains when to use the dispatcher app skill.
- An operator-facing `requirements.md` that lists host prerequisites and cloudflared binary choices before init.
- Bootstrap or init guidance for creating local runtime state after install; the durable command flow lives in `memory/runtime-init-workflow.md`.
- Commands for starting, inspecting, and stopping the dispatcher runtime through documented helpers.
- Operator onboarding rules for runtime audit, mutex use, handoff processing, and memory hygiene.
- Integrated helper capabilities that support the dispatcher app, such as tunnel management and context compression.
- A release checklist for packaging, smoke checks, cloudflared profile choice, and rollback; tracked checklist guidance lives in `memory/release-checklist.md`.

The root skill should describe the whole repository as one dispatcher app skill. It should not make integrated helper scripts appear to be independent replacement roots.

## Packaged Source

`dispatcher_app/` is packaged as the source and runtime code for the dispatcher app. It includes the web server, queue loop, Codex runner boundary, worker client/runner, reboot watcher, runtime lock helper, static assets, templates, schema files, and the agent registry generator. The per-install `dispatcher_app/agents.json` identity state is generated locally and is not package content.

Runtime state is local to each installation and is not shipped as package content. The app creates or updates local state after install/bootstrap/runtime, including SQLite databases, runtime logs, lock/token files, generated tunnel settings, and local environment files.

## Helper Scripts

The helper scripts are part of the root dispatcher skill, not independent nested skills. Keep their trigger conditions, maintained paths, and local-state boundaries in `memory/helper-scripts.md`.

## Include Policy

Package current source and final documentation needed to operate the dispatcher app and helper features:

- Root `SKILL.md` and selected final docs distilled from portable project memory. Include a skill-local `AGENTS.md` only if one is intentionally added to the payload; operator-container instructions, migration memory, and development decision logs such as `memory/decisions.md` are not package payload.
- `VERSION` and `CHANGELOG.md` as non-secret package metadata for current version, release history, and bump policy.
- `requirements.md` so operators can verify Python, Codex, tmux, network, and cloudflared expectations before initialization.
- `dispatcher_app/` source code, templates, static assets, schemas, and the non-secret registry generation helper.
- Helper scripts under `scripts/run_dispatcher_tunnel.py` and `scripts/context_compact.py`.
- Policy-managed `bin/cloudflared` only when the selected packaging profile intentionally includes a vetted binary.
- `.gitignore` and other non-secret repo metadata needed for local operation.

## Packaging Profiles

- `server-external`: preferred exported profile; omit `bin/cloudflared` and require an operator-provided binary through `--cloudflared`, `DISPATCHER_CLOUDFLARED`, PATH, or explicit local install such as `~/.local/bin/cloudflared`.
- `repo-local`: may keep `bin/cloudflared` in this development repository when the local runtime uses it here.
- `exported-with-binary`: may include `bin/cloudflared` only when the package intentionally ships a vetted binary.
- `exported-slim`: may omit `bin/cloudflared` to reduce package size only when explicit install/provide paths are documented.
- `high-assurance`: requires a pinned cloudflared version plus checksum verification or a separately vetted binary before release.

## Binary Policy

The preferred deployment policy is `server-external`: keep `cloudflared` outside the skill package and persist the chosen path in ignored local state during init. Existing repo-local binaries can remain for development/runtime continuity, but exported packages should not include them unless the release profile explicitly says so.

The explicit install/provide paths are:

- Put `cloudflared` on PATH.
- Pass `--cloudflared /path/to/cloudflared`; `init` persists the resolved path in `data/dispatcher.env` as `DISPATCHER_CLOUDFLARED`.
- Set `DISPATCHER_CLOUDFLARED=/path/to/cloudflared` before runtime commands.
- Run `install-cloudflared`, which defaults to ignored `data/bin/cloudflared`.
- Use `--cloudflared-install-dir ~/.local/bin` for the recommended server-local user install path; use another external install directory only when host policy requires it.
- Use `--install-cloudflared` with `start` or `foreground`.

Normal `start` must not download executables silently. Installer paths are explicit operator choices.

The current installer uses Cloudflare's latest GitHub release URL for standalone Linux amd64 or arm64, checks only that the downloaded data is nonempty, writes the target binary, and marks it executable. It is not pinned to a version and does not verify a checksum.

## Smoke Test

Use this non-runtime smoke test against a packaged or copied candidate tree before trying a live runtime start:

```bash
python3 scripts/smoke_skill_package.py --repo .
```

The smoke test checks that `dispatcher_app` imports from the candidate root, dispatcher app modules expose `--help` without crashing, `SKILL.md`, `VERSION`, `CHANGELOG.md`, and `requirements.md` exist, `scripts/run_dispatcher_tunnel.py init-status --repo <candidate>` works without starting tmux or Cloudflare, the guarded `reset` dry run works, `scripts/context_compact.py --help` works, and root `SKILL.md` validates with the system `quick_validate.py` when it is available.

This is source/help/init-status validation only. It must not start the server, dispatcher loop, reboot watcher, tmux, cloudflared, tunnel foreground/start/restart paths, or network work.

## Deployment Reset

Before creating a deployment artifact, run the reset helper against the candidate tree:

```bash
python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>
```

Without `--confirm-reset`, this is a dry run that lists ignored local state and generated caches. After reviewing the target list, rerun with `--confirm-reset` to delete only the skill-local `data/` directory, Python bytecode, `__pycache__/`, and `.pytest_cache/`. It does not remove source files, portable docs, or the policy-managed `bin/cloudflared` binary.

## Updating Installed Skills

When replacing an installed dispatcher skill with a newer payload, keep the source/candidate tree, installed skill root, and install-local runtime state separate.

Path conventions:

- In this development repository, the shippable source payload is `skills/dispatcher-skill/`.
- In a project-local npx install, the live skill root is normally `.agents/skills/dispatcher-skill/`.
- From inside a skill root, helper commands use `--repo .`; from the surrounding project root, pass the explicit installed path such as `--repo .agents/skills/dispatcher-skill`.

Prepare the candidate before touching the live install:

1. Build or select a candidate from packaged source files, not from the live runtime directory.
2. Run `python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>` and review the dry-run list. Use `--confirm-reset` only on a disposable candidate or when ignored local state should actually be removed.
3. Run `python3 scripts/smoke_skill_package.py --repo <candidate>` against the candidate. This validates source/help/init-status behavior without starting server, dispatcher, tmux, Cloudflare, or network work.

Update the installed skill by replacing only package content: `SKILL.md`, portable `memory/` docs, `requirements.md`, `VERSION`, `CHANGELOG.md`, `dispatcher_app/` source/templates/static/schema files, helper scripts, and any selected non-secret package metadata or vetted binary profile content.

Do not copy, package, overwrite, or delete installation-local runtime state during an update:

- `data/`, including `dispatcher.env`, SQLite DB/WAL files, runtime logs, reboot state, request logs, PID/socket files, and lock/token files.
- `dispatcher_app/agents.json`, which is per-install identity state.
- Passwords, helper tokens, Cloudflare tokens, local tunnel URLs, shell history, raw logs, full transcripts, prompts, stdout/stderr dumps, full JSON records, and other secrets.
- Host-specific tmux, process, network, and tunnel state.

After copying the payload, run `init-status` against the installed root:

```bash
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py init-status --repo .agents/skills/dispatcher-skill
```

or, from inside the installed skill root:

```bash
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
```

If the update changes runtime code or static assets, restart only the affected runtime process after validation: `restart-server` for server/static/API-only changes, `restart-dispatcher` for dispatcher, manager prompt, worker, or queue-loop changes, and the broader host-side restart flow only when multiple runtime processes must reload together. Dispatcher managers must not call restart helpers directly; a manager task asks for the restart by adding the appropriate final `REBOOT_AFTER_TASK ...` marker after implementation, documentation or memory updates, and verification. Operators perform host-side restarts after the runtime audit and mutex procedure.

For rollback, restore the previous payload source files while preserving the same install-local runtime state. Do not roll back by restoring old `data/`, `dispatcher_app/agents.json`, env files, DB files, logs, or tokens unless the user explicitly approves a separate runtime-state recovery.

## Update Helper

Implemented helper shape:

```bash
python3 scripts/run_dispatcher_tunnel.py update-skill --repo <installed-skill-root> --source <source-payload-root> --candidate <candidate-root>
```

Arguments:

- `--repo <installed-skill-root>` is the live installed dispatcher skill root to update, such as `.agents/skills/dispatcher-skill` in a project-local npx install. This matches the existing helper convention where `--repo .` means the current skill root.
- `--source <source-payload-root>` is the desired new payload source, such as `skills/dispatcher-skill` in this development repository or another checked-out release payload.
- `--candidate <candidate-root>` is a prepared staging copy used for reset dry-run, smoke checks, comparison, and eventual copy. It must not be the live installed root. If omitted, the helper treats `--source` as the candidate for dry-run only; `--confirm` requires an explicit candidate path.

Default behavior is dry-run only. The dry-run:

- Validate that source, candidate, and installed roots are distinct where destructive writes could occur, and refuse a candidate or source that points at the live runtime tree for confirmed updates.
- Run or report the equivalent of `python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>` without `--confirm-reset`, showing ignored runtime state and generated cache paths that would be cleaned from the candidate, not from the installed runtime.
- Compare package files from candidate to installed root and list planned adds, modifications, removals, and unchanged files. The comparison must use the package include/exclude policy, not raw directory copying.
- Separately list runtime state that will be preserved: `data/`, `dispatcher_app/agents.json`, env files including `dispatcher.env`, DB/WAL files, logs, reboot state, request logs, PID/socket files, lock/token files, local tunnel URLs, raw logs, prompts, stdout/stderr dumps, full JSON records, and secrets.
- Read installed and candidate `VERSION`; read the candidate `CHANGELOG.md` top release heading; report version movement and warn when the candidate version is missing, older, equal, or not represented in the changelog.
- Run `python3 scripts/smoke_skill_package.py --repo <candidate>` by default because it is non-runtime validation. `--skip-smoke` supports quick comparison, but confirmed updates require a passing smoke check unless a deliberate `--allow-unsmoked` override is supplied.
- Print the post-copy validation command, especially `init-status` against the installed root, and print the appropriate restart guidance without performing runtime actions.

Confirmed behavior requires `--confirm`. With `--confirm`, the helper replaces only package content under the installed skill root, deletes installed package files that are absent from the candidate, and preserves all excluded runtime state. It stages copies before mutating the install tree and rolls back package-file changes when an apply failure is raised. It does not run tmux, Cloudflare, server, dispatcher, reboot watcher, or restart commands. If runtime code changed, the output suggests the narrow restart class: `restart-server` for server/static/API-only changes, `restart-dispatcher` for dispatcher, manager prompt, worker, or queue-loop changes, and a broader operator restart flow only when multiple runtime processes must reload together.

Manager boundary: dispatcher manager tasks may design, document, or run dry-run checks when appropriate, but they must not call tmux, cloudflared, tunnel start/restart commands, or `dispatcher_app.reboot request` directly. If a manager-run update task changes code that needs a runtime reload, the manager finishes implementation, docs or memory updates, and verification first, then adds the final `REBOOT_AFTER_TASK ...` marker. Operators perform any host-side confirmed update or restart after the runtime audit and mutex procedure.

## Exclude Policy

Do not package local runtime or machine-specific state:

- `data/`, including runtime logs, SQLite DBs, reboot state, request logs, local lock/token state, and agent conversation records.
- Per-install `dispatcher_app/agents.json` registry state; `dispatcher_app/agent_registry.py` recreates a default registry during init or first direct runtime use, and init fills the operator handle when an operator key source is available.
- Generated caches such as `__pycache__/`, `.pytest_cache/`, bytecode, temporary outputs, and downloaded build artifacts.
- Secrets, passwords, Cloudflare tokens, local tunnel URLs, shell history, raw logs, full transcripts, prompts, stdout/stderr dumps, and full JSONL records.
- Host-specific process state such as tmux session state, PID files, sockets, and lock tokens.

Runtime log summaries or handoffs may be included only when they contain compressed durable facts and no raw audit content.

Development decision logs are excluded from exported packages. Before export, distill any still-relevant architectural decisions into focused portable docs such as `memory/architecture.md`, `memory/dispatcher-app.md`, or `memory/skill-packaging.md`, then leave the raw decision log out of the payload.

## Deferred Follow-ups

- Verify a fresh install/init/start path in a temporary directory.
- Add a live runtime smoke test for an isolated temporary copy only when starting tmux/cloudflared is explicitly allowed.
- For `high-assurance`, implement pinned cloudflared version and checksum verification before release, or ship a separately vetted binary.
