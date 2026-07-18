"""Black-box portability tests for the Claude-Codex continuity pilot.

These tests check the shared entry points, durable workstream records, and
portable team configuration described in
``docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md``. They
also exercise the exact-path staging audit that every continuity commit must
pass before it is created (see the implementation plan's Global Constraints).

Later tasks extend this module with tests for ``scripts/ai-handoff.ps1``.
"""

import re
import subprocess
from pathlib import Path
from typing import NamedTuple, Sequence

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The exact portable implementation allowlist from the approved design spec
# (docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md,
# "Portable implementation allowlist"). Every continuity commit stages only
# these 21 repository-relative paths; wildcard pathspecs and directory
# staging are prohibited.
PORTABLE_ALLOWLIST = (
    "AGENTS.md",
    "CLAUDE.md",
    "RTK.md",
    ".ai/WORKFLOW.md",
    ".ai/PROJECT_STATUS.md",
    ".ai/workstreams/continuity-pilot.md",
    ".ai/workstreams/match-stroke-recognition-b.md",
    ".claude/active-team.md",
    ".claude/agents/architect.md",
    ".claude/agents/builder.md",
    ".claude/agents/qa-reviewer.md",
    ".claude/agents/scope-planner.md",
    ".claude/agents/shipper.md",
    ".codex/active-team.md",
    ".codex/agents/architect.toml",
    ".codex/agents/builder.toml",
    ".codex/agents/qa-reviewer.toml",
    ".codex/agents/scope-planner.toml",
    ".codex/agents/shipper.toml",
    "scripts/ai-handoff.ps1",
    "tests/test_ai_handoff.py",
)

assert len(PORTABLE_ALLOWLIST) == 21, "portable allowlist must have exactly 21 paths"

MARKER_START = "<!-- ai-continuity:milestones:start -->"
MARKER_END = "<!-- ai-continuity:milestones:end -->"

WORKSTREAM_FILES = (
    PROJECT_ROOT / ".ai" / "workstreams" / "continuity-pilot.md",
    PROJECT_ROOT / ".ai" / "workstreams" / "match-stroke-recognition-b.md",
)

PORTABLE_TEAM_FILES = (
    ".claude/active-team.md",
    ".claude/agents/architect.md",
    ".claude/agents/builder.md",
    ".claude/agents/qa-reviewer.md",
    ".claude/agents/scope-planner.md",
    ".claude/agents/shipper.md",
    ".codex/active-team.md",
    ".codex/agents/architect.toml",
    ".codex/agents/builder.toml",
    ".codex/agents/qa-reviewer.toml",
    ".codex/agents/scope-planner.toml",
    ".codex/agents/shipper.toml",
)

# Any drive-letter, UNC, POSIX-home, or tilde-relative absolute path. Portable
# team files must describe role behavior only; they must never bake in one
# contributor's machine-specific checkout location.
ABSOLUTE_PATH_PATTERN = re.compile(
    r"[A-Za-z]:[\\/]"        # C:\... or D:/...
    r"|\\\\[A-Za-z0-9._-]+"  # \\server\share UNC paths
    r"|/home/\S+"
    r"|/Users/\S+"
    r"|~[\\/]"
)


def _run_git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class AuditResult(NamedTuple):
    ok: bool
    extra: list
    missing: list


def audit_staged_paths(repo_dir: Path, task_paths: Sequence[str]) -> AuditResult:
    """Mirror the pre-commit staging audit from the plan's Global Constraints.

    The complete staged path set must equal ``task_paths`` exactly. Returns
    the paths staged outside the allowlist (``extra``) and allowlisted paths
    that are not staged (``missing``); ``ok`` is false whenever either is
    non-empty, matching the non-zero result of the PowerShell audit.
    """
    result = _run_git(["diff", "--cached", "--name-only"], cwd=repo_dir)
    assert result.returncode == 0, result.stderr
    staged = sorted(line for line in result.stdout.splitlines() if line)
    allowed = sorted(task_paths)
    extra = sorted(set(staged) - set(allowed))
    missing = sorted(set(allowed) - set(staged))
    return AuditResult(ok=not extra and not missing, extra=extra, missing=missing)


