from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DECISIONS = {"execute", "needs_approval", "failed"}
MAX_RUNNER_EVENT_MESSAGE_CHARS = 800
MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS = 12000
STRUCTURED_RUNNER_EVENT_SUFFIXES = ("_command", "_file_change", "_delegation")
INTERNAL_DELEGATION_STATUSES = {"pending_init"}
TERMINAL_DELEGATION_STATUSES = {"completed", "done"}
NOISY_CODEX_STDERR_MARKERS = (
    "WARN codex_core_plugins::manifest: ignoring interface.defaultPrompt",
    "WARN codex_core_skills::loader: ignoring interface.icon_",
    "WARN codex_core::file_watcher: failed to unwatch",
    "WARN codex_analytics::reducer: dropping tool item analytics event",
    "challenge-error-text",
    "__cf_chl_",
)
CODEX_APPROVAL_POLICY = "never"
CODEX_SANDBOX_MODE = "workspace-write"
EventCallback = Callable[[str, str], None]
TranscriptCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


class CodexRunCancelled(RuntimeError):
    """Raised when a running Codex subprocess is stopped by a caller cancellation check."""


@dataclass(frozen=True)
class ManagerDecision:
    decision: str
    summary: str
    result: str = ""
    reason: str = ""
    error: str = ""
    manager_key: str | None = None


