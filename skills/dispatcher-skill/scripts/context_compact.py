#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path


MAX_RUNTIME_LOG_DETAILS = 20


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return name.strip("._-") or "agent"


def repo_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def context_dir(repo: Path) -> Path:
    return repo / "memory" / "context-compression"


def context_path(repo: Path, agent: str) -> Path:
    return context_dir(repo) / f"{safe_name(agent)}.md"


def header(agent: str, note: str = "") -> str:
    lines = [
        f"# Context Compression - {agent}",
        "",
        "Unread compressed handoff entries for this agent role.",
    ]
    if note:
        lines.extend(["", note])
    return "\n".join(lines).rstrip() + "\n\n"


def ensure_context_file(path: Path, agent: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(header(agent), encoding="utf-8")


def read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8").strip()
    if args.body:
        return args.body.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def append_entry(repo: Path, agent: str, recipient: str, summary: str, body: str) -> Path:
    path = context_path(repo, agent)
    ensure_context_file(path, agent)
    detail = body.strip() or summary.strip()
    entry = [
        f"## {utc_now()} - to {recipient}",
        "",
        f"- from: {agent}",
        f"- to: {recipient}",
        "- status: unread",
        f"- summary: {summary.strip() or 'Context handoff'}",
        "",
        detail,
        "",
    ]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(entry).rstrip() + "\n\n")
    return path


