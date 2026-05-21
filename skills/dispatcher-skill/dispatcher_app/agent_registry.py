from __future__ import annotations

import json
from pathlib import Path
from typing import Any


AGENT_KEY_TYPE = "codex_agent_handle"
AGENT_REGISTRY_VERSION = 5


def default_agent_registry() -> dict[str, Any]:
    return {
        "version": AGENT_REGISTRY_VERSION,
        "key_type": AGENT_KEY_TYPE,
        "agents": [
            {
                "role_key": "operator",
                "name": "Operator",
                "key": None,
                "key_status": "missing",
            },
            {
                "role_key": "manager.default",
                "name": "Manager",
                "key": None,
                "key_status": "missing",
            },
            {
                "role_key": "worker.default",
                "name": "Worker",
                "key": None,
                "key_status": "missing",
            },
        ],
    }


def validate_agent_registry(raw: dict[str, Any]) -> None:
    if raw.get("key_type") != AGENT_KEY_TYPE:
        raise ValueError("agents.json key_type must be codex_agent_handle")
    agents = raw.get("agents")
    if not isinstance(agents, list):
        raise ValueError("agents.json agents must be a list")
    role_keys = set()
    for item in agents:
        if not isinstance(item, dict):
            raise ValueError("agents.json agents must contain objects")
        role_key = str(item.get("role_key") or "")
        if not role_key:
            raise ValueError("agents.json agent is missing role_key")
        if role_key in role_keys:
            raise ValueError(f"agents.json contains duplicate role_key: {role_key}")
        role_keys.add(role_key)
        if "name" not in item:
            raise ValueError(f"agents.json agent is missing name: {role_key}")
        item.setdefault("key", None)
        item.setdefault("key_status", "missing")
    if "manager.default" not in role_keys:
        raise ValueError("agents.json must include manager.default")


def read_agent_registry(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("agents.json must contain an object")
    validate_agent_registry(raw)
    return raw


def write_agent_registry(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def update_agent_registry_agent(
    path: Path,
    role_key: str,
    *,
    key: str | None = None,
    key_status: str | None = None,
    name: str | None = None,
) -> None:
    raw = read_agent_registry(path)
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
    write_agent_registry(path, raw)


def upsert_agent_registry_agent(
    path: Path,
    role_key: str,
    name: str,
    *,
    key: str | None = None,
    key_status: str | None = None,
) -> None:
    raw = read_agent_registry(path)
    for item in raw.get("agents", []):
        if item.get("role_key") == role_key:
            if key is not None:
                item["key"] = key
            if key_status is not None:
                item["key_status"] = key_status
            item.setdefault("name", name)
            write_agent_registry(path, raw)
            return
    raw.setdefault("agents", []).append(
        {
            "role_key": role_key,
            "name": name,
            "key": key,
            "key_status": key_status or "missing",
        }
    )
    write_agent_registry(path, raw)


def ensure_agents_file(path: Path) -> bool:
    if path.is_file():
        read_agent_registry(path)
        return False
    write_agent_registry(path, default_agent_registry())
    return True
