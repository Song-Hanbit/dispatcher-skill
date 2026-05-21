# Dispatcher Skill

Dispatcher Skill is a Codex skill for running a local dispatcher queue: users submit work in a small web UI, and dispatcher-managed manager and worker agents process the queue.

Most users should not initialize the runtime by hand. Install the skill with an npx-based Agent Skills installer or your Codex CLI skill installation flow, then ask your Codex operator to initialize and start it.

## Install With npx

Install into the repository where you want Codex to use the dispatcher skill. Use the skill payload directory, not the repository root:

```bash
npx skills add https://github.com/Song-Hanbit/dispatcher-skill/tree/main/skills/dispatcher-skill -a codex -y
```

For a local checkout:

```bash
npx skills add ./skills/dispatcher-skill -a codex -y
```

Notes:

- `-a codex` targets Codex.
- Project-local installation is the default here so each repository can carry its own dispatcher skill configuration.
- `-y` skips interactive confirmation. Omit it if you want to review prompts.
- After installing, restart Codex so the new skill is discovered.

## User Flow

1. Install this repository's skill payload with `npx skills add` or your Codex CLI skill installation flow.
   - The shippable skill is in `skills/dispatcher-skill/`.
   - If your installer asks for a directory, use that directory.
   - If your installer accepts a Git repository and skill path, point it at this repository and `skills/dispatcher-skill/`.
2. Open Codex in the environment where the skill is installed.
3. Ask the operator to initialize it, for example:

```text
Initialize the dispatcher skill and start the local dispatcher runtime.
```

4. The operator checks host requirements, configures local runtime state, and starts the dispatcher.
5. Use the dispatcher web UI to create and review queued tasks.

## What The Operator Handles

The operator should work from the installed skill root:

```bash
cd skills/dispatcher-skill
```

Before starting runtime services, the operator reviews:

- `SKILL.md`: skill entrypoint and operating boundaries.
- `requirements.md`: host requirements and `cloudflared` setup.
- `memory/`: durable operator, runtime, packaging, and safety procedures.

The operator chooses how `cloudflared` is provided. The recommended server-local path is outside the skill checkout:

```bash
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo . --cloudflared-install-dir ~/.local/bin
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared ~/.local/bin/cloudflared --port 8000
```

`init-status` prints copy-ready commands for this setup. The operator may also use a host-managed `cloudflared` on `PATH`, an explicit binary path, or the ignored `data/bin/cloudflared` fallback for local throwaway installs.

## What Is Included

- `skills/dispatcher-skill/SKILL.md`: Codex skill entrypoint.
- `skills/dispatcher-skill/dispatcher_app/`: Python dispatcher server, queue runtime, manager/worker runners, templates, static assets, schemas, runtime lock helper, and reboot watcher.
- `skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py`: init, status, start, stop, reset, and Cloudflare Quick Tunnel helper.
- `skills/dispatcher-skill/scripts/context_compact.py`: context handoff and guarded purge helper.
- `skills/dispatcher-skill/requirements.md`: operator-facing runtime prerequisites.
- `skills/dispatcher-skill/memory/`: portable skill operating memory.
- `skill-migration-memory/`: ignored development-container notes, not package payload.

## Package And State Boundary

Package source and portable docs, not local runtime state.

Include the skill payload source, scripts, memory docs, and selected non-secret metadata. Exclude `data/`, `skill-migration-memory/`, SQLite databases, logs, lock/token/PID/socket files, local env files, local or public tunnel URLs, prompts, full transcripts, stdout/stderr dumps, generated caches, and unvetted large binaries.

`cloudflared` should usually live outside the exported skill package. Include `skills/dispatcher-skill/bin/cloudflared` only when a packaging profile intentionally ships a vetted binary.

## Maintainer Checks

From the skill root:

```bash
cd skills/dispatcher-skill
python3 scripts/smoke_skill_package.py --repo .
python3 -m py_compile dispatcher_app/*.py
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py reset --repo .
```

These checks do not start tmux, Cloudflare, the server, the dispatcher loop, or the reboot watcher. `reset` is a dry run unless `--confirm-reset` is supplied.

## Safety

- Do not commit secrets, passwords, local tunnel URLs, runtime databases, raw logs, prompts, full transcripts, or local env files.
- Keep installed runtime state under the skill's ignored `data/` directory.
- Keep development-container notes in `skill-migration-memory/`, not in portable skill memory.
- Runtime start, restart, tunnel, and reboot watcher actions are operator/host-side work.

---

# Dispatcher Skill 한국어

Dispatcher Skill은 로컬 dispatcher queue를 실행하는 Codex skill입니다. 사용자는 작은 웹 UI에 작업을 넣고, dispatcher가 관리하는 manager와 worker 에이전트가 queue를 처리합니다.

대부분의 사용자는 runtime을 직접 초기화하지 않아도 됩니다. npx 기반 Agent Skills installer 또는 Codex CLI의 skill 설치 흐름으로 이 skill을 설치한 뒤, Codex operator에게 초기화와 시작을 요청하면 됩니다.

## npx로 설치

Codex가 dispatcher skill을 사용할 repository 안에 직접 설치하는 것을 기본으로 합니다. Repository root가 아니라 skill payload 디렉토리를 지정합니다.

