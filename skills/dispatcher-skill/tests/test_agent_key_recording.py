from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from dispatcher_app.agent_registry import (
    default_agent_registry,
    read_agent_registry,
    update_agent_registry_agent,
    write_agent_registry,
)
from dispatcher_app.codex_runner import run_codex_streaming
from scripts.run_dispatcher_tunnel import operator_key_from_sources, record_operator_key


class AgentKeyRecordingTests(unittest.TestCase):
    def test_operator_key_from_sources_prefers_explicit_value(self) -> None:
        self.assertEqual(
            operator_key_from_sources(
                " explicit ",
                {"DISPATCHER_OPERATOR_KEY": "env-operator", "CODEX_THREAD_ID": "env-thread"},
            ),
            "explicit",
        )

    def test_operator_key_from_sources_uses_codex_thread_id(self) -> None:
        self.assertEqual(
            operator_key_from_sources(
                None,
                {"CODEX_THREAD_ID": "thread-from-env"},
            ),
            "thread-from-env",
        )

    def test_update_agent_registry_agent_records_operator_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "agents.json"
            write_agent_registry(path, default_agent_registry())

            update_agent_registry_agent(
                path,
                "operator",
                key="operator-thread",
                key_status="ready",
            )

            agents = {
                item["role_key"]: item
                for item in read_agent_registry(path)["agents"]
            }
            self.assertEqual(agents["operator"]["key"], "operator-thread")
            self.assertEqual(agents["operator"]["key_status"], "ready")
            self.assertIsNone(agents["manager.default"]["key"])

    def test_record_operator_key_updates_repo_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            agents_dir = repo / "dispatcher_app"
            agents_dir.mkdir()
            write_agent_registry(agents_dir / "agents.json", default_agent_registry())

            self.assertTrue(record_operator_key(repo, "operator-thread"))

            agents = {
                item["role_key"]: item
                for item in read_agent_registry(agents_dir / "agents.json")["agents"]
            }
            self.assertEqual(agents["operator"]["key"], "operator-thread")
            self.assertEqual(agents["operator"]["key_status"], "ready")

    def test_record_operator_key_adds_missing_operator_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            agents_dir = repo / "dispatcher_app"
            agents_dir.mkdir()
            payload = default_agent_registry()
            payload["agents"] = [
                item
                for item in payload["agents"]
                if item["role_key"] != "operator"
            ]
            write_agent_registry(agents_dir / "agents.json", payload)

            self.assertTrue(record_operator_key(repo, "operator-thread"))

            agents = {
                item["role_key"]: item
                for item in read_agent_registry(agents_dir / "agents.json")["agents"]
            }
            self.assertEqual(agents["operator"]["name"], "Operator")
            self.assertEqual(agents["operator"]["key"], "operator-thread")
            self.assertEqual(agents["operator"]["key_status"], "ready")

    def test_run_codex_streaming_reports_thread_started_immediately(self) -> None:
        script = (
            "import json, sys; "
            "print(json.dumps({'type':'thread.started','thread_id':'worker-thread'}), flush=True); "
            "sys.stdin.read()"
        )
        seen: list[str] = []

        returncode, stdout, stderr = run_codex_streaming(
            [sys.executable, "-c", script],
            "",
            timeout=5,
            cwd=Path.cwd(),
            thread_started_callback=seen.append,
        )

        self.assertEqual(returncode, 0, stderr)
        self.assertIn("\"thread_id\": \"worker-thread\"", stdout)
        self.assertEqual(seen, ["worker-thread"])


if __name__ == "__main__":
    unittest.main()
