from __future__ import annotations

import argparse
import inspect
import json
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_registry import ensure_agents_file, read_agent_registry
from .codex_runner import CodexManagerRunner, CodexRunCancelled
from .reboot import append_reboot_request, normalize_command, unprocessed_request_count
from .server import GLOBAL_RUNTIME_LOCK, Store
from .worker_runner import CodexWorkerRunner, DISPATCHER_WORKER_KEY_STATUS


REBOOT_AFTER_TASK_MARKER = "REBOOT_AFTER_TASK"


@dataclass(frozen=True)
class AgentRecord:
    role_key: str
    name: str
    key: str | None
    key_status: str


@dataclass(frozen=True)
class PostTaskRebootRequest:
    command: str
    reason: str
    raw: str


@dataclass(frozen=True)
class ParsedPostTaskReboots:
    result: str
    requests: list[PostTaskRebootRequest]
    invalid_markers: list[str]


class AgentRegistry:
    def __init__(self, path: Path):
        self.path = path
        ensure_agents_file(self.path)

    def load(self) -> list[AgentRecord]:
        raw = read_agent_registry(self.path)

        agents = []
        for item in raw.get("agents", []):
            agents.append(
                AgentRecord(
                    role_key=str(item["role_key"]),
                    name=str(item["name"]),
                    key=item.get("key"),
                    key_status=str(item.get("key_status") or "unknown"),
                )
            )
        return agents

    def managers(self) -> list[AgentRecord]:
        return [agent for agent in self.load() if agent.role_key.startswith("manager.")]

    def worker_catalog_for_manager(self) -> list[dict[str, str | None]]:
        workers = [agent for agent in self.load() if agent.role_key.startswith("worker.")]
        workers.sort(key=lambda agent: agent.role_key)
        return [
            {
                "role_key": worker.role_key,
                "name": worker.name,
                "key": worker.key,
                "key_status": worker.key_status,
            }
            for worker in workers
        ]

    def default_manager(self) -> AgentRecord:
        managers = self.managers()
        for manager in managers:
            if manager.role_key == "manager.default":
                return manager
        if managers:
            return managers[0]
        raise RuntimeError("No manager agent is registered in agents.json")

    def agent(self, role_key: str) -> AgentRecord | None:
        for agent in self.load():
            if agent.role_key == role_key:
                return agent
        return None

    def update_agent_key(self, role_key: str, key: str, key_status: str = "ready") -> None:
        self.update_agent(role_key, key=key, key_status=key_status)

    def update_agent_name(self, role_key: str, name: str) -> None:
        self.update_agent(role_key, name=name)

    def update_agent(
        self,
        role_key: str,
        *,
        key: str | None = None,
        key_status: str | None = None,
        name: str | None = None,
    ) -> None:
        raw = read_agent_registry(self.path)
        changed = False
        for item in raw.get("agents", []):
            if item.get("role_key") == role_key:
                if key is not None:
                    item["key"] = key
                if key_status is not None:
                    item["key_status"] = key_status
                if name is not None:
                    item["name"] = name
                changed = True
                break
        if not changed:
            raise RuntimeError(f"Agent role_key not found: {role_key}")

        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self.path)


