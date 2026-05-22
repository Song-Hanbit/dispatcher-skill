#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


MODULES = (
    "server",
    "dispatcher",
    "worker_client",
    "worker_runner",
    "reboot",
    "task_client",
)
SKILL_PATHS = (
    ".",
)
VALIDATOR_RELATIVE_PATH = Path("skills/.system/skill-creator/scripts/quick_validate.py")


@dataclass
class CheckResult:
    label: str
    ok: bool
    detail: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-check packaged dispatcher-skill source paths without starting runtime services."
    )
    parser.add_argument("--repo", default=".", help="Candidate dispatcher-skill root to smoke-check.")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use for module checks.")
    parser.add_argument(
        "--quick-validate",
        default=str(default_quick_validate_path()),
        help="Path to system skill quick_validate.py. Missing validator is reported as skipped.",
    )
    return parser.parse_args()


def default_quick_validate_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / VALIDATOR_RELATIVE_PATH
    return Path.home() / ".codex" / VALIDATOR_RELATIVE_PATH


def smoke_env(repo: Path, pycache_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(repo) if not existing_pythonpath else f"{repo}{os.pathsep}{existing_pythonpath}"
    env["PYTHONPYCACHEPREFIX"] = str(pycache_dir)
    return env


def run_check(
    label: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 20,
) -> CheckResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return CheckResult(label, False, f"missing executable: {exc.filename}")
    except subprocess.TimeoutExpired:
        return CheckResult(label, False, "timed out")
    if completed.returncode != 0:
        return CheckResult(label, False, f"exit {completed.returncode}")
    return CheckResult(label, True)


def require_file(label: str, path: Path) -> CheckResult:
    if path.is_file():
        return CheckResult(label, True)
    return CheckResult(label, False, f"missing {path}")


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).expanduser().resolve()
    python = args.python
    validator = Path(args.quick_validate).expanduser()

    results: list[CheckResult] = []
    results.append(require_file("repo root dispatcher_app/server.py", repo / "dispatcher_app" / "server.py"))
    results.append(require_file("root SKILL.md", repo / "SKILL.md"))
    results.append(require_file("version file", repo / "VERSION"))
    results.append(require_file("changelog", repo / "CHANGELOG.md"))
    results.append(require_file("operator requirements", repo / "requirements.md"))

    with tempfile.TemporaryDirectory(prefix="dispatcher-skill-smoke-pycache-") as pycache:
        env = smoke_env(repo, Path(pycache))

        results.append(
            run_check(
                "import dispatcher_app",
                [python, "-c", "import dispatcher_app"],
                cwd=repo,
                env=env,
            )
        )

        for module in MODULES:
            module_path = repo / "dispatcher_app" / f"{module}.py"
            if not module_path.is_file():
                results.append(CheckResult(f"dispatcher_app.{module} --help", True, "skipped missing optional module"))
                continue
            results.append(
                run_check(
                    f"dispatcher_app.{module} --help",
                    [python, "-m", f"dispatcher_app.{module}", "--help"],
                    cwd=repo,
                    env=env,
                )
            )

        tunnel_helper = repo / "scripts" / "run_dispatcher_tunnel.py"
        results.append(require_file("tunnel helper", tunnel_helper))
        if tunnel_helper.is_file():
            results.append(
                run_check(
                    "tunnel helper init-status",
                    [python, str(tunnel_helper), "init-status", "--repo", str(repo)],
                    cwd=repo,
                    env=env,
                )
            )
            results.append(
                run_check(
                    "tunnel helper reset dry-run",
                    [python, str(tunnel_helper), "reset", "--repo", str(repo)],
                    cwd=repo,
                    env=env,
                )
            )

        context_helper = repo / "scripts" / "context_compact.py"
        results.append(require_file("context compact helper", context_helper))
        if context_helper.is_file():
            results.append(
                run_check(
                    "context compact --help",
                    [python, str(context_helper), "--help"],
                    cwd=repo,
                    env=env,
                )
            )

        if validator.is_file():
            for skill_path in SKILL_PATHS:
                target = repo / skill_path
                results.append(
                    run_check(
                        f"skill validate {skill_path}",
                        [python, str(validator), str(target)],
                        cwd=repo,
                        env=env,
                    )
                )
        else:
            results.append(CheckResult("skill quick_validate.py", True, "skipped missing validator"))

    failed = [result for result in results if not result.ok]
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        suffix = f" ({result.detail})" if result.detail else ""
        print(f"{status} {result.label}{suffix}")

    if failed:
        print(f"Smoke test failed: {len(failed)} check(s) failed.", file=sys.stderr)
        return 1
    print(f"Smoke test passed: {len(results)} check(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
