# Dispatcher Skill

This repository packages a Codex skill that turns a repository into a small local dispatcher queue. A user adds work through a browser UI, then the dispatcher runs manager and worker Codex sessions to process those tasks while preserving queue state, approvals, activity history, and restart safety.

The shippable skill is under `skills/dispatcher-skill/`. The repository root is the development/operator container for that skill.

## What It Solves

Codex is useful for interactive work, but repeated repository tasks need more structure than a single chat turn. Dispatcher Skill adds that structure:

- A browser queue for creating, reviewing, approving, retrying, and canceling tasks.
- A deterministic Python dispatcher that owns task state in SQLite.
- A manager role that interprets queued tasks and can ask bounded worker roles for help.
- Activity views that show task requests, manager actions, worker activity, and results in one place.
- Runtime safety around approvals, local state, memory cleanup, and dispatcher restarts.

The goal is not to replace Codex. It is to give Codex a local task lane so users can submit work, leave it running, and come back to inspect what happened.

## Quick Start

1. Install the skill into the repository where you want the dispatcher.

```bash
npx skills add https://github.com/Song-Hanbit/dispatcher-skill/tree/main/skills/dispatcher-skill -a codex -y
```

For a local checkout of this repository:

```bash
npx skills add ./skills/dispatcher-skill -a codex -y
```

2. Restart Codex so it discovers the new skill.

3. Ask Codex to initialize and start it:

```text
Initialize the dispatcher skill and start the local dispatcher runtime.
```

4. Open the dispatcher web UI URL that the operator reports.

5. Add work items to the queue. New tasks enter `Inbox`; queue them into `Pending` when they are ready for the dispatcher.

Most users should use the Codex operator flow above instead of running helper commands by hand.

## Manual Operator Flow

If you are operating the skill directly, stay in the target repository root and pass the installed skill root with `--repo`.

For a project-local install:

```bash
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py init-status --repo .agents/skills/dispatcher-skill
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py init --repo .agents/skills/dispatcher-skill --cloudflared ~/.local/bin/cloudflared --port 8000
```

For this source repository:

```bash
cd skills/dispatcher-skill
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared ~/.local/bin/cloudflared --port 8000
```

`init-status` checks local setup without starting tmux, Cloudflare, the server, the dispatcher, or the reboot watcher. `init` creates ignored local runtime state, records available local agent handles, and starts the runtime unless `--no-start` is supplied.

The init helper also enforces memory bootstrap checks: required operator memory files are loaded, and durable memory is checked so raw logs, secrets, full transcripts, private keys, and live tunnel URLs do not get mixed into portable memory.

## Requirements

Review `skills/dispatcher-skill/requirements.md` before first runtime initialization. In short, the runtime expects:

- Python 3 with the standard library.
- Codex CLI installed and authenticated.
- `tmux` for the normal multi-window runtime.
- `cloudflared` for browser access through a Quick Tunnel, unless you use another local access path.
- A dispatcher password supplied through prompt, environment, or `--password-file`.

Recommended `cloudflared` setup keeps the binary outside the skill checkout:

```bash
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py install-cloudflared --repo .agents/skills/dispatcher-skill --cloudflared-install-dir ~/.local/bin
```

## How It Works

The runtime has two planes:

- The user/operator plane initializes, inspects, and maintains the dispatcher runtime.
- The task plane claims queued work and runs manager and worker Codex sessions.

The flow is:

1. The web UI writes tasks into SQLite.
2. The dispatcher loop claims the oldest `Pending` task when the task plane is free.
3. The manager Codex session receives one task payload and returns `execute`, `needs_approval`, or `failed`.
4. If useful, the manager queues a dispatcher-owned worker request; the Python dispatcher runs the worker and streams worker activity back into the same Activity view.
5. The dispatcher records the result, handles approval pauses, and safely queues any post-task runtime restart marker.

Managers do not start tmux, Cloudflare, or reboot commands directly. Runtime restart requests are returned as a final result marker and processed by the host-side reboot watcher only after the task is done.

## Using The UI

- `Inbox`: newly created task candidates. Review or edit these before queueing.
- `Pending`: tasks ready for dispatcher processing.
- `In Progress`: the currently claimed task.
- `Needs Approval`: tasks paused by the manager because user approval is required.
- `Done`: completed tasks.
- `Closed`: failed or canceled tasks.

The Activity panel shows the task request, manager messages, command/file-change activity, worker reports, and final result. The Agent panel shows manager/worker/operator status and the local repository directory view.

## Package And State Boundary

