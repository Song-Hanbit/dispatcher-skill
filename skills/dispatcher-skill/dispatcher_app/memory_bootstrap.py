from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path


RAW_MEMORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("codex stdout event record", re.compile(r'"kind"\s*:\s*"codex_stdout_json"')),
    ("manager prompt audit record", re.compile(r'"kind"\s*:\s*"manager_prompt"')),
    ("raw run id JSON record", re.compile(r'"run_id"\s*:')),
    ("raw worker request JSON record", re.compile(r'"worker_request_id"\s*:')),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Cloudflare Quick Tunnel URL", re.compile(r"https://[A-Za-z0-9-]+\.trycloudflare\.com")),
)

SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:DISPATCHER_PASSWORD|DISPATCHER_HELPER_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|CLOUDFLARE_API_TOKEN|CF_API_TOKEN)\s*="
)


@dataclass(frozen=True)
class MemoryRequirement:
    path: str
    reason: str


@dataclass(frozen=True)
class MemoryDocumentStatus:
    path: str
    reason: str
    exists: bool
    characters: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class MemoryIssue:
    path: str
    line: int
    reason: str


def safe_role_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return name.strip("._-") or "agent"


def role_kind(role_key: str) -> str:
    if role_key == "operator":
        return "operator"
    if role_key.startswith("manager."):
        return "manager"
    return role_key


def role_requirements(role_key: str) -> list[MemoryRequirement]:
    kind = role_kind(role_key)
    if kind == "operator":
        return [
            MemoryRequirement("memory/memory.md", "memory topic index and maintenance rules"),
            MemoryRequirement("memory/operator-onboarding.md", "operator audit, lock, and handoff procedure"),
            MemoryRequirement("memory/runtime-init-workflow.md", "init/start workflow and local-state boundary"),
            MemoryRequirement(
                "memory/context-compression/operator.md",
                "operator handoff inbox for compressed context",
            ),
        ]
    if kind == "manager":
        context_path = f"memory/context-compression/{safe_role_name(role_key)}.md"
        return [
            MemoryRequirement("memory/memory.md", "memory topic index and maintenance rules"),
            MemoryRequirement("memory/agents.md", "agent role, key, and registry rules"),
            MemoryRequirement("memory/dispatcher-app.md", "dispatcher task-plane behavior"),
            MemoryRequirement(context_path, "manager handoff inbox for compressed context"),
        ]
    return [
        MemoryRequirement("memory/memory.md", "memory topic index and maintenance rules"),
    ]


def document_status(repo: Path, requirement: MemoryRequirement) -> MemoryDocumentStatus:
    path = repo / requirement.path
    if not path.is_file():
        return MemoryDocumentStatus(requirement.path, requirement.reason, False)
    text = path.read_text(encoding="utf-8")
    return MemoryDocumentStatus(
        requirement.path,
        requirement.reason,
        True,
        len(text),
        hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    )


def role_memory_status(repo: Path, role_key: str) -> dict[str, object]:
    documents = [document_status(repo, requirement) for requirement in role_requirements(role_key)]
    missing = [document.path for document in documents if not document.exists]
    return {
        "role_key": role_key,
        "ok": not missing,
        "missing": missing,
        "documents": [
            {
                "path": document.path,
                "reason": document.reason,
                "exists": document.exists,
                "characters": document.characters,
                "sha256": document.sha256,
            }
            for document in documents
        ],
    }


def ensure_role_memory_loaded(repo: Path, role_key: str) -> list[MemoryDocumentStatus]:
    documents = [document_status(repo, requirement) for requirement in role_requirements(role_key)]
    missing = [document.path for document in documents if not document.exists]
    if missing:
        formatted = ", ".join(missing)
        raise SystemExit(f"Missing required {role_key} initialization memory: {formatted}")
    return documents


def iter_memory_markdown_files(repo: Path) -> list[Path]:
    memory_root = repo / "memory"
    if not memory_root.is_dir():
        return []
    return sorted(path for path in memory_root.rglob("*.md") if path.is_file())


def memory_compact_checkpoint_status(repo: Path) -> dict[str, object]:
    issues: list[MemoryIssue] = []
    files = iter_memory_markdown_files(repo)
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            issues.append(MemoryIssue(relative_path(repo, path), 0, "non UTF-8 memory file"))
            continue
        for line_number, line in enumerate(lines, start=1):
            if SECRET_ASSIGNMENT_PATTERN.search(line):
                issues.append(MemoryIssue(relative_path(repo, path), line_number, "secret assignment"))
                continue
            for label, pattern in RAW_MEMORY_PATTERNS:
                if pattern.search(line):
                    issues.append(MemoryIssue(relative_path(repo, path), line_number, label))
                    break
    return {
        "ok": not issues,
        "checked_files": len(files),
        "issues": [
            {"path": issue.path, "line": issue.line, "reason": issue.reason}
            for issue in issues
        ],
    }


def ensure_memory_compact_checkpoint(repo: Path) -> dict[str, object]:
    status = memory_compact_checkpoint_status(repo)
    if status["ok"]:
        return status
    raise SystemExit(f"Memory compact checkpoint failed: {compact_checkpoint_issue_summary(status)}")


def compact_checkpoint_issue_summary(status: dict[str, object], limit: int = 6) -> str:
    raw_issues = status.get("issues")
    if not isinstance(raw_issues, list) or not raw_issues:
        return "unknown memory issue"
    visible = raw_issues[:limit]
    issue_text = ", ".join(
        f"{issue['path']}:{issue['line']} {issue['reason']}"
        for issue in visible
        if isinstance(issue, dict)
    )
    remaining = len(raw_issues) - len(visible)
    if remaining > 0:
        issue_text = f"{issue_text}, {remaining} more"
    return issue_text or "unknown memory issue"


def manager_memory_prompt_block(repo: Path, role_key: str) -> str:
    status = role_memory_status(repo, role_key)
    checkpoint = memory_compact_checkpoint_status(repo)
    lines = [
        "Mandatory manager initialization memory:",
        "The dispatcher loaded the memory files below before building this prompt; treat this as the initialization memory checkpoint for this manager turn.",
    ]
    for document in status["documents"]:
        exists = "loaded" if document["exists"] else "missing"
        detail = f"{document['characters']} chars, sha256:{document['sha256']}" if document["exists"] else "missing"
        lines.append(f"- {document['path']}: {exists} ({detail}) - {document['reason']}")
    if not status["ok"]:
        lines.append("If any required memory file is missing, fail safely and report the missing path instead of guessing.")
    lines.extend(
        [
            "Before substantive repo work, open memory/memory.md and the role handoff file, then open only the topic files needed for the task.",
            "Before returning the final JSON, perform the compact-equivalent cleanup: update the relevant durable memory topic when this task changes behavior or operating procedure; otherwise leave memory unchanged and mention no memory update was needed.",
            "Never write raw logs, secrets, full transcripts, full prompts, local tunnel URLs, stdout/stderr dumps, SQLite data, or full JSON records into memory.",
            f"Initialization compact checkpoint: {'passed' if checkpoint['ok'] else 'failed'} over {checkpoint['checked_files']} memory file(s).",
        ]
    )
    if not checkpoint["ok"]:
        lines.append("Resolve memory compact checkpoint issues before adding more memory content.")
    return "\n".join(lines)


def relative_path(repo: Path, path: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()
