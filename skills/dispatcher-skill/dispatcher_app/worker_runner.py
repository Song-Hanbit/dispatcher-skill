from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_runner import (
    CancelCheck,
    CodexRunCancelled,
    EventCallback,
    agent_log_path,
    append_agent_jsonl,
    codex_exec_prefix,
    codex_workspace_root,
    codex_failure_error,
    parse_codex_events,
    run_codex_streaming,
    utc_now,
)


DISPATCHER_WORKER_KEY_STATUS = "dispatcher_ready"


@dataclass(frozen=True)
class WorkerRunResult:
    success: bool
    result: str = ""
    error: str = ""
    worker_key: str | None = None
    canceled: bool = False


class CodexWorkerRunner:
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

    def run(
        self,
        request: dict[str, Any],
        worker: Any,
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> WorkerRunResult:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.conversation_dir.mkdir(parents=True, exist_ok=True)

        request_id = int(request["id"])
        task_id = int(request["task_id"])
        run_id = f"worker-{request_id}-{int(time.time())}"
        output_path = self.run_dir / f"{run_id}.txt"
        prompt = build_worker_prompt(request, worker)
        worker_key = (
            worker.key
            if getattr(worker, "key_status", "") == DISPATCHER_WORKER_KEY_STATUS
            else None
        )
        command = self._command(worker_key, output_path)
        log_path = agent_log_path(self.conversation_dir, worker.role_key)
        base_record = {
            "run_id": run_id,
            "task_id": task_id,
            "worker_request_id": request_id,
            "created_at": utc_now(),
            "agent_role_key": worker.role_key,
            "agent_name": worker.name,
        }
        append_agent_jsonl(
            log_path,
            {
                **base_record,
                "direction": "request",
                "kind": "worker_prompt",
                "manager_role_key": request["manager_role_key"],
                "content": prompt,
                "output_path": output_path.as_posix(),
            },
        )

        def append_transcript(record: dict[str, Any]) -> None:
            append_agent_jsonl(
                log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "source_agent_role_key": request["manager_role_key"],
                    **record,
                },
            )

        def emit_worker_event(event_type: str, message: str) -> None:
            mapped_type, mapped_message = map_worker_activity_event(
                request_id,
                event_type,
                message,
            )
            if mapped_message:
                if event_callback:
                    event_callback(mapped_type, mapped_message)

        try:
            returncode, stdout, stderr = run_codex_streaming(
                command,
                prompt,
                timeout=self.timeout_seconds,
                cwd=codex_workspace_root(self.repo),
                worker_catalog=[],
                event_callback=emit_worker_event,
                transcript_callback=append_transcript,
                cancel_check=cancel_check,
            )
        except CodexRunCancelled:
            error = "Worker request canceled because the task stopped."
            append_agent_jsonl(
                log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "direction": "response",
                    "kind": "worker_canceled",
                    "error": error,
                },
            )
            return WorkerRunResult(
                success=False,
                error=error,
                worker_key=worker_key,
                canceled=True,
            )
        except FileNotFoundError:
            error = f"Executable not found: {self.codex_bin}"
            append_agent_jsonl(
                log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "direction": "response",
                    "kind": "worker_error",
                    "error": error,
                },
            )
            return WorkerRunResult(success=False, error=error)
        except subprocess.TimeoutExpired:
            error = f"Timed out after {self.timeout_seconds} seconds."
            append_agent_jsonl(
                log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "direction": "response",
                    "kind": "worker_error",
                    "error": error,
                },
            )
            return WorkerRunResult(success=False, error=error, worker_key=worker_key)

        thread_id, event_error = parse_codex_events(stdout)
        next_worker_key = worker_key or thread_id
        try:
            result = output_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            error = codex_failure_error(returncode, stderr, event_error, stdout) or repr(exc)
            append_agent_jsonl(
                log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "direction": "response",
                    "kind": "worker_error",
                    "returncode": returncode,
                    "error": error,
                    "worker_key": next_worker_key,
                },
            )
            return WorkerRunResult(success=False, error=error, worker_key=next_worker_key)

        if returncode != 0 and not result:
            error = codex_failure_error(returncode, stderr, event_error, stdout)
            append_agent_jsonl(
                log_path,
                {
                    **base_record,
                    "created_at": utc_now(),
                    "direction": "response",
                    "kind": "worker_error",
                    "returncode": returncode,
                    "error": error,
                    "worker_key": next_worker_key,
                },
            )
            return WorkerRunResult(success=False, error=error, worker_key=next_worker_key)

        response_record = {
            **base_record,
            "created_at": utc_now(),
            "direction": "response",
            "kind": "worker_final",
            "returncode": returncode,
            "content": result,
            "worker_key": next_worker_key,
        }
        if returncode != 0:
            response_record["warning"] = codex_failure_error(returncode, stderr, event_error, stdout)
        append_agent_jsonl(log_path, response_record)
        return WorkerRunResult(success=True, result=result, worker_key=next_worker_key)

    def _command(self, worker_key: str | None, output_path: Path) -> list[str]:
        command_prefix = codex_exec_prefix(self.codex_bin, self.repo)
        if worker_key:
            return [
                *command_prefix,
                "resume",
                "--json",
                "--skip-git-repo-check",
                "-o",
                str(output_path),
                worker_key,
                "-",
            ]
        return [
            *command_prefix,
            "--json",
            "--skip-git-repo-check",
            "-o",
            str(output_path),
            "-",
        ]


def build_worker_prompt(request: dict[str, Any], worker: Any) -> str:
    requested_name = str(request.get("worker_name") or worker.name).strip() or worker.name
    return "\n".join(
        [
            f"You are the dispatcher-owned worker {worker.role_key} ({requested_name}).",
            f"You were called by manager {request['manager_role_key']} for task #{request['task_id']}.",
            "",
            "Handle only the bounded worker request below.",
            "Edit files directly when the request asks for implementation.",
            "Run focused verification when practical.",
            "Do not use Codex subagents for this worker request.",
            "Do not close or lifecycle-manage other agents.",
            "Do not revert unrelated edits made by others.",
            "Return a concise final report with changed paths and verification.",
            "",
            "Worker request:",
            str(request.get("prompt") or "").strip(),
        ]
    )


def map_worker_activity_event(
    request_id: int,
    event_type: str,
    message: str,
) -> tuple[str, str]:
    if not message:
        return event_type, message
    if event_type.startswith("manager_"):
        event_type = f"worker_{event_type[len('manager_'):]}"
    if event_type in {"worker_command", "worker_file_change", "worker_delegation"}:
        message = prefix_worker_activity_id(request_id, event_type, message)
    return event_type, message


def prefix_worker_activity_id(request_id: int, event_type: str, message: str) -> str:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return message
    if not isinstance(payload, dict):
        return message
    key_by_type = {
        "worker_command": "command_id",
        "worker_file_change": "change_id",
        "worker_delegation": "delegation_id",
    }
    key = key_by_type.get(event_type)
    if not key or key not in payload:
        return message
    payload[key] = f"worker-request:{request_id}:{payload[key]}"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