def _init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-b", "main"], cwd=repo_dir)
    _run_git(["config", "user.email", "continuity-test@example.invalid"], cwd=repo_dir)
    _run_git(["config", "user.name", "Continuity Test"], cwd=repo_dir)
    # Disposable test repositories can land under a long --basetemp path (for
    # example a nested scratch directory). Allow long paths so the audit test
    # is not sensitive to how deep the caller's temp root happens to be.
    _run_git(["config", "core.longpaths", "true"], cwd=repo_dir)


def test_root_instruction_references_resolve_once():
    """AGENTS.md and CLAUDE.md each load the shared documents exactly once,
    in the exact order the approved design specifies, and every reference
    resolves to a tracked-candidate file that actually exists."""
    agents_lines = [
        line for line in (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert agents_lines == [
        "@RTK.md",
        "@.ai/WORKFLOW.md",
        "@.ai/PROJECT_STATUS.md",
        "@.codex/active-team.md",
    ]
    for line in agents_lines:
        ref = line[1:]
        assert (PROJECT_ROOT / ref).is_file(), f"AGENTS.md reference {ref} does not resolve"

    claude_lines = [
        line for line in (PROJECT_ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert claude_lines == [
        "@.ai/WORKFLOW.md",
        "@.ai/PROJECT_STATUS.md",
        "@.claude/active-team.md",
    ]
    for line in claude_lines:
        ref = line[1:]
        assert (PROJECT_ROOT / ref).is_file(), f"CLAUDE.md reference {ref} does not resolve"


def test_instruction_files_use_lowercase_codex_reference():
    """The Codex directory reference must use the actual lowercase `.codex`
    directory name so case-sensitive clones resolve it."""
    agents_text = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    codex_mentions = re.findall(r"\.[Cc][Oo][Dd][Ee][Xx]\b", agents_text)
    assert codex_mentions, "AGENTS.md must reference the .codex directory"
    assert all(mention == ".codex" for mention in codex_mentions), (
        f"AGENTS.md must use lowercase .codex only, found: {codex_mentions}"
    )
    assert "@.codex/active-team.md" in agents_text.splitlines()


@pytest.mark.parametrize("workstream_path", WORKSTREAM_FILES, ids=lambda p: p.name)
def test_workstream_files_have_one_managed_marker_pair(workstream_path: Path):
    """Each workstream file has exactly one byte-exact managed marker pair,
    start before end, so `update` never has to guess where to write."""
    text = workstream_path.read_text(encoding="utf-8")
    assert text.count(MARKER_START) == 1, f"{workstream_path.name} must have one start marker"
    assert text.count(MARKER_END) == 1, f"{workstream_path.name} must have one end marker"
    assert text.index(MARKER_START) < text.index(MARKER_END)


@pytest.mark.parametrize("relative_path", PORTABLE_TEAM_FILES)
def test_portable_team_files_contain_no_absolute_project_path(relative_path: str):
    """Portable Claude/Codex team files describe role behavior only and must
    never bake in a contributor's local machine-specific checkout path."""
    path = PROJECT_ROOT / relative_path
    assert path.is_file(), f"{relative_path} must exist for the portable pilot"
    text = path.read_text(encoding="utf-8")
    match = ABSOLUTE_PATH_PATTERN.search(text)
    assert match is None, f"{relative_path} contains an absolute path: {match.group(0) if match else ''}"


def test_candidate_staged_path_outside_allowlist_fails_audit(tmp_path):
    """The exact-path staging audit rejects any staged path outside the
    task allowlist, naming only the offending extra path."""
    repo_dir = tmp_path / "audit-repo"
    _init_repo(repo_dir)

    allowed_relative_path = "AGENTS.md"
    (repo_dir / allowed_relative_path).write_text("@RTK.md\n", encoding="utf-8")
    (repo_dir / "server.log").write_text("noise\n", encoding="utf-8")

    add_result = _run_git(["add", "--", allowed_relative_path, "server.log"], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr

    audit = audit_staged_paths(repo_dir, [allowed_relative_path])

    assert audit.ok is False
    assert audit.extra == ["server.log"]
    assert audit.missing == []