Package source and portable documentation. Do not package local runtime state.

Included in the skill payload:

- `skills/dispatcher-skill/SKILL.md`
- `skills/dispatcher-skill/VERSION`
- `skills/dispatcher-skill/CHANGELOG.md`
- `skills/dispatcher-skill/dispatcher_app/`
- `skills/dispatcher-skill/scripts/`
- `skills/dispatcher-skill/requirements.md`
- selected portable docs under `skills/dispatcher-skill/memory/`

Ignored local state:

- `skills/dispatcher-skill/data/`
- `skills/dispatcher-skill/dispatcher_app/agents.json`
- SQLite databases and runtime logs
- lock, token, PID, socket, and local env files
- local or public tunnel URLs
- raw prompts, stdout/stderr dumps, full transcripts, and full JSONL records

`skill-migration-memory/` is for this development container only. It is not package payload.
Development decision logs such as `memory/decisions.md` are not package payload; distill any durable, portable facts into the focused memory docs before export.

## Maintainer Checks

From the skill root:

```bash
python3 scripts/smoke_skill_package.py --repo .
python3 -m py_compile dispatcher_app/*.py
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py reset --repo .
```

These checks do not start runtime services. `reset` is a dry run unless `--confirm-reset` is supplied.

## Versioning

The current version is recorded in `skills/dispatcher-skill/VERSION`. Release history and bump rules are in `skills/dispatcher-skill/CHANGELOG.md`.

---

# Dispatcher Skill 한국어

이 repository는 Codex용 Dispatcher Skill을 패키징합니다. 사용자는 브라우저 UI에 작업을 넣고, dispatcher는 manager/worker Codex 세션을 실행해 queue를 처리합니다. Task 상태, 승인, activity, runtime restart 안전성은 Python dispatcher가 관리합니다.

배포되는 skill payload는 `skills/dispatcher-skill/` 아래에 있습니다. Repository root는 이 skill을 개발하고 운영하기 위한 container입니다.

## 해결하는 문제

Codex 대화 한 번으로 끝나지 않는 repository 작업에는 queue, 상태, 승인, 재시도, 이력 관리가 필요합니다. Dispatcher Skill은 그 흐름을 로컬에서 제공합니다.

- 작업을 만들고 검토하고 승인할 수 있는 브라우저 queue.
- SQLite를 source of truth로 쓰는 deterministic Python dispatcher.
- Queue task를 해석하는 manager 역할.
- Manager가 필요할 때 부를 수 있는 dispatcher-owned worker 역할.
- Task 요청, command/file 변경, worker 보고, 최종 결과를 함께 보여주는 Activity.
- Secret, raw log, memory, runtime restart에 대한 운영 안전장치.

목표는 Codex를 대체하는 것이 아니라, Codex가 반복 작업을 안전하게 처리할 수 있는 로컬 task lane을 제공하는 것입니다.

## 빠른 시작

1. Dispatcher를 사용할 repository에 skill을 설치합니다.

```bash
npx skills add https://github.com/Song-Hanbit/dispatcher-skill/tree/main/skills/dispatcher-skill -a codex -y
```

로컬 checkout에서는 다음을 사용할 수 있습니다.

```bash
npx skills add ./skills/dispatcher-skill -a codex -y
```

2. Codex를 재시작해 새 skill을 로드합니다.

3. Codex operator에게 초기화와 시작을 요청합니다.

```text
dispatcher skill을 초기화하고 로컬 dispatcher runtime을 시작해줘.
```

4. Operator가 알려주는 dispatcher web UI URL을 엽니다.

5. 작업을 추가합니다. 새 작업은 `Inbox`에 들어가며, 실행 준비가 되면 `Pending`으로 보냅니다.

대부분의 사용자는 helper command를 직접 실행하지 말고 Codex operator에게 요청하는 방식이 좋습니다.

## 수동 Operator 절차

직접 운영한다면 target repository root에 머물고 설치된 skill root를 `--repo`로 넘깁니다.

Project-local 설치:

```bash
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py init-status --repo .agents/skills/dispatcher-skill
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py init --repo .agents/skills/dispatcher-skill --cloudflared ~/.local/bin/cloudflared --port 8000
```

이 source repository:

```bash
cd skills/dispatcher-skill
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py init --repo . --cloudflared ~/.local/bin/cloudflared --port 8000
```

`init-status`는 tmux, Cloudflare, server, dispatcher, reboot watcher를 시작하지 않고 현재 local setup을 점검합니다. `init`은 ignored local runtime state를 만들고, 가능한 agent handle을 기록하고, `--no-start`가 없으면 runtime을 시작합니다.

