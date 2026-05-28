from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TERMINAL_STATUSES = {"done", "failed"}
DEFAULT_API_REQUEST_TIMEOUT_SECONDS = 10.0


class WorkerApiError(RuntimeError):
    pass


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("worker prompt is required via --prompt-file, --prompt, or stdin")


def open_store(db_path: Path) -> Any:
    from .server import Store

    return Store(db_path)


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


def first_nonempty(*values: str | None) -> str:
    for value in values:
        if value:
            stripped = str(value).strip()
            if stripped:
                return stripped
    return ""


def local_api_host(host: str) -> str:
    host = host.strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def normalize_api_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value


def resolve_api_config(args: argparse.Namespace) -> tuple[str, str]:
    env_values = read_shell_env(Path(args.env_file)) if args.env_file else {}
    api_url = normalize_api_url(
        first_nonempty(
            args.api_url,
            os.environ.get("DISPATCHER_API_URL"),
            env_values.get("DISPATCHER_API_URL"),
        )
    )
    if not api_url:
        host = local_api_host(
            first_nonempty(
                os.environ.get("DISPATCHER_HOST"),
                env_values.get("DISPATCHER_HOST"),
                "127.0.0.1",
            )
        )
        port = first_nonempty(
            os.environ.get("DISPATCHER_PORT"),
            env_values.get("DISPATCHER_PORT"),
            "8000",
        )
        api_url = f"http://{host}:{port}"

    helper_token = first_nonempty(
        args.helper_token,
        os.environ.get("DISPATCHER_HELPER_TOKEN"),
        env_values.get("DISPATCHER_HELPER_TOKEN"),
    )
    if not helper_token:
        raise WorkerApiError(
            "DISPATCHER_HELPER_TOKEN is required for worker API transport; "
            "pass --helper-token, set the environment, provide it in data/dispatcher.env, "
            "or use --transport db for explicit debug fallback."
        )
    return api_url, helper_token


def parse_json_error(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace").strip()
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("message") or "").strip()
    return str(payload).strip()


def api_json_request(
    api_url: str,
    helper_token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    url = f"{api_url.rstrip('/')}{path}"
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {helper_token}",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except HTTPError as exc:
        message = parse_json_error(exc.read()) or str(exc.reason)
        raise WorkerApiError(f"Worker API HTTP {exc.code}: {message}") from exc
    except TimeoutError as exc:
        raise WorkerApiError(f"Dispatcher server request timed out at {api_url}.") from exc
    except URLError as exc:
        raise WorkerApiError(f"Dispatcher server is not reachable at {api_url}: {exc.reason}") from exc

    if not raw:
        return {}
    try:
        payload_data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerApiError("Worker API returned invalid JSON.") from exc
    if not isinstance(payload_data, dict):
        raise WorkerApiError("Worker API returned a non-object JSON response.")
    return payload_data


def call_worker_api(args: argparse.Namespace, prompt: str) -> int:
    try:
        api_url, helper_token = resolve_api_config(args)
        response = api_json_request(
            api_url,
            helper_token,
            "POST",
            "/api/worker-requests",
            {
                "task_id": args.task_id,
                "manager_role_key": args.manager_role_key,
                "worker_role_key": args.worker_role_key,
                "worker_name": args.worker_name,
                "prompt": prompt,
            },
            args.api_request_timeout_seconds,
        )
        request_id = int(response.get("id"))
    except (TypeError, ValueError, WorkerApiError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"Queued worker request #{request_id} for {args.worker_role_key} via dispatcher API.",
        flush=True,
    )
    deadline = time.time() + args.timeout_seconds
    while time.time() < deadline:
        try:
            response = api_json_request(
                api_url,
                helper_token,
                "GET",
                f"/api/worker-requests/{request_id}",
                None,
                args.api_request_timeout_seconds,
            )
        except WorkerApiError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        request = response.get("request")
        if not isinstance(request, dict):
            print("Worker API returned no worker request.", file=sys.stderr)
            return 1
        status = str(request.get("status") or "")
        if status in TERMINAL_STATUSES:
            if status == "done":
                result = str(request.get("result") or "")
                if result:
                    print(result)
                return 0
            error = str(request.get("error") or "Worker request failed.")
            print(error, file=sys.stderr)
            return 1
        time.sleep(args.poll_seconds)
    print(f"Timed out waiting for worker request #{request_id}.", file=sys.stderr)
    return 1


def call_worker_db(args: argparse.Namespace, prompt: str) -> int:
    store = open_store(Path(args.db))
    try:
        request_id = store.create_worker_request(
            args.task_id,
            args.manager_role_key,
            args.worker_role_key,
            prompt,
            worker_name=args.worker_name,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Queued worker request #{request_id} for {args.worker_role_key}.", flush=True)
    deadline = time.time() + args.timeout_seconds
    while time.time() < deadline:
        request = store.get_worker_request(request_id)
        if request is None:
            print(f"Worker request #{request_id} disappeared.", file=sys.stderr)
            return 1
        status = str(request["status"])
        if status in TERMINAL_STATUSES:
            if status == "done":
                result = str(request.get("result") or "")
                if result:
                    print(result)
                return 0
            error = str(request.get("error") or "Worker request failed.")
            print(error, file=sys.stderr)
            return 1
        time.sleep(args.poll_seconds)
    print(f"Timed out waiting for worker request #{request_id}.", file=sys.stderr)
    return 1


def call_worker(args: argparse.Namespace) -> int:
    prompt = read_prompt(args)
    if args.transport == "db":
        return call_worker_db(args, prompt)
    return call_worker_api(args, prompt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Request dispatcher-owned worker execution.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    call = subparsers.add_parser("call", help="Queue a worker request and wait for its result.")
    call.add_argument("--transport", choices=("api", "db"), default="api", help="Request transport. Default uses the dispatcher server API.")
    call.add_argument("--api-url", default="", help="Dispatcher server base URL. Defaults to DISPATCHER_API_URL or DISPATCHER_HOST/PORT.")
    call.add_argument("--helper-token", default="", help="Dispatcher helper API token. Defaults to DISPATCHER_HELPER_TOKEN.")
    call.add_argument("--env-file", default="data/dispatcher.env", help="Skill-local runtime env file used for API defaults.")
    call.add_argument("--db", default="data/dispatcher.db", help="SQLite DB path used only with --transport db.")
    call.add_argument("--task-id", required=True, type=int)
    call.add_argument("--manager-role-key", required=True)
    call.add_argument("--worker-role-key", required=True)
    call.add_argument("--worker-name", default="", help="Optional manager-chosen worker display name.")
    call.add_argument("--prompt-file", default="")
    call.add_argument("--prompt", default="")
    call.add_argument("--poll-seconds", default=0.5, type=float)
    call.add_argument("--timeout-seconds", default=1800.0, type=float)
    call.add_argument("--api-request-timeout-seconds", default=DEFAULT_API_REQUEST_TIMEOUT_SECONDS, type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "call":
        return call_worker(args)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
