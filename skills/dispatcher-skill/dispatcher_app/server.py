from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from datetime import datetime, timezone
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


STATUSES = [
    "inbox",
    "pending",
    "in_progress",
    "needs_approval",
    "done",
    "failed",
    "canceled",
]

AUTH_PASSWORD = os.environ.get("DISPATCHER_PASSWORD", "")
COOKIE_NAME = "dispatcher_session"
SESSION_TOKEN = secrets.token_urlsafe(32)
BASE_DIR = Path(__file__).resolve().parent


def operator_cwd() -> Path:
    raw = os.environ.get("DISPATCHER_WORKSPACE_ROOT") or os.environ.get("DISPATCHER_OPERATOR_CWD")
    if raw:
        try:
            return workspace_root_for_path(Path(raw).expanduser().resolve())
        except OSError:
            pass
    repo_raw = os.environ.get("DISPATCHER_REPO")
    if repo_raw:
        try:
            return workspace_root_for_path(Path(repo_raw).expanduser().resolve())
        except OSError:
            pass
    return workspace_root_for_path(Path.cwd().resolve())


def workspace_root_for_path(path: Path) -> Path:
    if path.name == "dispatcher-skill" and path.parent.name == "skills":
        skills_container = path.parent.parent
        if skills_container.name == ".agents":
            return skills_container.parent.resolve()
        return skills_container.resolve()
    return path