class CodexManagerRunner:
    def __init__(
        self,
        repo: Path,
        codex_bin: str = "codex",
        run_dir: Path | None = None,
        timeout_seconds: float = 1800,
        conversation_dir: Path | None = None,
    ):
        self.repo = repo
        self.codex_bin = codex_bin
        self.run_dir = run_dir or repo / "data" / "codex_runs"
        self.conversation_dir = conversation_dir or repo / "data" / "agent_conversations"
        self.timeout_seconds = timeout_seconds
        self.schema_path = Path(__file__).resolve().parent / "schemas" / "manager_decision.schema.json"

    def plan(
        self,
        task: dict[str, Any],
        manager: Any,
        worker_catalog: list[dict[str, str | None]],
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ManagerDecision:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.conversation_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"task-{task['id']}-{int(time.time())}"
        output_path = self.run_dir / f"{run_id}.json"
        prompt = build_manager_prompt(task, manager, worker_catalog, repo=self.repo)
        command = self._command(manager.key, output_path)
        workspace_root = codex_workspace_root(self.repo)
        manager_log_path = agent_log_path(self.conversation_dir, manager.role_key)
        base_record = {
            "run_id": run_id,
            "task_id": task["id"],
            "created_at": utc_now(),
        }
        append_agent_jsonl(
            manager_log_path,
            {
                **base_record,
                "agent_role_key": manager.role_key,
                "agent_name": manager.name,
                "direction": "request",
                "kind": "manager_prompt",
                "content": prompt,
                "output_path": output_path.as_posix(),
            },
        )

        def append_transcript(record: dict[str, Any]) -> None:
            transcript_record = {
                **base_record,
                "created_at": utc_now(),
                "agent_role_key": manager.role_key,
                "agent_name": manager.name,
                **record,
            }
            append_agent_jsonl(manager_log_path, transcript_record)

        try:
            returncode, stdout, stderr = run_codex_streaming(
                command,
                prompt,
                timeout=self.timeout_seconds,
                cwd=workspace_root,
                worker_catalog=worker_catalog,
                event_callback=event_callback,
                transcript_callback=append_transcript,
                cancel_check=cancel_check,
            )
        except CodexRunCancelled:
            append_agent_jsonl(
                manager_log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "agent_role_key": manager.role_key,
                    "agent_name": manager.name,
                    "direction": "response",
                    "kind": "runner_canceled",
                    "error": "Codex manager run canceled because the task stopped.",
                },
            )
            raise
        except FileNotFoundError:
            append_agent_jsonl(
                manager_log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "agent_role_key": manager.role_key,
                    "agent_name": manager.name,
                    "direction": "response",
                    "kind": "runner_error",
                    "error": f"Executable not found: {self.codex_bin}",
                },
            )
            return ManagerDecision(
                decision="failed",
                summary="Codex CLI was not found.",
                error=f"Executable not found: {self.codex_bin}",
            )
        except subprocess.TimeoutExpired:
            append_agent_jsonl(
                manager_log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "agent_role_key": manager.role_key,
                    "agent_name": manager.name,
                    "direction": "response",
                    "kind": "runner_error",
                    "error": f"Timed out after {self.timeout_seconds} seconds.",
                },
            )
            return ManagerDecision(
                decision="failed",
                summary="Codex manager call timed out.",
                error=f"Timed out after {self.timeout_seconds} seconds.",
                manager_key=manager.key,
            )

        thread_id, event_error = parse_codex_events(stdout)
        manager_key = manager.key or thread_id

        try:
            final_text = output_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            error = codex_failure_error(returncode, stderr, event_error, stdout)
            append_agent_jsonl(
                manager_log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "agent_role_key": manager.role_key,
                    "agent_name": manager.name,
                    "direction": "response",
                    "kind": "runner_error",
                    "returncode": returncode,
                    "error": error or repr(exc),
                },
            )
            return ManagerDecision(
                decision="failed",
                summary=(
                    "Codex manager call failed before writing a final message."
                    if returncode != 0
                    else "Codex manager did not write a final message file."
                ),
                error=error or repr(exc),
                manager_key=manager_key,
            )

        try:
            decision = parse_manager_decision(final_text)
        except json.JSONDecodeError as exc:
            error = codex_failure_error(returncode, stderr, event_error, stdout)
            append_agent_jsonl(
                manager_log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "agent_role_key": manager.role_key,
                    "agent_name": manager.name,
                    "direction": "response",
                    "kind": "manager_final_parse_error",
                    "returncode": returncode,
                    "content": final_text,
                    "error": repr(exc),
                    "runner_error": error,
                },
            )
            return ManagerDecision(
                decision="failed",
                summary=(
                    "Codex manager call failed and returned an unparseable final message."
                    if returncode != 0
                    else "Codex manager returned an unparseable final message."
                ),
                error=error or repr(exc),
                manager_key=manager_key,
            )
        if returncode != 0:
            append_agent_jsonl(
                manager_log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "agent_role_key": manager.role_key,
                    "agent_name": manager.name,
                    "direction": "response",
                    "kind": "runner_warning",
                    "returncode": returncode,
                    "warning": codex_failure_error(returncode, stderr, event_error, stdout)
                    or f"codex exited with status {returncode}",
                },
            )
        final_record = {
            **base_record,
            "created_at": utc_now(),
            "agent_role_key": manager.role_key,
            "agent_name": manager.name,
            "direction": "response",
            "kind": "manager_final",
            "content": final_text,
            "decision": {
                "decision": decision.decision,
                "summary": decision.summary,
                "result": decision.result,
                "reason": decision.reason,
                "error": decision.error,
            },
            "manager_key": manager_key,
        }
        append_agent_jsonl(manager_log_path, final_record)
        return ManagerDecision(
            decision=decision.decision,
            summary=decision.summary,
            result=decision.result,
            reason=decision.reason,
            error=decision.error,
            manager_key=manager_key,
        )

    def _command(self, manager_key: str | None, output_path: Path) -> list[str]:
        command_prefix = codex_exec_prefix(self.codex_bin, self.repo)
        if manager_key:
            return [
                *command_prefix,
                "resume",
                "--json",
                "--skip-git-repo-check",
                "-o",
                str(output_path),
                manager_key,
                "-",
            ]
        return [
            *command_prefix,
            "--json",
            "--skip-git-repo-check",
            "--output-schema",
            str(self.schema_path),
            "-o",
            str(output_path),
            "-",
        ]


def codex_exec_prefix(codex_bin: str, repo: Path) -> list[str]:
    workspace_root = codex_workspace_root(repo)
    return [
        codex_bin,
        "--ask-for-approval",
        CODEX_APPROVAL_POLICY,
        "--disable",
        "plugins",
        "exec",
        "--sandbox",
        CODEX_SANDBOX_MODE,
        "--cd",
        str(workspace_root),
    ]