```bash
npx skills add https://github.com/Song-Hanbit/dispatcher-skill/tree/main/skills/dispatcher-skill -a codex -y
```

로컬 checkout에서 설치하려면:

```bash
npx skills add ./skills/dispatcher-skill -a codex -y
```

메모:

- `-a codex`는 Codex를 대상으로 설치한다는 뜻입니다.
- 여기서는 repository-local 설치를 기본으로 하므로 각 repository가 자신의 dispatcher skill 설정을 가질 수 있습니다.
- `-y`는 확인 prompt를 건너뜁니다. 직접 확인하고 싶다면 빼면 됩니다.
- 설치 후에는 Codex를 다시 시작해야 새 skill이 발견됩니다.

## 사용자 흐름

1. `npx skills add` 또는 Codex CLI의 skill 설치 흐름으로 이 repository의 skill payload를 설치합니다.
   - 배포되는 skill은 `skills/dispatcher-skill/` 안에 있습니다.
   - 설치 도구가 디렉토리를 묻는다면 이 디렉토리를 사용합니다.
   - 설치 도구가 Git repository와 skill path를 받는다면 이 repository와 `skills/dispatcher-skill/`을 지정합니다.
2. Skill이 설치된 환경에서 Codex를 엽니다.
3. Operator에게 초기화를 요청합니다. 예:

```text
dispatcher skill을 초기화하고 로컬 dispatcher runtime을 시작해줘.
```

4. Operator가 host 요구사항을 확인하고, 로컬 runtime state를 설정한 뒤 dispatcher를 시작합니다.
5. Dispatcher 웹 UI에서 작업을 만들고 처리 상태를 확인합니다.

## Operator가 처리하는 일

Operator는 설치된 skill root에서 작업해야 합니다.

```bash
cd skills/dispatcher-skill
```

Runtime service를 시작하기 전에 operator는 다음 문서를 확인합니다.

- `SKILL.md`: skill entrypoint와 작업 경계.
- `requirements.md`: host 요구사항과 `cloudflared` 설정.
- `memory/`: operator, runtime, packaging, safety 절차.

Operator는 `cloudflared`를 어디에서 제공할지 선택합니다. 서버 로컬 설치에는 skill checkout 밖의 `~/.local/bin` 경로를 권장합니다.

```bash
python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo . --cloudflared-install-dir ~/.local/bin
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared ~/.local/bin/cloudflared --port 8000
```

`init-status`는 이 설정에 맞는 복사 가능한 명령을 출력합니다. Host가 관리하는 `PATH` 상의 `cloudflared`, 명시적인 binary path, 또는 임시 로컬 설치용으로 무시되는 `data/bin/cloudflared`도 사용할 수 있습니다.

## 포함된 것

- `skills/dispatcher-skill/SKILL.md`: Codex skill entrypoint.
- `skills/dispatcher-skill/dispatcher_app/`: Python dispatcher server, queue runtime, manager/worker runner, template, static asset, schema, runtime lock helper, reboot watcher.
- `skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py`: init, status, start, stop, reset, Cloudflare Quick Tunnel helper.
- `skills/dispatcher-skill/scripts/context_compact.py`: context handoff와 승인된 purge helper.
- `skills/dispatcher-skill/requirements.md`: operator가 확인할 runtime 요구사항.
- `skills/dispatcher-skill/memory/`: 설치된 skill에도 유효해야 하는 portable 운영 memory.
- `skill-migration-memory/`: package payload가 아닌, 무시되는 개발 컨테이너 노트.

## Package와 State 경계

Package에는 source와 portable docs만 넣고, local runtime state는 넣지 않습니다.

Skill payload source, scripts, memory docs, secret이 아닌 선택된 metadata는 포함합니다. `data/`, `skill-migration-memory/`, SQLite database, log, lock/token/PID/socket file, local env file, local/public tunnel URL, prompt, full transcript, stdout/stderr dump, generated cache, 검증되지 않은 큰 binary는 제외합니다.

`cloudflared`는 보통 export된 skill package 밖에 두는 것이 좋습니다. `skills/dispatcher-skill/bin/cloudflared`는 vetted binary를 의도적으로 포함하는 packaging profile에서만 포함합니다.

## Maintainer 확인

Skill root에서 실행합니다.

```bash
cd skills/dispatcher-skill
python3 scripts/smoke_skill_package.py --repo .
python3 -m py_compile dispatcher_app/*.py
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py reset --repo .
```

이 확인 작업은 tmux, Cloudflare, server, dispatcher loop, reboot watcher를 시작하지 않습니다. `reset`은 `--confirm-reset`을 붙이지 않으면 dry run입니다.

## Safety

- Secret, password, local tunnel URL, runtime database, raw log, prompt, full transcript, local env file을 commit하지 않습니다.
- 설치별 runtime state는 skill의 ignored `data/` directory 아래에 둡니다.
- 개발 컨테이너 노트는 portable skill memory가 아니라 `skill-migration-memory/`에 둡니다.
- Runtime start, restart, tunnel, reboot watcher 작업은 operator/host-side 작업입니다.