REPO_ROOT = operator_cwd()
DIRECTORY_ROOT = REPO_ROOT
PROJECT_NAME = REPO_ROOT.name or "Dispatcher"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
AGENTS_PATH = BASE_DIR / "agents.json"
GLOBAL_RUNTIME_LOCK = "global_execution"
TREE_EXCLUDED_NAMES = {
    "__pycache__",
}
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
TECHNICAL_MANAGER_STREAM_PREFIXES = (
    "Codex thread started",
    "item.started",
    "item.completed",
    "thread.started",
    "turn.started",
    "turn.completed",
)
ACTIVITY_CONVERSATION_EVENT_TYPES = {
    "manager_command",
    "manager_file_change",
    "manager_delegation",
    "manager_item",
    "manager_stream",
    "manager_completed",
    "worker_command",
    "worker_file_change",
    "worker_delegation",
    "worker_item",
    "worker_stream",
    "worker_completed",
    "worker_failed",
    "operator_command",
    "operator_file_change",
    "operator_item",
    "operator_stream",
    "operator_completed",
    "operator_failed",
    "task_failed",
}
ACTION_ITEM_EVENT_TYPES = {
    "manager_command",
    "manager_file_change",
    "manager_delegation",
    "manager_item",
    "worker_command",
    "worker_file_change",
    "worker_delegation",
    "worker_item",
    "operator_command",
    "operator_file_change",
    "operator_item",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_now() -> float:
    return time.time()


def timestamp_to_unix(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return queue_now()


def list_agent_states(store: "Store") -> list[dict[str, Any]]:
    raw = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    runtimes = {
        runtime["role_key"]: runtime
        for runtime in store.list_manager_runtime()
    }
    runtime_lock = store.get_runtime_lock(GLOBAL_RUNTIME_LOCK)
    operator_lock_active = (
        runtime_lock is not None
        and not bool(runtime_lock.get("expired"))
        and runtime_lock.get("owner_plane") == "operator"
    )
    states = []
    for agent in raw.get("agents", []):
        role_key = str(agent["role_key"])
        runtime = runtimes.get(role_key)
        current_task_id = runtime["current_task_id"] if runtime else None
        heartbeat_at = runtime["heartbeat_at"] if runtime else None
        updated_at = runtime["updated_at"] if runtime else None
        progress = None
        if role_key.startswith("manager."):
            runtime_status = runtime["status"] if runtime else "idle"
        elif role_key.startswith("worker."):
            active_worker_request = store.latest_active_worker_request(role_key)
            latest_worker_request = active_worker_request or store.latest_worker_request(role_key)
            worker_activity = store.latest_worker_activity(role_key)
            if active_worker_request is not None:
                runtime_status = "busy"
                current_task_id = int(active_worker_request["task_id"])
                heartbeat_at = (
                    active_worker_request.get("heartbeat_at")
                    or active_worker_request.get("updated_at")
                )
                updated_at = active_worker_request.get("updated_at")
                progress = active_worker_request.get("progress") or "worker request active"
            else:
                runtime_status = worker_runtime_status(worker_activity)
                if latest_worker_request is not None:
                    request_status = str(latest_worker_request.get("status") or "")
                    if request_status == "failed":
                        runtime_status = "error"
                    heartbeat_at = latest_worker_request.get("heartbeat_at")
                    updated_at = latest_worker_request.get("updated_at")
                    if request_status == "done":
                        progress = "worker request completed"
                    elif request_status == "failed":
                        progress = "worker request failed"
                    else:
                        progress = latest_worker_request.get("progress") or None
            if worker_activity is not None and active_worker_request is None:
                current_task_id = (
                    worker_activity["task_id"]
                    if is_worker_activity_running(worker_activity)
                    else None
                )
                if updated_at is None:
                    updated_at = worker_activity["created_at"]
        else:
            runtime_status = "manual"
            if (
                operator_lock_active
                and runtime_lock is not None
                and operator_lock_matches_agent(role_key, str(runtime_lock.get("owner_id") or ""))
            ):
                runtime_status = "busy"
                current_task_id = runtime_lock.get("task_id")
                heartbeat_at = runtime_lock.get("heartbeat_at")
                updated_at = runtime_lock.get("updated_at")
        states.append(
            {
                "role_key": role_key,
                "name": str(agent["name"]),
                "key_status": str(agent.get("key_status") or "unknown"),
                "has_key": bool(agent.get("key")),
                "runtime_status": runtime_status,
                "current_task_id": current_task_id,
                "heartbeat_at": heartbeat_at,
                "updated_at": updated_at,
                "progress": progress,
            }
        )
    return states


def operator_lock_matches_agent(role_key: str, owner_id: str) -> bool:
    if role_key == "operator":
        return owner_id == "operator" or owner_id.startswith(("operator/", "operator."))
    if not role_key.startswith("operator."):
        return False
    return owner_id == role_key or owner_id.startswith(f"{role_key}/")


def worker_runtime_status(worker_activity: dict[str, Any] | None) -> str:
    if worker_activity is None:
        return "idle"
    if is_worker_activity_running(worker_activity):
        return "busy"
    if worker_activity["status"] in {"failed", "not_found", "error"}:
        return "error"
    return "idle"


def is_worker_activity_running(worker_activity: dict[str, Any] | None) -> bool:
    if worker_activity is None:
        return False
    return (
        worker_activity["state"] == "running"
        and worker_activity.get("task_status") == "in_progress"
    )


def list_manager_activity(store: "Store") -> list[dict[str, Any]]:
    raw = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
    runtimes = {
        runtime["role_key"]: runtime
        for runtime in store.list_manager_runtime()
    }
    managers = []
    for agent in raw.get("agents", []):
        role_key = str(agent["role_key"])
        if not role_key.startswith("manager."):
            continue
        runtime = activity_runtime_for_agent(store, runtimes.get(role_key), role_key)
        task = None
        events: list[dict[str, Any]] = []
        current_task_id = runtime["current_task_id"]
        activity_task_id = int(current_task_id) if current_task_id is not None else None
        activity_status = "current" if activity_task_id is not None else "empty"
        if activity_task_id is not None:
            task = store.get_task(activity_task_id)
            if task is None:
                activity_task_id = None
                activity_status = "empty"
        if activity_task_id is None:
            activity_task_id = latest_activity_task_id_for_agent(store, role_key)
            if activity_task_id is not None:
                task = store.get_task(activity_task_id)
                if task is None:
                    activity_task_id = None
                else:
                    activity_status = "latest"
        if task is not None and activity_task_id is not None:
            events = store.list_task_events(activity_task_id, limit=80)
            events = [
                event
                for event in events
                if event["type"] in ACTIVITY_CONVERSATION_EVENT_TYPES
                and event_belongs_to_activity_agent(event, role_key)
            ]
        managers.append(
            {
                "role_key": role_key,
                "name": str(agent["name"]),
                "runtime_status": runtime["status"],
                "current_task_id": current_task_id,
                "activity_task_id": activity_task_id,
                "activity_status": activity_status,
                "heartbeat_at": runtime["heartbeat_at"],
                "updated_at": runtime["updated_at"],
                "task": task,
                "events": events,
            }
        )
    return managers


def activity_runtime_for_agent(
    store: "Store",
    runtime: dict[str, Any] | None,
    role_key: str,
) -> dict[str, Any]:
    if role_key.startswith("manager."):
        return runtime or {
            "role_key": role_key,
            "status": "idle",
            "current_task_id": None,
            "heartbeat_at": None,
            "updated_at": None,
        }
    worker_activity = store.latest_worker_activity(role_key)
    status = worker_runtime_status(worker_activity)
    return {
        "role_key": role_key,
        "status": status,
        "current_task_id": (
            worker_activity["task_id"]
            if is_worker_activity_running(worker_activity)
            else None
        ),
        "heartbeat_at": None,
        "updated_at": worker_activity["created_at"] if worker_activity is not None else None,
    }


def latest_activity_task_id_for_agent(store: "Store", role_key: str) -> int | None:
    if role_key.startswith("worker."):
        worker_activity = store.latest_worker_activity(role_key)
        return worker_activity["task_id"] if worker_activity is not None else None
    return store.latest_manager_activity_task_id(role_key)


def event_belongs_to_activity_agent(event: dict[str, Any], role_key: str) -> bool:
    event_type = str(event["type"])
    if role_key.startswith("manager."):
        return event_type.startswith(("manager_", "worker_")) or event_type == "task_failed"
    if role_key.startswith("worker."):
        if not event_type.startswith("worker_"):
            return event_type == "task_failed"
        return role_key in str(event.get("message") or "")
    return False


def parse_delegation_payload(message: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def one_line(text: str, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 3)].rstrip()}..."


def worker_request_delegation_message(
    request_id: int,
    worker_role_key: str,
    state: str,
    detail: str,
    *,
    status: str = "",
    content: str = "",
) -> str:
    payload = {
        "delegation_id": f"worker-request:{request_id}",
        "state": state,
        "detail": detail,
        "content": content or detail,
        "status": status or state,
    }
    if worker_role_key:
        payload["worker_role_key"] = worker_role_key
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def list_directory_tree(
    root: Path = DIRECTORY_ROOT,
    *,
    max_depth: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    items: list[dict[str, Any]] = []

    def walk(directory: Path, depth: int) -> None:
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda child: (not child.is_dir(), child.name.lower()),
            )
        except OSError:
            return

        for child in children:
            if child.name in TREE_EXCLUDED_NAMES:
                continue
            if child.name.startswith(".") and not child.is_dir() and child.name != ".gitignore":
                continue
            is_dir = child.is_dir() and not child.is_symlink()
            items.append(
                {
                    "name": child.name,
                    "path": child.relative_to(root).as_posix(),
                    "depth": depth,
                    "type": "dir" if is_dir else "file",
                }
            )
            if is_dir and (max_depth is None or depth < max_depth):
                walk(child, depth + 1)

    walk(root, 0)
    return {"root": root.as_posix(), "items": items}


def rows_digest(rows: list[sqlite3.Row]) -> str:
    payload = [dict(row) for row in rows]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def public_runtime_lock(lock: dict[str, Any] | None) -> dict[str, Any] | None:
    if lock is None:
        return None
    public = dict(lock)
    public.pop("fencing_token", None)
    return public


def should_hide_event(row: sqlite3.Row | dict[str, Any]) -> bool:
    event_type = str(row["type"])
    message = str(row["message"] or "").strip()
    if event_type == "manager_stderr":
        return True
    if event_type == "manager_stream":
        return message.startswith(TECHNICAL_MANAGER_STREAM_PREFIXES)
    return False


def is_action_item_event(row: sqlite3.Row | dict[str, Any]) -> bool:
    event_type = str(row["type"])
    return event_type in ACTION_ITEM_EVENT_TYPES


def visible_event(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    event = dict(row)
    if event["type"] == "manager_stream":
        event["message"] = format_manager_stream_message(str(event["message"] or ""))
    return event


def format_manager_stream_message(message: str) -> str:
    text = message.strip()
    if not text.startswith("{"):
        return message
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return message
    if not isinstance(payload, dict):
        return message
    parts = [
        str(payload.get(key) or "").strip()
        for key in ("summary", "result", "reason", "error")
    ]
    parts = [part for part in parts if part]
    return "\n\n".join(parts) or message


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    acceptance_criteria TEXT NOT NULL DEFAULT '',
                    priority INTEGER NOT NULL DEFAULT 3,
                    status TEXT NOT NULL DEFAULT 'inbox',
                    queued_at REAL,
                    result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    owner TEXT,
                    approved INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );

                CREATE TABLE IF NOT EXISTS manager_runtime (
                    role_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'idle',
                    current_task_id INTEGER,
                    heartbeat_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_locks (
                    resource TEXT PRIMARY KEY,
                    owner_plane TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    task_id INTEGER,
                    fencing_token TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worker_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    manager_role_key TEXT NOT NULL,
                    worker_role_key TEXT NOT NULL,
                    worker_name TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    owner TEXT,
                    lease_expires_at REAL,
                    heartbeat_at TEXT,
                    progress TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status
                    ON tasks(status, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_task_id
                    ON events(task_id, id);
                CREATE INDEX IF NOT EXISTS idx_manager_runtime_status
                    ON manager_runtime(status);
                CREATE INDEX IF NOT EXISTS idx_runtime_locks_lease
                    ON runtime_locks(lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_worker_requests_status
                    ON worker_requests(status, created_at, id);
                """
            )
            self._ensure_task_queue_schema(conn)
            self._ensure_worker_request_schema(conn)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_pending_fifo
                    ON tasks(status, queued_at, id)
                """
            )

    def _ensure_task_queue_schema(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "queued_at" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN queued_at REAL")
        rows = conn.execute(
            """
            SELECT id, COALESCE(updated_at, created_at) AS queued_at_source
            FROM tasks
            WHERE status = 'pending'
              AND queued_at IS NULL
            ORDER BY id ASC
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE tasks SET queued_at = ? WHERE id = ?",
                (timestamp_to_unix(row["queued_at_source"]), row["id"]),
            )

    def _ensure_worker_request_schema(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(worker_requests)")}
        if "heartbeat_at" not in columns:
            conn.execute("ALTER TABLE worker_requests ADD COLUMN heartbeat_at TEXT")
        if "progress" not in columns:
            conn.execute("ALTER TABLE worker_requests ADD COLUMN progress TEXT NOT NULL DEFAULT ''")
        if "worker_name" not in columns:
            conn.execute("ALTER TABLE worker_requests ADD COLUMN worker_name TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            UPDATE worker_requests
            SET heartbeat_at = COALESCE(heartbeat_at, updated_at, created_at)
            WHERE heartbeat_at IS NULL
              AND status IN ('pending', 'running')
            """
        )
        conn.execute(
            """
            UPDATE worker_requests
            SET progress = CASE status
                WHEN 'pending' THEN 'worker request queued'
                WHEN 'running' THEN 'worker request running'
                WHEN 'done' THEN 'worker request completed'
                WHEN 'failed' THEN 'worker request failed'
                ELSE 'worker request'
            END
            WHERE progress IS NULL
               OR progress = ''
            """
        )

    def create_task(self, payload: dict[str, Any]) -> int:
        title = str(payload.get("title") or "").strip()
        description = str(payload.get("description") or "").strip()
        acceptance = str(payload.get("acceptance_criteria") or "").strip()
        priority = int(payload.get("priority") or 3)
        priority = max(1, min(priority, 5))
        requested_status = str(payload.get("status") or "inbox").strip()
        status = "pending" if requested_status == "pending" else "inbox"
        status_label = "Pending" if status == "pending" else "Inbox"
        now = utc_now()
        queued_at = queue_now() if status == "pending" else None
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tasks (
                    title, description, acceptance_criteria, priority,
                    status, queued_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (title, description, acceptance, priority, status, queued_at, now, now),
            )
            task_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO events (task_id, type, message, created_at)
                VALUES (?, 'task_created', ?, ?)
                """,
                (task_id, f"Task created in {status_label} with priority {priority}.", now),
            )
            return task_id

    def suggest_inbox_tasks(
        self,
        source_task_id: int,
        manager_role_key: str,
        items: list[dict[str, Any]],
    ) -> list[int]:
        manager_role_key = str(manager_role_key or "").strip()
        if not manager_role_key.startswith("manager."):
            raise ValueError("manager_role_key must start with manager.")
        if not items:
            raise ValueError("at least one suggested task item is required")

        normalized_items = [self._suggested_task_payload(item) for item in items]
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (source_task_id,),
            ).fetchone()
            if task is None:
                raise ValueError(f"Task not found: {source_task_id}")
            if task["status"] != "in_progress":
                raise RuntimeError("task suggestions require an in-progress source task")

            runtime = conn.execute(
                """
                SELECT status, current_task_id
                FROM manager_runtime
                WHERE role_key = ?
                """,
                (manager_role_key,),
            ).fetchone()
            if (
                runtime is None
                or runtime["status"] != "busy"
                or int(runtime["current_task_id"] or 0) != int(source_task_id)
            ):
                raise RuntimeError("task suggestions require the calling manager to own the active task")

            runtime_lock = conn.execute(
                """
                SELECT owner_plane, owner_id, task_id, lease_expires_at
                FROM runtime_locks
                WHERE resource = ?
                """,
                (GLOBAL_RUNTIME_LOCK,),
            ).fetchone()
            if (
                runtime_lock is None
                or runtime_lock["owner_plane"] != "task"
                or runtime_lock["owner_id"] != manager_role_key
                or int(runtime_lock["task_id"] or 0) != int(source_task_id)
                or float(runtime_lock["lease_expires_at"] or 0) < time.time()
            ):
                raise RuntimeError("task suggestions require the active task runtime lock")

            task_ids = []
            for item in normalized_items:
                cur = conn.execute(
                    """
                    INSERT INTO tasks (
                        title, description, acceptance_criteria, priority,
                        status, queued_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'inbox', NULL, ?, ?)
                    """,
                    (
                        item["title"],
                        item["description"],
                        item["acceptance_criteria"],
                        item["priority"],
                        now,
                        now,
                    ),
                )
                task_id = int(cur.lastrowid)
                task_ids.append(task_id)
                conn.execute(
                    """
                    INSERT INTO events (task_id, type, message, created_at)
                    VALUES (?, 'task_suggested', ?, ?)
                    """,
                    (
                        task_id,
                        f"Suggested by {manager_role_key} from task #{source_task_id}.",
                        now,
                    ),
                )
            task_list = ", ".join(f"#{task_id}" for task_id in task_ids)
            self._insert_event(
                conn,
                source_task_id,
                "manager_stream",
                f"Suggested Inbox task candidates: {task_list}.",
                now,
            )
            return task_ids

    @staticmethod
    def _suggested_task_payload(item: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError("each suggested task item must be an object")
        priority = item.get("priority") or 3
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            priority = 3
        priority = max(1, min(priority, 5))
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        if not title and not description:
            raise ValueError("each suggested task item needs a title or description")
        return {
            "title": title,
            "description": description,
            "acceptance_criteria": str(item.get("acceptance_criteria") or "").strip(),
            "priority": priority,
        }

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM tasks
                ORDER BY
                    CASE status
                        WHEN 'inbox' THEN 0
                        WHEN 'pending' THEN 1
                        WHEN 'in_progress' THEN 2
                        WHEN 'needs_approval' THEN 3
                        WHEN 'done' THEN 4
                        WHEN 'failed' THEN 5
                        WHEN 'canceled' THEN 5
                        ELSE 6
                    END,
                    CASE WHEN status = 'pending' THEN queued_at IS NULL ELSE 0 END ASC,
                    CASE
                        WHEN status = 'pending' THEN queued_at
                        WHEN status = 'inbox' THEN created_at
                        ELSE updated_at
                    END DESC,
                    id DESC
                """
            ).fetchall()
            tasks = [dict(row) for row in rows]
            for task in tasks:
                events = conn.execute(
                    """
                    SELECT id, type, message, created_at
                    FROM events
                    WHERE task_id = ?
                    ORDER BY id DESC
                    LIMIT 40
                    """,
                    (task["id"],),
                ).fetchall()
                task["events"] = [
                    visible_event(event)
                    for event in events
                    if not should_hide_event(event) and not is_action_item_event(event)
                ][:8]
            return tasks

    def list_task_events(self, task_id: int, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, type, message, created_at
                FROM events
                WHERE task_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (task_id, limit * 4),
            ).fetchall()
            events = [visible_event(row) for row in rows if not should_hide_event(row)][:limit]
            return list(reversed(events))

    def latest_manager_activity_task_id(self, role_key: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT task_id
                FROM events
                WHERE events.task_id IS NOT NULL
                  AND type = 'manager_claimed'
                  AND message = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (f"Assigned to manager {role_key}.",),
            ).fetchone()
            return int(row["task_id"]) if row else None

    def latest_worker_activity(self, role_key: str) -> dict[str, Any] | None:
        marker = f"%{role_key}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT events.id,
                       events.task_id,
                       events.message,
                       events.created_at,
                       tasks.status AS task_status
                FROM events
                LEFT JOIN tasks ON tasks.id = events.task_id
                WHERE task_id IS NOT NULL
                  AND events.type = 'worker_delegation'
                  AND events.message LIKE ?
                ORDER BY events.id DESC
                LIMIT 20
                """,
                (marker,),
            ).fetchall()
        for row in rows:
            payload = parse_delegation_payload(str(row["message"] or ""))
            if payload is None:
                continue
            haystack = "\n".join(
                str(payload.get(key) or "")
                for key in ("detail", "content")
            )
            if role_key not in haystack:
                continue
            return {
                "event_id": int(row["id"]),
                "task_id": int(row["task_id"]),
                "state": str(payload.get("state") or "ran"),
                "status": str(payload.get("status") or ""),
                "task_status": str(row["task_status"] or ""),
                "created_at": str(row["created_at"]),
            }
        return None

    def latest_event_id(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS latest_id FROM events").fetchone()
            return int(row["latest_id"])

    def create_worker_request(
        self,
        task_id: int,
        manager_role_key: str,
        worker_role_key: str,
        prompt: str,
        worker_name: str = "",
    ) -> int:
        prompt = str(prompt or "").strip()
        manager_role_key = str(manager_role_key or "").strip()
        worker_role_key = str(worker_role_key or "").strip()
        worker_name = one_line(worker_name, 80)
        if not prompt:
            raise ValueError("worker prompt is required")
        if not manager_role_key.startswith("manager."):
            raise ValueError("manager_role_key must start with manager.")
        if not worker_role_key.startswith("worker."):
            raise ValueError("worker_role_key must start with worker.")

        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise ValueError(f"Task not found: {task_id}")
            if task["status"] != "in_progress":
                raise RuntimeError("worker requests require an in-progress task")

            runtime = conn.execute(
                """
                SELECT status, current_task_id
                FROM manager_runtime
                WHERE role_key = ?
                """,
                (manager_role_key,),
            ).fetchone()
            if (
                runtime is None
                or runtime["status"] != "busy"
                or int(runtime["current_task_id"] or 0) != int(task_id)
            ):
                raise RuntimeError("worker requests require the calling manager to own the active task")
            runtime_lock = conn.execute(
                """
                SELECT owner_plane, owner_id, task_id, lease_expires_at
                FROM runtime_locks
                WHERE resource = ?
                """,
                (GLOBAL_RUNTIME_LOCK,),
            ).fetchone()
            if (
                runtime_lock is None
                or runtime_lock["owner_plane"] != "task"
                or runtime_lock["owner_id"] != manager_role_key
                or int(runtime_lock["task_id"] or 0) != int(task_id)
                or float(runtime_lock["lease_expires_at"] or 0) < time.time()
            ):
                raise RuntimeError("worker requests require the active task runtime lock")

            active = conn.execute(
                """
                SELECT id
                FROM worker_requests
                WHERE task_id = ?
                  AND manager_role_key = ?
                  AND status IN ('pending', 'running')
                ORDER BY id ASC
                LIMIT 1
                """,
                (task_id, manager_role_key),
            ).fetchone()
            if active is not None:
                raise RuntimeError(f"worker request already active for this manager task: {active['id']}")

            cur = conn.execute(
                """
                INSERT INTO worker_requests (
                    task_id, manager_role_key, worker_role_key, worker_name, prompt,
                    status, heartbeat_at, progress, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'pending', ?, 'worker request queued', ?, ?)
                """,
                (task_id, manager_role_key, worker_role_key, worker_name, prompt, now, now, now),
            )
            request_id = int(cur.lastrowid)
            worker_label = f"{worker_role_key} ({worker_name})" if worker_name else worker_role_key
            detail = f"{worker_label}: {one_line(prompt)}"
            self._insert_event(
                conn,
                task_id,
                "worker_delegation",
                worker_request_delegation_message(
                    request_id,
                    worker_role_key,
                    "running",
                    detail,
                    status="pending",
                    content=prompt,
                ),
                now,
            )
            return request_id

    def get_worker_request(self, request_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM worker_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return dict(row) if row else None

    def latest_active_worker_request(self, role_key: str) -> dict[str, Any] | None:
        return self._latest_worker_request(role_key, active_only=True)

    def latest_worker_request(self, role_key: str) -> dict[str, Any] | None:
        return self._latest_worker_request(role_key, active_only=False)

    def _latest_worker_request(
        self,
        role_key: str,
        active_only: bool,
    ) -> dict[str, Any] | None:
        role_key = str(role_key or "").strip()
        active_filter = ""
        if active_only:
            active_filter = """
              AND worker_requests.status IN ('pending', 'running')
              AND tasks.status = 'in_progress'
            """
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT worker_requests.*,
                       tasks.status AS task_status
                FROM worker_requests
                LEFT JOIN tasks ON tasks.id = worker_requests.task_id
                WHERE worker_requests.worker_role_key = ?
                {active_filter}
                ORDER BY worker_requests.id DESC
                LIMIT 1
                """,
                (role_key,),
            ).fetchone()
            return dict(row) if row else None

    def heartbeat_worker_request(
        self,
        request_id: int,
        lease_seconds: int,
        progress: str | None = None,
    ) -> bool:
        now = utc_now()
        lease = time.time() + max(1, lease_seconds)
        progress = str(progress or "").strip()
        with self.connect() as conn:
            if progress:
                cur = conn.execute(
                    """
                    UPDATE worker_requests
                    SET heartbeat_at = ?,
                        lease_expires_at = ?,
                        progress = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status = 'running'
                    """,
                    (now, lease, progress, now, request_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE worker_requests
                    SET heartbeat_at = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status = 'running'
                    """,
                    (now, lease, now, request_id),
                )
            return cur.rowcount > 0

    def claim_next_worker_request(self, owner: str, lease_seconds: int) -> dict[str, Any] | None:
        now = utc_now()
        lease = time.time() + lease_seconds
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT worker_requests.*
                FROM worker_requests
                WHERE worker_requests.status = 'pending'
                  AND EXISTS (
                    SELECT 1
                    FROM tasks
                    WHERE tasks.id = worker_requests.task_id
                      AND tasks.status = 'in_progress'
                  )
                  AND EXISTS (
                    SELECT 1
                    FROM manager_runtime
                    WHERE manager_runtime.role_key = worker_requests.manager_role_key
                      AND manager_runtime.status = 'busy'
                      AND manager_runtime.current_task_id = worker_requests.task_id
                  )
                  AND EXISTS (
                    SELECT 1
                    FROM runtime_locks
                    WHERE runtime_locks.resource = ?
                      AND runtime_locks.owner_plane = 'task'
                      AND runtime_locks.owner_id = worker_requests.manager_role_key
                      AND runtime_locks.task_id = worker_requests.task_id
                      AND runtime_locks.lease_expires_at >= ?
                  )
                ORDER BY worker_requests.created_at ASC, worker_requests.id ASC
                LIMIT 1
                """,
                (GLOBAL_RUNTIME_LOCK, time.time()),
            ).fetchone()
            if row is None:
                return None
            request = dict(row)
            conn.execute(
                """
                UPDATE worker_requests
                SET status = 'running',
                    owner = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    progress = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    owner,
                    lease,
                    now,
                    f"worker request #{request['id']} running",
                    now,
                    now,
                    request["id"],
                ),
            )
            detail = f"{request['worker_role_key']}: {one_line(request['prompt'])}"
            self._insert_event(
                conn,
                int(request["task_id"]),
                "worker_delegation",
                worker_request_delegation_message(
                    int(request["id"]),
                    str(request["worker_role_key"]),
                    "running",
                    detail,
                    status="running",
                    content=str(request["prompt"]),
                ),
                now,
            )
            request.update(
                {
                    "status": "running",
                    "owner": owner,
                    "lease_expires_at": lease,
                    "heartbeat_at": now,
                    "progress": f"worker request #{request['id']} running",
                    "started_at": now,
                    "updated_at": now,
                }
            )
            return request

    def complete_worker_request(self, request_id: int, result: str) -> bool:
        now = utc_now()
        result = str(result or "").strip()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM worker_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return False
            task_row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (int(row["task_id"]),),
            ).fetchone()
            if task_row is None or task_row["status"] == "canceled":
                conn.execute(
                    """
                    UPDATE worker_requests
                    SET status = 'failed',
                        error = ?,
                        owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = ?,
                        progress = 'worker request canceled',
                        completed_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ('pending', 'running')
                    """,
                    (
                        "Worker request canceled because the task stopped.",
                        now,
                        now,
                        now,
                        request_id,
                    ),
                )
                return False
            conn.execute(
                """
                UPDATE worker_requests
                SET status = 'done',
                    result = ?,
                    error = '',
                    owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = ?,
                    progress = 'worker request completed',
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (result, now, now, now, request_id),
            )
            detail = f"{row['worker_role_key']}: report received"
            self._insert_event(
                conn,
                int(row["task_id"]),
                "worker_delegation",
                worker_request_delegation_message(
                    request_id,
                    str(row["worker_role_key"]),
                    "ran",
                    detail,
                    status="done",
                ),
                now,
            )
            if result:
                self._insert_event(
                    conn,
                    int(row["task_id"]),
                    "worker_stream",
                    f"{row['worker_role_key']} report\n\n{result}",
                    now,
                )
            self._insert_event(
                conn,
                int(row["task_id"]),
                "worker_completed",
                f"{row['worker_role_key']} completed worker request #{request_id}.",
                now,
            )
            return True

    def fail_worker_request(self, request_id: int, error: str) -> bool:
        now = utc_now()
        error = str(error or "Worker request failed.").strip()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM worker_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return False
            task_row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (int(row["task_id"]),),
            ).fetchone()
            if task_row is None or task_row["status"] == "canceled":
                conn.execute(
                    """
                    UPDATE worker_requests
                    SET status = 'failed',
                        error = ?,
                        owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = ?,
                        progress = 'worker request canceled',
                        completed_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ('pending', 'running')
                    """,
                    (
                        "Worker request canceled because the task stopped.",
                        now,
                        now,
                        now,
                        request_id,
                    ),
                )
                return False
            conn.execute(
                """
                UPDATE worker_requests
                SET status = 'failed',
                    error = ?,
                    owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = ?,
                    progress = 'worker request failed',
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error, now, now, now, request_id),
            )
            detail = f"{row['worker_role_key']}: failed"
            self._insert_event(
                conn,
                int(row["task_id"]),
                "worker_delegation",
                worker_request_delegation_message(
                    request_id,
                    str(row["worker_role_key"]),
                    "ran",
                    detail,
                    status="failed",
                    content=error,
                ),
                now,
            )
            self._insert_event(
                conn,
                int(row["task_id"]),
                "worker_failed",
                f"{row['worker_role_key']} failed worker request #{request_id}: {error}",
                now,
            )
            return True

    def cancel_worker_request(self, request_id: int, reason: str) -> None:
        now = utc_now()
        reason = str(reason or "Worker request canceled.").strip()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE worker_requests
                SET status = 'failed',
                    error = ?,
                    owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = ?,
                    progress = 'worker request canceled',
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('pending', 'running')
                """,
                (reason, now, now, now, request_id),
            )

    def acquire_runtime_lock(
        self,
        resource: str,
        owner_plane: str,
        owner_id: str,
        lease_seconds: int,
        task_id: int | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now()
        now_ts = time.time()
        lease = now_ts + max(1, lease_seconds)
        token = secrets.token_urlsafe(16)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM runtime_locks
                WHERE resource = ?
                """,
                (resource,),
            ).fetchone()
            if row is not None and float(row["lease_expires_at"]) >= now_ts:
                return None
            if row is None:
                conn.execute(
                    """
                    INSERT INTO runtime_locks (
                        resource, owner_plane, owner_id, task_id, fencing_token,
                        heartbeat_at, lease_expires_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (resource, owner_plane, owner_id, task_id, token, now, lease, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE runtime_locks
                    SET owner_plane = ?,
                        owner_id = ?,
                        task_id = ?,
                        fencing_token = ?,
                        heartbeat_at = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE resource = ?
                    """,
                    (owner_plane, owner_id, task_id, token, now, lease, now, resource),
                )
            return {
                "resource": resource,
                "owner_plane": owner_plane,
                "owner_id": owner_id,
                "task_id": task_id,
                "fencing_token": token,
                "heartbeat_at": now,
                "lease_expires_at": lease,
                "updated_at": now,
            }

    def assign_runtime_lock_task(
        self,
        resource: str,
        fencing_token: str,
        task_id: int,
    ) -> bool:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE runtime_locks
                SET task_id = ?,
                    updated_at = ?
                WHERE resource = ?
                  AND fencing_token = ?
                """,
                (task_id, now, resource, fencing_token),
            )
            return cur.rowcount > 0

    def heartbeat_runtime_lock(
        self,
        resource: str,
        fencing_token: str,
        lease_seconds: int,
    ) -> bool:
        now = utc_now()
        lease = time.time() + max(1, lease_seconds)
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE runtime_locks
                SET heartbeat_at = ?,
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE resource = ?
                  AND fencing_token = ?
                """,
                (now, lease, now, resource, fencing_token),
            )
            return cur.rowcount > 0

    def release_runtime_lock(self, resource: str, fencing_token: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM runtime_locks
                WHERE resource = ?
                  AND fencing_token = ?
                """,
                (resource, fencing_token),
            )
            return cur.rowcount > 0

    def get_runtime_lock(self, resource: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM runtime_locks
                WHERE resource = ?
                """,
                (resource,),
            ).fetchone()
        if row is None:
            return None
        lock = dict(row)
        lock["expired"] = float(lock["lease_expires_at"]) < time.time()
        return lock

    def ui_revision(self) -> dict[str, Any]:
        with self.connect() as conn:
            latest_event = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS latest_id FROM events"
            ).fetchone()
            task_rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
            runtime_rows = conn.execute("SELECT * FROM manager_runtime ORDER BY role_key").fetchall()
            lock_rows = conn.execute("SELECT * FROM runtime_locks ORDER BY resource").fetchall()
            worker_request_rows = conn.execute("SELECT * FROM worker_requests ORDER BY id").fetchall()

        task_latest_updated_at = max((str(row["updated_at"]) for row in task_rows), default="")
        runtime_latest_updated_at = max((str(row["updated_at"]) for row in runtime_rows), default="")
        lock_latest_updated_at = max((str(row["updated_at"]) for row in lock_rows), default="")
        worker_request_latest_updated_at = max(
            (str(row["updated_at"]) for row in worker_request_rows),
            default="",
        )
        task_digest = rows_digest(task_rows)
        runtime_digest = rows_digest(runtime_rows)
        lock_digest = rows_digest(lock_rows)
        worker_request_digest = rows_digest(worker_request_rows)

        agents_mtime = 0
        try:
            agents_mtime = AGENTS_PATH.stat().st_mtime_ns
        except OSError:
            pass

        revision = ":".join(
            [
                str(int(latest_event["latest_id"])),
                str(len(task_rows)),
                task_latest_updated_at,
                task_digest,
                str(len(runtime_rows)),
                runtime_latest_updated_at,
                runtime_digest,
                str(len(lock_rows)),
                lock_latest_updated_at,
                lock_digest,
                str(len(worker_request_rows)),
                worker_request_latest_updated_at,
                worker_request_digest,
                str(agents_mtime),
            ]
        )
        return {
            "revision": revision,
            "latest_event_id": int(latest_event["latest_id"]),
            "task_digest": task_digest,
            "runtime_digest": runtime_digest,
            "lock_digest": lock_digest,
            "worker_request_digest": worker_request_digest,
        }

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def claim_next_task_for_manager(
        self,
        owner: str,
        lease_seconds: int,
        manager_role_key: str,
    ) -> dict[str, Any] | None:
        now = utc_now()
        lease = time.time() + lease_seconds
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            runtime = conn.execute(
                """
                SELECT status, current_task_id
                FROM manager_runtime
                WHERE role_key = ?
                """,
                (manager_role_key,),
            ).fetchone()
            if runtime is not None and runtime["status"] != "idle":
                if runtime["status"] != "busy":
                    return None
                current_task_id = runtime["current_task_id"]
                if current_task_id is not None:
                    task_state = conn.execute(
                        """
                        SELECT status
                        FROM tasks
                        WHERE id = ?
                        """,
                        (current_task_id,),
                    ).fetchone()
                    if task_state is not None and task_state["status"] == "in_progress":
                        return None
                conn.execute(
                    """
                    UPDATE manager_runtime
                    SET status = 'idle',
                        current_task_id = NULL,
                        heartbeat_at = NULL,
                        updated_at = ?
                    WHERE role_key = ?
                    """,
                    (now, manager_role_key),
                )
                self._insert_event(
                    conn,
                    None,
                    "manager_runtime_reclaimed",
                    f"Reclaimed stale runtime state for {manager_role_key}.",
                    now,
                )

            row = conn.execute(
                """
                SELECT *
                FROM tasks
                WHERE status = 'pending'
                ORDER BY queued_at IS NULL ASC, queued_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None

            task = dict(row)
            conn.execute(
                """
                UPDATE tasks
                SET status = 'in_progress',
                    owner = ?,
                    lease_expires_at = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (owner, lease, now, task["id"]),
            )
            conn.execute(
                """
                INSERT INTO manager_runtime (
                    role_key, status, current_task_id, heartbeat_at, updated_at
                )
                VALUES (?, 'busy', ?, ?, ?)
                ON CONFLICT(role_key) DO UPDATE SET
                    status = 'busy',
                    current_task_id = excluded.current_task_id,
                    heartbeat_at = excluded.heartbeat_at,
                    updated_at = excluded.updated_at
                """,
                (manager_role_key, task["id"], now, now),
            )
            self._insert_event(conn, task["id"], "task_claimed", f"Claimed by {owner}.", now)
            self._insert_event(
                conn,
                task["id"],
                "manager_claimed",
                f"Assigned to manager {manager_role_key}.",
                now,
            )
            return task

    def get_manager_runtime(self, role_key: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT role_key, status, current_task_id, heartbeat_at, updated_at
                FROM manager_runtime
                WHERE role_key = ?
                """,
                (role_key,),
            ).fetchone()
            if row:
                return dict(row)
            return {
                "role_key": role_key,
                "status": "idle",
                "current_task_id": None,
                "heartbeat_at": None,
                "updated_at": None,
            }

    def list_manager_runtime(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT role_key, status, current_task_id, heartbeat_at, updated_at
                FROM manager_runtime
                ORDER BY role_key ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def touch_manager_runtime(self, role_key: str, task_id: int) -> bool:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE manager_runtime
                SET heartbeat_at = ?, updated_at = ?
                WHERE role_key = ?
                  AND status = 'busy'
                  AND current_task_id = ?
                """,
                (now, now, role_key, task_id),
            )
            return cur.rowcount > 0

    def release_manager(self, role_key: str, task_id: int) -> bool:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE manager_runtime
                SET status = 'idle',
                    current_task_id = NULL,
                    heartbeat_at = NULL,
                    updated_at = ?
                WHERE role_key = ?
                  AND current_task_id = ?
                """,
                (now, role_key, task_id),
            )
            return cur.rowcount > 0

    def touch_lease(self, task_id: int, lease_seconds: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (time.time() + lease_seconds, utc_now(), task_id),
            )

    def complete_task(self, task_id: int, result: str) -> bool:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE tasks
                SET status = 'done',
                    result = ?,
                    error = '',
                    owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND status != 'canceled'
                """,
                (result, now, task_id),
            )
            if cur.rowcount > 0:
                self._insert_event(conn, task_id, "task_done", "Task completed.", now)
                return True
            return False

    def require_approval(self, task_id: int, result: str) -> None:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE tasks
                SET status = 'needs_approval',
                    result = ?,
                    owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND status != 'canceled'
                """,
                (result, now, task_id),
            )
            if cur.rowcount > 0:
                self._insert_event(
                    conn,
                    task_id,
                    "approval_required",
                    "Risky task paused until the user approves it.",
                    now,
                )

    def fail_task(self, task_id: int, error: str) -> None:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error = ?,
                    owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND status != 'canceled'
                """,
                (error, now, task_id),
            )
            if cur.rowcount > 0:
                self._insert_event(conn, task_id, "task_failed", error, now)

    def approve_task(self, task_id: int, note: str = "") -> bool:
        now = utc_now()
        queued_at = queue_now()
        approval_note = str(note or "").strip()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status, acceptance_criteria FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return False
            acceptance = str(row["acceptance_criteria"] or "")
            if approval_note:
                note_section = f"User approval note:\n{approval_note}"
                acceptance = f"{acceptance.rstrip()}\n\n{note_section}" if acceptance.strip() else note_section
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    approved = 1,
                    owner = NULL,
                    lease_expires_at = NULL,
                    acceptance_criteria = ?,
                    queued_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (acceptance, queued_at, now, task_id),
            )
            message = "User approved retry with a note." if approval_note else "User approved retry."
            self._insert_event(conn, task_id, "task_approved", message, now)
            return True

    def queue_task(self, task_id: int) -> bool:
        now = utc_now()
        queued_at = queue_now()
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    owner = NULL,
                    lease_expires_at = NULL,
                    queued_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (queued_at, now, task_id),
            )
            self._insert_event(conn, task_id, "task_queued", "User moved task from Inbox to Pending.", now)
            return True

    def delete_task(self, task_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM worker_requests WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return True

    def retry_task(self, task_id: int) -> bool:
        now = utc_now()
        queued_at = queue_now()
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    owner = NULL,
                    lease_expires_at = NULL,
                    error = '',
                    queued_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (queued_at, now, task_id),
            )
            self._insert_event(conn, task_id, "task_retried", "User queued task again.", now)
            return True

    def cancel_task(self, task_id: int) -> bool:
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE tasks
                SET status = 'canceled',
                    owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
            self._insert_event(conn, task_id, "task_canceled", "User canceled the task.", now)
            return True

    def add_event(self, task_id: int | None, event_type: str, message: str) -> None:
        with self.connect() as conn:
            self._insert_event(conn, task_id, event_type, message, utc_now())

    def add_event_if_task_active(self, task_id: int, event_type: str, message: str) -> bool:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None or row["status"] == "canceled":
                return False
            self._insert_event(conn, task_id, event_type, message, now)
            return True

    def reclaim_expired(self, exclude_task_ids: set[int] | None = None) -> None:
        exclude_task_ids = exclude_task_ids or set()
        now_ts = time.time()
        now = utc_now()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM tasks
                WHERE status = 'in_progress'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now_ts,),
            ).fetchall()
            for row in rows:
                task_id = int(row["id"])
                if task_id in exclude_task_ids:
                    continue
                queued_at = queue_now()
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'pending',
                        owner = NULL,
                        lease_expires_at = NULL,
                        queued_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (queued_at, now, task_id),
                )
                self._insert_event(
                    conn,
                    task_id,
                    "lease_expired",
                    "Task lease expired and was returned to the queue.",
                    now,
                )
                conn.execute(
                    """
                    UPDATE manager_runtime
                    SET status = 'idle',
                        current_task_id = NULL,
                        heartbeat_at = NULL,
                        updated_at = ?
                    WHERE current_task_id = ?
                    """,
                    (now, task_id),
                )

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        task_id: int | None,
        event_type: str,
        message: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO events (task_id, type, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, event_type, message, created_at),
        )


class DispatcherServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], store: Store):
        super().__init__(address, handler)
        self.store = store


class Handler(BaseHTTPRequestHandler):
    server: DispatcherServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/static/"):
            self.send_static(path.removeprefix("/static/"))
            return
        if path == "/logout":
            self.send_redirect("/login", clear_cookie=True)
            return
        if path == "/login":
            if self.is_authenticated():
                self.send_redirect("/")
            else:
                self.send_login()
            return
        if not self.require_authenticated(path):
            return
        if path == "/":
            self.send_template("index.html", {"PROJECT_NAME": html_escape(PROJECT_NAME)})
            return
        if path == "/api/tasks":
            self.send_json({"tasks": self.server.store.list_tasks()})
            return
        if path == "/api/activity":
            self.send_json(
                {
                    "manager_activity": list_manager_activity(self.server.store),
                }
            )
            return
        if path == "/api/agents":
            self.send_json({"agents": list_agent_states(self.server.store)})
            return
        if path == "/api/runtime-lock":
            lock = public_runtime_lock(self.server.store.get_runtime_lock(GLOBAL_RUNTIME_LOCK))
            self.send_json({"lock": lock})
            return
        if path == "/api/directory":
            self.send_json({"directory": list_directory_tree()})
            return
        if path == "/api/stream":
            self.send_event_stream()
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/login":
            payload = self.read_payload()
            password = str(payload.get("password") or "")
            if secrets.compare_digest(password, AUTH_PASSWORD):
                self.send_redirect("/", set_cookie=True)
            else:
                self.send_login("Wrong password.", status=401)
            return
        if not self.is_authenticated():
            self.discard_request_body()
            self.require_authenticated(path)
            return
        if path == "/api/tasks":
            payload = self.read_json()
            task_id = self.server.store.create_task(payload)
            self.send_json({"id": task_id}, status=201)
            return

        match = re.fullmatch(r"/api/tasks/(\d+)/(approve|retry|cancel|queue|delete)", path)
        if match:
            task_id = int(match.group(1))
            action = match.group(2)
            if action == "approve":
                payload = self.read_json()
                ok = self.server.store.approve_task(task_id, str(payload.get("note") or ""))
            else:
                self.discard_request_body()
                ok = {
                    "retry": self.server.store.retry_task,
                    "cancel": self.server.store.cancel_task,
                    "queue": self.server.store.queue_task,
                    "delete": self.server.store.delete_task,
                }[action](task_id)
            if not ok:
                self.send_error(404, "Task not found")
                return
            self.send_json({"ok": True})
            return

        self.discard_request_body()
        self.send_error(404, "Not found")

    def is_authenticated(self) -> bool:
        raw_cookie = self.headers.get("Cookie", "")
        cookie = SimpleCookie(raw_cookie)
        morsel = cookie.get(COOKIE_NAME)
        if morsel is None:
            return False
        return secrets.compare_digest(morsel.value, SESSION_TOKEN)

    def require_authenticated(self, path: str) -> bool:
        if self.is_authenticated():
            return True
        if path.startswith("/api/"):
            self.send_json({"error": "Authentication required."}, status=401)
        else:
            self.send_login()
        return False

    def read_payload(self) -> dict[str, Any]:
        raw_bytes = self.read_body()
        if not raw_bytes:
            return {}
        raw = raw_bytes.decode("utf-8")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(raw)
        parsed = parse_qs(raw)
        return {key: values[-1] if values else "" for key, values in parsed.items()}

    def read_json(self) -> dict[str, Any]:
        raw_bytes = self.read_body()
        if not raw_bytes:
            return {}
        raw = raw_bytes.decode("utf-8")
        return json.loads(raw)

    def read_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            chunks = []
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                size_text = line.split(b";", 1)[0].strip()
                try:
                    size = int(size_text, 16)
                except ValueError:
                    break
                if size == 0:
                    while True:
                        trailer = self.rfile.readline()
                        if trailer in (b"", b"\r\n", b"\n"):
                            break
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(chunks)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def discard_request_body(self) -> None:
        self.read_body()

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_event_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        current_revision = self.server.store.ui_revision()
        heartbeat_at = time.time()
        try:
            self.write_sse("snapshot", current_revision)
            while True:
                time.sleep(0.5)
                next_revision = self.server.store.ui_revision()
                if next_revision["revision"] != current_revision["revision"]:
                    current_revision = next_revision
                    self.write_sse("update", current_revision)
                    heartbeat_at = time.time()
                elif time.time() - heartbeat_at >= 5:
                    self.write_sse("update", current_revision)
                    heartbeat_at = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def write_sse(self, event: str, payload: dict[str, Any]) -> None:
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        data = json.dumps(payload, ensure_ascii=False)
        for line in data.splitlines():
            self.wfile.write(f"data: {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    def send_login(self, error: str = "", status: int = 200) -> None:
        error_html = f'<div class="error">{html_escape(error)}</div>' if error else ""
        self.send_template("login.html", {"ERROR": error_html}, status=status)

    def send_template(
        self,
        name: str,
        replacements: dict[str, str] | None = None,
        *,
        status: int = 200,
    ) -> None:
        html = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        for key, value in (replacements or {}).items():
            html = html.replace(f"{{{{{key}}}}}", value)
        self.send_html(html, status=status)

    def send_static(self, relative_path: str) -> None:
        requested = (STATIC_DIR / relative_path).resolve()
        try:
            requested.relative_to(STATIC_DIR)
        except ValueError:
            self.send_error(404, "Not found")
            return
        if not requested.is_file():
            self.send_error(404, "Not found")
            return
        data = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", STATIC_TYPES.get(requested.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(
        self,
        location: str,
        *,
        set_cookie: bool = False,
        clear_cookie: bool = False,
    ) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={SESSION_TOKEN}; Path=/; HttpOnly; SameSite=Lax",
            )
        if clear_cookie:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
            )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_html(self, html: str, status: int = 200) -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        message = fmt % args
        print(f"{self.address_string()} - {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the dispatcher server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--db", default="data/dispatcher.db")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not AUTH_PASSWORD:
        raise SystemExit(
            "DISPATCHER_PASSWORD is required. Run scripts/run_dispatcher_tunnel.py init or set it in the environment."
        )
    store = Store(Path(args.db))
    server = DispatcherServer((args.host, args.port), Handler, store)
    print(f"Dispatcher web server running at http://{args.host}:{args.port}")
    print(f"SQLite database: {Path(args.db).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dispatcher...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