def codex_workspace_root(repo: Path) -> Path:
    repo = repo.resolve()
    if repo.name == "dispatcher-skill" and repo.parent.name == "skills":
        outer = repo.parent.parent
        try:
            if (outer / "skills" / "dispatcher-skill").resolve() == repo:
                return outer
        except OSError:
            pass
    return repo


def skill_root_command(repo: Path, command: str) -> str:
    repo = repo.resolve()
    workspace_root = codex_workspace_root(repo)
    try:
        relative = repo.relative_to(workspace_root)
    except ValueError:
        return command
    if relative == Path("."):
        return command
    return f"cd {shlex.quote(relative.as_posix())} && {command}"


def run_codex_streaming(
    command: list[str],
    prompt: str,
    *,
    timeout: float,
    cwd: Path,
    worker_catalog: list[dict[str, str | None]] | None = None,
    event_callback: EventCallback | None = None,
    transcript_callback: TranscriptCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> tuple[int, str, str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            emit_transcript(
                transcript_callback,
                {
                    "direction": "event",
                    "kind": "codex_stdout_json",
                    "stream": "stdout",
                    "payload": event,
                },
            )
            for activity_event in codex_activity_events(event, worker_catalog or []):
                emit_runner_event(event_callback, activity_event[0], activity_event[1])

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line)
            message = line.strip()
            if message:
                emit_transcript(
                    transcript_callback,
                    {
                        "direction": "event",
                        "kind": "codex_stderr",
                        "stream": "stderr",
                        "content": message,
                    },
                )

    stdout_thread = threading.Thread(target=read_stdout, name="codex-stdout-reader", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name="codex-stderr-reader", daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    def stop_process(force: bool = False) -> None:
        if process.poll() is not None:
            return
        if force:
            process.kill()
        else:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    try:
        if process.stdin is not None:
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except BrokenPipeError:
                pass
        deadline = time.monotonic() + timeout
        while True:
            if cancel_check is not None and cancel_check():
                stop_process()
                raise CodexRunCancelled("Codex run canceled because the task stopped.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_process(force=True)
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                returncode = process.wait(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except subprocess.TimeoutExpired:
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        raise
    except CodexRunCancelled:
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        raise

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    return returncode, "".join(stdout_lines), "".join(stderr_lines)


def codex_failure_error(
    returncode: int,
    stderr: str,
    event_error: str | None,
    stdout: str,
) -> str:
    if returncode == 0:
        return ""
    return (
        actionable_codex_stderr(stderr)
        or event_error
        or actionable_codex_stdout(stdout)
        or f"codex exited with status {returncode}"
    )


def actionable_codex_stderr(stderr: str) -> str:
    lines = [
        line.strip()
        for line in stderr.splitlines()
        if line.strip() and not is_noisy_codex_stderr_line(line)
    ]
    return "\n".join(lines)


def is_noisy_codex_stderr_line(line: str) -> bool:
    return any(marker in line for marker in NOISY_CODEX_STDERR_MARKERS)


def actionable_codex_stdout(stdout: str) -> str:
    lines = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            json.loads(text)
        except json.JSONDecodeError:
            lines.append(text)
    return "\n".join(lines)


def emit_runner_event(
    event_callback: EventCallback | None,
    event_type: str,
    message: str,
) -> None:
    if event_callback is None:
        return
    message = message.replace("\r", "").strip()
    if not message:
        return
    try:
        event_callback(event_type, runner_event_message_for_db(event_type, message))
    except Exception:
        pass


def runner_event_message_for_db(event_type: str, message: str) -> str:
    if not is_structured_runner_event(event_type):
        return message[:MAX_RUNNER_EVENT_MESSAGE_CHARS]
    if len(message) <= MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS:
        return message
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return message[:MAX_RUNNER_EVENT_MESSAGE_CHARS]
    if not isinstance(payload, dict):
        return message[:MAX_RUNNER_EVENT_MESSAGE_CHARS]
    if event_type.endswith("_command"):
        return truncated_command_payload(payload)
    if event_type.endswith("_file_change"):
        return truncated_file_change_payload(payload)
    if event_type.endswith("_delegation"):
        return truncated_delegation_payload(payload)
    return message[:MAX_RUNNER_EVENT_MESSAGE_CHARS]


def is_structured_runner_event(event_type: str) -> bool:
    return event_type.endswith(STRUCTURED_RUNNER_EVENT_SUFFIXES)


def truncated_command_payload(payload: dict[str, Any]) -> str:
    original_command = str(payload.get("command") or "")
    budget = min(len(original_command), MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS - 500)
    while True:
        command = original_command
        if len(original_command) > budget:
            command = original_command[:budget].rstrip() + "\n[truncated]"
        compact = {
            "command_id": payload.get("command_id") or "",
            "state": payload.get("state") or "running",
            "command": command,
        }
        encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS or budget <= 1000:
            return encoded
        budget = max(1000, budget - (len(encoded) - MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS) - 200)


def truncated_file_change_payload(payload: dict[str, Any]) -> str:
    changes = payload.get("changes")
    if not isinstance(changes, list):
        changes = []
    kept_changes = changes[:200]
    if len(changes) > len(kept_changes):
        kept_changes.append({"kind": "truncated", "path": f"{len(changes) - len(kept_changes)} more changes"})
    compact = {
        "change_id": payload.get("change_id") or "",
        "state": payload.get("state") or "running",
        "changes": kept_changes,
    }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def truncated_delegation_payload(payload: dict[str, Any]) -> str:
    original_content = str(payload.get("content") or payload.get("detail") or "")
    budget = min(len(original_content), MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS - 700)
    while True:
        content = original_content
        if len(original_content) > budget:
            content = original_content[:budget].rstrip() + "\n[truncated]"
        compact = {
            "delegation_id": payload.get("delegation_id") or "",
            "state": payload.get("state") or "running",
            "detail": str(payload.get("detail") or "worker delegation")[:1000],
            "content": content,
            "status": str(payload.get("status") or "")[:80],
        }
        encoded = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS or budget <= 1000:
            return encoded
        budget = max(1000, budget - (len(encoded) - MAX_STRUCTURED_RUNNER_EVENT_MESSAGE_CHARS) - 200)


def emit_transcript(
    transcript_callback: TranscriptCallback | None,
    record: dict[str, Any],
) -> None:
    if transcript_callback is None:
        return
    try:
        transcript_callback(record)
    except Exception:
        pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def agent_log_path(base_dir: Path, role_key: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", role_key).strip("._") or "agent"
    return base_dir / f"{safe_name}.jsonl"


def append_agent_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def codex_activity_events(
    event: dict[str, Any],
    worker_catalog: list[dict[str, str | None]] | None = None,
) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    activity_event = codex_activity_event(event, worker_catalog or [])
    if activity_event:
        events.append(activity_event)
    return events


def codex_activity_event(
    event: dict[str, Any],
    worker_catalog: list[dict[str, str | None]] | None = None,
) -> tuple[str, str] | None:
    event_type = str(event.get("type") or "event")
    if event_type.startswith("item."):
        item = event.get("item")
        if isinstance(item, dict) and str(item.get("type") or "") == "command_execution":
            message = summarize_codex_command_event(event)
            return ("manager_command", message) if message else None
        if isinstance(item, dict) and str(item.get("type") or "") == "file_change":
            message = summarize_codex_file_change_event(event)
            return ("manager_file_change", message) if message else None
        if isinstance(item, dict) and str(item.get("type") or "") == "collab_tool_call":
            event_type, message = summarize_codex_delegation_event(event, worker_catalog or [])
            return (event_type, message) if message else None
        message = summarize_codex_item_event(event)
        return ("manager_item", message) if message else None
    message = summarize_codex_event(event)
    return ("manager_stream", message) if message else None


def summarize_codex_event(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    if event_type in {
        "agent_message.delta",
        "raw_model_stream_event",
        "response.output_text.delta",
        "token",
        "thread.started",
        "turn.started",
        "turn.completed",
    }:
        return ""
    if event_type.startswith("item."):
        return ""
    if event_type == "error":
        message = event.get("message")
        if message:
            return f"Codex error: {message}"
        return f"Codex error: {json.dumps(event, ensure_ascii=False)[:240]}"

    for key in ("message", "summary", "text", "status", "name", "command"):
        value = event.get(key)
        if value:
            text = str(value).replace("\n", " ").strip()
            return f"{event_type}: {text[:240]}"

    compact = {
        key: event[key]
        for key in ("status", "name", "thread_id", "turn_id", "item_id")
        if key in event
    }
    if compact:
        return f"{event_type}: {json.dumps(compact, ensure_ascii=False)}"
    return event_type


def summarize_codex_item_event(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "item")
    item = event.get("item")
    if not isinstance(item, dict):
        return event_type

    item_type = str(item.get("type") or "item")
    if item_type in {"agent_message", "assistant_message", "message"}:
        text = extract_message_text(item)
        return format_agent_message_text(text) if text else f"{event_type}: {item_type}"

    status = str(item.get("status") or "").strip()
    status_text = f" {status}" if status else ""
    if item_type == "command_execution":
        command = str(item.get("command") or "").strip()
        exit_code = item.get("exit_code")
        if exit_code is not None:
            status_text = f" exit {exit_code}"
        return "\n".join(part for part in (f"{event_type}: command{status_text}", command) if part)

    if item_type == "file_change":
        changes = item.get("changes")
        paths: list[str] = []
        if isinstance(changes, list):
            for change in changes[:6]:
                if isinstance(change, dict):
                    path = str(change.get("path") or "").strip()
                    kind = str(change.get("kind") or "").strip()
                    paths.append(f"{kind}: {path}" if kind and path else path or kind)
        details = "\n".join(path for path in paths if path)
        return "\n".join(part for part in (f"{event_type}: file_change{status_text}", details) if part)

    name = str(item.get("name") or item.get("id") or "").strip()
    return " ".join(part for part in (f"{event_type}: {item_type}{status_text}", name) if part)


def summarize_codex_command_event(event: dict[str, Any]) -> str:
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    command = str(item.get("command") or "").strip()
    if not command:
        return ""
    event_type = str(event.get("type") or "")
    state = "ran" if event_type == "item.completed" else "running"
    command_id = str(item.get("id") or item.get("item_id") or command).strip()
    payload = {
        "command_id": command_id,
        "state": state,
        "command": command,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def summarize_codex_file_change_event(event: dict[str, Any]) -> str:
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    event_type = str(event.get("type") or "")
    state = "ran" if event_type == "item.completed" else "running"
    change_id = str(item.get("id") or item.get("item_id") or "").strip()
    changes_payload: list[dict[str, str]] = []
    changes = item.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or "").strip()
            kind = str(change.get("kind") or "").strip()
            if path or kind:
                changes_payload.append({"kind": kind, "path": path})
    if not change_id:
        change_id = json.dumps(changes_payload, ensure_ascii=False, sort_keys=True)
    payload = {
        "change_id": change_id,
        "state": state,
        "changes": changes_payload,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def summarize_codex_delegation_event(
    event: dict[str, Any],
    worker_catalog: list[dict[str, str | None]],
) -> tuple[str, str]:
    item = event.get("item")
    if not isinstance(item, dict):
        return "manager_delegation", ""
    event_type = str(event.get("type") or "")
    delegation_id = str(item.get("id") or item.get("item_id") or "").strip()
    tool = str(item.get("tool") or "collaboration").strip()
    item_status = str(item.get("status") or "").strip()
    worker_labels = worker_labels_for_delegation(item, worker_catalog)
    target = ", ".join(worker_labels) if worker_labels else "collaboration"
    detail = " ".join(part for part in (target, tool) if part).strip()
    state = "ran" if event_type == "item.completed" else "running"
    content_parts = [detail]
    status_key = item_status.lower()
    if (
        item_status
        and item_status != "in_progress"
        and status_key not in INTERNAL_DELEGATION_STATUSES
    ):
        content_parts.append(f"status: {item_status}")
    prompt = str(item.get("prompt") or "").strip()
    if prompt:
        content_parts.append(prompt)
    for message in delegation_status_lines(item):
        if message:
            content_parts.append(message)
    payload = {
        "delegation_id": delegation_id or detail,
        "state": state,
        "detail": detail,
        "content": "\n\n".join(content_parts),
        "status": item_status,
    }
    return "manager_delegation", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def worker_labels_for_delegation(
    item: dict[str, Any],
    worker_catalog: list[dict[str, str | None]],
) -> list[str]:
    receiver_ids = {
        str(receiver_id)
        for receiver_id in item.get("receiver_thread_ids") or []
        if receiver_id
    }
    prompt = str(item.get("prompt") or "").lower()
    labels = []
    for worker in worker_catalog:
        role_key = str(worker.get("role_key") or "")
        name = str(worker.get("name") or "")
        key = str(worker.get("key") or "")
        if (
            (key and key in receiver_ids)
            or (role_key and role_key.lower() in prompt)
            or (name and name.lower() in prompt)
        ):
            labels.append(role_key or name or key)
    return labels


def delegation_status_lines(item: dict[str, Any], *, include_messages: bool = True) -> list[str]:
    agents_states = item.get("agents_states")
    if not isinstance(agents_states, dict):
        return []
    messages = []
    for agent_id, state in agents_states.items():
        if isinstance(state, dict):
            status = str(state.get("status") or "").strip()
            status_key = status.lower()
            if (
                status
                and status_key not in TERMINAL_DELEGATION_STATUSES
                and status_key not in INTERNAL_DELEGATION_STATUSES
            ):
                messages.append(f"{agent_id}: {status}")
            if not include_messages:
                continue
            message = str(state.get("message") or "").strip()
            if message:
                messages.append(message)
    return messages


def extract_message_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            text
            for item in value
            if (text := extract_message_text(item))
        ).strip()
    if not isinstance(value, dict):
        return ""
    for key in ("text", "message", "content", "output_text"):
        if key in value:
            text = extract_message_text(value[key])
            if text:
                return text
    return ""


def format_agent_message_text(text: str) -> str:
    message = text.strip()
    if not message.startswith("{"):
        return message
    try:
        payload = json.loads(message)
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


def build_manager_prompt(
    task: dict[str, Any],
    manager: Any,
    worker_catalog: list[dict[str, str | None]],
    repo: Path | None = None,
) -> str:
    repo_root = (repo or Path.cwd()).resolve()
    worker_command = skill_root_command(
        repo_root,
        f"python3 -m dispatcher_app.worker_client call --task-id {task['id']} --manager-role-key {manager.role_key} --worker-role-key <worker.role_key> --worker-name <manager-chosen-worker-name> --prompt-file <path>",
    )
    suggest_command = skill_root_command(
        repo_root,
        f"python3 -m dispatcher_app.task_client suggest --task-id {task['id']} --manager-role-key {manager.role_key} --items-file <path>",
    )
    payload = {
        "manager": {
            "role_key": manager.role_key,
            "name": manager.name,
            "key_status": manager.key_status,
        },
        "task": {
            "id": task["id"],
            "title": task["title"],
            "description": task["description"],
            "acceptance_criteria": task["acceptance_criteria"],
            "priority": task["priority"],
            "approved": bool(task["approved"]),
            "attempt_count": task["attempt_count"],
        },
        "worker_catalog": worker_catalog,
    }
    return "\n".join(
        [
            "You are the dispatcher manager for this repository.",
            "",
            "Process exactly one queued task. The Python dispatcher only calls you, not workers.",
            "Your file-action scope is the whole repository working tree passed to Codex, not only the dispatcher skill payload.",
            "If this skill is nested under skills/dispatcher-skill, repository-level files such as AGENTS.md and skill-migration-memory/ are in scope for non-runtime task work.",
            "Run dispatcher_app helper commands from the dispatcher skill root; use the command forms below.",
            "Use the worker_catalog as context for worker identities, but do not use Codex subagent tools for workers.",
            "If worker agents are useful, request a dispatcher-owned worker through the local worker client command.",
            "When initializing or renaming a dispatcher-owned worker, choose a concise human display name and pass it with --worker-name.",
            "Write the worker instructions to a temporary prompt file, then run:",
            worker_command,
            "The worker client queues the request in SQLite and blocks until the dispatcher-run worker finishes.",
            "Only the active manager for the current in-progress task can queue worker requests; this is the manager-worker mutex.",
            "Worker execution is transitional: until the dispatcher-owned worker path is verified end to end, you may still implement directly when worker use is unavailable, fails, or would block the task.",
            "Prefer delegating scoped implementation or verification to a worker when it is useful and feasible; after worker execution is proven reliable, manager direct implementation will be restricted.",
            "When delegating work or verification to a worker, keep the prompt bounded and include ownership, expected output, verification, and the worker role_key.",
            "The dispatcher streams dispatcher-owned worker command/file-change/message events into Activity; do not ask workers to include raw logs or secrets.",
            "When a large user request should be split into optional Todo candidates for the user to choose, write a JSON items file and run:",
            suggest_command,
            "Each item must include title or description, and may include acceptance_criteria and priority. This creates Inbox cards only; do not queue them yourself.",
            "Do not call tmux, cloudflared, the tunnel script, or dispatcher_app.reboot request directly from this manager session.",
            "If code changes require a runtime restart, add one operational marker line to the final result after all implementation, docs or memory updates, and verification are complete:",
            "REBOOT_AFTER_TASK restart-dispatcher <short reason>",
            "The dispatcher removes REBOOT_AFTER_TASK marker lines from the stored user-facing result and appends the host-side reboot request only after the task is marked done.",
            "While any appended reboot request is unprocessed, the dispatcher pauses new pending task claims so the restart cannot interrupt the next task.",
            "Use restart-server, restart-dispatcher, or restart-reboot instead of restart when only one process must be restarted.",
            "Use the same language as the user's task for user-facing field values, while keeping the JSON field names exactly as specified.",
            "Before performing risky work, inspect payload.task.approved. If it is false and the request involves destructive changes, deployment, production data, payment, external APIs, secrets, or other user-approval-sensitive actions, do not perform the action; return needs_approval with your own concise summary and reason.",
            "If the user approved a paused task with notes, those notes are appended to task.acceptance_criteria under a clearly labeled user approval note section; treat them as additional user guidance.",
            "",
            "Return a single JSON object with these string fields:",
            '- decision: one of "execute", "needs_approval", "failed"',
            "- summary: one concise sentence",
            "- result: user-facing result when decision is execute",
            "- reason: why approval is needed when decision is needs_approval",
            "- error: error detail when decision is failed",
            "",
            "Use needs_approval for risky actions that require the user before proceeding.",
            "Use failed only when you cannot complete or safely continue.",
            "",
            "Payload:",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
    )


def parse_codex_events(stdout: str) -> tuple[str | None, str]:
    thread_id = None
    errors = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id") or thread_id
        if event.get("type") == "error":
            errors.append(json.dumps(event, ensure_ascii=False))
    return thread_id, "\n".join(errors)


def parse_manager_decision(text: str) -> ManagerDecision:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = json.loads(extract_json_object(text))

    decision = str(raw.get("decision") or "failed")
    if decision not in DECISIONS:
        return ManagerDecision(
            decision="failed",
            summary="Manager returned an unknown decision.",
            error=f"Unknown decision: {decision}",
        )
    return ManagerDecision(
        decision=decision,
        summary=str(raw.get("summary") or ""),
        result=str(raw.get("result") or ""),
        reason=str(raw.get("reason") or ""),
        error=str(raw.get("error") or ""),
    )


def extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    return text[start : end + 1]