class DispatcherLoop:
    def __init__(
        self,
        store: Store,
        registry: AgentRegistry,
        poll_seconds: float = 0.5,
        lease_seconds: int = 60,
        max_concurrent_tasks: int = 1,
        manager_runner: object | None = None,
        worker_runner: object | None = None,
        reboot_requests: Path | None = None,
        reboot_state: Path | None = None,
        reboot_delay_seconds: float = 5.0,
    ):
        self.store = store
        self.registry = registry
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.max_concurrent_tasks = max_concurrent_tasks
        self.stop_event = threading.Event()
        self.active: dict[int, threading.Thread] = {}
        self.lock = threading.Lock()
        self.manager_runner = manager_runner or CodexManagerRunner(Path.cwd())
        self.worker_runner = worker_runner or CodexWorkerRunner(Path.cwd())
        self.reboot_requests = reboot_requests or Path("data/reboot_requests.jsonl")
        self.reboot_state = reboot_state or Path("data/reboot_state.json")
        self.reboot_delay_seconds = reboot_delay_seconds
        self.reboot_barrier_active = False

    def request_stop(self) -> None:
        self.stop_event.set()

    def run_forever(self) -> None:
        print("Dispatcher loop started.", flush=True)
        self.store.add_event(None, "dispatcher_started", "External dispatcher loop started.")
        try:
            while not self.stop_event.is_set():
                self.run_once()
                self.stop_event.wait(self.poll_seconds)
        finally:
            self.wait_for_task_threads(timeout=5)
            self.store.add_event(None, "dispatcher_stopped", "External dispatcher loop stopped.")
            print("Dispatcher loop stopped.", flush=True)

    def run_once(self) -> None:
        try:
            manager = self.registry.default_manager()
            self.clear_finished_task_threads()
            self.store.reclaim_expired(exclude_task_ids=self.active_task_ids())
            self.service_worker_requests()
            if self.has_unprocessed_reboot_requests():
                return
            while self.active_count() < self.max_concurrent_tasks:
                runtime_lock = self.store.acquire_runtime_lock(
                    GLOBAL_RUNTIME_LOCK,
                    "task",
                    manager.role_key,
                    self.lease_seconds,
                )
                if runtime_lock is None:
                    break
                runtime_lock_token = str(runtime_lock["fencing_token"])
                try:
                    task = self.store.claim_next_task_for_manager(
                        "dispatcher",
                        self.lease_seconds,
                        manager.role_key,
                    )
                except Exception:
                    self.store.release_runtime_lock(GLOBAL_RUNTIME_LOCK, runtime_lock_token)
                    raise
                if task is None:
                    self.store.release_runtime_lock(GLOBAL_RUNTIME_LOCK, runtime_lock_token)
                    break
                task_id = int(task["id"])
                if not self.store.assign_runtime_lock_task(
                    GLOBAL_RUNTIME_LOCK,
                    runtime_lock_token,
                    task_id,
                ):
                    self.store.release_runtime_lock(GLOBAL_RUNTIME_LOCK, runtime_lock_token)
                    self.store.fail_task(task_id, "Failed to bind runtime lock to claimed task.")
                    self.store.release_manager(manager.role_key, task_id)
                    break
                task_thread = threading.Thread(
                    target=self.process_task,
                    args=(task_id, manager, runtime_lock_token),
                    name=f"dispatcher-task-{task_id}",
                    daemon=True,
                )
                with self.lock:
                    self.active[task_id] = task_thread
                task_thread.start()
                self.service_worker_requests()
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.store.add_event(None, "dispatcher_error", repr(exc))

    def has_unprocessed_reboot_requests(self) -> bool:
        try:
            pending_count = unprocessed_request_count(self.reboot_requests, self.reboot_state)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.store.add_event(None, "reboot_barrier_error", repr(exc))
            return False

        if pending_count:
            if not self.reboot_barrier_active:
                self.store.add_event(
                    None,
                    "reboot_barrier_started",
                    f"Paused pending task claims until {pending_count} reboot request(s) are processed.",
                )
            self.reboot_barrier_active = True
            return True

        if self.reboot_barrier_active:
            self.store.add_event(
                None,
                "reboot_barrier_cleared",
                "Reboot requests processed; pending task claims resumed.",
            )
        self.reboot_barrier_active = False
        return False

    def active_count(self) -> int:
        with self.lock:
            return len(self.active)

    def active_task_ids(self) -> set[int]:
        with self.lock:
            return set(self.active)

    def clear_finished_task_threads(self) -> None:
        with self.lock:
            finished = [task_id for task_id, thread in self.active.items() if not thread.is_alive()]
            for task_id in finished:
                self.active.pop(task_id, None)

    def wait_for_task_threads(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.clear_finished_task_threads()
            if self.active_count() == 0:
                return
            time.sleep(0.1)

    def task_canceled_or_missing(self, task_id: int) -> bool:
        task = self.store.get_task(task_id)
        return task is None or task["status"] == "canceled"

    def start_task_heartbeat(
        self,
        task_id: int,
        manager_role_key: str,
        runtime_lock_token: str,
    ) -> tuple[threading.Event, threading.Thread]:
        stop_event = threading.Event()
        interval = max(1.0, min(10.0, self.lease_seconds / 3))

        def beat() -> None:
            lock_lost_reported = False
            while not stop_event.wait(interval):
                try:
                    self.store.touch_lease(task_id, self.lease_seconds)
                    self.store.touch_manager_runtime(manager_role_key, task_id)
                    lock_ok = self.store.heartbeat_runtime_lock(
                        GLOBAL_RUNTIME_LOCK,
                        runtime_lock_token,
                        self.lease_seconds,
                    )
                    if not lock_ok and not lock_lost_reported:
                        lock_lost_reported = True
                        self.store.add_event(
                            task_id,
                            "runtime_lock_lost",
                            "Runtime lock heartbeat failed; fencing token no longer owns the lock.",
                        )
                except Exception as exc:  # pragma: no cover - defensive runtime guard
                    self.store.add_event(task_id, "runtime_lock_heartbeat_error", repr(exc))

        thread = threading.Thread(
            target=beat,
            name=f"dispatcher-heartbeat-{task_id}",
            daemon=True,
        )
        thread.start()
        return stop_event, thread

    def start_worker_request_heartbeat(
        self,
        request_id: int,
        task_id: int,
    ) -> tuple[threading.Event, threading.Thread]:
        stop_event = threading.Event()
        interval = max(1.0, min(10.0, self.lease_seconds / 3))
        progress = f"worker request #{request_id} active"

        def beat() -> None:
            while not stop_event.wait(interval):
                try:
                    self.store.heartbeat_worker_request(
                        request_id,
                        self.lease_seconds,
                        progress,
                    )
                except Exception as exc:  # pragma: no cover - defensive runtime guard
                    self.store.add_event(task_id, "worker_heartbeat_error", repr(exc))

        thread = threading.Thread(
            target=beat,
            name=f"dispatcher-worker-heartbeat-{request_id}",
            daemon=True,
        )
        thread.start()
        return stop_event, thread

    def process_task(
        self,
        task_id: int,
        manager: AgentRecord,
        runtime_lock_token: str,
    ) -> None:
        heartbeat_stop: threading.Event | None = None
        heartbeat_thread: threading.Thread | None = None
        try:
            worker_catalog = self.registry.worker_catalog_for_manager()
            manager_runtime = self.store.get_manager_runtime(manager.role_key)
            task = self.store.get_task(task_id)
            if task is None or task["status"] == "canceled":
                return

            self.store.add_event(
                task_id,
                "manager_started",
                (
                    f"{manager.role_key} started planning with key_status={manager.key_status}; "
                    f"runtime_status={manager_runtime['status']}; "
                    f"worker_catalog_entries={len(worker_catalog)}."
                ),
            )
            self.store.touch_lease(task_id, self.lease_seconds)
            self.store.touch_manager_runtime(manager.role_key, task_id)
            self.store.heartbeat_runtime_lock(
                GLOBAL_RUNTIME_LOCK,
                runtime_lock_token,
                self.lease_seconds,
            )
            heartbeat_stop, heartbeat_thread = self.start_task_heartbeat(
                task_id,
                manager.role_key,
                runtime_lock_token,
            )

            def record_manager_event(event_type: str, message: str) -> None:
                self.store.add_event_if_task_active(task_id, event_type, message)

            def task_should_stop() -> bool:
                return self.task_canceled_or_missing(task_id)

            plan_method = self.manager_runner.plan
            plan_kwargs: dict[str, Any] = {}
            if supports_parameter(plan_method, "event_callback", default=True):
                plan_kwargs["event_callback"] = record_manager_event
            if supports_parameter(plan_method, "cancel_check"):
                plan_kwargs["cancel_check"] = task_should_stop
            decision = plan_method(task, manager, worker_catalog, **plan_kwargs)
            if self.task_canceled_or_missing(task_id):
                return
            if decision.manager_key and decision.manager_key != manager.key:
                self.registry.update_agent_key(manager.role_key, decision.manager_key)
                self.store.add_event_if_task_active(
                    task_id,
                    "manager_key_recorded",
                    f"Recorded Codex session key for {manager.role_key}.",
                )
            task = self.store.get_task(task_id)
            if task is None or task["status"] == "canceled":
                return

            if decision.decision == "needs_approval":
                self.store.require_approval(
                    task_id,
                    f"Manager summary: {decision.summary}\nReason: {decision.reason}",
                )
                return
            if decision.decision == "failed":
                self.store.fail_task(task_id, decision.error or "Manager failed.")
                return
            if decision.decision != "execute":
                self.store.fail_task(task_id, f"Unknown manager decision: {decision.decision}")
                return

            task = self.store.get_task(task_id)
            if task is None or task["status"] == "canceled":
                return

            parsed_reboots = parse_post_task_reboot_markers(decision.result)
            for invalid_marker in parsed_reboots.invalid_markers:
                self.store.add_event_if_task_active(
                    task_id,
                    "post_task_reboot_invalid",
                    invalid_marker,
                )
            if not self.store.add_event_if_task_active(task_id, "manager_completed", decision.summary):
                return
            if self.store.complete_task(task_id, parsed_reboots.result):
                self.queue_post_task_reboots(task_id, manager.role_key, parsed_reboots.requests)
        except CodexRunCancelled:
            if not self.task_canceled_or_missing(task_id):
                self.store.fail_task(task_id, "Manager run was canceled.")
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            self.store.fail_task(task_id, repr(exc))
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1)
            try:
                self.store.release_manager(manager.role_key, task_id)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                self.store.add_event(None, "manager_release_error", repr(exc))
            try:
                self.store.release_runtime_lock(GLOBAL_RUNTIME_LOCK, runtime_lock_token)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                self.store.add_event(None, "runtime_lock_release_error", repr(exc))
            with self.lock:
                self.active.pop(task_id, None)

    def queue_post_task_reboots(
        self,
        task_id: int,
        manager_role_key: str,
        requests: list[PostTaskRebootRequest],
    ) -> None:
        def record_event(event_type: str, message: str) -> None:
            try:
                self.store.add_event(task_id, event_type, message)
            except Exception:
                pass

        for reboot_request in requests:
            reason = reboot_request.reason or f"post-task reboot for task #{task_id}"
            try:
                queued = append_reboot_request(
                    self.reboot_requests,
                    reboot_request.command,
                    created_by=manager_role_key,
                    reason=reason,
                    delay_seconds=self.reboot_delay_seconds,
                )
            except Exception as exc:  # pragma: no cover - filesystem guard
                record_event(
                    "post_task_reboot_failed",
                    f"{reboot_request.raw}: {exc!r}",
                )
                continue
            record_event(
                "post_task_reboot_queued",
                f"Queued {queued['command']} reboot request {queued['id']}: {reason}",
            )

    def service_worker_requests(self) -> None:
        while True:
            request = self.store.claim_next_worker_request(
                "dispatcher-worker",
                self.lease_seconds,
            )
            if request is None:
                return
            self.process_worker_request(request)

    def process_worker_request(self, request: dict[str, Any]) -> None:
        request_id = int(request["id"])
        task_id = int(request["task_id"])
        worker_role_key = str(request["worker_role_key"])
        worker = self.registry.agent(worker_role_key)
        if worker is None:
            self.store.fail_worker_request(request_id, f"Unknown worker role: {worker_role_key}")
            return
        worker_name = str(request.get("worker_name") or "").strip()
        if worker_name and worker_name != worker.name:
            self.registry.update_agent_name(worker.role_key, worker_name)
            worker = AgentRecord(
                role_key=worker.role_key,
                name=worker_name,
                key=worker.key,
                key_status=worker.key_status,
            )
            self.store.add_event_if_task_active(
                task_id,
                "worker_name_recorded",
                f"Recorded manager-chosen display name for {worker.role_key}: {worker_name}.",
            )
        if self.task_canceled_or_missing(task_id):
            self.store.cancel_worker_request(
                request_id,
                "Worker request canceled because the task stopped.",
            )
            return
        heartbeat_stop: threading.Event | None = None
        heartbeat_thread: threading.Thread | None = None

        def record_worker_event(event_type: str, message: str) -> None:
            if self.task_canceled_or_missing(task_id):
                return
            progress = worker_event_progress(event_type, request_id)
            if progress:
                self.store.heartbeat_worker_request(
                    request_id,
                    self.lease_seconds,
                    progress,
                )
            self.store.add_event_if_task_active(task_id, event_type, message)

        def task_should_stop() -> bool:
            return self.task_canceled_or_missing(task_id)

        try:
            heartbeat_stop, heartbeat_thread = self.start_worker_request_heartbeat(
                request_id,
                task_id,
            )
            run_kwargs: dict[str, Any] = {"event_callback": record_worker_event}
            if supports_parameter(self.worker_runner.run, "cancel_check"):
                run_kwargs["cancel_check"] = task_should_stop
            result = self.worker_runner.run(request, worker, **run_kwargs)
        except CodexRunCancelled:
            self.store.cancel_worker_request(
                request_id,
                "Worker request canceled because the task stopped.",
            )
            return
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            if self.task_canceled_or_missing(task_id):
                self.store.cancel_worker_request(
                    request_id,
                    "Worker request canceled because the task stopped.",
                )
                return
            self.store.fail_worker_request(request_id, repr(exc))
            return
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=1)

        if getattr(result, "canceled", False) or self.task_canceled_or_missing(task_id):
            self.store.cancel_worker_request(
                request_id,
                "Worker request canceled because the task stopped.",
            )
            return
        if result.worker_key and (
            result.worker_key != worker.key
            or worker.key_status != DISPATCHER_WORKER_KEY_STATUS
        ):
            self.registry.update_agent_key(
                worker.role_key,
                result.worker_key,
                key_status=DISPATCHER_WORKER_KEY_STATUS,
            )
            self.store.add_event_if_task_active(
                task_id,
                "worker_key_recorded",
                f"Recorded dispatcher-owned Codex session key for {worker.role_key}.",
            )
        if self.task_canceled_or_missing(task_id):
            self.store.cancel_worker_request(
                request_id,
                "Worker request canceled because the task stopped.",
            )
            return
        if result.success:
            self.store.complete_worker_request(request_id, result.result)
        else:
            self.store.fail_worker_request(request_id, result.error or "Worker failed.")