def cmd_append(args: argparse.Namespace) -> int:
    repo = repo_path(args.repo)
    path = append_entry(repo, args.agent, args.to, args.summary, read_body(args))
    print(f"Appended handoff to {path}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    repo = repo_path(args.repo)
    path = context_path(repo, args.source)
    if not path.exists():
        print(f"No handoff file at {path}")
        return 0
    print(path.read_text(encoding="utf-8").rstrip())
    return 0


def has_entries(text: str) -> bool:
    return "\n## " in text or text.startswith("## ")


def cmd_ack(args: argparse.Namespace) -> int:
    repo = repo_path(args.repo)
    path = context_path(repo, args.source)
    if not path.exists():
        print(f"No handoff file at {path}")
        return 0
    text = path.read_text(encoding="utf-8")
    if not has_entries(text):
        print(f"No unread entries in {path}")
        return 0
    note = f"Last acknowledged by {args.reader} at {utc_now()}."
    path.write_text(header(args.source, note), encoding="utf-8")
    print(f"Purged acknowledged handoff file {path}")
    return 0


def one_line(value: object, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def task_digest(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], str]:
    tasks = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    lines = [f"Task purge snapshot at {utc_now()}.", ""]
    if not tasks:
        lines.append("No task cards existed at purge time.")
        return tasks, "\n".join(lines).strip()
    for task in tasks:
        title = str(task["title"] or "").strip()
        label = f"#{task['id']}" + (f" {title}" if title else "")
        lines.extend(
            [
                f"### {label}",
                "",
                f"- status: {task['status']}",
                f"- priority: {task['priority']}",
                f"- approved: {bool(task['approved'])}",
                f"- attempts: {task['attempt_count']}",
                f"- created: {task['created_at']}",
                f"- updated: {task['updated_at']}",
            ]
        )
        if task["description"]:
            lines.append(f"- request: {one_line(task['description'], 500)}")
        if task["acceptance_criteria"]:
            lines.append(f"- acceptance: {one_line(task['acceptance_criteria'], 300)}")
        if task["result"]:
            lines.append(f"- result: {one_line(task['result'], 500)}")
        if task["error"]:
            lines.append(f"- error: {one_line(task['error'], 500)}")
        events = conn.execute(
            """
            SELECT type, message, created_at
            FROM events
            WHERE task_id = ?
            ORDER BY id ASC
            """,
            (task["id"],),
        ).fetchall()
        lines.append(f"- events: {len(events)} total")
        for event in events[-8:]:
            lines.append(
                f"  - {event['created_at']} {event['type']}: {one_line(event['message'], 220)}"
            )
        lines.append("")
    return tasks, "\n".join(lines).strip()


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def relative_label(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def bytes_label(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def summary_token(value: object, limit: int = 80) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "")).strip("._-")
    if not text:
        return "unknown"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip("._-") + "..."


def id_token(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    text = str(value or "").strip()
    return text if text.isdigit() else ""


def counter_text(counter: Counter[str], limit: int = 6) -> str:
    if not counter:
        return "none"
    parts = [
        f"{summary_token(name, 40)}={count}"
        for name, count in counter.most_common(limit)
    ]
    remaining = len(counter) - len(parts)
    if remaining > 0:
        parts.append(f"{remaining} more")
    return ", ".join(parts)


def limited_ids(values: set[str], limit: int = 8) -> str:
    if not values:
        return "none"
    ordered = sorted(values, key=lambda item: int(item))
    visible = ordered[:limit]
    suffix = f", {len(ordered) - len(visible)} more" if len(ordered) > len(visible) else ""
    return ", ".join(visible) + suffix


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def runtime_activity(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    busy: list[sqlite3.Row] = []
    active: list[sqlite3.Row] = []
    if table_exists(conn, "manager_runtime"):
        busy = conn.execute(
            "SELECT role_key, current_task_id FROM manager_runtime WHERE status = 'busy'"
        ).fetchall()
    if table_exists(conn, "tasks"):
        active = conn.execute("SELECT id, status FROM tasks WHERE status = 'in_progress'").fetchall()
    return busy, active


def resolve_runtime_log_dirs(
    repo: Path,
    agent_conversations_dir: str,
    codex_runs_dir: str,
) -> list[tuple[str, Path]]:
    allowed_roots = {
        "agent_conversations": (repo / "data" / "agent_conversations").resolve(),
        "codex_runs": (repo / "data" / "codex_runs").resolve(),
    }
    resolved: list[tuple[str, Path]] = []
    for label, value in (
        ("agent_conversations", agent_conversations_dir),
        ("codex_runs", codex_runs_dir),
    ):
        raw = Path(value).expanduser()
        path = raw.resolve() if raw.is_absolute() else (repo / raw).resolve()
        allowed_root = allowed_roots[label]
        if not is_relative_to(path, allowed_root):
            raise ValueError(f"Runtime log directory must stay under {allowed_root}: {path}")
        resolved.append((label, path))
    return resolved


def collect_runtime_log_files(
    repo: Path,
    directories: list[tuple[str, Path]],
) -> tuple[list[Path], Counter[str]]:
    data_root = (repo / "data").resolve()
    files: list[Path] = []
    skipped: Counter[str] = Counter()
    for _label, directory in directories:
        if not directory.exists():
            skipped["missing_dirs"] += 1
            continue
        if directory.is_symlink():
            skipped["symlinks"] += 1
            continue
        if not directory.is_dir():
            skipped["non_dirs"] += 1
            continue
        for root, dir_names, file_names in os.walk(directory, followlinks=False):
            root_path = Path(root)
            kept_dirs = []
            for name in dir_names:
                dir_path = root_path / name
                if dir_path.is_symlink():
                    skipped["symlinks"] += 1
                    continue
                resolved_dir = dir_path.resolve()
                if not is_relative_to(resolved_dir, directory) or not is_relative_to(resolved_dir, data_root):
                    skipped["out_of_scope"] += 1
                    continue
                kept_dirs.append(name)
            dir_names[:] = kept_dirs
            for name in file_names:
                path = root_path / name
                if path.is_symlink():
                    skipped["symlinks"] += 1
                    continue
                if not path.is_file():
                    skipped["non_files"] += 1
                    continue
                resolved = path.resolve()
                if not is_relative_to(resolved, directory) or not is_relative_to(resolved, data_root):
                    skipped["out_of_scope"] += 1
                    continue
                files.append(path)
    files.sort(key=lambda item: relative_label(repo, item))
    return files, skipped


def directory_for_path(path: Path, directories: list[tuple[str, Path]]) -> str:
    resolved = path.resolve()
    for label, directory in directories:
        if is_relative_to(resolved, directory):
            return label
    return "unknown"


def file_mtime(path: Path) -> str:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        return "unknown"
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def summarize_jsonl_file(path: Path) -> dict[str, object]:
    kinds: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    task_ids: set[str] = set()
    worker_request_ids: set[str] = set()
    records = 0
    invalid = 0
    first_created = ""
    last_created = ""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            records += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if not isinstance(payload, dict):
                invalid += 1
                continue
            kinds[summary_token(payload.get("kind") or payload.get("type"))] += 1
            directions[summary_token(payload.get("direction"))] += 1
            task_id = id_token(payload.get("task_id"))
            if task_id:
                task_ids.add(task_id)
            worker_request_id = id_token(payload.get("worker_request_id"))
            if worker_request_id:
                worker_request_ids.add(worker_request_id)
            created = str(payload.get("created_at") or "").strip()
            if created:
                first_created = first_created or created
                last_created = created
    return {
        "records": records,
        "invalid": invalid,
        "kinds": kinds,
        "directions": directions,
        "task_ids": task_ids,
        "worker_request_ids": worker_request_ids,
        "first_created": first_created,
        "last_created": last_created,
    }


def summarize_codex_run_file(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if path.suffix != ".json":
        return metadata
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"json": "unreadable"}
    if not isinstance(payload, dict):
        return {"json": "non_object"}
    for key in (
        "task_id",
        "worker_request_id",
        "decision",
        "status",
        "returncode",
        "agent_role_key",
        "manager_role_key",
        "worker_role_key",
        "created_at",
    ):
        value = payload.get(key)
        if value is None or value == "":
            continue
        if key.endswith("_id") or key == "returncode":
            token = id_token(value)
            if not token:
                continue
        else:
            token = summary_token(value)
        metadata[key] = token
    return metadata


def runtime_logs_digest(
    repo: Path,
    directories: list[tuple[str, Path]],
) -> tuple[list[Path], str]:
    files, skipped = collect_runtime_log_files(repo, directories)
    total_bytes = sum(path.stat().st_size for path in files if path.exists())
    lines = [
        f"Runtime log purge snapshot at {utc_now()}.",
        "",
        "Scope:",
    ]
    for label, directory in directories:
        status = "present" if directory.exists() else "missing"
        lines.append(f"- {label}: {relative_label(repo, directory)} ({status})")
    lines.extend(
        [
            "",
            f"Regular files found: {len(files)} ({bytes_label(total_bytes)}).",
            f"Skipped entries: {counter_text(skipped) if skipped else 'none'}.",
            "Raw log lines, stdout/stderr, prompts, command output, secrets, and full JSON records were intentionally omitted.",
        ]
    )
    if not files:
        return files, "\n".join(lines).strip()

    by_dir: dict[str, list[Path]] = {}
    for path in files:
        by_dir.setdefault(directory_for_path(path, directories), []).append(path)

    conversation_files = by_dir.get("agent_conversations", [])
    if conversation_files:
        all_kinds: Counter[str] = Counter()
        total_records = 0
        total_invalid = 0
        summaries: list[tuple[float, Path, dict[str, object]]] = []
        for path in conversation_files:
            summary = summarize_jsonl_file(path)
            total_records += int(summary["records"])
            total_invalid += int(summary["invalid"])
            all_kinds.update(summary["kinds"])
            summaries.append((path.stat().st_mtime, path, summary))
        lines.extend(
            [
                "",
                "Agent conversation logs:",
                f"- files: {len(conversation_files)}",
                f"- records: {total_records}",
                f"- invalid records: {total_invalid}",
                f"- record kinds: {counter_text(all_kinds)}",
            ]
        )
        for _mtime, path, summary in sorted(summaries, key=lambda item: item[0], reverse=True)[:MAX_RUNTIME_LOG_DETAILS]:
            detail = (
                f"- {relative_label(repo, path)}: records={summary['records']}, "
                f"kinds={counter_text(summary['kinds'], 4)}, "
                f"tasks={limited_ids(summary['task_ids'])}, "
                f"worker_requests={limited_ids(summary['worker_request_ids'])}, "
                f"first={summary['first_created'] or 'unknown'}, "
                f"last={summary['last_created'] or 'unknown'}"
            )
            lines.append(detail)
        if len(summaries) > MAX_RUNTIME_LOG_DETAILS:
            lines.append(f"- {len(summaries) - MAX_RUNTIME_LOG_DETAILS} older conversation files omitted from detail.")

    codex_files = by_dir.get("codex_runs", [])
    if codex_files:
        suffixes: Counter[str] = Counter(path.suffix or "(none)" for path in codex_files)
        summaries = [
            (path.stat().st_mtime, path, summarize_codex_run_file(path))
            for path in codex_files
        ]
        lines.extend(
            [
                "",
                "Codex run artifacts:",
                f"- files: {len(codex_files)}",
                f"- suffixes: {counter_text(suffixes)}",
            ]
        )
        for _mtime, path, metadata in sorted(summaries, key=lambda item: item[0], reverse=True)[:MAX_RUNTIME_LOG_DETAILS]:
            meta_text = ", ".join(f"{key}={value}" for key, value in sorted(metadata.items())) or "metadata=not_read"
            lines.append(
                f"- {relative_label(repo, path)}: size={bytes_label(path.stat().st_size)}, "
                f"modified={file_mtime(path)}, {meta_text}"
            )
        if len(summaries) > MAX_RUNTIME_LOG_DETAILS:
            lines.append(f"- {len(summaries) - MAX_RUNTIME_LOG_DETAILS} older run artifacts omitted from detail.")

    return files, "\n".join(lines).strip()


def confirm_runtime_idle(repo: Path, db: str) -> int:
    db_path = repo / db
    if not db_path.exists():
        print(f"Database not found for runtime safety check: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        busy, active = runtime_activity(conn)
        if busy or active:
            print("Refusing to purge runtime logs while a manager or task is active.", file=sys.stderr)
            return 3
        return 0
    finally:
        conn.close()


def purge_runtime_log_files(
    repo: Path,
    directories: list[tuple[str, Path]],
    files: list[Path],
) -> int:
    data_root = (repo / "data").resolve()
    deleted = 0
    for path in files:
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if not is_relative_to(resolved, data_root):
            continue
        if not any(is_relative_to(resolved, directory) for _label, directory in directories):
            continue
        path.unlink()
        deleted += 1
    return deleted


def cmd_purge_runtime_logs(args: argparse.Namespace) -> int:
    repo = repo_path(args.repo)
    try:
        directories = resolve_runtime_log_dirs(
            repo,
            args.agent_conversations_dir,
            args.codex_runs_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    files, digest = runtime_logs_digest(repo, directories)
    if args.dry_run or not args.confirm:
        print(digest)
        print("")
        print("Dry run only. Re-run with --confirm to append this summary and purge runtime log files.")
        return 0
    safety_status = confirm_runtime_idle(repo, args.db)
    if safety_status != 0:
        return safety_status
    path = append_entry(repo, args.agent, args.to, "Runtime logs purged into compressed context", digest)
    deleted = purge_runtime_log_files(repo, directories, files)
    print(f"Purged {deleted} runtime log files into {path}")
    return 0


def cmd_purge_tasks(args: argparse.Namespace) -> int:
    repo = repo_path(args.repo)
    db_path = repo / args.db
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        busy, active = runtime_activity(conn)
        if busy or active:
            print("Refusing to purge while a manager or task is active.", file=sys.stderr)
            return 3
        tasks, digest = task_digest(conn)
        if args.dry_run or not args.confirm:
            print(digest)
            print("")
            print("Dry run only. Re-run with --confirm to purge task cards.")
            return 0
        path = append_entry(repo, args.agent, args.to, "Task cards purged into compressed context", digest)
        with conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('tasks', 'events')")
            conn.execute("UPDATE manager_runtime SET current_task_id = NULL WHERE current_task_id IS NOT NULL")
        print(f"Purged {len(tasks)} task cards into {path}")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dispatcher context compression handoff helper.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    append = subparsers.add_parser("append", help="Append a handoff entry to an agent Markdown file.")
    append.add_argument("--repo", default=argparse.SUPPRESS, help="Repository root.")
    append.add_argument("--agent", required=True, help="Author agent role key, such as operator.")
    append.add_argument("--to", required=True, help="Recipient agent role key.")
    append.add_argument("--summary", default="", help="One-line handoff summary.")
    append.add_argument("--body", default="", help="Detailed handoff body.")
    append.add_argument("--body-file", default="", help="Read detailed handoff body from a file.")
    append.set_defaults(func=cmd_append)

    read = subparsers.add_parser("read", help="Print an agent handoff file.")
    read.add_argument("--repo", default=argparse.SUPPRESS, help="Repository root.")
    read.add_argument("--source", required=True, help="Source agent role key to read.")
    read.set_defaults(func=cmd_read)

    ack = subparsers.add_parser("ack", help="Acknowledge and purge a read handoff file.")
    ack.add_argument("--repo", default=argparse.SUPPRESS, help="Repository root.")
    ack.add_argument("--source", required=True, help="Source agent role key that was read.")
    ack.add_argument("--reader", required=True, help="Agent role key that read the handoff.")
    ack.set_defaults(func=cmd_ack)

    purge = subparsers.add_parser("purge-tasks", help="Compress all task cards into context, then purge them.")
    purge.add_argument("--repo", default=argparse.SUPPRESS, help="Repository root.")
    purge.add_argument("--agent", required=True, help="Agent role key writing the purge snapshot.")
    purge.add_argument("--to", required=True, help="Recipient agent role key.")
    purge.add_argument("--db", default="data/dispatcher.db", help="Dispatcher SQLite DB path, relative to repo.")
    purge.add_argument("--dry-run", action="store_true", help="Print the snapshot without deleting cards.")
    purge.add_argument("--confirm", action="store_true", help="Actually delete task/event rows after snapshotting.")
    purge.set_defaults(func=cmd_purge_tasks)

    purge_logs = subparsers.add_parser(
        "purge-runtime-logs",
        help="Compress runtime audit logs into context, then purge the log files.",
    )
    purge_logs.add_argument("--repo", default=argparse.SUPPRESS, help="Repository root.")
    purge_logs.add_argument("--agent", required=True, help="Agent role key writing the purge snapshot.")
    purge_logs.add_argument("--to", required=True, help="Recipient agent role key.")
    purge_logs.add_argument("--db", default="data/dispatcher.db", help="Dispatcher SQLite DB path, relative to repo.")
    purge_logs.add_argument(
        "--agent-conversations-dir",
        default="data/agent_conversations",
        help="Agent conversation log directory, relative to repo and under data/agent_conversations/.",
    )
    purge_logs.add_argument(
        "--codex-runs-dir",
        default="data/codex_runs",
        help="Codex run artifact directory, relative to repo and under data/codex_runs/.",
    )
    purge_logs.add_argument("--dry-run", action="store_true", help="Print the summary without deleting files.")
    purge_logs.add_argument("--confirm", action="store_true", help="Actually delete runtime log files after snapshotting.")
    purge_logs.set_defaults(func=cmd_purge_runtime_logs)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
