#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
CLOUDFLARED_DOWNLOAD_BASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"
LOCAL_STATE_DIR = "data"
LOCAL_BIN_DIR = "bin"
ENV_FILE_NAME = "dispatcher.env"
CONFIG_FILE_NAME = "run-dispatcher-tunnel.json"
REQUIREMENTS_FILE_NAME = "requirements.md"
CLOUDFLARED_ENV = "DISPATCHER_CLOUDFLARED"
CLOUDFLARED_INSTALL_DIR_ENV = "DISPATCHER_CLOUDFLARED_INSTALL_DIR"
HELPER_TOKEN_ENV = "DISPATCHER_HELPER_TOKEN"
WORKSPACE_ROOT_ENV = "DISPATCHER_WORKSPACE_ROOT"
CANDIDATE_OPERATOR_KEY_ENV = ("DISPATCHER_OPERATOR_KEY", "CODEX_THREAD_ID")
RECOMMENDED_CLOUDFLARED_INSTALL_DIR = "~/.local/bin"
RECOMMENDED_CLOUDFLARED_PATH = "~/.local/bin/cloudflared"
OPERATOR_BASELINE_BEGIN = "<!-- BEGIN DISPATCHER SKILL OPERATOR BASELINE -->"
OPERATOR_BASELINE_END = "<!-- END DISPATCHER SKILL OPERATOR BASELINE -->"
RESET_CACHE_DIR_NAMES = ("__pycache__", ".pytest_cache")
RESET_BYTECODE_SUFFIXES = (".pyc", ".pyo")
PACKAGE_EXCLUDED_TOP_LEVEL = (LOCAL_STATE_DIR, ".git")
PACKAGE_EXCLUDED_FILES = ("dispatcher_app/agents.json",)
PACKAGE_EXCLUDED_FILE_NAMES = (ENV_FILE_NAME,)
PACKAGE_EXCLUDED_SUFFIXES = (
    *RESET_BYTECODE_SUFFIXES,
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-wal",
    ".db-shm",
    ".log",
    ".jsonl",
    ".pid",
    ".sock",
    ".token",
)
PRESERVED_RUNTIME_STATE_LABELS = (
    "data/",
    "dispatcher_app/agents.json",
    "dispatcher.env",
    "SQLite DB/WAL files",
    "runtime logs",
    "reboot state",
    "request logs",
    "PID/socket files",
    "lock/token files",
    "local tunnel URLs",
    "raw logs",
    "prompts",
    "stdout/stderr dumps",
    "full JSON records",
    "secrets",
)
REQUIRED_REPO_FILES = (
    REQUIREMENTS_FILE_NAME,
    "dispatcher_app/agent_registry.py",
    "dispatcher_app/memory_bootstrap.py",
    "dispatcher_app/server.py",
    "dispatcher_app/dispatcher.py",
    "dispatcher_app/reboot.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start dispatcher_app server, dispatcher loop, reboot watcher, and Cloudflare Quick Tunnel."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "start",
            "init",
            "init-status",
            "reset",
            "restart",
            "restart-server",
            "restart-dispatcher",
            "restart-reboot",
            "url",
            "status",
            "stop",
            "foreground",
            "install-cloudflared",
            "update-skill",
        ],
        default="start",
        help="Operation to run. Use init-status before runtime commands and init after migrating the skill into a new repository.",
    )
    parser.add_argument("--repo", default=None, help="Repository root containing dispatcher_app/server.py.")
    parser.add_argument("--host", default="127.0.0.1", help="Dispatcher bind host.")
    parser.add_argument("--port", default=8000, type=int, help="Requested dispatcher port. Use 0 for any free port.")
    parser.add_argument("--db", default="data/dispatcher.db", help="SQLite database path shared by server and dispatcher.")
    parser.add_argument("--strict-port", action="store_true", help="Fail if the requested port is busy.")
    parser.add_argument("--python", default=sys.executable, help="Python executable for dispatcher_app.server.")
    parser.add_argument("--max-concurrent-tasks", default=1, type=int, help="Maximum concurrent dispatcher tasks.")
    parser.add_argument("--poll-seconds", default=0.5, type=float, help="Dispatcher poll interval.")
    parser.add_argument("--lease-seconds", default=60, type=int, help="Dispatcher task lease duration.")
    parser.add_argument("--cloudflared", default=None, help="Existing cloudflared binary path. init persists it to local state.")
    parser.add_argument(
        "--cloudflared-install-dir",
        default=None,
        help="Directory for install-cloudflared. Defaults to ignored data/bin; ~/.local/bin is recommended for server-local installs.",
    )
    parser.add_argument("--password", default=None, help="Dispatcher login password for init. Prefer --password-file or prompt.")
    parser.add_argument("--password-file", default=None, help="File containing the dispatcher login password for init.")
    parser.add_argument(
        "--operator-key",
        default=None,
        help="Codex operator session handle to store in agents.json during init. Defaults to DISPATCHER_OPERATOR_KEY or CODEX_THREAD_ID when present.",
    )
    parser.add_argument("--no-start", action="store_true", help="For init, write config but do not start tmux/tunnel.")
    parser.add_argument(
        "--install-cloudflared",
        action="store_true",
        help="Install the Linux cloudflared binary into --cloudflared-install-dir or ignored data/bin if missing.",
    )
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="For reset, actually remove ignored local runtime state and generated caches. Without this flag reset is a dry run.",
    )
    parser.add_argument("--source", default=None, help="For update-skill, source dispatcher-skill payload root.")
    parser.add_argument("--candidate", default=None, help="For update-skill, prepared candidate dispatcher-skill root.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="For update-skill, apply the package-file update. Without this flag update-skill is a dry run.",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="For update-skill, skip the non-runtime package smoke check.",
    )
    parser.add_argument(
        "--allow-unsmoked",
        action="store_true",
        help="For update-skill --confirm, allow applying without a passing smoke check.",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="tmux session name. Defaults to <surrounding-repo>-tunnel.",
    )
    parser.add_argument("--replace", action="store_true", help="Replace an existing tmux session on start.")
    parser.add_argument("--url-timeout", default=45, type=float, help="Seconds to wait for a Quick Tunnel URL.")
    return parser.parse_args()


@dataclass(frozen=True)
class UpdatePlan:
    added: list[str]
    modified: list[str]
    removed: list[str]
    unchanged: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.modified or self.removed)


def skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def bundled_cloudflared_path() -> Path:
    return skill_dir() / "bin" / "cloudflared"


def local_state_dir(repo: Path) -> Path:
    return repo / LOCAL_STATE_DIR


def local_bin_dir(repo: Path) -> Path:
    return local_state_dir(repo) / LOCAL_BIN_DIR


def local_cloudflared_path(repo: Path) -> Path:
    return local_bin_dir(repo) / "cloudflared"


def local_env_path(repo: Path) -> Path:
    return local_state_dir(repo) / ENV_FILE_NAME


def local_config_path(repo: Path) -> Path:
    return local_state_dir(repo) / CONFIG_FILE_NAME


def requirements_path(repo: Path) -> Path:
    return repo / REQUIREMENTS_FILE_NAME


def agents_path(repo: Path) -> Path:
    return repo / "dispatcher_app" / "agents.json"


def import_agent_registry(repo: Path):
    repo_entry = str(repo)
    if repo_entry not in sys.path:
        sys.path.insert(0, repo_entry)
    from dispatcher_app.agent_registry import (
        ensure_agents_file,
        read_agent_registry,
        upsert_agent_registry_agent,
    )

    return ensure_agents_file, read_agent_registry, upsert_agent_registry_agent


def import_memory_bootstrap(repo: Path):
    repo_entry = str(repo)
    if repo_entry not in sys.path:
        sys.path.insert(0, repo_entry)
    from dispatcher_app.memory_bootstrap import (
        ensure_memory_compact_checkpoint,
        ensure_role_memory_loaded,
        memory_compact_checkpoint_status,
        role_memory_status,
    )

    return (
        ensure_memory_compact_checkpoint,
        ensure_role_memory_loaded,
        memory_compact_checkpoint_status,
        role_memory_status,
    )


def ensure_agent_registry(repo: Path) -> bool:
    ensure_agents_file, _, _ = import_agent_registry(repo)
    return bool(ensure_agents_file(agents_path(repo)))


def agent_registry_status(repo: Path) -> dict[str, object]:
    path = agents_path(repo)
    exists = path.is_file()
    valid = False
    error = ""
    if exists:
        try:
            _, read_agent_registry, _ = import_agent_registry(repo)
            read_agent_registry(path)
            valid = True
        except Exception as exc:
            error = str(exc)
    return {
        "agents_path": str(path),
        "agents_file_exists": exists,
        "agents_file_valid": valid,
        "agents_error": error,
    }


def operator_key_from_sources(
    explicit_key: str | None,
    environ: dict[str, str] | None = None,
) -> str:
    if explicit_key is not None:
        return explicit_key.strip()
    values = environ if environ is not None else os.environ
    for name in CANDIDATE_OPERATOR_KEY_ENV:
        value = values.get(name, "").strip()
        if value:
            return value
    return ""


