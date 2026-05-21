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

`dispatcher_app/` is packaged as the source and runtime code for the dispatcher app. It includes the web server, queue loop, Codex runner boundary, worker client/runner, reboot watcher, runtime lock helper, static assets, templates, schema files, and agent identity registry.

Runtime state is local to each installation and is not shipped as package content. The app creates or updates local state after install/bootstrap/runtime, including SQLite databases, runtime logs, lock/token files, generated tunnel settings, and local environment files.

## Helper Scripts

The helper scripts are part of the root dispatcher skill, not independent nested skills. Keep their trigger conditions, maintained paths, and local-state boundaries in `memory/helper-scripts.md`.

## Include Policy

Package current source and final documentation needed to operate the dispatcher app and helper features:

- Root `SKILL.md` and selected final docs distilled from portable project memory. Include a skill-local `AGENTS.md` only if one is intentionally added to the payload; operator-container instructions and migration memory are not package payload.
- `requirements.md` so operators can verify Python, Codex, tmux, network, and cloudflared expectations before initialization.
- `dispatcher_app/` source code, templates, static assets, schemas, and non-secret registry metadata.
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

The smoke test checks that `dispatcher_app` imports from the candidate root, dispatcher app modules expose `--help` without crashing, `requirements.md` exists, `scripts/run_dispatcher_tunnel.py init-status --repo <candidate>` works without starting tmux or Cloudflare, the guarded `reset` dry run works, `scripts/context_compact.py --help` works, and root `SKILL.md` validates with the system `quick_validate.py` when it is available.

This is source/help/init-status validation only. It must not start the server, dispatcher loop, reboot watcher, tmux, cloudflared, tunnel foreground/start/restart paths, or network work.

## Deployment Reset

Before creating a deployment artifact, run the reset helper against the candidate tree:

```bash
python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>
```

Without `--confirm-reset`, this is a dry run that lists ignored local state and generated caches. After reviewing the target list, rerun with `--confirm-reset` to delete only the skill-local `data/` directory, Python bytecode, `__pycache__/`, and `.pytest_cache/`. It does not remove source files, portable docs, or the policy-managed `bin/cloudflared` binary.

## Exclude Policy

Do not package local runtime or machine-specific state:

- `data/`, including runtime logs, SQLite DBs, reboot state, request logs, local lock/token state, and agent conversation records.
- Generated caches such as `__pycache__/`, `.pytest_cache/`, bytecode, temporary outputs, and downloaded build artifacts.
- Secrets, passwords, Cloudflare tokens, local tunnel URLs, shell history, raw logs, full transcripts, prompts, stdout/stderr dumps, and full JSONL records.
- Host-specific process state such as tmux session state, PID files, sockets, and lock tokens.

Runtime log summaries or handoffs may be included only when they contain compressed durable facts and no raw audit content.

## Deferred Follow-ups

- Verify a fresh install/init/start path in a temporary directory.
- Add a live runtime smoke test for an isolated temporary copy only when starting tmux/cloudflared is explicitly allowed.
- For `high-assurance`, implement pinned cloudflared version and checksum verification before release, or ship a separately vetted binary.
