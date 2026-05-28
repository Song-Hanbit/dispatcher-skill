from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from .server import GLOBAL_RUNTIME_LOCK, Store, public_runtime_lock


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def lock_access_error_payload(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    return {
        "error": str(exc),
        "db": args.db,
        "hint": (
            "Could not access the runtime lock database or token file. Run this command from the "
            "dispatcher skill root or pass --db <skill-root>/data/dispatcher.db, then ensure the "
            "skill-local data directory is writable. In a sandboxed first init, request the approved "
            "write path instead of retrying with an ad-hoc database path."
        ),
    }


def write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")


def read_token(args: argparse.Namespace) -> str:
    if args.token:
        return str(args.token)
    if args.token_file:
        return Path(args.token_file).read_text(encoding="utf-8").strip()
    raise ValueError("Either --token or --token-file is required.")


def command_status(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    lock = public_runtime_lock(store.get_runtime_lock(args.resource))
    print_json({"lock": lock})
    return 0


def command_acquire(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    lock = store.acquire_runtime_lock(
        args.resource,
        args.owner_plane,
        args.owner_id,
        args.lease_seconds,
        args.task_id,
    )
    if lock is None:
        current = public_runtime_lock(store.get_runtime_lock(args.resource))
        print_json({"acquired": False, "lock": current})
        return 2

    token = str(lock["fencing_token"])
    if args.token_file:
        try:
            write_token_file(Path(args.token_file), token)
        except OSError:
            try:
                store.release_runtime_lock(args.resource, token)
            except (OSError, sqlite3.Error):
                pass
            raise
        print_json({"acquired": True, "lock": public_runtime_lock(lock), "token_file": args.token_file})
    else:
        print_json({"acquired": True, "lock": lock})
    return 0


def command_heartbeat(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    token = read_token(args)
    ok = store.heartbeat_runtime_lock(args.resource, token, args.lease_seconds)
    print_json({"heartbeat": ok, "lock": public_runtime_lock(store.get_runtime_lock(args.resource))})
    return 0 if ok else 2


def command_release(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    token = read_token(args)
    ok = store.release_runtime_lock(args.resource, token)
    print_json({"released": ok, "lock": public_runtime_lock(store.get_runtime_lock(args.resource))})
    return 0 if ok else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the dispatcher global runtime mutex.")
    parser.add_argument("--db", default="data/dispatcher.db", help="SQLite database path.")
    parser.add_argument(
        "--resource",
        default=GLOBAL_RUNTIME_LOCK,
        help="Runtime lock resource name.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Print the current runtime lock owner.")

    acquire = subparsers.add_parser("acquire", help="Acquire the runtime lock.")
    acquire.add_argument("--owner-plane", default="operator", choices=("operator", "task"))
    acquire.add_argument("--owner-id", default="operator")
    acquire.add_argument("--lease-seconds", type=int, default=300)
    acquire.add_argument("--task-id", type=int)
    acquire.add_argument("--token-file", help="Optional path where the fencing token is written.")

    heartbeat = subparsers.add_parser("heartbeat", help="Renew an owned runtime lock.")
    heartbeat.add_argument("--lease-seconds", type=int, default=300)
    heartbeat.add_argument("--token")
    heartbeat.add_argument("--token-file")

    release = subparsers.add_parser("release", help="Release an owned runtime lock.")
    release.add_argument("--token")
    release.add_argument("--token-file")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "status":
            return command_status(args)
        if args.command == "acquire":
            return command_acquire(args)
        if args.command == "heartbeat":
            return command_heartbeat(args)
        if args.command == "release":
            return command_release(args)
    except ValueError as exc:
        print_json({"error": str(exc)})
        return 1
    except (OSError, sqlite3.Error) as exc:
        print_json(lock_access_error_payload(args, exc))
        return 1
    print_json({"error": f"Unknown command: {args.command}"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
