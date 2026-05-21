from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


COMMANDS = {
    "restart": "restart",
    "restart-server": "restart-server",
    "restart-dispatcher": "restart-dispatcher",
    "restart-reboot": "restart-reboot",
}
DISPATCHER_AFFECTING_COMMANDS = {"restart", "restart-dispatcher"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def normalize_command(command: str) -> str:
    normalized = command.strip().lower().replace("_", "-")
    if normalized not in COMMANDS:
        choices = ", ".join(sorted(COMMANDS))
        raise ValueError(f"Unknown reboot command: {command}. Expected one of: {choices}")
    return COMMANDS[normalized]


def build_reboot_request(
    command: str,
    *,
    created_by: str,
    reason: str = "",
    delay_seconds: float = 5.0,
) -> dict[str, Any]:
    normalized = normalize_command(command)
    now = datetime.now(timezone.utc)
    return {
        "id": str(uuid4()),
        "command": normalized,
        "created_at": now.isoformat(),
        "created_by": created_by,
        "not_before": (now + timedelta(seconds=delay_seconds)).isoformat(),
        "reason": reason,
    }


def append_reboot_request(
    path: Path,
    command: str,
    *,
    created_by: str,
    reason: str = "",
    delay_seconds: float = 5.0,
) -> dict[str, Any]:
    request = build_reboot_request(
        command,
        created_by=created_by,
        reason=reason,
        delay_seconds=delay_seconds,
    )
    append_jsonl(path, request)
    return request


def request_reboot(args: argparse.Namespace) -> int:
    request = append_reboot_request(
        Path(args.requests),
        args.command,
        created_by=args.created_by,
        reason=args.reason,
        delay_seconds=args.delay_seconds,
    )
    print(f"Queued reboot request {request['id']} command={request['command']}", flush=True)
    return 0


def read_new_requests(path: Path, offset: int) -> tuple[list[tuple[int, dict[str, Any] | None, str]], int]:
    if not path.exists():
        return [], 0
    size = path.stat().st_size
    if offset > size:
        offset = 0

    records: list[tuple[int, dict[str, Any] | None, str]] = []
    with path.open("r", encoding="utf-8") as file:
        file.seek(offset)
        while True:
            line = file.readline()
            if not line:
                break
            offset = file.tell()
            raw = line.strip()
            if not raw:
                continue
            try:
                records.append((offset, json.loads(raw), raw))
            except json.JSONDecodeError:
                records.append((offset, None, raw))
    return records, offset


def unprocessed_request_count(requests_path: Path, state_path: Path) -> int:
    state = load_state(state_path)
    offset = int(state.get("offset") or 0)
    records, _ = read_new_requests(requests_path, offset)
    return len(records)


def build_skill_command(args: argparse.Namespace, command: str) -> list[str]:
    script = Path(args.script) if args.script else Path(args.repo) / "scripts" / "run_dispatcher_tunnel.py"
    return [
        args.python,
        str(script),
        command,
        "--repo",
        args.repo,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--db",
        args.db,
        "--python",
        args.python,
        "--session",
        args.session,
        "--max-concurrent-tasks",
        str(args.max_concurrent_tasks),
        "--poll-seconds",
        str(args.dispatcher_poll_seconds),
        "--lease-seconds",
        str(args.lease_seconds),
    ]


def run_reboot_command(args: argparse.Namespace, request: dict[str, Any]) -> dict[str, Any]:
    command = normalize_command(str(request.get("command") or ""))
    process_command = build_skill_command(args, command)
    started_at = utc_now()
    if args.dry_run:
        return {
            "status": "dry_run",
            "started_at": started_at,
            "finished_at": utc_now(),
            "command": process_command,
            "returncode": 0,
        }

    completed = subprocess.run(
        process_command,
        cwd=args.repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=args.timeout_seconds,
    )
    return {
        "status": "completed" if completed.returncode == 0 else "failed",
        "started_at": started_at,
        "finished_at": utc_now(),
        "command": process_command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def request_is_due(request: dict[str, Any]) -> bool:
    not_before = request.get("not_before")
    if not not_before:
        return True
    try:
        return parse_utc(str(not_before)) <= datetime.now(timezone.utc)
    except ValueError:
        return True


def request_affects_dispatcher(request: dict[str, Any]) -> bool:
    try:
        command = normalize_command(str(request.get("command") or ""))
    except ValueError:
        return False
    return command in DISPATCHER_AFFECTING_COMMANDS


def active_work_defer_reason(db_path: Path, now_ts: float | None = None) -> str:
    if not db_path.exists():
        return ""
    now_ts = time.time() if now_ts is None else now_ts
    try:
        conn = sqlite3.connect(db_path, timeout=2)
        try:
            in_progress = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'in_progress'"
            ).fetchone()[0]
            busy_managers = conn.execute(
                "SELECT COUNT(*) FROM manager_runtime WHERE status != 'idle'"
            ).fetchone()[0]
            active_locks = conn.execute(
                "SELECT COUNT(*) FROM runtime_locks WHERE lease_expires_at > ?",
                (now_ts,),
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return ""
        return f"runtime state unavailable: {exc}"
    except sqlite3.Error as exc:
        return f"runtime state unavailable: {exc}"

    reasons = []
    if in_progress:
        reasons.append(f"{in_progress} in_progress task(s)")
    if busy_managers:
        reasons.append(f"{busy_managers} non-idle manager runtime row(s)")
    if active_locks:
        reasons.append(f"{active_locks} active runtime lock(s)")
    return "; ".join(reasons)


def defer_reboot_request(args: argparse.Namespace, request: dict[str, Any], reason: str) -> None:
    now = datetime.now(timezone.utc)
    deferred = dict(request)
    deferred["not_before"] = (now + timedelta(seconds=args.defer_seconds)).isoformat()
    deferred["deferred_at"] = now.isoformat()
    deferred["defer_reason"] = reason
    deferred["defer_count"] = int(deferred.get("defer_count") or 0) + 1
    append_jsonl(Path(args.requests), deferred)
    event = {
        "type": "reboot_request_deferred",
        "created_at": utc_now(),
        "request": request,
        "deferred_request": deferred,
        "reason": reason,
    }
    append_jsonl(Path(args.events), event)
    print(
        f"Deferred reboot request {request.get('id')} command={request.get('command')}: {reason}",
        flush=True,
    )


def process_request(args: argparse.Namespace, request: dict[str, Any] | None, raw: str) -> str:
    if request is None:
        event = {
            "type": "reboot_request_rejected",
            "created_at": utc_now(),
            "error": "invalid JSON",
            "raw": raw[:800],
        }
        print(f"Rejected invalid reboot request: {raw[:160]}", flush=True)
        append_jsonl(Path(args.events), event)
        return "processed"

    defer_reason = active_work_defer_reason(Path(args.db)) if request_affects_dispatcher(request) else ""
    if defer_reason:
        defer_reboot_request(args, request, defer_reason)
        return "deferred"

    event = {
        "type": "reboot_request_started",
        "created_at": utc_now(),
        "request": request,
    }
    print(
        f"Processing reboot request {request.get('id')} command={request.get('command')}",
        flush=True,
    )
    append_jsonl(Path(args.events), event)
    try:
        result = run_reboot_command(args, request)
    except Exception as exc:  # pragma: no cover - long-running process guard
        result = {
            "status": "failed",
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "error": repr(exc),
        }

    append_jsonl(
        Path(args.events),
        {
            "type": "reboot_request_finished",
            "created_at": utc_now(),
            "request": request,
            "result": result,
        },
    )
    print(
        f"Finished reboot request {request.get('id')} status={result.get('status')} returncode={result.get('returncode')}",
        flush=True,
    )
    return "processed"


def watch_requests(args: argparse.Namespace) -> int:
    stop = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal stop
        print(f"Received signal {signum}; stopping reboot watcher.", flush=True)
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    state_path = Path(args.state)
    state = load_state(state_path)
    offset = int(state.get("offset") or 0)
    print(f"Reboot watcher started. requests={args.requests} offset={offset}", flush=True)

    while not stop:
        records, next_offset = read_new_requests(Path(args.requests), offset)
        for record_offset, request, raw in records:
            if request is not None and not request_is_due(request):
                break
            process_request(args, request, raw)
            offset = record_offset
            save_state(state_path, {"offset": offset, "updated_at": utc_now()})
        if next_offset != offset and not records:
            offset = next_offset
            save_state(state_path, {"offset": offset, "updated_at": utc_now()})
        if args.once:
            break
        time.sleep(args.interval)

    print("Reboot watcher stopped.", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue and process dispatcher runtime reboot requests.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    request = subparsers.add_parser("request", help="Append a reboot request for the reboot watcher.")
    request.add_argument("command", choices=sorted(COMMANDS), help="Reboot command to request.")
    request.add_argument("--requests", default="data/reboot_requests.jsonl")
    request.add_argument("--created-by", default="manager.default")
    request.add_argument("--delay-seconds", default=5.0, type=float)
    request.add_argument("--reason", default="")

    watch = subparsers.add_parser("watch", help="Watch reboot requests and apply them from a host-side process.")
    watch.add_argument("--repo", default=".")
    watch.add_argument("--session", default="dispatcher-tunnel")
    watch.add_argument("--host", default="127.0.0.1")
    watch.add_argument("--port", default=8000, type=int)
    watch.add_argument("--db", default="data/dispatcher.db")
    watch.add_argument("--python", default=sys.executable)
    watch.add_argument("--script", default="")
    watch.add_argument("--requests", default="data/reboot_requests.jsonl")
    watch.add_argument("--state", default="data/reboot_state.json")
    watch.add_argument("--events", default="data/reboot_events.jsonl")
    watch.add_argument("--interval", default=1.0, type=float)
    watch.add_argument("--timeout-seconds", default=60.0, type=float)
    watch.add_argument("--max-concurrent-tasks", default=1, type=int)
    watch.add_argument("--dispatcher-poll-seconds", default=0.5, type=float)
    watch.add_argument("--lease-seconds", default=60, type=int)
    watch.add_argument("--defer-seconds", default=10.0, type=float)
    watch.add_argument("--once", action="store_true")
    watch.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.subcommand == "request":
        return request_reboot(args)
    if args.subcommand == "watch":
        return watch_requests(args)
    raise SystemExit(f"Unknown subcommand: {args.subcommand}")


if __name__ == "__main__":
    raise SystemExit(main())
