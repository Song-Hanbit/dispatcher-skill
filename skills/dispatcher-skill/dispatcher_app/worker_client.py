from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .server import Store


TERMINAL_STATUSES = {"done", "failed"}


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("worker prompt is required via --prompt-file, --prompt, or stdin")


def call_worker(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    prompt = read_prompt(args)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Request dispatcher-owned worker execution.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    call = subparsers.add_parser("call", help="Queue a worker request and wait for its result.")
    call.add_argument("--db", default="data/dispatcher.db")
    call.add_argument("--task-id", required=True, type=int)
    call.add_argument("--manager-role-key", required=True)
    call.add_argument("--worker-role-key", required=True)
    call.add_argument("--worker-name", default="", help="Optional manager-chosen worker display name.")
    call.add_argument("--prompt-file", default="")
    call.add_argument("--prompt", default="")
    call.add_argument("--poll-seconds", default=0.5, type=float)
    call.add_argument("--timeout-seconds", default=1800.0, type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "call":
        return call_worker(args)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
