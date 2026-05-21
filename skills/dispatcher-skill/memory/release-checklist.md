# Release Checklist Memory

Use this checklist before handing off a packaged `dispatcher-skill` artifact. This is an operator/host-side checklist; managers running under `codex exec` must not call tmux, Cloudflare tunnel commands, tunnel scripts, or `dispatcher_app.reboot request`.

## Package Boundary

- [ ] Include root `SKILL.md`, `requirements.md`, selected final docs distilled from `memory/`, `dispatcher_app/`, `scripts/`, and optional `bin/cloudflared` according to the selected packaging profile. Include a skill-local `AGENTS.md` only if one is intentionally added to the payload.
- [ ] Include `dispatcher_app/` as source/runtime code only; runtime state must be created locally after install/init.
- [ ] Treat `scripts/run_dispatcher_tunnel.py` and `scripts/context_compact.py` as integrated helper capabilities, not replacement roots.
- [ ] Exclude `data/`, SQLite DBs, audit logs, reboot state, local lock/token/PID/socket state, local env files, and local tunnel URLs.
- [ ] Exclude generated caches, bytecode, `.pytest_cache/`, temporary outputs, migration scratch files, raw logs, full transcripts, prompts, stdout/stderr dumps, full JSONL records, passwords, tokens, and other secrets.
- [ ] Run `python3 scripts/run_dispatcher_tunnel.py reset --repo <candidate>` first as a dry run, review the target list, then use `--confirm-reset` only on the candidate tree when deleting ignored local state and generated caches is intended.

## Cloudflared Profile

- [ ] Choose one profile from `memory/skill-packaging.md`: `server-external`, `repo-local`, `exported-with-binary`, `exported-slim`, or `high-assurance`.
- [ ] Prefer `server-external` for exported packages: omit `bin/cloudflared`, then use `--cloudflared ~/.local/bin/cloudflared`, `DISPATCHER_CLOUDFLARED`, PATH, or ignored `data/bin/cloudflared` during host init.
- [ ] For `repo-local`, keep `bin/cloudflared` only in the working tree used as the local runtime source.
- [ ] For `exported-with-binary`, include a vetted `cloudflared` binary intentionally.
- [ ] For `exported-slim`, omit the binary only when explicit install/provide paths are documented: PATH `cloudflared`, `--cloudflared /path/to/cloudflared`, `DISPATCHER_CLOUDFLARED`, `install-cloudflared`, or `--install-cloudflared` with `start` or `foreground`.
- [ ] For `high-assurance`, require a pinned cloudflared version plus checksum verification or a separately vetted binary before release. The current installer uses latest GitHub release for Linux amd64/arm64, checks only nonempty download data, and marks the file executable.
- [ ] Confirm normal `start` does not download executables silently; installer paths must be explicit operator choices.

## Smoke Checks

- [ ] Run the non-runtime package smoke test against the candidate tree:

```bash
python3 scripts/smoke_skill_package.py --repo <candidate>
```

- [ ] Confirm the smoke test covers `dispatcher_app` import, dispatcher CLI `--help` surfaces, `requirements.md`, tunnel helper `init-status`, guarded reset dry-run, context compact helper `--help`, and root skill validation when `quick_validate.py` is available.
- [ ] If debugging manually, use help or status-only checks such as `python3 -m dispatcher_app.server --help`, `python3 -m dispatcher_app.dispatcher --help`, and `python3 scripts/run_dispatcher_tunnel.py init-status --repo <candidate>`.
- [ ] Do not start server, dispatcher, reboot watcher, tmux, cloudflared, tunnel `start`/`foreground`/`restart`, or network work during non-runtime smoke checks.

## Migration Init

- [ ] In the copied/installed candidate, run `init-status --repo <candidate>` before runtime work to detect whether ignored local state exists.
- [ ] Confirm `init-status --repo <candidate>` presents copy-ready `~/.local/bin` install/init commands when `cloudflared` is missing or unconfigured.
- [ ] For operator-approved setup, run `init --repo <candidate> --cloudflared ~/.local/bin/cloudflared --port <port>` after the suggested install, or use another host-approved binary path.
- [ ] Confirm `init` persists the chosen binary path in `data/dispatcher.env` as `DISPATCHER_CLOUDFLARED`.
- [ ] Use `init --no-start` when the release review should write local config without starting tmux or Cloudflare; do not use `--port 0` with `--no-start`.
- [ ] Use `start` for normal tmux runtime start after settings exist or when explicit args are supplied.
- [ ] Use `foreground` only for one-shot/no-tmux execution when operator runtime work is explicitly approved.
- [ ] Provide passwords through an interactive prompt, `DISPATCHER_PASSWORD`, `--password-file`, or `--password`; do not print passwords or copy them into memory.

## Reject Or Pause

- [ ] Reject or pause if required source/docs are missing, root `SKILL.md` validation fails, smoke checks fail, or stale package names/old repo paths remain.
- [ ] Reject or pause if any `data/`, raw audit content, local tunnel URL, secret, token, password, prompt, stdout/stderr dump, full transcript, or full JSONL record is included in the package payload.
- [ ] Reject or pause if the cloudflared profile is unclear, an omitted binary lacks explicit install/provide paths, or a `high-assurance` release lacks pinned version/checksum verification or a separately vetted binary.
- [ ] Reject or pause if migration/init requires manager-task actions. Runtime init/start/foreground operations are operator/host-side only.

## Rollback

- [ ] Keep the source working tree and runtime state separate from candidate package contents so a failed candidate can be discarded without copying runtime data back into source.
- [ ] If release validation fails before runtime start, remove or archive only the candidate copy/artifact; keep the source working tree unchanged.
- [ ] If a candidate runtime was started under operator approval, return users to the prior known-good source/runtime using operator/host-side controls, not manager task commands.
- [ ] Do not embed rollback secrets, local tunnel URLs, runtime DBs, raw logs, full transcripts, prompts, stdout/stderr dumps, or full JSONL records in the package or memory.