def worker_event_progress(event_type: str, request_id: int) -> str | None:
    if event_type == "worker_command":
        return f"worker request #{request_id} command activity"
    if event_type == "worker_file_change":
        return f"worker request #{request_id} file activity"
    if event_type == "worker_stream":
        return f"worker request #{request_id} reporting"
    return None


def supports_parameter(callable_obj: Any, name: str, default: bool = False) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return default
    return any(
        parameter.name == name
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def parse_post_task_reboot_markers(result: str) -> ParsedPostTaskReboots:
    kept_lines = []
    requests = []
    invalid_markers = []
    for line in str(result or "").splitlines():
        stripped = line.strip()
        parts = stripped.split(maxsplit=2)
        if not parts or parts[0] != REBOOT_AFTER_TASK_MARKER:
            kept_lines.append(line)
            continue
        if len(parts) < 2:
            invalid_markers.append(f"{stripped}: missing reboot command")
            continue
        try:
            command = normalize_command(parts[1])
        except ValueError as exc:
            invalid_markers.append(f"{stripped}: {exc}")
            continue
        reason = parts[2].strip() if len(parts) > 2 else ""
        requests.append(
            PostTaskRebootRequest(
                command=command,
                reason=reason,
                raw=stripped,
            )
        )
    return ParsedPostTaskReboots(
        result="\n".join(kept_lines).strip(),
        requests=requests,
        invalid_markers=invalid_markers,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the dispatcher queue loop.")
    parser.add_argument("--db", default="data/dispatcher.db")
    parser.add_argument("--agents", default="dispatcher_app/agents.json")
    parser.add_argument("--poll-seconds", default=0.5, type=float)
    parser.add_argument("--lease-seconds", default=60, type=int)
    parser.add_argument("--max-concurrent-tasks", default=1, type=int)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-timeout-seconds", default=1800.0, type=float)
    parser.add_argument("--reboot-requests", default="data/reboot_requests.jsonl")
    parser.add_argument("--reboot-state", default="data/reboot_state.json")
    parser.add_argument("--post-task-reboot-delay-seconds", default=5.0, type=float)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loop = DispatcherLoop(
        Store(Path(args.db)),
        AgentRegistry(Path(args.agents)),
        poll_seconds=args.poll_seconds,
        lease_seconds=args.lease_seconds,
        max_concurrent_tasks=args.max_concurrent_tasks,
        manager_runner=CodexManagerRunner(
            Path.cwd(),
            codex_bin=args.codex_bin,
            timeout_seconds=args.codex_timeout_seconds,
        ),
        worker_runner=CodexWorkerRunner(
            Path.cwd(),
            codex_bin=args.codex_bin,
            timeout_seconds=args.codex_timeout_seconds,
        ),
        reboot_requests=Path(args.reboot_requests),
        reboot_state=Path(args.reboot_state),
        reboot_delay_seconds=args.post_task_reboot_delay_seconds,
    )

    def handle_signal(signum: int, _frame: object) -> None:
        print(f"Received signal {signum}; stopping dispatcher loop.", flush=True)
        loop.request_stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    loop.run_forever()


if __name__ == "__main__":
    main()