Init helper는 memory bootstrap도 강제합니다. 필요한 operator memory 파일을 읽고, durable memory에 raw log, secret, full transcript, private key, live tunnel URL이 섞이지 않았는지 확인합니다.

## 요구사항

처음 초기화하기 전에 `skills/dispatcher-skill/requirements.md`를 확인하세요. 핵심 요구사항은 다음과 같습니다.

- Python 3.
- 인증된 Codex CLI.
- 일반 runtime을 위한 `tmux`.
- Quick Tunnel 접근을 위한 `cloudflared` 또는 다른 로컬 접근 방식.
- prompt, environment, 또는 `--password-file`로 제공하는 dispatcher password.

권장 `cloudflared` 설치 위치는 skill checkout 밖입니다.

```bash
python3 .agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py install-cloudflared --repo .agents/skills/dispatcher-skill --cloudflared-install-dir ~/.local/bin
```

## 작동 원리

Runtime은 두 plane으로 나뉩니다.

- User/operator plane은 dispatcher를 초기화하고 점검하고 유지보수합니다.
- Task plane은 queued work를 claim하고 manager/worker Codex 세션을 실행합니다.

흐름은 다음과 같습니다.

1. Web UI가 SQLite에 task를 저장합니다.
2. Dispatcher loop가 task plane이 비어 있을 때 가장 오래된 `Pending` task를 claim합니다.
3. Manager Codex 세션이 task payload 하나를 받고 `execute`, `needs_approval`, `failed` 중 하나를 반환합니다.
4. 필요하면 manager가 dispatcher-owned worker request를 queue에 넣고, Python dispatcher가 worker를 실행해 Activity에 표시합니다.
5. Dispatcher가 결과를 기록하고, 승인 대기와 post-task runtime restart marker를 안전하게 처리합니다.

Manager는 tmux, Cloudflare, reboot command를 직접 실행하지 않습니다. Runtime restart가 필요하면 final result marker로 요청하고, task 완료 후 host-side reboot watcher가 처리합니다.

## UI 사용법

- `Inbox`: 새 task 후보입니다. 검토 후 실행 준비가 되면 queue합니다.
- `Pending`: dispatcher가 처리할 준비가 된 task입니다.
- `In Progress`: 현재 실행 중인 task입니다.
- `Needs Approval`: manager가 사용자 승인을 요청해 멈춘 task입니다.
- `Done`: 완료된 task입니다.
- `Closed`: 실패 또는 취소된 task입니다.

Activity panel은 task 요청, manager 메시지, command/file-change activity, worker 보고, 최종 결과를 함께 보여줍니다. Agent panel은 manager/worker/operator 상태와 local repository directory view를 보여줍니다.

## Package와 State 경계

Package에는 source와 portable docs만 넣고 local runtime state는 넣지 않습니다.

Skill payload에 포함되는 것:

- `skills/dispatcher-skill/SKILL.md`
- `skills/dispatcher-skill/VERSION`
- `skills/dispatcher-skill/CHANGELOG.md`
- `skills/dispatcher-skill/dispatcher_app/`
- `skills/dispatcher-skill/scripts/`
- `skills/dispatcher-skill/requirements.md`
- `skills/dispatcher-skill/memory/` 아래의 선별된 portable 문서

무시되는 local state:

- `skills/dispatcher-skill/data/`
- `skills/dispatcher-skill/dispatcher_app/agents.json`
- SQLite database와 runtime log
- lock, token, PID, socket, local env file
- local/public tunnel URL
- raw prompt, stdout/stderr dump, full transcript, full JSONL record

`skill-migration-memory/`는 이 development container 전용이며 package payload가 아닙니다.
`memory/decisions.md` 같은 development decision log는 package payload가 아닙니다. 배포 전에 필요한 durable/portable 사실만 목적별 memory 문서로 증류합니다.

## Maintainer 확인

Skill root에서 실행합니다.

```bash
python3 scripts/smoke_skill_package.py --repo .
python3 -m py_compile dispatcher_app/*.py
python3 scripts/run_dispatcher_tunnel.py init-status --repo .
python3 scripts/run_dispatcher_tunnel.py reset --repo .
```

이 확인은 runtime service를 시작하지 않습니다. `reset`은 `--confirm-reset`이 없으면 dry run입니다.

## Versioning

현재 version은 `skills/dispatcher-skill/VERSION`에 있습니다. Release history와 bump rule은 `skills/dispatcher-skill/CHANGELOG.md`에 있습니다.
