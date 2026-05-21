from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .server import Store


def load_items(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"items file could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"items file is not valid JSON: {exc}") from exc
    if isinstance(raw, dict):
        raw = raw.get("items")
    if not isinstance(raw, list):
        raise ValueError("items file must be a JSON array or an object with an items array")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"item {index} must be an object")
        items.append(item)
    if not items:
        raise ValueError("items file must contain at least one item")
    return items


def cmd_suggest(args: argparse.Namespace) -> int:
    store = Store(Path(args.db))
    try:
        task_ids = store.suggest_inbox_tasks(
            args.task_id,
            args.manager_role_key,
            load_items(Path(args.items_file)),
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    labels = ", ".join(f"#{task_id}" for task_id in task_ids)
    print(f"Created Inbox suggestions: {labels}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manager-facing dispatcher task helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    suggest = subparsers.add_parser("suggest", help="Create Inbox todo candidates from a JSON items file.")
    suggest.add_argument("--db", default="data/dispatcher.db", help="Dispatcher SQLite DB path.")
    suggest.add_argument("--task-id", required=True, type=int, help="Current in-progress source task id.")
    suggest.add_argument("--manager-role-key", required=True, help="Active manager role key.")
    suggest.add_argument("--items-file", required=True, help="JSON array or object with an items array.")
    suggest.set_defaults(func=cmd_suggest)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