def record_operator_key(repo: Path, operator_key: str) -> bool:
    operator_key = operator_key.strip()
    if not operator_key:
        return False
    _, _, upsert_agent_registry_agent = import_agent_registry(repo)
    upsert_agent_registry_agent(
        agents_path(repo),
        "operator",
        "Operator",
        key=operator_key,
        key_status="ready",
    )
    return True


def repo_relative_path(repo: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else repo / path


def cloudflared_download_target(repo: Path, install_dir: str | None = None) -> Path:
    raw_dir = install_dir or os.environ.get(CLOUDFLARED_INSTALL_DIR_ENV)
    if raw_dir:
        base_dir = repo_relative_path(repo, raw_dir)
    else:
        base_dir = local_bin_dir(repo)
    return base_dir / "cloudflared"


def cloudflared_user_bin_commands(repo: Path, port: int = 8000) -> dict[str, str]:
    repo_cd = shlex.quote(str(repo))
    install_dir = RECOMMENDED_CLOUDFLARED_INSTALL_DIR
    binary_path = RECOMMENDED_CLOUDFLARED_PATH
    return {
        "install": (
            f"cd {repo_cd} && "
            f"python3 scripts/run_dispatcher_tunnel.py install-cloudflared --repo . "
            f"--cloudflared-install-dir {install_dir}"
        ),
        "init": (
            f"cd {repo_cd} && "
            f"python3 scripts/run_dispatcher_tunnel.py init --repo . "
            f"--cloudflared {binary_path} --port {port}"
        ),
    }


def display_path(path: str | Path) -> str:
    path_obj = Path(path).expanduser()
    try:
        home = Path.home().resolve()
        resolved = path_obj.resolve(strict=False)
        relative = resolved.relative_to(home)
        return f"~/{relative.as_posix()}"
    except (OSError, ValueError):
        return str(path_obj)


def repo_install_kind(repo: Path) -> str:
    if repo.name == "dispatcher-skill" and repo.parent.name == "skills":
        if repo.parent.parent.name == ".agents":
            return "project-local-npx"
        return "source-repo"
    return "standalone"


def repo_arg_from_workspace_root(repo: Path) -> str:
    workspace_root = workspace_root_for_path(repo)
    try:
        return repo.relative_to(workspace_root).as_posix()
    except ValueError:
        return str(repo)


def init_command(
    *,
    script_path: str,
    repo_arg: str,
    cloudflared_path: str,
    port: int = 8000,
    password_arg: str = "--password-file <password-file>",
) -> str:
    return (
        f"python3 {script_path} init --repo {repo_arg} "
        f"--cloudflared {cloudflared_path} --port {port} {password_arg}"
    )


def init_status_next_steps(repo: Path, cloudflared_status: dict[str, object]) -> dict[str, object]:
    detected_path = str(cloudflared_status.get("cloudflared_path") or "")
    detected_display = display_path(detected_path) if detected_path else ""
    current_repo_arg = repo_arg_from_workspace_root(repo)
    current_script = f"{current_repo_arg}/scripts/run_dispatcher_tunnel.py"
    cloudflared_arg = shlex.quote(detected_path) if detected_path else "<cloudflared-path>"
    current_command = init_command(
        script_path=shlex.quote(current_script),
        repo_arg=shlex.quote(current_repo_arg),
        cloudflared_path=cloudflared_arg,
    )
    skill_root_command = init_command(
        script_path="scripts/run_dispatcher_tunnel.py",
        repo_arg=".",
        cloudflared_path=cloudflared_arg,
    )
    project_local_npx_command = init_command(
        script_path=".agents/skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py",
        repo_arg=".agents/skills/dispatcher-skill",
        cloudflared_path=cloudflared_arg,
    )
    source_repo_command = init_command(
        script_path="skills/dispatcher-skill/scripts/run_dispatcher_tunnel.py",
        repo_arg="skills/dispatcher-skill",
        cloudflared_path=cloudflared_arg,
    )
    return {
        "cloudflared_detected": bool(cloudflared_status.get("cloudflared_available")),
        "cloudflared_path": detected_display,
        "current_install_kind": repo_install_kind(repo),
        "current_repo_arg_from_workspace_root": current_repo_arg,
        "commands": {
            "from_skill_root": skill_root_command,
            "from_current_workspace_root": current_command,
            "project_local_npx_from_repo_root": project_local_npx_command,
            "source_repo_from_repo_root": source_repo_command,
        },
        "password_inputs": [
            {
                "method": "--password-file",
                "recommended": True,
                "usage": "put the password in a temporary local file and pass --password-file <password-file>",
            },
            {
                "method": "DISPATCHER_PASSWORD",
                "recommended": False,
                "usage": "set DISPATCHER_PASSWORD in the process environment before running init",
            },
            {
                "method": "TTY prompt",
                "recommended": False,
                "usage": "omit --password and --password-file in an interactive terminal",
            },
        ],
        "port": {
            "default": 8000,
            "fallback": "if the requested port is busy, init chooses a free fallback unless --strict-port is supplied",
            "random": "use --port 0 to request any free port; init --no-start requires a concrete port",
        },
        "session": {
            "default": default_init_session(repo),
            "rule": "<surrounding-repo-name>-tunnel based on the repository above .agents/ or skills/",
        },
        "secret_policy": "passwords, helper tokens, and tunnel URLs are not printed by init-status",
    }


def cloudflared_missing_message(repo: Path, *, detail: str | None = None, port: int = 8000) -> str:
    commands = cloudflared_user_bin_commands(repo, port)
    lines = []
    if detail:
        lines.append(detail)
    else:
        lines.append(
            "cloudflared was not found. Install it on PATH, pass --cloudflared /path/to/cloudflared, "
            f"set {CLOUDFLARED_ENV}, run install-cloudflared, or re-run with --install-cloudflared."
        )
    lines.extend(
        [
            "Suggested server-local install:",
            commands["install"],
            "Then initialize with:",
            commands["init"],
        ]
    )
    return "\n".join(lines)


def session_repo_name(repo: Path) -> str:
    workspace_root = workspace_root_for_path(repo)
    return workspace_root.name or repo.name


def default_init_session(repo: Path) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", session_repo_name(repo)).strip("-") or "dispatcher"
    return f"{slug}-tunnel"


def print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def read_shell_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError:
            continue
        values[key] = parsed[0] if parsed else ""
    return values


def generated_helper_token() -> str:
    return secrets.token_urlsafe(32)


def helper_token_from_sources(repo: Path) -> str:
    existing = read_shell_env(local_env_path(repo)).get(HELPER_TOKEN_ENV, "")
    return os.environ.get(HELPER_TOKEN_ENV) or existing or generated_helper_token()


def ensure_local_helper_token(repo: Path) -> str:
    env_path = local_env_path(repo)
    if not env_path.is_file():
        return os.environ.get(HELPER_TOKEN_ENV, "")
    values = read_shell_env(env_path)
    if values.get(HELPER_TOKEN_ENV):
        return values[HELPER_TOKEN_ENV]
    token = os.environ.get(HELPER_TOKEN_ENV) or generated_helper_token()
    current = env_path.read_text(encoding="utf-8")
    separator = "" if not current or current.endswith("\n") else "\n"
    with env_path.open("a", encoding="utf-8") as file:
        file.write(f"{separator}export {HELPER_TOKEN_ENV}={shlex.quote(token)}\n")
    env_path.chmod(0o600)
    return token


def option_was_supplied(name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in sys.argv[1:])


def apply_local_defaults(args: argparse.Namespace, repo: Path) -> None:
    values = read_shell_env(local_env_path(repo))
    if not values:
        return
    if not option_was_supplied("--host") and values.get("DISPATCHER_HOST"):
        args.host = values["DISPATCHER_HOST"]
    if not option_was_supplied("--port") and values.get("DISPATCHER_PORT"):
        args.port = int(values["DISPATCHER_PORT"])
    if not option_was_supplied("--db") and values.get("DISPATCHER_DB"):
        args.db = values["DISPATCHER_DB"]
    if not option_was_supplied("--session") and values.get("DISPATCHER_SESSION"):
        args.session = values["DISPATCHER_SESSION"]
    if not option_was_supplied("--python") and values.get("DISPATCHER_PYTHON"):
        args.python = values["DISPATCHER_PYTHON"]
    if (
        not args.install_cloudflared
        and not option_was_supplied("--cloudflared")
        and values.get(CLOUDFLARED_ENV)
    ):
        args.cloudflared = values[CLOUDFLARED_ENV]
    if not option_was_supplied("--cloudflared-install-dir") and values.get(CLOUDFLARED_INSTALL_DIR_ENV):
        args.cloudflared_install_dir = values[CLOUDFLARED_INSTALL_DIR_ENV]


def apply_repo_defaults(args: argparse.Namespace, repo: Path) -> None:
    if not option_was_supplied("--session") and not args.session:
        args.session = default_init_session(repo)


def dispatcher_env(repo: Path) -> dict[str, str]:
    ensure_local_helper_token(repo)
    env = os.environ.copy()
    env.update(read_shell_env(local_env_path(repo)))
    env.setdefault(WORKSPACE_ROOT_ENV, workspace_root_value())
    return env


def workspace_root_value() -> str:
    raw = os.environ.get(WORKSPACE_ROOT_ENV)
    if raw:
        return str(workspace_root_for_path(Path(raw).expanduser().resolve()))
    return str(workspace_root_for_path(Path.cwd().resolve()))


def workspace_root_for_path(path: Path) -> Path:
    path = path.resolve()
    if path.name == "dispatcher-skill" and path.parent.name == "skills":
        skills_container = path.parent.parent
        if skills_container.name == ".agents":
            return skills_container.parent.resolve()
        return skills_container.resolve()
    return path


def agents_baseline_path(repo: Path) -> Path:
    return workspace_root_for_path(repo) / "AGENTS.md"


def operator_baseline_block(repo: Path) -> str:
    repo_arg = repo_arg_from_workspace_root(repo)
    quoted_repo = shlex.quote(repo_arg)
    return "\n".join(
        [
            OPERATOR_BASELINE_BEGIN,
            "## Dispatcher Skill Operator Baseline",
            "",
            f"- This repository uses the dispatcher skill at `{repo_arg}`; run dispatcher helper commands from that skill root.",
            "- The operator is outside the dispatcher task plane and owns maintenance, runtime inspection, verification, and app evolution.",
            "- Start operator work with runtime audit and inspect only relevant recent audit records; do not copy raw logs, full transcripts, prompts, secrets, stdout/stderr dumps, local or public tunnel URLs, full JSON records, DB contents, or runtime state into chat, memory, or docs.",
            f"- Before substantive operator work, acquire the global runtime lock from the skill root, for example `cd {quoted_repo} && python3 -m dispatcher_app.runtime_lock --db data/dispatcher.db acquire --owner-plane operator --owner-id operator --lease-seconds 900 --token-file data/operator_runtime_lock.token`; on first install, required memory/requirements reads plus non-runtime `init-status` or smoke checks are allowed before this lock.",
            "- After audit and lock acquisition, process unread handoffs addressed to the current agent before other substantive work, cataloging only durable facts before ack or purge.",
            "- Managers must not call tmux, Cloudflare, tunnel scripts, restarts, or `dispatcher_app.reboot request` directly; manager runtime reloads use a final `REBOOT_AFTER_TASK ...` marker after implementation, docs or memory updates, and verification.",
            "- Passwords, helper tokens, lock tokens, and tunnel URLs are local runtime secrets and must not be printed or copied into tracked docs or durable memory.",
            OPERATOR_BASELINE_END,
            "",
        ]
    )


def replace_legacy_operator_baseline(text: str, block: str) -> str | None:
    heading = re.search(r"(?im)^#{1,6}\s+Dispatcher Skill Operator Baseline\s*$", text)
    if not heading:
        return None
    heading_line = text[heading.start() : heading.end()]
    level = len(heading_line) - len(heading_line.lstrip("#"))
    end = len(text)
    for match in re.finditer(r"(?m)^#{1,6}\s+\S.*$", text[heading.end() :]):
        candidate = heading.end() + match.start()
        candidate_line = text[candidate : heading.end() + match.end()]
        candidate_level = len(candidate_line) - len(candidate_line.lstrip("#"))
        if candidate_level <= level:
            end = candidate
            break
    prefix = text[: heading.start()].rstrip()
    suffix = text[end:].lstrip("\n")
    return join_markdown_sections(prefix, block.rstrip(), suffix)


def join_markdown_sections(*sections: str) -> str:
    present = [section.strip("\n") for section in sections if section.strip()]
    if not present:
        return ""
    return "\n\n".join(present) + "\n"


def update_operator_baseline_text(text: str, block: str) -> str:
    start = text.find(OPERATOR_BASELINE_BEGIN)
    end = text.find(OPERATOR_BASELINE_END)
    if start != -1 and end != -1 and start < end:
        end += len(OPERATOR_BASELINE_END)
        prefix = text[:start].rstrip()
        suffix = text[end:].lstrip("\n")
        return join_markdown_sections(prefix, block.rstrip(), suffix)

    legacy = replace_legacy_operator_baseline(text, block)
    if legacy is not None:
        return legacy

    return join_markdown_sections(text, block.rstrip())


def ensure_operator_baseline(repo: Path) -> str:
    path = agents_baseline_path(repo)
    block = operator_baseline_block(repo)
    try:
        original = path.read_text(encoding="utf-8") if path.is_file() else ""
        updated = update_operator_baseline_text(original, block)
        if updated == original:
            return "verified"
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Could not write Dispatcher Skill Operator Baseline to {path}: {exc.strerror or exc}") from exc
    return "updated" if original else "created"


def workspace_root_fallback_command() -> str:
    value = shlex.quote(workspace_root_value())
    return f'if [ -z "${{{WORKSPACE_ROOT_ENV}:-}}" ]; then export {WORKSPACE_ROOT_ENV}={value}; fi'


def command_with_repo_env(repo: Path, command: str) -> str:
    env_path = local_env_path(repo)
    workspace_fallback = workspace_root_fallback_command()
    if not env_path.is_file():
        return f"{workspace_fallback}; {command}"
    ensure_local_helper_token(repo)
    quoted = shlex.quote(str(env_path))
    return f"set -a; . {quoted}; set +a; {workspace_fallback}; {command}"


def write_local_state_files(
    repo: Path,
    args: argparse.Namespace,
    *,
    password: str,
    port: int,
) -> None:
    state_dir = local_state_dir(repo)
    state_dir.mkdir(parents=True, exist_ok=True)
    env_path = local_env_path(repo)
    helper_token = helper_token_from_sources(repo)
    env_lines = [
        "# Local dispatcher skill runtime settings. This directory is ignored by Git.",
        f"export DISPATCHER_REPO={shlex.quote(str(repo))}",
        f"export {WORKSPACE_ROOT_ENV}={shlex.quote(workspace_root_value())}",
        f"export DISPATCHER_HOST={shlex.quote(args.host)}",
        f"export DISPATCHER_PORT={shlex.quote(str(port))}",
        f"export DISPATCHER_DB={shlex.quote(args.db)}",
        f"export DISPATCHER_SESSION={shlex.quote(args.session)}",
        f"export DISPATCHER_PYTHON={shlex.quote(args.python)}",
        f"export DISPATCHER_PASSWORD={shlex.quote(password)}",
        f"export {HELPER_TOKEN_ENV}={shlex.quote(helper_token)}",
    ]
    if args.cloudflared:
        env_lines.append(f"export {CLOUDFLARED_ENV}={shlex.quote(str(args.cloudflared))}")
    if args.cloudflared_install_dir:
        env_lines.append(f"export {CLOUDFLARED_INSTALL_DIR_ENV}={shlex.quote(str(args.cloudflared_install_dir))}")
    env_lines.append("")
    env_path.write_text("\n".join(env_lines), encoding="utf-8")
    env_path.chmod(0o600)

    cloudflared_path = str(Path(args.cloudflared).expanduser()) if args.cloudflared else ""
    config = {
        "repo": str(repo),
        "workspace_root": workspace_root_value(),
        "host": args.host,
        "port": port,
        "db": args.db,
        "session": args.session,
        "python": args.python,
        "password_set": True,
        "helper_token_set": bool(helper_token),
        "cloudflared": cloudflared_path,
        "cloudflared_install_dir": str(args.cloudflared_install_dir or ""),
        "requirements_path": str(requirements_path(repo)),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    local_config_path(repo).write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_init_password(args: argparse.Namespace, repo: Path) -> str:
    if args.password and args.password_file:
        raise SystemExit("Use either --password or --password-file, not both.")
    if args.password_file:
        password = Path(args.password_file).expanduser().read_text(encoding="utf-8").strip()
    elif args.password is not None:
        password = args.password
    elif os.environ.get("DISPATCHER_PASSWORD"):
        password = str(os.environ["DISPATCHER_PASSWORD"])
    else:
        existing = read_shell_env(local_env_path(repo))
        password = existing.get("DISPATCHER_PASSWORD", "")

    if not password and sys.stdin.isatty():
        password = getpass.getpass("Dispatcher login password: ")
        confirm = getpass.getpass("Confirm dispatcher login password: ")
        if password != confirm:
            raise SystemExit("Password confirmation did not match.")

    if not password:
        raise SystemExit("init requires --password, --password-file, DISPATCHER_PASSWORD, an existing env file, or a TTY prompt.")
    return password


def validate_repo_requirements(repo: Path) -> None:
    missing = [path for path in REQUIRED_REPO_FILES if not (repo / path).is_file()]
    if missing:
        formatted = ", ".join(missing)
        raise SystemExit(f"Repository is missing required dispatcher files: {formatted}")


def reset_targets(repo: Path) -> list[Path]:
    targets: list[Path] = []
    state_dir = local_state_dir(repo)
    if state_dir.exists():
        targets.append(state_dir)

    for root, dir_names, file_names in os.walk(repo):
        root_path = Path(root)
        if root_path == repo and LOCAL_STATE_DIR in dir_names:
            dir_names.remove(LOCAL_STATE_DIR)

        for dir_name in list(dir_names):
            if dir_name not in RESET_CACHE_DIR_NAMES:
                continue
            targets.append(root_path / dir_name)
            dir_names.remove(dir_name)

        for file_name in file_names:
            if file_name.endswith(RESET_BYTECODE_SUFFIXES):
                targets.append(root_path / file_name)

    seen: set[Path] = set()
    unique_targets: list[Path] = []
    for target in targets:
        entry = target if target.is_absolute() else repo / target
        try:
            key = entry.resolve(strict=False)
        except OSError:
            key = entry.absolute()
        if key in seen:
            continue
        seen.add(key)
        unique_targets.append(entry)
    return sorted(unique_targets, key=lambda path: str(path))


def require_reset_target_inside_repo(repo: Path, target: Path) -> None:
    repo_root = repo.resolve()
    entry = target if target.is_absolute() else repo / target
    if entry.resolve(strict=False) == repo_root:
        raise SystemExit(f"Refusing to reset repository root: {entry}")
    try:
        entry.parent.resolve(strict=False).relative_to(repo_root)
    except ValueError as exc:
        raise SystemExit(f"Refusing to reset a path outside the repository: {entry}") from exc


def relative_display_path(repo: Path, target: Path) -> str:
    try:
        return str(target.relative_to(repo))
    except ValueError:
        return str(target)


def remove_reset_target(repo: Path, target: Path) -> None:
    require_reset_target_inside_repo(repo, target)
    if not target.exists() and not target.is_symlink():
        return
    if target.is_symlink() or target.is_file():
        target.unlink()
        return
    if target.is_dir():
        shutil.rmtree(target)
        return
    raise SystemExit(f"Refusing to reset unsupported path type: {target}")


def reset_for_deployment(args: argparse.Namespace, repo: Path) -> int:
    validate_repo_requirements(repo)
    targets = reset_targets(repo)
    print(f"Deployment reset target: {repo}", flush=True)
    if not targets:
        print("No ignored local runtime state or generated caches were found.", flush=True)
        return 0

    action = "Removing" if args.confirm_reset else "Would remove"
    print(f"{action}:", flush=True)
    for target in targets:
        print(f"- {relative_display_path(repo, target)}", flush=True)

    if not args.confirm_reset:
        print("Dry run only. Re-run with --confirm-reset to delete these paths.", flush=True)
        return 0

    for target in targets:
        remove_reset_target(repo, target)
    print(f"Removed {len(targets)} reset target(s).", flush=True)
    return 0


def resolve_required_skill_root(raw_path: str | None, label: str) -> Path:
    if not raw_path:
        raise SystemExit(f"update-skill requires {label}.")
    root = Path(raw_path).expanduser().resolve()
    validate_repo_requirements(root)
    return root


def same_resolved_path(left: Path, right: Path) -> bool:
    return left.resolve(strict=False) == right.resolve(strict=False)


def package_path_excluded(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return True
    if parts[0] in PACKAGE_EXCLUDED_TOP_LEVEL:
        return True
    if any(part in RESET_CACHE_DIR_NAMES for part in parts):
        return True
    normalized = relative_path.as_posix()
    if normalized in PACKAGE_EXCLUDED_FILES:
        return True
    if relative_path.name in PACKAGE_EXCLUDED_FILE_NAMES:
        return True
    return relative_path.name.endswith(PACKAGE_EXCLUDED_SUFFIXES)


def iter_package_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        relative_dir = current_path.relative_to(root)
        kept_dir_names = []
        for dir_name in dir_names:
            relative = relative_dir / dir_name if str(relative_dir) != "." else Path(dir_name)
            if package_path_excluded(relative):
                continue
            kept_dir_names.append(dir_name)
        dir_names[:] = kept_dir_names

        for file_name in file_names:
            path = current_path / file_name
            relative = path.relative_to(root)
            if package_path_excluded(relative):
                continue
            files[relative.as_posix()] = path
    return files


def files_equal(left: Path, right: Path) -> bool:
    try:
        left_stat = left.stat()
        right_stat = right.stat()
    except OSError:
        return False
    if left_stat.st_size != right_stat.st_size:
        return False
    try:
        return left.read_bytes() == right.read_bytes()
    except OSError:
        return False


def build_update_plan(candidate: Path, installed: Path) -> UpdatePlan:
    candidate_files = iter_package_files(candidate)
    installed_files = iter_package_files(installed)
    candidate_keys = set(candidate_files)
    installed_keys = set(installed_files)

    added = sorted(candidate_keys - installed_keys)
    removed = sorted(installed_keys - candidate_keys)
    modified = sorted(
        relative for relative in candidate_keys & installed_keys if not files_equal(candidate_files[relative], installed_files[relative])
    )
    unchanged = sorted(relative for relative in candidate_keys & installed_keys if relative not in set(modified))
    return UpdatePlan(added=added, modified=modified, removed=removed, unchanged=unchanged)


def read_text_if_exists(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def changelog_top_release(root: Path) -> str:
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            return stripped
    return ""


def parsed_version(value: str) -> tuple[int, ...] | None:
    if not value:
        return None
    parts = value.strip().split(".")
    parsed: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        parsed.append(int(part))
    return tuple(parsed)


def version_warnings(installed: Path, candidate: Path) -> list[str]:
    installed_version = read_text_if_exists(installed / "VERSION")
    candidate_version = read_text_if_exists(candidate / "VERSION")
    top_release = changelog_top_release(candidate)
    warnings: list[str] = []
    if not candidate_version:
        warnings.append("candidate VERSION is missing or empty")
    if candidate_version and top_release and candidate_version not in top_release:
        warnings.append("candidate CHANGELOG.md top release does not mention candidate VERSION")
    if candidate_version and not top_release:
        warnings.append("candidate CHANGELOG.md has no release heading")
    installed_parsed = parsed_version(installed_version)
    candidate_parsed = parsed_version(candidate_version)
    if installed_parsed is not None and candidate_parsed is not None and candidate_parsed <= installed_parsed:
        relation = "equal to" if candidate_parsed == installed_parsed else "older than"
        warnings.append(f"candidate VERSION is {relation} installed VERSION")
    return warnings


def print_version_summary(installed: Path, candidate: Path) -> None:
    installed_version = read_text_if_exists(installed / "VERSION") or "(missing)"
    candidate_version = read_text_if_exists(candidate / "VERSION") or "(missing)"
    top_release = changelog_top_release(candidate) or "(missing)"
    print(f"Version: installed {installed_version} -> candidate {candidate_version}", flush=True)
    print(f"Candidate changelog top release: {top_release}", flush=True)
    for warning in version_warnings(installed, candidate):
        print(f"WARN {warning}", flush=True)


def print_reset_dry_run_summary(candidate: Path) -> None:
    targets = reset_targets(candidate)
    print(f"Candidate reset dry run: {candidate}", flush=True)
    if not targets:
        print("- no ignored runtime state or generated caches found", flush=True)
        return
    for target in targets:
        print(f"- would remove {relative_display_path(candidate, target)}", flush=True)


def print_update_plan(plan: UpdatePlan) -> None:
    print(
        "Package diff: "
        f"{len(plan.added)} add, {len(plan.modified)} modify, {len(plan.removed)} remove, {len(plan.unchanged)} unchanged",
        flush=True,
    )
    for label, paths in (("ADD", plan.added), ("MODIFY", plan.modified), ("REMOVE", plan.removed)):
        if not paths:
            continue
        print(f"{label}:", flush=True)
        for path in paths:
            print(f"- {path}", flush=True)


def print_preserved_runtime_state(installed: Path) -> None:
    print("Runtime state preserved:", flush=True)
    for label in PRESERVED_RUNTIME_STATE_LABELS:
        print(f"- {label}", flush=True)
    existing = [path for path in reset_targets(installed) if path.exists() or path.is_symlink()]
    agents = agents_path(installed)
    if agents.exists() or agents.is_symlink():
        existing.append(agents)
    if existing:
        print("Existing preserved paths:", flush=True)
        for path in sorted(existing, key=lambda item: str(item)):
            print(f"- {relative_display_path(installed, path)}", flush=True)


def run_update_smoke(candidate: Path, python: str) -> None:
    smoke_script = candidate / "scripts" / "smoke_skill_package.py"
    if not smoke_script.is_file():
        raise SystemExit(f"Candidate smoke check helper is missing: {smoke_script}")
    completed = subprocess.run(
        [python, str(smoke_script), "--repo", str(candidate), "--python", python],
        cwd=candidate,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"Candidate package smoke check failed with exit {completed.returncode}.")
    print("Candidate package smoke check passed.", flush=True)


def ensure_update_paths_safe(args: argparse.Namespace, installed: Path, source: Path, candidate: Path) -> None:
    if args.confirm and not args.candidate:
        raise SystemExit("update-skill --confirm requires an explicit --candidate path.")
    if args.confirm and same_resolved_path(candidate, installed):
        raise SystemExit("Refusing confirmed update because --candidate points at the installed skill root.")
    if args.confirm and same_resolved_path(source, installed):
        raise SystemExit("Refusing confirmed update because --source points at the installed skill root.")
    if same_resolved_path(candidate, installed):
        print("WARN candidate points at the installed skill root; dry-run only is allowed.", flush=True)
    if same_resolved_path(source, installed):
        print("WARN source points at the installed skill root; dry-run only is allowed.", flush=True)


def copy_package_file(source: Path, destination: Path) -> None:
    if source.is_symlink():
        target = os.readlink(source)
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        os.symlink(target, destination)
        return
    shutil.copy2(source, destination)


def remove_package_file(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        raise OSError(f"Refusing to remove non-file package path: {path}")


def prune_empty_dirs(root: Path, changed_paths: list[str]) -> None:
    candidates = sorted({(root / path).parent for path in changed_paths}, key=lambda item: len(item.parts), reverse=True)
    for directory in candidates:
        while directory != root and directory.exists():
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent


def apply_update_plan(plan: UpdatePlan, candidate: Path, installed: Path) -> None:
    if not plan.changed:
        print("No package file changes to apply.", flush=True)
        return

    changed_paths = plan.added + plan.modified + plan.removed
    with tempfile.TemporaryDirectory(prefix="dispatcher-skill-update-", dir=str(installed.parent)) as temp_dir:
        temp_root = Path(temp_dir)
        staged_root = temp_root / "staged"
        backup_root = temp_root / "backup"

        for relative in plan.added + plan.modified:
            source = candidate / relative
            staged = staged_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            copy_package_file(source, staged)

        for relative in plan.modified + plan.removed:
            destination = installed / relative
            if not destination.exists() and not destination.is_symlink():
                continue
            if not (destination.is_file() or destination.is_symlink()):
                raise SystemExit(f"Refusing to replace non-file package path: {relative}")
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            copy_package_file(destination, backup)

        try:
            for relative in plan.removed:
                remove_package_file(installed / relative)
            for relative in plan.added + plan.modified:
                destination = installed / relative
                if destination.exists() and not (destination.is_file() or destination.is_symlink()):
                    raise OSError(f"Refusing to replace non-file package path: {destination}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_package_file(staged_root / relative, destination)
            prune_empty_dirs(installed, plan.removed)
        except Exception:
            for relative in plan.added:
                destination = installed / relative
                if destination.exists() or destination.is_symlink():
                    remove_package_file(destination)
            for relative in plan.modified + plan.removed:
                backup = backup_root / relative
                if not backup.exists() and not backup.is_symlink():
                    continue
                destination = installed / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy_package_file(backup, destination)
            raise

    print(
        f"Applied package update: {len(plan.added)} added, {len(plan.modified)} modified, {len(plan.removed)} removed.",
        flush=True,
    )


def restart_guidance(plan: UpdatePlan) -> str:
    changed = plan.added + plan.modified + plan.removed
    if not changed:
        return "no restart"
    if any(path.startswith("dispatcher_app/static/") or path.startswith("dispatcher_app/templates/") for path in changed):
        if not any(
            path.startswith("dispatcher_app/")
            and not (path.startswith("dispatcher_app/static/") or path.startswith("dispatcher_app/templates/"))
            for path in changed
        ):
            return "restart-server"
    if any(path.startswith("dispatcher_app/") or path == "SKILL.md" or path.startswith("memory/") for path in changed):
        return "restart-dispatcher"
    return "restart-server"


def update_skill(args: argparse.Namespace, installed: Path) -> int:
    validate_repo_requirements(installed)
    source = resolve_required_skill_root(args.source, "--source")
    candidate = resolve_required_skill_root(args.candidate or args.source, "--candidate")
    ensure_update_paths_safe(args, installed, source, candidate)

    print("Skill update helper", flush=True)
    print(f"Installed root: {installed}", flush=True)
    print(f"Source root: {source}", flush=True)
    print(f"Candidate root: {candidate}", flush=True)
    print("Mode: confirm" if args.confirm else "Mode: dry-run", flush=True)

    print_version_summary(installed, candidate)
    print_reset_dry_run_summary(candidate)
    plan = build_update_plan(candidate, installed)
    print_update_plan(plan)
    print_preserved_runtime_state(installed)

    smoke_passed = False
    if args.skip_smoke:
        print("Candidate package smoke check skipped.", flush=True)
    else:
        run_update_smoke(candidate, args.python)
        smoke_passed = True

    print("Post-copy validation:", flush=True)
    print(f"- python3 scripts/run_dispatcher_tunnel.py init-status --repo {shlex.quote(str(installed))}", flush=True)
    print(f"Restart guidance: {restart_guidance(plan)} after validation if runtime code was changed.", flush=True)

    if not args.confirm:
        print("Dry run only. Re-run with --confirm to apply package file changes.", flush=True)
        return 0
    if not smoke_passed and not args.allow_unsmoked:
        raise SystemExit("Refusing confirmed update without a passing smoke check. Use --allow-unsmoked to override.")

    apply_update_plan(plan, candidate, installed)
    return 0


def cloudflared_availability(repo: Path, path_arg: str | None) -> dict[str, object]:
    try:
        path = find_cloudflared(repo, path_arg)
    except SystemExit:
        return {"cloudflared_available": False, "cloudflared_path": ""}
    return {"cloudflared_available": True, "cloudflared_path": path}


def initialization_status(repo: Path) -> dict[str, object]:
    missing_files = [path for path in REQUIRED_REPO_FILES if not (repo / path).is_file()]
    agents_status = agent_registry_status(repo)
    try:
        _, _, memory_compact_checkpoint_status, role_memory_status = import_memory_bootstrap(repo)
        operator_memory_status = role_memory_status(repo, "operator")
        memory_compact_status = memory_compact_checkpoint_status(repo)
    except Exception as exc:
        operator_memory_status = {
            "role_key": "operator",
            "ok": False,
            "missing": [],
            "documents": [],
            "error": str(exc),
        }
        memory_compact_status = {
            "ok": False,
            "checked_files": 0,
            "issues": [{"path": "memory", "line": 0, "reason": str(exc)}],
        }
    env_path = local_env_path(repo)
    config_path = local_config_path(repo)
    env_values = read_shell_env(env_path)
    required_settings = (
        "DISPATCHER_REPO",
        "DISPATCHER_HOST",
        "DISPATCHER_PORT",
        "DISPATCHER_DB",
        "DISPATCHER_SESSION",
        "DISPATCHER_PYTHON",
        "DISPATCHER_PASSWORD",
        HELPER_TOKEN_ENV,
    )
    missing_settings = [key for key in required_settings if not env_values.get(key)]
    cloudflared_config = env_values.get(CLOUDFLARED_ENV, "")
    cloudflared_install_dir = env_values.get(CLOUDFLARED_INSTALL_DIR_ENV, "")
    cloudflared_status = cloudflared_availability(repo, cloudflared_config or None)
    cloudflared_commands = cloudflared_user_bin_commands(repo)
    next_init = init_status_next_steps(repo, cloudflared_status)
    return {
        "initialized": (
            not missing_files
            and bool(agents_status["agents_file_valid"])
            and env_path.is_file()
            and not missing_settings
        ),
        "repo": str(repo),
        "workspace_root": env_values.get(WORKSPACE_ROOT_ENV, ""),
        "requirements_path": str(requirements_path(repo)),
        "requirements_file_exists": requirements_path(repo).is_file(),
        "env_path": str(env_path),
        "config_path": str(config_path),
        "env_file_exists": env_path.is_file(),
        "config_file_exists": config_path.is_file(),
        "missing_files": missing_files,
        "missing_settings": missing_settings,
        "operator_memory_ready": bool(operator_memory_status.get("ok")),
        "operator_memory": operator_memory_status,
        "memory_compact_ready": bool(memory_compact_status.get("ok")),
        "memory_compact_checkpoint": memory_compact_status,
        "host": env_values.get("DISPATCHER_HOST", ""),
        "port": env_values.get("DISPATCHER_PORT", ""),
        "session": env_values.get("DISPATCHER_SESSION", ""),
        "password_set": bool(env_values.get("DISPATCHER_PASSWORD")),
        "helper_token_set": bool(env_values.get(HELPER_TOKEN_ENV)),
        "cloudflared_config": cloudflared_config,
        "cloudflared_install_dir": cloudflared_install_dir or str(local_bin_dir(repo)),
        "cloudflared_user_bin_install_command": cloudflared_commands["install"],
        "cloudflared_user_bin_init_command": cloudflared_commands["init"],
        "detected_workspace_root": str(workspace_root_for_path(repo)),
        "next_init": next_init,
        **agents_status,
        **cloudflared_status,
    }


def print_init_status(repo: Path) -> int:
    print_json(initialization_status(repo))
    return 0


def cloudflared_download_url(
    system: str | None = None,
    machine: str | None = None,
) -> str:
    system_name = (system or platform.system()).lower()
    machine_name = (machine or platform.machine()).lower()
    if system_name != "linux":
        raise SystemExit(f"install-cloudflared supports Linux only, not {system_name or 'unknown'}.")

    arch_by_machine = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    arch = arch_by_machine.get(machine_name)
    if arch is None:
        raise SystemExit(f"Unsupported Linux architecture for cloudflared install: {machine_name or 'unknown'}.")
    return f"{CLOUDFLARED_DOWNLOAD_BASE}/cloudflared-linux-{arch}"


def install_cloudflared(repo: Path, install_dir: str | None = None) -> Path:
    url = cloudflared_download_url()
    target = cloudflared_download_target(repo, install_dir)
    if target.exists():
        if not target.is_file():
            raise SystemExit(f"cloudflared install target exists but is not a file: {target}")
        target.chmod(target.stat().st_mode | 0o111)
        print(f"cloudflared already present at {target}", flush=True)
        return target.resolve()

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.download")
    try:
        with urlopen(url, timeout=120) as response:
            data = response.read()
        if not data:
            raise SystemExit(f"Downloaded empty cloudflared binary from {url}")
        temp_path.write_bytes(data)
        temp_path.chmod(0o755)
        temp_path.replace(target)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    print(f"Installed cloudflared at {target}", flush=True)
    return target.resolve()


def find_repo(path_arg: str | None) -> Path:
    candidates: list[Path] = []
    if path_arg:
        candidates.append(Path(path_arg).expanduser().resolve())
    cwd = Path.cwd().resolve()
    candidates.extend([skill_dir(), cwd, *cwd.parents])

    for candidate in candidates:
        if (candidate / "dispatcher_app" / "server.py").is_file():
            return candidate
        for nested in (
            candidate / ".agents" / "skills" / "dispatcher-skill",
            candidate / "skills" / "dispatcher-skill",
        ):
            if (nested / "dispatcher_app" / "server.py").is_file():
                return nested
    raise SystemExit("Could not find dispatcher_app/server.py. Pass --repo /path/to/repo.")


def resolve_command_repo(args: argparse.Namespace) -> Path:
    if args.command == "update-skill" and args.repo:
        repo = Path(args.repo).expanduser().resolve()
        validate_repo_requirements(repo)
        return repo
    return find_repo(args.repo)


def find_cloudflared(repo: Path, path_arg: str | None, *, port: int = 8000) -> str:
    if path_arg:
        configured_path = repo_relative_path(repo, path_arg)
        if configured_path.is_file() and os.access(configured_path, os.X_OK):
            return str(configured_path.resolve())
        raise SystemExit(
            cloudflared_missing_message(
                repo,
                detail=f"Configured cloudflared path is not an executable file: {configured_path}",
                port=port,
            )
        )

    candidates = [
        local_cloudflared_path(repo),
    ]
    found = shutil.which("cloudflared")
    if found:
        candidates.append(Path(found))
    candidates.append(bundled_cloudflared_path())

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    raise SystemExit(cloudflared_missing_message(repo, port=port))


def ss_local_port(field: str) -> int | None:
    field = field.strip()
    if not field:
        return None
    if field.startswith("["):
        _, separator, port_text = field.rpartition("]:")
        if not separator:
            return None
    else:
        _, separator, port_text = field.rpartition(":")
        if not separator:
            return None
    if not port_text.isdigit():
        return None
    return int(port_text)


def ss_occupied_ports() -> set[int] | None:
    try:
        result = subprocess.run(
            ["ss", "-H", "-tuln"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None

    ports: set[int] = set()
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) < 5:
            continue
        port = ss_local_port(columns[4])
        if port is not None:
            ports.add(port)
    return ports


def port_probe_hosts(host: str) -> list[str]:
    hosts = [host, "127.0.0.1", "0.0.0.0"]
    if host == "localhost":
        hosts.append("::1")
    seen: set[str] = set()
    return [entry for entry in hosts if entry and not (entry in seen or seen.add(entry))]


def bind_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def port_available(host: str, port: int, occupied_ports: set[int] | None = None) -> bool:
    if occupied_ports is not None and port in occupied_ports:
        return False
    return all(bind_available(candidate, port) for candidate in port_probe_hosts(host))


def reserve_free_port(host: str, occupied_ports: set[int] | None = None) -> int:
    for _ in range(100):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            port = int(sock.getsockname()[1])
        if port_available(host, port, occupied_ports):
            return port
    raise SystemExit("Could not find a free port that is available on the requested bind host and local wildcard addresses.")


def choose_port(host: str, requested: int, strict: bool) -> int:
    occupied_ports = ss_occupied_ports()
    if requested == 0:
        return reserve_free_port(host, occupied_ports)
    if port_available(host, requested, occupied_ports):
        return requested
    if strict:
        raise SystemExit(f"Requested port {requested} is busy.")
    fallback = reserve_free_port(host, occupied_ports)
    print(f"Port {requested} is busy; using free fallback port {fallback}.", flush=True)
    return fallback


def wait_for_port_available(host: str, port: int, timeout: float = 5.0) -> None:
    if port == 0:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_available(host, port, ss_occupied_ports()):
            return
        time.sleep(0.2)


def wait_for_server(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except URLError as exc:
            last_error = exc
        time.sleep(0.4)
    raise SystemExit(f"Dispatcher did not become ready at {url}: {last_error}")


def terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def server_command(args: argparse.Namespace, port: int) -> list[str]:
    return [
        args.python,
        "-m",
        "dispatcher_app.server",
        "--host",
        args.host,
        "--port",
        str(port),
        "--db",
        args.db,
    ]


def dispatcher_command(args: argparse.Namespace) -> list[str]:
    return [
        args.python,
        "-m",
        "dispatcher_app.dispatcher",
        "--db",
        args.db,
        "--max-concurrent-tasks",
        str(args.max_concurrent_tasks),
        "--poll-seconds",
        str(args.poll_seconds),
        "--lease-seconds",
        str(args.lease_seconds),
    ]


def reboot_command(args: argparse.Namespace, repo: Path, port: int) -> list[str]:
    return [
        args.python,
        "-m",
        "dispatcher_app.reboot",
        "watch",
        "--repo",
        str(repo),
        "--session",
        args.session,
        "--host",
        args.host,
        "--port",
        str(port),
        "--db",
        args.db,
        "--python",
        args.python,
        "--max-concurrent-tasks",
        str(args.max_concurrent_tasks),
        "--dispatcher-poll-seconds",
        str(args.poll_seconds),
        "--lease-seconds",
        str(args.lease_seconds),
    ]


def require_tmux() -> str:
    tmux = shutil.which("tmux")
    if not tmux:
        raise SystemExit("tmux was not found. Install tmux or use the foreground command.")
    return tmux


def tmux_run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def tmux_access_error(result: subprocess.CompletedProcess[str]) -> bool:
    stderr = result.stderr.lower()
    return "operation not permitted" in stderr or "permission denied" in stderr


def tmux_session_target(session: str) -> str:
    return f"={session}"


def tmux_window_target(session: str, window: str) -> str:
    return f"={session}:{window}"


def require_tmux_access(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0 and tmux_access_error(result):
        detail = result.stderr.strip() or "tmux command failed."
        raise SystemExit(f"tmux is not accessible from this process: {detail}")


def tmux_session_exists(session: str) -> bool:
    result = tmux_run("has-session", "-t", tmux_session_target(session), check=False)
    require_tmux_access(result)
    return result.returncode == 0


def tmux_window_exists(session: str, window: str) -> bool:
    result = tmux_run("has-session", "-t", tmux_window_target(session, window), check=False)
    require_tmux_access(result)
    return result.returncode == 0


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def helper_command(repo: Path, command: str, *args: str) -> str:
    return shell_join(["python3", Path(__file__).resolve(), command, "--repo", repo, *args])


def tmux_capture(target: str) -> str:
    result = tmux_run("capture-pane", "-Jpt", target, "-S", "-200", check=False)
    require_tmux_access(result)
    return result.stdout if result.returncode == 0 else ""


def quick_url_from_text(text: str) -> str | None:
    match = URL_RE.search(text)
    return match.group(0) if match else None


def wait_for_quick_url(session: str, timeout: float) -> str | None:
    deadline = time.time() + timeout
    target = tmux_window_target(session, "tunnel")
    while time.time() < deadline:
        url = quick_url_from_text(tmux_capture(target))
        if url:
            return url
        time.sleep(1)
    return None


RUNTIME_WINDOWS = ("server", "dispatcher", "reboot", "tunnel")


def first_error_line(message: str) -> str:
    return message.strip().splitlines()[0] if message.strip() else ""


def server_http_summary(host: str, port: int) -> dict[str, object]:
    if port <= 0:
        return {
            "ok": False,
            "status": 0,
            "url": "",
            "error": "server port is not configured",
        }
    url = f"http://{host}:{port}/"
    try:
        with urlopen(url, timeout=2) as response:
            status = int(getattr(response, "status", 0) or 0)
    except (OSError, URLError) as exc:
        return {
            "ok": False,
            "status": 0,
            "url": url,
            "error": first_error_line(str(exc)) or exc.__class__.__name__,
        }
    return {
        "ok": status < 500,
        "status": status,
        "url": url,
        "error": "",
    }


def safe_tmux_runtime_summary(session: str) -> dict[str, object]:
    summary: dict[str, object] = {
        "available": bool(shutil.which("tmux")),
        "accessible": False,
        "session": "unknown",
        "windows": {window: "unknown" for window in RUNTIME_WINDOWS},
        "quick_tunnel_url_detected": False,
        "error": "",
    }
    if not summary["available"]:
        summary["session"] = "unknown"
        summary["error"] = "tmux was not found"
        return summary

    session_result = tmux_run("has-session", "-t", tmux_session_target(session), check=False)
    if tmux_access_error(session_result):
        summary["error"] = "tmux is not accessible from this process"
        return summary
    summary["accessible"] = True
    if session_result.returncode != 0:
        summary["session"] = "absent"
        summary["windows"] = {window: "absent" for window in RUNTIME_WINDOWS}
        return summary

    summary["session"] = "running"
    windows: dict[str, str] = {}
    for window in RUNTIME_WINDOWS:
        result = tmux_run("has-session", "-t", tmux_window_target(session, window), check=False)
        if tmux_access_error(result):
            summary["accessible"] = False
            summary["error"] = "tmux is not accessible from this process"
            windows[window] = "unknown"
            continue
        windows[window] = "present" if result.returncode == 0 else "absent"
    summary["windows"] = windows

    if windows.get("tunnel") == "present":
        capture = tmux_run(
            "capture-pane",
            "-Jpt",
            tmux_window_target(session, "tunnel"),
            "-S",
            "-200",
            check=False,
        )
        if capture.returncode == 0:
            summary["quick_tunnel_url_detected"] = quick_url_from_text(capture.stdout) is not None
        elif tmux_access_error(capture):
            summary["accessible"] = False
            summary["error"] = "tmux is not accessible from this process"
    return summary


def status_recommendations(server: dict[str, object], tmux: dict[str, object]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    if not server.get("ok"):
        recommendations.append(
            {
                "target": "server",
                "command": "restart-server",
                "reason": "local server health check failed",
            }
        )
    windows = tmux.get("windows") if isinstance(tmux.get("windows"), dict) else {}
    if windows.get("dispatcher") == "absent":
        recommendations.append(
            {
                "target": "dispatcher",
                "command": "restart-dispatcher",
                "reason": "dispatcher tmux window is absent",
            }
        )
    if windows.get("reboot") == "absent":
        recommendations.append(
            {
                "target": "reboot",
                "command": "restart-reboot",
                "reason": "reboot watcher tmux window is absent",
            }
        )
    if windows.get("tunnel") == "absent":
        recommendations.append(
            {
                "target": "tunnel",
                "command": "start --replace",
                "reason": "tunnel tmux window is absent; this can issue a new tunnel URL",
            }
        )
    return recommendations


def runtime_status_summary(args: argparse.Namespace, repo: Path) -> dict[str, object]:
    server = server_http_summary(args.host, int(args.port))
    tmux = safe_tmux_runtime_summary(args.session)
    return {
        "repo": str(repo),
        "session": args.session,
        "server": server,
        "tmux": tmux,
        "quick_tunnel": {
            "url_detected": bool(tmux.get("quick_tunnel_url_detected")),
            "url_printed": False,
            "url_command": helper_command(repo, "url", "--session", args.session),
        },
        "recommendations": status_recommendations(server, tmux),
        "manager_boundary": (
            "Manager tasks must not call tmux, cloudflared, tunnel helpers, or restarts directly; "
            "they request runtime reloads with a final REBOOT_AFTER_TASK marker."
        ),
    }


def print_tmux_summary(repo: Path, session: str, local_url: str, quick_url: str | None) -> None:
    print(f"tmux session: {session}", flush=True)
    print(f"Dispatcher local URL: {local_url}", flush=True)
    print("tmux windows: server, dispatcher, reboot, tunnel", flush=True)
    if quick_url:
        print(f"Quick Tunnel URL: {quick_url}", flush=True)
    else:
        print("Quick Tunnel URL not found yet. Inspect with:", flush=True)
        print(f"tmux capture-pane -pt ={session}:tunnel -S -200", flush=True)
    print(f"Attach: tmux attach -t ={session}", flush=True)
    print(f"Restart server and dispatcher: {sys.argv[0]} restart --session {session}", flush=True)
    print(f"Restart server only: {sys.argv[0]} restart-server --session {session}", flush=True)
    print(f"Restart dispatcher only: {sys.argv[0]} restart-dispatcher --session {session}", flush=True)
    print(f"Restart reboot watcher only: {sys.argv[0]} restart-reboot --session {session}", flush=True)
    print("Manager restart marker after task completion:", flush=True)
    print("REBOOT_AFTER_TASK restart-dispatcher <reason>", flush=True)
    print(f"Stop: tmux kill-session -t ={session}", flush=True)
    print(f"Check Quick Tunnel URL: {helper_command(repo, 'url', '--session', session)}", flush=True)


def ensure_reboot_window(args: argparse.Namespace, repo: Path, port: int) -> None:
    if tmux_window_exists(args.session, "reboot"):
        return
    cmd = command_with_repo_env(repo, shell_join(reboot_command(args, repo, port)))
    tmux_run("new-window", "-t", tmux_session_target(args.session), "-n", "reboot", "-c", str(repo))
    tmux_run("send-keys", "-t", tmux_window_target(args.session, "reboot"), cmd, "C-m")


def start_tmux(args: argparse.Namespace, repo: Path, cloudflared: str) -> int:
    require_tmux()
    if tmux_session_exists(args.session):
        if not args.replace:
            print(f"tmux session '{args.session}' already exists.", file=sys.stderr)
            print(f"Use --replace to recreate it, or run: {sys.argv[0]} restart", file=sys.stderr)
            return 1
        tmux_run("kill-session", "-t", tmux_session_target(args.session), check=False)
        wait_for_port_available(args.host, args.port)

    port = int(getattr(args, "resolved_port", 0) or choose_port(args.host, args.port, args.strict_port))
    local_url = f"http://{args.host}:{port}"
    server_cmd = command_with_repo_env(repo, shell_join(server_command(args, port)))
    dispatcher_cmd = command_with_repo_env(repo, shell_join(dispatcher_command(args)))
    reboot_cmd = command_with_repo_env(repo, shell_join(reboot_command(args, repo, port)))
    tunnel_cmd = shell_join([cloudflared, "tunnel", "--url", local_url])

    print(f"Repository: {repo}", flush=True)
    print(f"Starting tmux session '{args.session}' with server, dispatcher, reboot, and tunnel windows.", flush=True)
    tmux_run("new-session", "-d", "-s", args.session, "-n", "server", "-c", str(repo))
    tmux_run("send-keys", "-t", tmux_window_target(args.session, "server"), server_cmd, "C-m")
    wait_for_server(f"{local_url}/")
    tmux_run("new-window", "-t", tmux_session_target(args.session), "-n", "dispatcher", "-c", str(repo))
    tmux_run("send-keys", "-t", tmux_window_target(args.session, "dispatcher"), dispatcher_cmd, "C-m")
    tmux_run("new-window", "-t", tmux_session_target(args.session), "-n", "reboot", "-c", str(repo))
    tmux_run("send-keys", "-t", tmux_window_target(args.session, "reboot"), reboot_cmd, "C-m")
    tmux_run("new-window", "-t", tmux_session_target(args.session), "-n", "tunnel", "-c", str(repo))
    tmux_run("send-keys", "-t", tmux_window_target(args.session, "tunnel"), tunnel_cmd, "C-m")
    quick_url = wait_for_quick_url(args.session, args.url_timeout)
    print_tmux_summary(repo, args.session, local_url, quick_url)
    return 0


def restart_server(args: argparse.Namespace, repo: Path) -> int:
    require_tmux()
    if not tmux_session_exists(args.session):
        print(f"tmux session '{args.session}' does not exist.", file=sys.stderr)
        return 1

    port = args.port
    if port == 0:
        raise SystemExit("restart-server requires a concrete --port value.")
    local_url = f"http://{args.host}:{port}"
    cmd = command_with_repo_env(repo, shell_join(server_command(args, port)))
    target = tmux_window_target(args.session, "server")
    if tmux_window_exists(args.session, "server"):
        tmux_run("send-keys", "-t", target, "C-c", check=False)
        time.sleep(0.5)
    else:
        tmux_run("new-window", "-t", tmux_session_target(args.session), "-n", "server", "-c", str(repo))
    tmux_run("send-keys", "-t", target, cmd, "C-m")
    wait_for_server(f"{local_url}/")
    ensure_reboot_window(args, repo, port)
    quick_url = quick_url_from_text(tmux_capture(tmux_window_target(args.session, "tunnel")))
    print_tmux_summary(repo, args.session, local_url, quick_url)
    return 0


def restart_dispatcher(args: argparse.Namespace, repo: Path) -> int:
    require_tmux()
    if not tmux_session_exists(args.session):
        print(f"tmux session '{args.session}' does not exist.", file=sys.stderr)
        return 1

    cmd = command_with_repo_env(repo, shell_join(dispatcher_command(args)))
    target = tmux_window_target(args.session, "dispatcher")
    if tmux_window_exists(args.session, "dispatcher"):
        tmux_run("send-keys", "-t", target, "C-c", check=False)
        time.sleep(0.5)
    else:
        tmux_run("new-window", "-t", tmux_session_target(args.session), "-n", "dispatcher", "-c", str(repo))
    tmux_run("send-keys", "-t", target, cmd, "C-m")
    local_url = f"http://{args.host}:{args.port}"
    ensure_reboot_window(args, repo, args.port)
    quick_url = quick_url_from_text(tmux_capture(tmux_window_target(args.session, "tunnel")))
    print_tmux_summary(repo, args.session, local_url, quick_url)
    return 0


def restart_reboot(args: argparse.Namespace, repo: Path) -> int:
    require_tmux()
    if not tmux_session_exists(args.session):
        print(f"tmux session '{args.session}' does not exist.", file=sys.stderr)
        return 1

    port = args.port
    if port == 0:
        raise SystemExit("restart-reboot requires a concrete --port value.")
    cmd = command_with_repo_env(repo, shell_join(reboot_command(args, repo, port)))
    target = tmux_window_target(args.session, "reboot")
    if tmux_window_exists(args.session, "reboot"):
        tmux_run("send-keys", "-t", target, "C-c", check=False)
        time.sleep(0.5)
    else:
        tmux_run("new-window", "-t", tmux_session_target(args.session), "-n", "reboot", "-c", str(repo))
    tmux_run("send-keys", "-t", target, cmd, "C-m")
    local_url = f"http://{args.host}:{port}"
    quick_url = quick_url_from_text(tmux_capture(tmux_window_target(args.session, "tunnel")))
    print_tmux_summary(repo, args.session, local_url, quick_url)
    return 0


def restart_runtime(args: argparse.Namespace, repo: Path) -> int:
    server_status = restart_server(args, repo)
    if server_status != 0:
        return server_status
    return restart_dispatcher(args, repo)


def print_url(args: argparse.Namespace) -> int:
    require_tmux()
    if not tmux_session_exists(args.session):
        print(f"tmux session '{args.session}' does not exist.", file=sys.stderr)
        return 1
    quick_url = quick_url_from_text(tmux_capture(tmux_window_target(args.session, "tunnel")))
    if not quick_url:
        print("Quick Tunnel URL not found in tunnel window.", file=sys.stderr)
        return 1
    print(f"Quick Tunnel URL: {quick_url}", flush=True)
    return 0


def print_status(args: argparse.Namespace, repo: Path) -> int:
    print_json(runtime_status_summary(args, repo))
    return 0


def stop_tmux(args: argparse.Namespace) -> int:
    require_tmux()
    if tmux_session_exists(args.session):
        tmux_run("kill-session", "-t", tmux_session_target(args.session))
        print(f"Stopped tmux session '{args.session}'.", flush=True)
    else:
        print(f"tmux session '{args.session}' is not running.", flush=True)
    return 0


def run_foreground(args: argparse.Namespace, repo: Path, cloudflared: str) -> int:
    port = int(getattr(args, "resolved_port", 0) or choose_port(args.host, args.port, args.strict_port))
    local_url = f"http://{args.host}:{port}"
    server_cmd = server_command(args, port)
    dispatcher_cmd = dispatcher_command(args)
    tunnel_cmd = [cloudflared, "tunnel", "--url", local_url]

    print(f"Repository: {repo}", flush=True)
    print(f"Dispatcher local URL: {local_url}", flush=True)

    server = subprocess.Popen(
        server_cmd,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=dispatcher_env(repo),
    )
    dispatcher: subprocess.Popen[str] | None = None
    tunnel: subprocess.Popen[str] | None = None

    try:
        wait_for_server(f"{local_url}/")
        dispatcher = subprocess.Popen(
            dispatcher_cmd,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=dispatcher_env(repo),
        )
        tunnel = subprocess.Popen(
            tunnel_cmd,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert tunnel.stdout is not None
        last_lines: list[str] = []
        quick_url: str | None = None
        for raw_line in tunnel.stdout:
            line = raw_line.rstrip()
            if line:
                print(f"[cloudflared] {line}", flush=True)
                last_lines.append(line)
                last_lines = last_lines[-20:]
            match = URL_RE.search(line)
            if match and quick_url is None:
                quick_url = match.group(0)
                print("", flush=True)
                print(f"Quick Tunnel URL: {quick_url}", flush=True)
                print("Keep this process running while the tunnel is needed.", flush=True)

        if tunnel.poll() not in (0, None):
            print("cloudflared exited before producing a usable tunnel.", file=sys.stderr)
            if last_lines:
                print("Last cloudflared lines:", file=sys.stderr)
                for entry in last_lines:
                    print(entry, file=sys.stderr)
            return tunnel.returncode or 1
        return 0
    except KeyboardInterrupt:
        print("\nStopping dispatcher and tunnel...", flush=True)
        return 130
    finally:
        if tunnel is not None:
            terminate(tunnel)
        if dispatcher is not None:
            terminate(dispatcher)
        terminate(server)


def init_migrated_repo(args: argparse.Namespace, repo: Path) -> int:
    validate_repo_requirements(repo)
    ensure_memory_compact_checkpoint, ensure_role_memory_loaded, _, _ = import_memory_bootstrap(repo)
    operator_memory = ensure_role_memory_loaded(repo, "operator")
    compact_checkpoint = ensure_memory_compact_checkpoint(repo)
    agents_created = ensure_agent_registry(repo)
    operator_key = operator_key_from_sources(args.operator_key)
    existing = read_shell_env(local_env_path(repo))
    if not option_was_supplied("--session") and not existing.get("DISPATCHER_SESSION"):
        args.session = default_init_session(repo)

    password = read_init_password(args, repo)
    if args.no_start:
        if args.port == 0:
            raise SystemExit("--no-start requires a concrete --port value because no runtime is started to resolve a free port.")
        port = args.port
    else:
        port = choose_port(args.host, args.port, args.strict_port)
    args.port = port
    args.resolved_port = port

    cloudflared = ""
    if args.install_cloudflared:
        args.cloudflared = str(install_cloudflared(repo, args.cloudflared_install_dir))
        cloudflared = args.cloudflared
    elif args.cloudflared:
        cloudflared = find_cloudflared(repo, args.cloudflared, port=port)
        args.cloudflared = cloudflared
    elif not args.no_start:
        cloudflared = find_cloudflared(repo, args.cloudflared, port=port)
        args.cloudflared = cloudflared
    if not args.no_start:
        require_tmux()

    baseline_status = ensure_operator_baseline(repo)
    write_local_state_files(repo, args, password=password, port=port)
    operator_key_recorded = record_operator_key(repo, operator_key)
    print(f"{baseline_status.capitalize()} Dispatcher Skill Operator Baseline at {agents_baseline_path(repo)}", flush=True)
    if agents_created:
        print(f"Created local agent registry at {agents_path(repo)}", flush=True)
    else:
        print(f"Verified local agent registry at {agents_path(repo)}", flush=True)
    if operator_key_recorded:
        print("Recorded operator Codex handle in local agent registry.", flush=True)
    memory_paths = ", ".join(document.path for document in operator_memory)
    print(f"Loaded operator initialization memory: {memory_paths}", flush=True)
    print(
        f"Initialization compact checkpoint passed for {compact_checkpoint['checked_files']} memory file(s).",
        flush=True,
    )
    print(f"Initialized dispatcher skill runtime state in {local_state_dir(repo)}", flush=True)
    print(f"Repository: {repo}", flush=True)
    print(f"Configured local URL: http://{args.host}:{port}", flush=True)
    print(f"tmux session: {args.session}", flush=True)
    print("Dispatcher password is stored only in the ignored local env file.", flush=True)

    if args.no_start:
        print("Skipped startup because --no-start was supplied.", flush=True)
        return 0
    return start_tmux(args, repo, cloudflared)


def main() -> int:
    args = parse_args()
    repo = resolve_command_repo(args)
    apply_local_defaults(args, repo)
    apply_repo_defaults(args, repo)

    if args.command == "install-cloudflared":
        install_cloudflared(repo, args.cloudflared_install_dir)
        return 0

    if args.command == "init-status":
        return print_init_status(repo)
    if args.command == "reset":
        return reset_for_deployment(args, repo)
    if args.command == "update-skill":
        return update_skill(args, repo)
    if args.command == "init":
        return init_migrated_repo(args, repo)

    if args.command == "status":
        return print_status(args, repo)
    if args.command == "stop":
        return stop_tmux(args)
    if args.command == "url":
        return print_url(args)
    if args.command == "restart-server":
        return restart_server(args, repo)
    if args.command == "restart-dispatcher":
        return restart_dispatcher(args, repo)
    if args.command == "restart-reboot":
        return restart_reboot(args, repo)
    if args.command == "restart":
        return restart_runtime(args, repo)

    if args.install_cloudflared:
        args.cloudflared = str(install_cloudflared(repo, args.cloudflared_install_dir))
    cloudflared = find_cloudflared(repo, args.cloudflared, port=args.port)
    if args.command == "foreground":
        return run_foreground(args, repo, cloudflared)
    return start_tmux(args, repo, cloudflared)


if __name__ == "__main__":
    raise SystemExit(main())
