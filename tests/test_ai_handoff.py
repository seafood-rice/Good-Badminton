"""Black-box portability tests for the Claude-Codex continuity pilot.

These tests check the shared entry points, durable workstream records, and
portable team configuration described in
``docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md``. They
also exercise the exact-path staging audit that every continuity commit must
pass before it is created (see the implementation plan's Global Constraints).

Task 2 adds the black-box harness that drives ``scripts/ai-handoff.ps1`` as a
subprocess (``run_helper``/``parse_json_stdout`` below) plus tests for the
read-only ``status`` operation. Later tasks extend this module with tests for
``start``, ``update``, ``handoff``, ``accept``, and ``takeover``.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "ai-handoff.ps1"

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

# Whole-branch review Fix I1: every portable tracked record the pilot ships
# -- not just the Claude/Codex team files -- must never bake in one
# contributor's machine-specific checkout location. This is the union of
# `PORTABLE_TEAM_FILES` above plus the shared entry points and every durable
# `.ai` record (`WORKFLOW.md`, `PROJECT_STATUS.md`, and both workstream
# files).
PORTABLE_NO_ABSOLUTE_PATH_FILES = PORTABLE_TEAM_FILES + (
    "AGENTS.md",
    "CLAUDE.md",
    "RTK.md",
    ".ai/WORKFLOW.md",
    ".ai/PROJECT_STATUS.md",
    ".ai/workstreams/continuity-pilot.md",
    ".ai/workstreams/match-stroke-recognition-b.md",
)

# Any drive-letter, msys (`/x/...`), UNC, POSIX-home, or tilde-relative
# absolute LOCAL path. Portable tracked records must describe role behavior,
# project state, and REMOTE references (`https://...` URLs) only; they must
# never bake in one contributor's machine-specific checkout location. The
# `(?<![A-Za-z0-9])` guard before a bare drive/msys letter is required so
# this never misfires on a URL SCHEME -- for example the "s:" immediately
# before "//" in "https://..." or the "p:" in "http://..." -- which is a
# single letter followed by ':' and '/' just like a real drive-letter path,
# but is always preceded by another letter, never by a path/quote/whitespace
# boundary.
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"             # C:\... or D:/... (not a URL scheme)
    r"|\\\\[A-Za-z0-9._-]+"                        # \\server\share UNC paths
    r"|(?<![A-Za-z0-9])/[A-Za-z]/[A-Za-z0-9._-]"   # msys/Git-Bash drive paths such as /d/Dev/...
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


# ---------------------------------------------------------------------------
# Black-box harness for scripts/ai-handoff.ps1
#
# Every invocation goes through one fixed pwsh wrapper script. Test
# parameters always travel as one JSON object on stdin, converted with
# ConvertFrom-Json -AsHashtable and splatted into the helper; no test input
# is ever concatenated into PowerShell source. This is what keeps array
# parameters (Scope, ChangedPath, VerificationDirtyPath) subprocess-safe
# ahead of Task 3, and it is why a JSON-parsing helper is required: stdout
# must be exactly one JSON object with no diagnostic text mixed in.
# ---------------------------------------------------------------------------


def _find_pwsh() -> str:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh (PowerShell 7) is not available on PATH")
    return pwsh


# Fixed wrapper source, identical for every call regardless of test input.
_HELPER_WRAPPER_SCRIPT = textwrap.dedent(
    """
    $ErrorActionPreference = 'Stop'
    $raw = [Console]::In.ReadToEnd()
    $parameters = if ([string]::IsNullOrEmpty($raw)) { @{} } else { $raw | ConvertFrom-Json -AsHashtable }
    $scriptPath = $parameters['ScriptPath']
    $operation = $parameters['Operation']
    $parameters.Remove('ScriptPath') | Out-Null
    $parameters.Remove('Operation') | Out-Null
    & $scriptPath $operation @parameters
    exit $LASTEXITCODE
    """
).strip()


class HelperResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def run_helper(
    cwd: Path,
    operation: str,
    env: Optional[dict] = None,
    **parameters,
) -> HelperResult:
    """Invoke scripts/ai-handoff.ps1 as a black box through the fixed pwsh
    wrapper above.

    All parameters (including PowerShell switches like ``Json=True`` and
    arrays like ``Scope=[".ai/", "scripts/"]``) travel as one JSON object on
    stdin, so the real array/splat mechanics are exercised without building
    any PowerShell source from test-controlled strings.

    ``env``, when supplied, is layered on top of the current process
    environment (never replacing it) -- Task 3 uses this to set the single
    fixed test-fault variable ``AI_CONTINUITY_TEST_FAULT`` for one call
    without otherwise changing how the helper is invoked. Omitting ``env``
    (the default) preserves Task 2's exact behavior: the child inherits the
    ambient environment untouched.
    """
    pwsh = _find_pwsh()
    payload = {"ScriptPath": str(SCRIPT_PATH), "Operation": operation, **parameters}
    run_env = None
    if env is not None:
        run_env = {**os.environ, **env}
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", _HELPER_WRAPPER_SCRIPT],
        cwd=cwd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
    )
    return HelperResult(completed.returncode, completed.stdout, completed.stderr)


def parse_json_stdout(result: HelperResult) -> dict:
    """Decode stdout as exactly one JSON object.

    Fails loudly if stdout is empty, is not valid JSON, or contains any
    extra non-JSON text before or after the JSON object -- for example a
    stray PowerShell warning/verbose line that leaked onto stdout and would
    otherwise silently corrupt a caller's ``json.loads``.
    """
    stripped = result.stdout.strip()
    assert stripped, (
        f"expected exactly one JSON object on stdout, got none. "
        f"returncode={result.returncode} stderr={result.stderr!r}"
    )
    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout is not valid JSON: {exc}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        ) from exc
    trailing = stripped[end:].strip()
    assert trailing == "", f"unexpected extra stdout after the JSON object: {trailing!r}"
    return parsed


def _write_minimal_ai_records(repo_dir: Path) -> None:
    """Minimal `.ai` records for the disposable harness repository, shaped
    like the real project's layout (Task 2 brief, Step 1)."""
    workstreams_dir = repo_dir / ".ai" / "workstreams"
    workstreams_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".ai" / "WORKFLOW.md").write_text("# Workflow\n", encoding="utf-8")
    (repo_dir / ".ai" / "PROJECT_STATUS.md").write_text("# Project Status\n", encoding="utf-8")
    (workstreams_dir / "continuity-pilot.md").write_text(
        "# Workstream: continuity-pilot\n\n" f"{MARKER_START}\n{MARKER_END}\n",
        encoding="utf-8",
    )


def _make_repo_on_branch(tmp_path: Path, branch: str, dir_name: str = "continuity-repo") -> Path:
    """Build a disposable Git repository shaped like the real project --
    `main` with minimal `.ai` records committed -- then check out an
    arbitrary `branch` on top of it. Task 3's branch-matrix tests reuse this
    to exercise every prefix/suffix/exception combination without repeating
    the same repo setup (Task 3 brief, Step 2)."""
    repo_dir = tmp_path / dir_name
    _init_repo(repo_dir)
    _write_minimal_ai_records(repo_dir)

    add_result = _run_git(["add", "."], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Initial commit"], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr
    checkout_result = _run_git(["checkout", "-b", branch], cwd=repo_dir)
    assert checkout_result.returncode == 0, checkout_result.stderr

    return repo_dir


@pytest.fixture()
def continuity_repo(tmp_path: Path) -> Path:
    """A disposable Git repository shaped like the real project: `main` with
    minimal `.ai` records committed, then checked out on the
    `codex/continuity-pilot` workstream branch (Task 2 brief, Step 1)."""
    return _make_repo_on_branch(tmp_path, "codex/continuity-pilot")


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


@pytest.mark.parametrize("relative_path", PORTABLE_NO_ABSOLUTE_PATH_FILES)
def test_portable_team_files_contain_no_absolute_project_path(relative_path: str):
    """Every portable tracked record -- Claude/Codex team files, the shared
    entry points, and the durable `.ai` records -- describes role behavior
    and project state only and must never bake in a contributor's local
    machine-specific checkout path. A `https://...` remote URL is not an
    absolute local path and must never be flagged (whole-branch review Fix
    I1)."""
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


def test_candidate_missing_allowlisted_path_fails_audit(tmp_path):
    """The exact-path staging audit rejects a candidate whose staged set
    omits an allowlisted path, naming only the missing path and reporting
    no extras."""
    repo_dir = tmp_path / "audit-missing-repo"
    _init_repo(repo_dir)

    staged_path = "AGENTS.md"
    missing_path = "CLAUDE.md"
    (repo_dir / staged_path).write_text("@RTK.md\n", encoding="utf-8")
    (repo_dir / missing_path).write_text("@.ai/WORKFLOW.md\n", encoding="utf-8")

    add_result = _run_git(["add", "--", staged_path], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr

    audit = audit_staged_paths(repo_dir, [staged_path, missing_path])

    assert audit.ok is False
    assert audit.extra == []
    assert audit.missing == [missing_path]


# ---------------------------------------------------------------------------
# Task 2: read-only `status` operation
# ---------------------------------------------------------------------------

# The exact top-level JSON keys the approved design specifies for every
# operation result (design spec, "Helper Contract"). `status` never sets
# `recovery` -- that key only appears for an operation that ends in a
# recoverable successor-owned state (Task 6+) -- so the stable shape here is
# exactly these eleven keys, no more and no fewer.
STATUS_TOP_LEVEL_KEYS = {
    "schema_version",
    "ok",
    "operation",
    "repo_root",
    "git_common_dir",
    "workstream_id",
    "claim_id",
    "git",
    "claims",
    "warnings",
    "errors",
}


def test_status_returns_exit_4_outside_git(tmp_path):
    """Running `status` outside any Git working tree returns the stable
    exit `4` with a Git-context error, never exit `0` and never a stray
    uncaught exception."""
    outside_dir = tmp_path / "not-a-repo"
    outside_dir.mkdir()

    result = run_helper(outside_dir, "status", Json=True)

    assert result.returncode == 4, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["operation"] == "status"
    assert parsed["repo_root"] is None
    assert parsed["git_common_dir"] is None
    assert parsed["errors"], "expected at least one error describing the missing Git context"


def test_status_json_has_stable_top_level_shape(continuity_repo):
    """`-Json` emits exactly one JSON object whose top-level keys match the
    approved design's `status` shape exactly, with `operation == "status"`,
    a full 40-character Git SHA, and canonical absolute `repo_root`/
    `git_common_dir`."""
    result = run_helper(continuity_repo, "status", Json=True)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)

    assert set(parsed.keys()) == STATUS_TOP_LEVEL_KEYS
    assert parsed["schema_version"] == 1
    assert parsed["ok"] is True
    assert parsed["operation"] == "status"
    assert parsed["claims"] == []
    assert parsed["warnings"] == []
    assert parsed["errors"] == []

    repo_root = Path(parsed["repo_root"])
    assert repo_root.is_absolute()
    assert repo_root.resolve() == continuity_repo.resolve()

    git_common_dir = Path(parsed["git_common_dir"])
    assert git_common_dir.is_absolute()

    assert re.fullmatch(r"[0-9a-f]{40}", parsed["git"]["head"]), parsed["git"]["head"]
    assert parsed["git"]["branch"] == "codex/continuity-pilot"
    assert parsed["git"]["detached"] is False
    assert parsed["git"]["protected_branch"] == "main"


def test_status_is_available_on_protected_main(continuity_repo):
    """Read-only `status` remains available on the protected `main` branch;
    only mutating operations reject it (design spec, "Branch commit
    policy")."""
    checkout_result = _run_git(["checkout", "main"], cwd=continuity_repo)
    assert checkout_result.returncode == 0, checkout_result.stderr

    result = run_helper(continuity_repo, "status", Json=True)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["errors"] == []
    assert parsed["git"]["branch"] == "main"
    assert parsed["git"]["protected_branch"] == "main"
    warning_codes = {warning["code"] for warning in parsed["warnings"]}
    assert "protected-branch" in warning_codes


def test_status_does_not_create_continuity_directory(continuity_repo):
    """`status` never acquires a write lock and never creates
    `<git-common-dir>/ai-continuity`, even when it completes successfully."""
    result = run_helper(continuity_repo, "status", Json=True)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)

    git_common_dir = Path(parsed["git_common_dir"])
    assert not (git_common_dir / "ai-continuity").exists()


def test_status_reports_shared_git_common_directory(continuity_repo):
    """A linked worktree of the same repository sees the identical
    canonical absolute Git common directory that the primary worktree
    reports, even though each worktree has its own distinct top-level
    `repo_root` and the primary worktree's own `--git-common-dir` output is
    relative (`.git`) rather than absolute."""
    linked_worktree = continuity_repo.parent / "linked-worktree"
    worktree_result = _run_git(
        ["worktree", "add", "-b", "codex/continuity-pilot-secondary", str(linked_worktree), "main"],
        cwd=continuity_repo,
    )
    assert worktree_result.returncode == 0, worktree_result.stderr

    primary_result = run_helper(continuity_repo, "status", Json=True)
    assert primary_result.returncode == 0, primary_result.stderr
    primary_parsed = parse_json_stdout(primary_result)

    linked_result = run_helper(linked_worktree, "status", Json=True)
    assert linked_result.returncode == 0, linked_result.stderr
    linked_parsed = parse_json_stdout(linked_result)

    assert primary_parsed["git_common_dir"] == linked_parsed["git_common_dir"]
    assert primary_parsed["repo_root"] != linked_parsed["repo_root"]


def test_status_reports_malformed_claim_as_exit_3(continuity_repo):
    """Malformed claim JSON under
    `<git-common-dir>/ai-continuity/claims` makes `status` fail closed with
    exit `3` rather than reporting a false success or crashing
    uncontrolled."""
    baseline = run_helper(continuity_repo, "status", Json=True)
    assert baseline.returncode == 0, baseline.stderr
    git_common_dir = Path(parse_json_stdout(baseline)["git_common_dir"])

    claims_dir = git_common_dir / "ai-continuity" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    (claims_dir / "broken.json").write_text("{ not valid json", encoding="utf-8")

    result = run_helper(continuity_repo, "status", Json=True)

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"], "expected a malformed-state error"


def test_helper_wrapper_passes_two_element_array_parameters(continuity_repo):
    """The JSON-over-stdin wrapper marshals PowerShell array parameters
    correctly for two-element arrays, ahead of Task 3 wiring `start`'s
    `-Scope` and Task 4 wiring `-ChangedPath`/`-VerificationDirtyPath` to
    real logic. `status` declares but does not use these parameters yet, so
    this proves the harness's array plumbing independently of any one
    operation (Task 2 brief, Step 1)."""
    result = run_helper(
        continuity_repo,
        "status",
        Json=True,
        Scope=[".ai/", "scripts/"],
        ChangedPath=["a.txt", "b.txt"],
        VerificationDirtyPath=["c.txt", "d.txt"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["operation"] == "status"
    assert parsed["ok"] is True


# ---------------------------------------------------------------------------
# Review-finding regression tests: validation must route through the stable
# exit-code contract (finding 1), and `status` must detect overlapping live
# claims, expired leases, and ordinary Git drift (finding 2).
# ---------------------------------------------------------------------------


def test_status_returns_exit_2_and_json_for_invalid_operation(continuity_repo):
    """A bogus operation name is a validation failure, not a raw
    parameter-binding crash: it must return the stable exit `2` with exactly
    one JSON object naming a validation error, never exit `1` with empty
    stdout and a raw PowerShell error on stderr."""
    result = run_helper(continuity_repo, "bogus-operation", Json=True)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"], "expected a validation error"
    assert any(error["code"] == "validation" for error in parsed["errors"])


def test_status_returns_exit_2_for_missing_operation(continuity_repo):
    """No operation supplied is also a validation failure routed through the
    stable exit-code contract, not exit `1` with empty stdout."""
    result = run_helper(continuity_repo, "", Json=True)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"], "expected a validation error"
    assert any(error["code"] == "validation" for error in parsed["errors"])


def test_status_returns_exit_2_for_out_of_range_lease_hours(continuity_repo):
    """An out-of-range `-LeaseHours` is a validation failure routed through
    the stable exit-code contract, not exit `1` with empty stdout, even
    though `status` itself never consumes the value."""
    result = run_helper(continuity_repo, "status", LeaseHours=99, Json=True)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"], "expected a validation error"
    assert any(error["code"] == "validation" for error in parsed["errors"])


def _claims_dir_for(repo_dir: Path) -> Path:
    """Resolve `<git-common-dir>/ai-continuity/claims` for a disposable
    repository, creating it directly the same way
    `test_status_reports_malformed_claim_as_exit_3` does, without going
    through `start` (not implemented yet)."""
    baseline = run_helper(repo_dir, "status", Json=True)
    assert baseline.returncode == 0, baseline.stderr
    git_common_dir = Path(parse_json_stdout(baseline)["git_common_dir"])
    claims_dir = git_common_dir / "ai-continuity" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    return claims_dir


def _write_claim(claims_dir: Path, file_name: str, **overrides) -> dict:
    """Write one well-formed live claim JSON file directly into the claims
    directory, shaped like the design's Local Claim Model, with sensible
    defaults callers override for the field(s) under test."""
    claim = {
        "schema_version": 1,
        "claim_id": "00000000-0000-4000-8000-000000000000",
        "workstream_id": "continuity-pilot",
        "agent": "codex",
        "session_id": "session-1",
        "worktree_path": "/tmp/does-not-matter",
        "branch": "codex/continuity-pilot",
        "base_commit": "0" * 40,
        "scope_paths": [".ai"],
        "started_utc": "2026-01-01T00:00:00Z",
        "heartbeat_utc": "2026-01-01T00:00:00Z",
        "lease_until_utc": "2999-01-01T00:00:00Z",
        "state": "active",
    }
    claim.update(overrides)
    (claims_dir / file_name).write_text(json.dumps(claim), encoding="utf-8")
    return claim


def test_status_reports_overlapping_live_claims_as_exit_2(continuity_repo):
    """Two live claims (`active`/`handoff-ready`) whose normalized scope
    paths overlap at a path boundary make `status` fail with the stable
    exit `2`, `ok: false`, and a conflict entry in `errors` -- distinct from
    the exit-`3` malformed-claim-JSON case."""
    claims_dir = _claims_dir_for(continuity_repo)
    head = _run_git(["rev-parse", "HEAD"], cwd=continuity_repo).stdout.strip()

    _write_claim(
        claims_dir,
        "continuity-pilot.json",
        claim_id="11111111-1111-4111-8111-111111111111",
        workstream_id="continuity-pilot",
        scope_paths=[".ai"],
        base_commit=head,
        state="active",
    )
    _write_claim(
        claims_dir,
        "match-stroke-recognition-b.json",
        claim_id="22222222-2222-4222-8222-222222222222",
        workstream_id="match-stroke-recognition-b",
        scope_paths=[".ai/workstreams"],
        base_commit=head,
        state="handoff-ready",
    )

    result = run_helper(continuity_repo, "status", Json=True)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"], "expected an overlapping-claims conflict error"
    assert any(error["code"] == "overlapping-claims" for error in parsed["errors"])


def test_status_warns_on_expired_lease(continuity_repo):
    """A live claim whose `lease_until_utc` is in the past makes `status`
    report an expiry warning at the stable exit `0`, never an error and
    never a non-zero exit."""
    claims_dir = _claims_dir_for(continuity_repo)
    head = _run_git(["rev-parse", "HEAD"], cwd=continuity_repo).stdout.strip()

    _write_claim(
        claims_dir,
        "continuity-pilot.json",
        claim_id="33333333-3333-4333-8333-333333333333",
        workstream_id="continuity-pilot",
        scope_paths=[".ai"],
        base_commit=head,
        lease_until_utc="2000-01-01T00:00:00Z",
        state="active",
    )

    result = run_helper(continuity_repo, "status", Workstream="continuity-pilot", Json=True)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["errors"] == []
    assert parsed["claim_id"] == "33333333-3333-4333-8333-333333333333"
    assert parsed["warnings"], "expected an expired-lease warning"
    assert any(warning["code"] == "expired-lease" for warning in parsed["warnings"])


# ---------------------------------------------------------------------------
# Task 3: scoped claims, locking, path validation, and dirty fingerprints
# ---------------------------------------------------------------------------

GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# The exact claim JSON shape the design's Local Claim Model specifies --
# no more, no fewer keys.
START_CLAIM_KEYS = {
    "schema_version", "claim_id", "workstream_id", "agent", "session_id",
    "worktree_path", "branch", "base_commit", "scope_paths", "started_utc",
    "heartbeat_utc", "lease_until_utc", "state", "preexisting_dirty",
    "predecessor_claim_id", "replaces_claim_id", "replacement_reason",
    "durable_status_path", "durable_status_sha256", "durable_status_blob_oid",
    "handoff_commit",
}


def _git_common_dir_for(repo_dir: Path) -> Path:
    """Resolve `<git-common-dir>` for a disposable repository without
    creating anything, by reading it off a plain `status` call."""
    baseline = run_helper(repo_dir, "status", Json=True)
    assert baseline.returncode == 0, baseline.stderr
    return Path(parse_json_stdout(baseline)["git_common_dir"])


def _claim_file_for(repo_dir: Path, workstream_id: str) -> Path:
    return _git_common_dir_for(repo_dir) / "ai-continuity" / "claims" / f"{workstream_id}.json"


def _checkout_new_branch(repo_dir: Path, branch: str) -> None:
    result = _run_git(["checkout", "-b", branch], cwd=repo_dir)
    assert result.returncode == 0, result.stderr


def _start(repo_dir: Path, **overrides) -> HelperResult:
    """Call `start` with reasonable defaults for the fields under test to
    override individually."""
    parameters = {
        "Agent": "codex",
        "SessionId": "session-1",
        "Workstream": "continuity-pilot",
        "Scope": ["shared"],
        "Json": True,
    }
    parameters.update(overrides)
    return run_helper(repo_dir, "start", **parameters)


# ---------------------------------------------------------------------------
# Step 1: identifier, scope, and path validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["Agent", "SessionId", "Workstream"])
def test_start_rejects_missing_required_parameter(continuity_repo, missing):
    """Each required `start` parameter is validated before any filesystem
    mutation; omitting one returns the stable exit `2`, never a raw
    parameter-binding crash."""
    parameters = {"Agent": "codex", "SessionId": "session-1", "Workstream": "continuity-pilot"}
    parameters[missing] = ""
    result = _start(continuity_repo, Scope=["shared"], **parameters)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


def test_start_rejects_empty_scope_list(continuity_repo):
    """`-Scope` must include at least one path; an empty array is rejected
    before any mutation."""
    result = _start(continuity_repo, Scope=[])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False


def test_start_rejects_invalid_agent(continuity_repo):
    result = _start(continuity_repo, Agent="gpt")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False


INVALID_WORKSTREAM_IDS = [
    "",
    "UPPER",
    "-leading-hyphen",
    "has_underscore",
    "has.dot",
    "has space",
    "has/slash",
    "a" * 65,
]


@pytest.mark.parametrize("workstream_id", INVALID_WORKSTREAM_IDS, ids=repr)
def test_start_rejects_invalid_workstream_id_forms(continuity_repo, workstream_id):
    """Every invalid workstream-ID form the design's `[a-z0-9][a-z0-9-]{0,63}`
    rule forbids returns exit `2` before any filesystem mutation."""
    result = _start(continuity_repo, Workstream=workstream_id)

    assert result.returncode == 2, (
        f"workstream={workstream_id!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


INVALID_SESSION_IDS = [
    "",
    " leading-space",
    ".leading-dot",
    "has space",
    "has/slash",
    "s" * 129,
]


@pytest.mark.parametrize("session_id", INVALID_SESSION_IDS, ids=repr)
def test_start_rejects_invalid_session_id_forms(continuity_repo, session_id):
    """Every invalid session-ID form the design's
    `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` rule forbids returns exit `2`."""
    result = _start(continuity_repo, SessionId=session_id)

    assert result.returncode == 2, (
        f"session={session_id!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False


def test_start_accepts_boundary_length_workstream_and_session_ids(tmp_path):
    """A 64-character workstream ID and a 128-character session ID -- the
    exact upper bounds the design's regexes allow -- both succeed."""
    workstream_id = "a" * 64
    session_id = "s" * 128
    repo_dir = _make_repo_on_branch(tmp_path, f"codex/{workstream_id}")

    result = _start(repo_dir, Workstream=workstream_id, SessionId=session_id)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["workstream_id"] == workstream_id


@pytest.mark.parametrize(
    "workstream_id", ["con", "nul", "com1", "com9", "lpt1", "lpt9", "prn", "aux"]
)
def test_start_rejects_reserved_device_name_workstream_case_insensitively(continuity_repo, workstream_id):
    """Reserved Windows device names are rejected case-insensitively as
    workstream IDs (design spec, "Identifier and path validation"; test
    list item 20)."""
    result = _start(continuity_repo, Workstream=workstream_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert any(
        "reserved windows device name" in error["message"].lower() for error in parsed["errors"]
    ), f"expected a reserved-device-name error, got {parsed['errors']!r}"


INVALID_SCOPE_FORMS = [
    "",
    "   ",
    ".",
    "..",
    "/",
    "a/../b",
    "../escape",
    "*",
    "a?b",
    "C:/absolute",
    "//server/share",
    ".git",
    "foo/.git",
    "foo/.git/bar",
]


@pytest.mark.parametrize("scope_value", INVALID_SCOPE_FORMS, ids=repr)
def test_start_rejects_invalid_scope_forms(continuity_repo, scope_value):
    """Every unsafe scope form the design always forbids -- empty/root,
    traversal, wildcards, drive-qualified/absolute/UNC paths, and `.git` or
    any descendant -- returns exit `2` before any filesystem mutation."""
    result = _start(continuity_repo, Scope=[scope_value])

    assert result.returncode == 2, (
        f"scope={scope_value!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


def test_start_rejects_out_of_range_lease_hours(continuity_repo):
    result = _start(continuity_repo, LeaseHours=99)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


def run_helper_file(cwd: Path, argv: Sequence[str]) -> HelperResult:
    """Invoke the helper through `pwsh -File`, the other real entry point.

    Unlike `-Command`, `-File` binds every argument as a literal string, so
    `-Scope a,b` reaches `[string[]] $Scope` as ONE element `'a,b'`. The
    arguments travel as a subprocess argv list; no PowerShell source is
    built from them."""
    pwsh = _find_pwsh()
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT_PATH), *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return HelperResult(completed.returncode, completed.stdout, completed.stderr)


_IDENTITY_ARGV = ["-Agent", "codex", "-SessionId", "session-1"]
_CLAIM_ID_ARGV = ["-ClaimId", "00000000-0000-0000-0000-000000000000"]

COMMA_JOINED_FILE_INVOCATIONS = {
    "start-Scope": [
        "start", *_IDENTITY_ARGV, "-Workstream", "continuity-pilot",
        "-Scope", ".ai/workstreams/continuity-pilot.md,shared",
    ],
    "takeover-Scope": [
        "takeover", *_IDENTITY_ARGV, "-Workstream", "continuity-pilot",
        "-PreviousClaimId", "00000000-0000-0000-0000-000000000000", "-Reason", "expired",
        "-Scope", ".ai/workstreams/continuity-pilot.md,shared",
    ],
    "update-ChangedPath": [
        "update", *_CLAIM_ID_ARGV, *_IDENTITY_ARGV, "-Summary", "milestone",
        "-ChangedPath", "shared/a.txt,shared/b.txt",
        "-VerificationResult", "not-run", "-NotRunReason", "n/a",
    ],
    "update-VerificationDirtyPath": [
        "update", *_CLAIM_ID_ARGV, *_IDENTITY_ARGV, "-Summary", "milestone",
        "-VerificationResult", "passed", "-VerificationCommand", "pytest",
        "-VerificationCommit", "0" * 40,
        "-VerificationDirtyPath", "shared/a.txt,shared/b.txt",
    ],
}


@pytest.mark.parametrize(
    "argv", COMMA_JOINED_FILE_INVOCATIONS.values(), ids=COMMA_JOINED_FILE_INVOCATIONS.keys()
)
def test_file_invocation_rejects_comma_joined_path_list(continuity_repo, argv):
    """Under `pwsh -File`, a comma list arrives as one comma-joined string.
    The helper must reject it with exit `2` and a hint to use `-Command`
    with `@('a','b')` -- never record it as a single scope path that guards
    no real file (observed on a live claim 2026-09-25) -- and must not
    create any continuity state."""
    result = run_helper_file(continuity_repo, [*argv, "-Json"])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    messages = [error["message"] for error in parsed["errors"] if error["code"] == "validation"]
    assert any("comma" in message and "@(" in message and "-Command" in message for message in messages), (
        f"expected a comma-list validation hint, got {parsed['errors']!r}"
    )
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


def test_get_help_documents_comma_list_rejection():
    """The comma-list decision is discoverable through `Get-Help`, not only
    by reading the source: the comment-based help block must be recognized
    and its Scope parameter help must point at the `-File` caveat."""
    pwsh = _find_pwsh()
    completed = subprocess.run(
        [
            pwsh, "-NoProfile", "-NonInteractive", "-Command",
            "Get-Help -Name ([Console]::In.ReadToEnd().Trim()) -Full | Out-String -Width 200",
        ],
        input=str(SCRIPT_PATH),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    help_text = completed.stdout
    assert "Claude-Codex continuity helper" in help_text
    assert "PATH ARRAYS AND -File" in help_text
    assert "@('a','b')" in help_text


def test_file_invocation_accepts_single_scope_path(continuity_repo):
    """`pwsh -File` stays usable for a single comma-free scope path; only
    the comma-joined form is refused."""
    result = run_helper_file(
        continuity_repo,
        ["start", *_IDENTITY_ARGV, "-Workstream", "continuity-pilot", "-Scope", "shared", "-Json"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    claim = json.loads(_claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8"))
    assert claim["scope_paths"] == ["shared"]


def _create_junction(link_path: Path, target_path: Path) -> None:
    """Create an unprivileged directory junction with `New-Item -ItemType
    Junction` (Task 3 brief, Step 1). Junctions never require elevation or
    Windows developer mode, unlike true symbolic links."""
    pwsh = _find_pwsh()
    script = (
        "New-Item -ItemType Junction -Path $env:JUNCTION_LINK "
        "-Target $env:JUNCTION_TARGET | Out-Null"
    )
    env = {**os.environ, "JUNCTION_LINK": str(link_path), "JUNCTION_TARGET": str(target_path)}
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _create_symlink_or_skip(link_path: Path, target_path: Path, *, target_is_directory: bool) -> None:
    """Create a real NTFS symbolic link, or skip the test outright when this
    machine has neither Windows developer mode nor the create-symlink
    privilege (Task 3 brief, Step 1: "capability-gate only the additional
    symbolic-link case")."""
    try:
        os.symlink(target_path, link_path, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symbolic links are not available in this environment: {exc}")


def test_start_rejects_scope_through_directory_junction(continuity_repo):
    """A scope path whose existing components pass through a directory
    junction is rejected -- the pilot never attempts to prove containment
    through a link (design spec, "Identifier and path validation")."""
    target_dir = continuity_repo.parent / "junction-target"
    target_dir.mkdir(parents=True, exist_ok=True)
    _create_junction(continuity_repo / "linked-scope", target_dir)

    result = _start(continuity_repo, Scope=["linked-scope/inner.txt"])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


def test_start_rejects_scope_through_symbolic_link(continuity_repo):
    """A scope path whose existing components pass through a symbolic link
    is rejected, exactly like a junction."""
    target_dir = continuity_repo.parent / "symlink-target"
    target_dir.mkdir(parents=True, exist_ok=True)
    _create_symlink_or_skip(
        continuity_repo / "linked-scope-symlink", target_dir, target_is_directory=True
    )

    result = _start(continuity_repo, Scope=["linked-scope-symlink/inner.txt"])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


# ---------------------------------------------------------------------------
# Step 2: branch validation
# ---------------------------------------------------------------------------


def test_start_rejects_protected_main_branch(continuity_repo):
    checkout = _run_git(["checkout", "main"], cwd=continuity_repo)
    assert checkout.returncode == 0, checkout.stderr

    result = _start(continuity_repo)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert not (_git_common_dir_for(continuity_repo) / "ai-continuity").exists()


def test_start_rejects_detached_head(continuity_repo):
    head = _run_git(["rev-parse", "HEAD"], cwd=continuity_repo).stdout.strip()
    checkout = _run_git(["checkout", "--detach", head], cwd=continuity_repo)
    assert checkout.returncode == 0, checkout.stderr

    result = _start(continuity_repo)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False


def test_start_rejects_wrong_branch_prefix(continuity_repo):
    _checkout_new_branch(continuity_repo, "feature/continuity-pilot")

    result = _start(continuity_repo)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_start_rejects_wrong_branch_suffix(continuity_repo):
    _checkout_new_branch(continuity_repo, "codex/some-other-id")

    result = _start(continuity_repo)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"


@pytest.mark.parametrize("prefix", ["codex", "claude"])
def test_start_accepts_both_valid_branch_prefixes(tmp_path, prefix):
    workstream_id = f"ws-{prefix}"
    repo_dir = _make_repo_on_branch(tmp_path, f"{prefix}/{workstream_id}")

    result = _start(repo_dir, Agent=prefix, Workstream=workstream_id)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True


@pytest.mark.parametrize("branch_prefix, agent", [("codex", "claude"), ("claude", "codex")])
def test_start_either_agent_can_operate_the_other_prefixs_branch(tmp_path, branch_prefix, agent):
    """The branch prefix identifies its creator, not its current owner:
    Claude may accept a `codex/` branch and Codex may accept a `claude/`
    branch (design spec, "Branch validation")."""
    workstream_id = "shared-workstream"
    repo_dir = _make_repo_on_branch(tmp_path, f"{branch_prefix}/{workstream_id}")

    result = _start(repo_dir, Agent=agent, Workstream=workstream_id)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_start_bootstrap_exception_allows_documented_branch_and_workstream(tmp_path):
    """The one documented backfill exception: `codex/good-badminton-
    development` may run `start` for workstream `continuity-pilot` only."""
    repo_dir = _make_repo_on_branch(tmp_path, "codex/good-badminton-development")

    result = _start(repo_dir, Workstream="continuity-pilot")

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_start_bootstrap_branch_rejects_other_workstream(tmp_path):
    """No workstream other than `continuity-pilot` may use the backfill
    branch exception."""
    repo_dir = _make_repo_on_branch(tmp_path, "codex/good-badminton-development")

    result = _start(repo_dir, Workstream="some-other-workstream")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"


# ---------------------------------------------------------------------------
# Step 3: claim tests -- identity, overlap, locking, and atomic-write faults
# ---------------------------------------------------------------------------


def test_start_creates_new_claim_with_expected_shape(continuity_repo):
    """A first `start` call creates exactly one claim matching the design's
    Local Claim Model shape, written to the documented on-disk path."""
    head = _run_git(["rev-parse", "HEAD"], cwd=continuity_repo).stdout.strip()

    result = _start(continuity_repo, Scope=[".ai", "scripts/ai-handoff.ps1"])

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["operation"] == "start"
    assert GUID_PATTERN.match(parsed["claim_id"])
    assert len(parsed["claims"]) == 1

    claim = parsed["claims"][0]
    assert set(claim.keys()) == START_CLAIM_KEYS
    assert claim["schema_version"] == 1
    assert claim["claim_id"] == parsed["claim_id"]
    assert claim["workstream_id"] == "continuity-pilot"
    assert claim["agent"] == "codex"
    assert claim["session_id"] == "session-1"
    assert claim["branch"] == "codex/continuity-pilot"
    assert claim["base_commit"] == head
    assert sorted(claim["scope_paths"]) == sorted([".ai", "scripts/ai-handoff.ps1"])
    assert claim["state"] == "active"
    assert claim["preexisting_dirty"] == []
    assert claim["predecessor_claim_id"] is None
    assert claim["replaces_claim_id"] is None
    assert claim["replacement_reason"] is None
    assert claim["durable_status_path"] is None
    assert claim["durable_status_sha256"] is None
    assert claim["durable_status_blob_oid"] is None
    assert claim["handoff_commit"] is None
    assert "/" in claim["worktree_path"] and "\\" not in claim["worktree_path"]

    claim_file = _claim_file_for(continuity_repo, "continuity-pilot")
    assert claim_file.is_file()
    on_disk = json.loads(claim_file.read_text(encoding="utf-8"))
    assert on_disk["claim_id"] == parsed["claim_id"]


def test_start_is_idempotent_for_same_identity_and_scope(continuity_repo):
    """Repeating the same agent/session/workstream claim with the identical
    normalized scope (regardless of input order) renews the same claim
    instead of creating another (design spec, Helper Contract; test list
    item 3)."""
    first = _start(continuity_repo, Scope=["shared", "other"])
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    first_claim_id = parse_json_stdout(first)["claim_id"]

    second = _start(continuity_repo, Scope=["other", "shared"])
    assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    second_parsed = parse_json_stdout(second)
    assert second_parsed["ok"] is True
    assert second_parsed["claim_id"] == first_claim_id
    assert len(second_parsed["claims"]) == 1


def test_start_rejects_scope_change_for_active_claim(continuity_repo):
    """A same-identity `start` with a different scope is rejected; the
    original claim's scope is left completely unchanged."""
    first = _start(continuity_repo, Scope=["shared"])
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    second = _start(continuity_repo, Scope=["different-scope"])

    assert second.returncode == 2, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    parsed = parse_json_stdout(second)
    assert parsed["ok"] is False

    on_disk = json.loads(_claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8"))
    assert on_disk["scope_paths"] == ["shared"]


def test_start_rejects_wrong_identity_for_active_claim(continuity_repo):
    """A different session ID for the same workstream/worktree/branch is
    rejected as a wrong identity and never renews or replaces the claim
    (design spec, "Claim ownership identity")."""
    first = _start(continuity_repo, SessionId="session-1")
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    first_claim_id = parse_json_stdout(first)["claim_id"]

    second = _start(continuity_repo, SessionId="session-2")

    assert second.returncode == 2, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    parsed = parse_json_stdout(second)
    assert parsed["ok"] is False

    on_disk = json.loads(_claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8"))
    assert on_disk["claim_id"] == first_claim_id
    assert on_disk["session_id"] == "session-1"


def test_start_renewal_rejects_drift_in_preexisting_dirty(continuity_repo):
    """Whole-branch review Fix C1: a same-identity/same-scope renewal must
    PRESERVE the existing claim's `preexisting_dirty` baseline as
    authoritative and VALIDATE the current working tree against it, rather
    than silently recapturing current out-of-scope dirt and re-baselining
    it. A captured fingerprint that has since drifted blocks renewal with
    exit `2`, and the stored baseline on disk is left completely
    unchanged."""
    setup = _setup_modified(continuity_repo, "keep")

    first = _start(continuity_repo, Scope=["claim"])
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    first_parsed = parse_json_stdout(first)
    first_claim_id = first_parsed["claim_id"]
    captured = [
        entry for entry in first_parsed["claims"][0]["preexisting_dirty"] if entry["path"] == setup["path"]
    ]
    assert len(captured) == 1, first_parsed["claims"][0]["preexisting_dirty"]

    # Drift the captured pre-existing dirty path's content AFTER `start`
    # captured its fingerprint, then attempt an identical-identity/
    # identical-scope renewal.
    (continuity_repo / setup["path"]).write_text("drifted after start captured it\n", encoding="utf-8")

    second = _start(continuity_repo, Scope=["claim"])

    assert second.returncode == 2, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    assert parse_json_stdout(second)["ok"] is False

    on_disk = json.loads(_claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8"))
    assert on_disk["claim_id"] == first_claim_id
    on_disk_captured = [entry for entry in on_disk["preexisting_dirty"] if entry["path"] == setup["path"]]
    assert on_disk_captured == captured, (
        "the existing claim's preexisting_dirty baseline must be left completely "
        f"unchanged by a rejected renewal, got {on_disk_captured!r} vs {captured!r}"
    )


def test_start_rejects_overlapping_scope_with_other_workstreams_claim(continuity_repo):
    """A live claim for one workstream blocks an overlapping-prefix `start`
    for a different workstream, and leaves the original claim untouched."""
    first = _start(continuity_repo, Workstream="continuity-pilot", Scope=["shared"])
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    _checkout_new_branch(continuity_repo, "codex/other-workstream")
    second = _start(
        continuity_repo,
        Workstream="other-workstream",
        SessionId="session-2",
        Scope=["shared/nested"],
    )

    assert second.returncode == 2, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    parsed = parse_json_stdout(second)
    assert parsed["ok"] is False

    status_after = run_helper(continuity_repo, "status", Json=True)
    claims = parse_json_stdout(status_after)["claims"]
    assert len(claims) == 1
    assert claims[0]["workstream_id"] == "continuity-pilot"


def test_start_allows_disjoint_scope_concurrent_claims(continuity_repo):
    """Two different workstreams with non-overlapping scope prefixes can
    both hold live claims at once (test list item 5)."""
    first = _start(continuity_repo, Workstream="continuity-pilot", Scope=["shared"])
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    _checkout_new_branch(continuity_repo, "codex/other-workstream")
    second = _start(
        continuity_repo,
        Workstream="other-workstream",
        SessionId="session-2",
        Scope=["disjoint"],
    )

    assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    parsed = parse_json_stdout(second)
    assert parsed["ok"] is True

    status_after = run_helper(continuity_repo, "status", Json=True)
    workstream_ids = {claim["workstream_id"] for claim in parse_json_stdout(status_after)["claims"]}
    assert workstream_ids == {"continuity-pilot", "other-workstream"}


def test_start_shares_claims_through_linked_worktree(continuity_repo):
    """A linked worktree sees the same live claims through the shared Git
    common directory: attempting an overlapping-scope claim for a different
    workstream from the linked worktree still fails (test list item 2)."""
    first = _start(continuity_repo, Workstream="continuity-pilot", Scope=["shared"])
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    linked_worktree = continuity_repo.parent / "linked-worktree"
    worktree_result = _run_git(
        ["worktree", "add", "-b", "codex/other-workstream", str(linked_worktree)],
        cwd=continuity_repo,
    )
    assert worktree_result.returncode == 0, worktree_result.stderr

    second = _start(
        linked_worktree,
        Workstream="other-workstream",
        SessionId="session-2",
        Scope=["shared/nested"],
    )

    assert second.returncode == 2, f"stdout={second.stdout!r} stderr={second.stderr!r}"
    parsed = parse_json_stdout(second)
    assert parsed["ok"] is False


def _lock_path_for(repo_dir: Path) -> Path:
    return _git_common_dir_for(repo_dir) / "ai-continuity" / "lock"


_LOCK_HOLDER_SCRIPT = textwrap.dedent(
    """
    $ErrorActionPreference = 'Stop'
    $directory = Split-Path -Parent $env:LOCK_HOLDER_PATH
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $stream = [System.IO.File]::Open(
        $env:LOCK_HOLDER_PATH,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    [Console]::Out.Write('locked')
    [Console]::Out.Flush()
    Start-Sleep -Seconds ([double] $env:LOCK_HOLDER_SECONDS)
    $stream.Dispose()
    """
).strip()


def _spawn_lock_holder(lock_path: Path, hold_seconds: float) -> subprocess.Popen:
    """Start a background process that exclusively opens `lock_path` --
    exactly like `Use-ContinuityLock` does -- and holds it for
    `hold_seconds`, simulating a concurrent continuity process. Blocks until
    the holder confirms it actually holds the lock, so the caller never
    races its own acquisition attempt. The caller must terminate or wait on
    the returned process."""
    pwsh = _find_pwsh()
    env = {
        **os.environ,
        "LOCK_HOLDER_PATH": str(lock_path),
        "LOCK_HOLDER_SECONDS": str(hold_seconds),
    }
    process = subprocess.Popen(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", _LOCK_HOLDER_SCRIPT],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    confirmation = process.stdout.read(len("locked"))
    assert confirmation == "locked", f"lock holder failed to start: {process.stderr.read()}"
    return process


def test_start_times_out_on_lock_contention_without_mutation(continuity_repo):
    """A concurrent process holding the continuity lock makes `start` fail
    with the stable exit `3` after the fixed timeout, without creating or
    corrupting any claim."""
    holder = _spawn_lock_holder(_lock_path_for(continuity_repo), hold_seconds=8)
    try:
        started = time.monotonic()
        result = _start(continuity_repo)
        elapsed = time.monotonic() - started

        assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        parsed = parse_json_stdout(result)
        assert parsed["ok"] is False
        assert elapsed >= 4.0, f"expected the fixed lock timeout to elapse, took {elapsed:.2f}s"
        assert not _claim_file_for(continuity_repo, "continuity-pilot").exists()
    finally:
        holder.wait(timeout=15)


def test_start_reacquires_lock_promptly_after_owner_process_is_killed(continuity_repo):
    """If the process holding the continuity lock is killed, its OS file
    handle is released immediately: a subsequent `start` reacquires the
    lock and succeeds well within the fixed timeout, never waiting for or
    deleting a stale marker (design spec, "Failure and conflict handling")."""
    holder = _spawn_lock_holder(_lock_path_for(continuity_repo), hold_seconds=30)
    holder.kill()
    holder.wait(timeout=15)

    started = time.monotonic()
    result = _start(continuity_repo)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert elapsed < 4.0, f"expected prompt reacquisition after the holder was killed, took {elapsed:.2f}s"


def test_start_before_replace_fault_preserves_absence_of_prior_claim(continuity_repo):
    """A fault injected before the atomic claim replace leaves no claim
    behind: the old/empty state remains authoritative (Task 3 brief, Step 3;
    fixed fault `start-before-claim-replace`)."""
    result = _start(
        continuity_repo, env={"AI_CONTINUITY_TEST_FAULT": "start-before-claim-replace"}
    )

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert "start-before-claim-replace" not in result.stdout
    assert "start-before-claim-replace" not in result.stderr
    assert not _claim_file_for(continuity_repo, "continuity-pilot").exists()


def test_start_before_replace_fault_preserves_existing_claim_on_renewal(continuity_repo):
    """The same before-replace fault on a renewal attempt leaves the
    existing claim file completely unchanged."""
    first = _start(continuity_repo)
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
    original_claim = parse_json_stdout(first)["claims"][0]

    result = _start(
        continuity_repo, env={"AI_CONTINUITY_TEST_FAULT": "start-before-claim-replace"}
    )

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "start-before-claim-replace" not in result.stdout
    assert "start-before-claim-replace" not in result.stderr

    on_disk = json.loads(_claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8"))
    assert on_disk == original_claim


def test_start_after_replace_fault_reports_new_claim_authoritative_and_retry_renews_it(continuity_repo):
    """A fault injected after the atomic claim replace leaves the new claim
    authoritative on disk; the operation returns exit `3` with that claim's
    ID and a same-identity `start` retry command, and the retry renews that
    claim rather than creating another (Task 3 brief, Step 3; fixed fault
    `start-after-claim-replace`)."""
    result = _start(
        continuity_repo, env={"AI_CONTINUITY_TEST_FAULT": "start-after-claim-replace"}
    )

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert "start-after-claim-replace" not in result.stdout
    assert "start-after-claim-replace" not in result.stderr
    assert parsed["claim_id"]
    assert GUID_PATTERN.match(parsed["claim_id"])
    assert parsed.get("recovery"), "expected recovery fields naming the authoritative claim"
    retry_command = parsed["recovery"].get("retry_command", "")
    assert "start" in retry_command
    assert "continuity-pilot" in retry_command

    on_disk = json.loads(_claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8"))
    assert on_disk["claim_id"] == parsed["claim_id"]

    retry = _start(continuity_repo)
    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["claim_id"] == parsed["claim_id"]
    assert len(retry_parsed["claims"]) == 1


def test_start_after_replace_fault_retry_command_is_copy_pasteable_with_quoted_scope(continuity_repo):
    """`Normalize-ScopePath` permits a literal single quote in a scope
    segment (for example `o'brien/notes`); the same-identity `retry_command`
    the exit-3 recovery payload emits embeds the normalized scope inside a
    single-quoted PowerShell array literal (`@('...')`) and must escape any
    embedded quote as `''`, or the emitted text is not valid PowerShell
    source a human could copy-paste (Task 3 review fix 2)."""
    quoted_scope = "o'brien/notes"
    result = _start(
        continuity_repo,
        Scope=[quoted_scope],
        env={"AI_CONTINUITY_TEST_FAULT": "start-after-claim-replace"},
    )

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    retry_command = parsed["recovery"]["retry_command"]
    assert "''" in retry_command, (
        f"expected the embedded single quote escaped as '' in retry_command, got {retry_command!r}"
    )

    pwsh = _find_pwsh()
    invocation = f"& '{SCRIPT_PATH}' {retry_command} -Json"
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", invocation],
        cwd=continuity_repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        f"retry_command was not copy-pasteable PowerShell source: "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    retried = parse_json_stdout(HelperResult(completed.returncode, completed.stdout, completed.stderr))
    assert retried["ok"] is True
    assert retried["claim_id"] == parsed["claim_id"]
    assert retried["claims"][0]["scope_paths"] == [quoted_scope]


def test_start_rejects_unknown_test_fault_value(continuity_repo):
    """The single test-fault hook accepts only the fixed set of known
    boundary names; any other value fails closed before mutation, and is
    checked regardless of which operation is running."""
    result = run_helper(
        continuity_repo, "status", Json=True, env={"AI_CONTINUITY_TEST_FAULT": "not-a-real-fault"}
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False


# ---------------------------------------------------------------------------
# Step 4: dirty-state fingerprinting
# ---------------------------------------------------------------------------


def _setup_modified(repo_dir: Path, prefix: str) -> dict:
    rel = f"{prefix}/modified.txt"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / rel).write_text("original\n", encoding="utf-8")
    _run_git(["add", "--", rel], cwd=repo_dir)
    _run_git(["commit", "-m", f"add {rel}"], cwd=repo_dir)
    (repo_dir / rel).write_text("changed\n", encoding="utf-8")
    return {
        "path": rel, "status": " M", "original_path": None,
        "kind": "regular-file", "has_hash": True, "expect_index_entries": True,
    }


def _setup_added(repo_dir: Path, prefix: str) -> dict:
    rel = f"{prefix}/added.txt"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / rel).write_text("new file\n", encoding="utf-8")
    _run_git(["add", "--", rel], cwd=repo_dir)
    return {
        "path": rel, "status": "A ", "original_path": None,
        "kind": "regular-file", "has_hash": True, "expect_index_entries": True,
    }


def _setup_deleted(repo_dir: Path, prefix: str) -> dict:
    rel = f"{prefix}/deleted.txt"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / rel).write_text("will be removed\n", encoding="utf-8")
    _run_git(["add", "--", rel], cwd=repo_dir)
    _run_git(["commit", "-m", f"add {rel}"], cwd=repo_dir)
    (repo_dir / rel).unlink()
    return {
        "path": rel, "status": " D", "original_path": None,
        "kind": "absent", "has_hash": False, "expect_index_entries": True,
    }


def _setup_renamed(repo_dir: Path, prefix: str) -> dict:
    old_rel = f"{prefix}/rename-src.txt"
    new_rel = f"{prefix}/rename-dst.txt"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / old_rel).write_text(
        "rename me\nwith enough content\nto be detected as a rename\n", encoding="utf-8"
    )
    _run_git(["add", "--", old_rel], cwd=repo_dir)
    _run_git(["commit", "-m", f"add {old_rel}"], cwd=repo_dir)
    _run_git(["mv", old_rel, new_rel], cwd=repo_dir)
    return {
        "path": new_rel, "status": "R ", "original_path": old_rel,
        "kind": "regular-file", "has_hash": True, "expect_index_entries": True,
    }


def _setup_untracked(repo_dir: Path, prefix: str) -> dict:
    rel = f"{prefix}/untracked.txt"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / rel).write_text("never added\n", encoding="utf-8")
    return {
        "path": rel, "status": "??", "original_path": None,
        "kind": "regular-file", "has_hash": True, "expect_index_entries": False,
    }


def _setup_executable_mode(repo_dir: Path, prefix: str) -> dict:
    rel = f"{prefix}/exec.sh"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / rel).write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _run_git(["add", "--", rel], cwd=repo_dir)
    _run_git(["commit", "-m", f"add {rel}"], cwd=repo_dir)
    _run_git(["update-index", "--chmod=+x", rel], cwd=repo_dir)
    return {
        "path": rel, "status": "M ", "original_path": None,
        "kind": "regular-file", "has_hash": True, "expect_index_entries": True,
        "expect_mode": "100755",
    }


def _setup_symlink(repo_dir: Path, prefix: str) -> dict:
    rel = f"{prefix}/link-to-outside.txt"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    target = repo_dir.parent / "symlink-target-content.txt"
    target.write_text("target content that must never be hashed\n", encoding="utf-8")
    _create_symlink_or_skip(repo_dir / rel, target, target_is_directory=False)
    return {
        "path": rel, "status": "??", "original_path": None,
        "kind": "symlink", "has_hash": True, "expect_index_entries": False,
        "target": target,
    }


def _setup_junction(repo_dir: Path, prefix: str) -> dict:
    """A previously tracked file whose exact worktree path is replaced by an
    unprivileged directory junction (`New-Item -ItemType Junction`, the same
    approach the Step-1 escape test at `_create_junction` above uses)
    pointing outside the repository. Directory junctions are always
    traversable at the filesystem level -- unlike a file symlink, Git walks
    straight through one when scanning untracked content -- so a purely
    untracked junction never itself appears as a `git status` entry (either
    Git recurses into it, reporting paths beyond it, or, empty, reports
    nothing at all). Replacing an already-tracked path with a junction is
    the one scenario where Git reports the junction path itself: the
    tracked blob is gone (status ' D', exactly like `_setup_deleted`), yet
    the path still resolves to a real filesystem object -- the reparse
    point -- so `Get-DirtyFingerprint` must take the junction branch (kind
    `junction`, a non-null hash), never the `absent` branch."""
    rel = f"{prefix}/tracked-becomes-junction"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / rel).write_text("tracked content before the junction replaces it\n", encoding="utf-8")
    _run_git(["add", "--", rel], cwd=repo_dir)
    _run_git(["commit", "-m", f"add {rel}"], cwd=repo_dir)
    (repo_dir / rel).unlink()
    target_dir = repo_dir.parent / "junction-target-content"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "must-never-be-hashed.txt"
    target_file.write_text("junction target content that must never be hashed\n", encoding="utf-8")
    _create_junction(repo_dir / rel, target_dir)
    return {
        "path": rel, "status": " D", "original_path": None,
        "kind": "junction", "has_hash": True, "expect_index_entries": True,
        "target": target_file,
    }


DIRTY_KIND_SETUPS = {
    "modified": _setup_modified,
    "added": _setup_added,
    "deleted": _setup_deleted,
    "renamed": _setup_renamed,
    "untracked": _setup_untracked,
    "executable-mode": _setup_executable_mode,
    "symlink": _setup_symlink,
    "junction": _setup_junction,
}


@pytest.mark.parametrize("kind", sorted(DIRTY_KIND_SETUPS))
def test_start_captures_out_of_scope_dirty_entry_with_full_fingerprint(continuity_repo, kind):
    """Every out-of-scope dirty-entry kind the design's Local Claim Model
    enumerates is captured in `preexisting_dirty` with its exact status,
    destination path, rename source, filesystem kind, worktree SHA-256 (or
    null for an absent tracked path), and complete index stage/mode/
    object-ID tuples -- and is never altered on disk (test list item 6)."""
    setup = DIRTY_KIND_SETUPS[kind](continuity_repo, "keep")

    result = _start(continuity_repo, Scope=["claim"])

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True

    claim = parsed["claims"][0]
    entries = [entry for entry in claim["preexisting_dirty"] if entry["path"] == setup["path"]]
    assert len(entries) == 1, (
        f"expected exactly one preexisting_dirty entry for {setup['path']!r}, "
        f"got {claim['preexisting_dirty']!r}"
    )
    entry = entries[0]

    assert entry["status"] == setup["status"]
    assert entry["original_path"] == setup["original_path"]
    assert entry["kind"] == setup["kind"]
    if setup["has_hash"]:
        assert entry["worktree_sha256"] is not None
        assert re.fullmatch(r"[0-9a-f]{64}", entry["worktree_sha256"])
    else:
        assert entry["worktree_sha256"] is None

    if setup.get("target"):
        target_hash = hashlib.sha256(setup["target"].read_bytes()).hexdigest()
        assert entry["worktree_sha256"] != target_hash, (
            "the link fingerprint must never hash the link target's content"
        )

    if setup["expect_index_entries"]:
        assert entry["index_entries"], f"expected index entries for kind {kind!r}"
        for index_entry in entry["index_entries"]:
            assert set(index_entry.keys()) == {"stage", "mode", "object_id"}
            assert re.fullmatch(r"[0-9a-f]{40}", index_entry["object_id"])
        if "expect_mode" in setup:
            assert entry["index_entries"][0]["mode"] == setup["expect_mode"]
    else:
        assert entry["index_entries"] == []

    warning_codes = {warning["code"] for warning in parsed["warnings"]}
    assert "preexisting-dirty" in warning_codes

    # The dirty path was reported, never altered.
    if setup["kind"] != "absent":
        assert (continuity_repo / setup["path"]).exists()


def _create_unmerged_conflict(repo_dir: Path, rel_path: str) -> None:
    """Produce a real merge conflict on the currently checked-out branch, so
    the resulting tracked path is genuinely unmerged ('UU') with three real
    index stages (common ancestor, ours, theirs) -- not a synthetic
    approximation."""
    current_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir).stdout.strip()
    (repo_dir / rel_path).parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / rel_path).write_text("base\n", encoding="utf-8")
    _run_git(["add", "--", rel_path], cwd=repo_dir)
    commit = _run_git(["commit", "-m", "add conflict base"], cwd=repo_dir)
    assert commit.returncode == 0, commit.stderr

    checkout_side = _run_git(["checkout", "-b", "conflict-side"], cwd=repo_dir)
    assert checkout_side.returncode == 0, checkout_side.stderr
    (repo_dir / rel_path).write_text("their change\n", encoding="utf-8")
    commit_side = _run_git(["commit", "-am", "their change"], cwd=repo_dir)
    assert commit_side.returncode == 0, commit_side.stderr

    checkout_back = _run_git(["checkout", current_branch], cwd=repo_dir)
    assert checkout_back.returncode == 0, checkout_back.stderr
    (repo_dir / rel_path).write_text("my change\n", encoding="utf-8")
    commit_mine = _run_git(["commit", "-am", "my change"], cwd=repo_dir)
    assert commit_mine.returncode == 0, commit_mine.stderr

    merge_result = _run_git(["merge", "conflict-side"], cwd=repo_dir)
    assert merge_result.returncode != 0, "expected the merge to conflict"
    assert (
        _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir).stdout.strip()
        == current_branch
    )


def test_start_captures_out_of_scope_unmerged_entry_with_all_index_stages(continuity_repo):
    """An unmerged (conflicted) out-of-scope path is captured with Git's
    'UU' status and all three index stages -- common ancestor, ours, and
    theirs -- never just one."""
    _create_unmerged_conflict(continuity_repo, "keep/conflict.txt")

    result = _start(continuity_repo, Scope=["claim"])

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True

    claim = parsed["claims"][0]
    entries = [e for e in claim["preexisting_dirty"] if e["path"] == "keep/conflict.txt"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "UU"
    assert entry["kind"] == "regular-file"
    assert entry["worktree_sha256"] is not None
    stages = sorted(index_entry["stage"] for index_entry in entry["index_entries"])
    assert stages == [1, 2, 3]


_FORCE_CULTURE_WRAPPER_SCRIPT = textwrap.dedent(
    """
    $ErrorActionPreference = 'Stop'
    [System.Threading.Thread]::CurrentThread.CurrentCulture =
        [System.Globalization.CultureInfo]::GetCultureInfo('da-DK')
    $raw = [Console]::In.ReadToEnd()
    $parameters = if ([string]::IsNullOrEmpty($raw)) { @{} } else { $raw | ConvertFrom-Json -AsHashtable }
    $scriptPath = $parameters['ScriptPath']
    $operation = $parameters['Operation']
    $parameters.Remove('ScriptPath') | Out-Null
    $parameters.Remove('Operation') | Out-Null
    & $scriptPath $operation @parameters
    exit $LASTEXITCODE
    """
).strip()


def _start_with_forced_culture(repo_dir: Path, **parameters) -> HelperResult:
    """Invoke `start` exactly like `run_helper`, except the current thread's
    culture is forced to Danish (`da-DK`) before the helper script ever
    runs -- in the very thread the wrapper's `&` call operator later
    dot-invokes it on, so the override reaches the script. Danish/Norwegian
    collation famously folds a leading `aa` together with `å` at the very
    end of the alphabet, which lets this prove `preexisting_dirty`'s path
    order is genuinely culture-invariant rather than incidentally correct
    on whatever locale this machine's own account happens to use (Task 3
    review fix 3)."""
    pwsh = _find_pwsh()
    payload = {"ScriptPath": str(SCRIPT_PATH), "Operation": "start", **parameters}
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", _FORCE_CULTURE_WRAPPER_SCRIPT],
        cwd=repo_dir,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    return HelperResult(completed.returncode, completed.stdout, completed.stderr)


def test_start_orders_preexisting_dirty_by_path_culture_invariantly(continuity_repo):
    """`preexisting_dirty` is sorted by `path` using a fixed, culture-
    invariant order, matching the InvariantCulture sort `Test-ScopeSetEqual`
    already uses -- not whatever linguistic collation the process's current
    culture happens to select. Forcing the current culture to Danish
    (`da-DK`), which sorts a leading `aa` after `z`, proves the ordering is
    genuinely invariant rather than only correct by accident on this
    machine's own locale (Task 3 review fix 3)."""
    (continuity_repo / "keep").mkdir(parents=True, exist_ok=True)
    for name in ("aa-file.txt", "m-file.txt", "z-file.txt"):
        (continuity_repo / "keep" / name).write_text("dirty\n", encoding="utf-8")

    result = _start_with_forced_culture(
        continuity_repo,
        Agent="codex",
        SessionId="session-1",
        Workstream="continuity-pilot",
        Scope=["claim"],
        Json=True,
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    paths = [
        entry["path"]
        for entry in parsed["claims"][0]["preexisting_dirty"]
        if entry["path"].startswith("keep/")
    ]
    assert paths == ["keep/aa-file.txt", "keep/m-file.txt", "keep/z-file.txt"], (
        "expected a fixed, culture-invariant ascending path order regardless of "
        f"the process's current culture, got {paths!r}"
    )


RFC3339_UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_start_emits_colon_separated_rfc3339_timestamps_under_forced_non_english_culture(
    continuity_repo,
):
    """`started_utc`/`heartbeat_utc`/`lease_until_utc` must be genuine
    colon-separated RFC3339 UTC timestamps regardless of the process's
    current culture. `.ToString("yyyy-MM-ddTHH:mm:ssZ")` treats `:` as the
    current culture's time-separator PLACEHOLDER, not a literal character,
    unless the format call is passed InvariantCulture explicitly; Danish
    (`da-DK`) collation renders that placeholder as `.`, corrupting the
    format into something like `2026-07-19T01.14.34Z` (Task 3 review fix 5)."""
    result = _start_with_forced_culture(
        continuity_repo,
        Agent="codex",
        SessionId="session-1",
        Workstream="continuity-pilot",
        Scope=["claim"],
        Json=True,
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    claim = parsed["claims"][0]
    for field in ("started_utc", "heartbeat_utc", "lease_until_utc"):
        value = claim[field]
        assert RFC3339_UTC_PATTERN.match(value), (
            f"{field} was not a colon-separated RFC3339 UTC timestamp under a forced "
            f"non-English culture: {value!r}"
        )


@pytest.mark.parametrize("kind", ["modified", "untracked", "deleted"])
def test_start_rejects_dirty_path_inside_requested_scope(continuity_repo, kind):
    """A dirty path inside the requested scope blocks `start` with exit `2`
    and creates no claim, regardless of which kind of dirt it is (test list
    item 7)."""
    DIRTY_KIND_SETUPS[kind](continuity_repo, "claim")

    result = _start(continuity_repo, Scope=["claim"])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"]
    assert not _claim_file_for(continuity_repo, "continuity-pilot").exists()


# ---------------------------------------------------------------------------
# Step 10: no prohibited Git mutation
# ---------------------------------------------------------------------------

PROHIBITED_GIT_TOKENS = {
    "add", "commit", "checkout", "switch", "branch",
    "stash", "reset", "clean", "merge", "rebase", "push", "worktree",
}
INVOKE_GIT_CALL_PATTERN = re.compile(r"Invoke-Git\s+-Arguments\s+@\(([^)]*)\)")
QUOTED_TOKEN_PATTERN = re.compile(r"'([^'\\]*)'")


def test_helper_never_invokes_prohibited_git_mutation_commands():
    """Every `Invoke-Git` call site in the helper passes only read-only Git
    subcommands. The continuity design forbids the helper from ever running
    a Git command that stages, commits, moves branches/worktrees, or
    otherwise mutates repository state (design spec, Design Principles: "No
    hidden mutation"; test list item 12). `PROHIBITED_GIT_TOKENS` above is
    the COMPLETE set the design and implementation plan name: `add`,
    `commit`, `checkout`, `switch`, `branch`, `worktree` (covering both
    `worktree add` and `worktree remove`), `stash`, `reset`, `clean`,
    `merge`, `rebase`, and `push` (Task 8 brief, Step 2: "all prohibited Git
    mutation commands")."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    # A literal `'git'` command name may appear ONLY once in the whole file:
    # the one place `Invoke-Git` itself sets the child process's file name.
    # If a second literal `'git'` (or `"git"`) ever appeared anywhere else,
    # it would be a second, unfiltered way to invoke Git that entirely
    # bypasses the `Invoke-Git` argument scan below, silently defeating this
    # whole test.
    literal_git_command_occurrences = len(re.findall(r"""(['"])git\1""", source))
    assert literal_git_command_occurrences == 1, (
        f"expected the literal 'git'/\"git\" command name to appear exactly once "
        f"(Invoke-Git's own FileName assignment), found "
        f"{literal_git_command_occurrences} occurrence(s); a second occurrence "
        f"could invoke Git outside Invoke-Git's argument filtering"
    )

    calls = INVOKE_GIT_CALL_PATTERN.findall(source)
    assert calls, "expected at least one Invoke-Git call site to scan"

    # The call-site regex stops at the FIRST unmatched `)`, so a call site
    # whose arguments happen to contain a nested parenthesized expression
    # would be silently truncated (and could hide a prohibited token past
    # the truncation point) without this independently-counted cross-check.
    plain_call_site_count = source.count("Invoke-Git -Arguments")
    assert len(calls) == plain_call_site_count, (
        f"the call-site pattern extracted {len(calls)} call site(s) but a plain "
        f"substring count found {plain_call_site_count}; a call site with nested "
        f"parentheses in its arguments could be evading this check"
    )

    for call_arguments in calls:
        tokens = QUOTED_TOKEN_PATTERN.findall(call_arguments)
        prohibited_hits = PROHIBITED_GIT_TOKENS.intersection(tokens)
        assert not prohibited_hits, (
            f"Invoke-Git call arguments {call_arguments!r} include prohibited "
            f"mutating token(s): {prohibited_hits}"
        )


# ---------------------------------------------------------------------------
# Task 4: durable milestone updates and verification validation
# ---------------------------------------------------------------------------


def _full_workstream_document(
    workstream_id: str,
    head_commit: str,
    state: str = "active",
    last_milestone: str = "none yet",
    next_action: str = "Do the first thing.",
) -> str:
    """A fully-shaped owned workstream document containing every field and
    marker `Read-WorkstreamDocument` requires, wrapped in boilerplate prose
    that `update` must never change (Task 4 brief, Steps 1 and 3)."""
    return (
        f"# Workstream: {workstream_id}\n\n"
        "## Overview\n\n"
        "This paragraph is untouched boilerplate prose that `update` must "
        "never change.\n\n"
        f"- **workstream_id:** `{workstream_id}`\n"
        "- **Objective:** Exercise the `update` operation in tests.\n"
        f"- **State:** {state}\n"
        f"- **Branch:** codex/{workstream_id}\n"
        f"- **Head commit:** `{head_commit}`\n"
        f"- **Last milestone:** {last_milestone}\n\n"
        "## Next action\n\n"
        f"{next_action}\n\n"
        f"{MARKER_START}\n{MARKER_END}\n"
    )


class UpdateFixture(NamedTuple):
    repo_dir: Path
    claim_id: str
    head: str
    workstream_path: Path


@pytest.fixture()
def update_repo(continuity_repo: Path) -> UpdateFixture:
    """A disposable repository with a fully-shaped, committed owned
    workstream document and one active `continuity-pilot` claim scoped to
    `.ai`, ready for `update` tests (Task 4 brief, Steps 1-3). The
    document's own `Head commit` field is a placeholder distinct from the
    repository's real `HEAD` (`update` always overwrites it on success and
    never requires it to already match)."""
    workstream_path = continuity_repo / ".ai" / "workstreams" / "continuity-pilot.md"
    workstream_path.write_text(
        _full_workstream_document("continuity-pilot", "0" * 40), encoding="utf-8"
    )
    add_result = _run_git(["add", "."], cwd=continuity_repo)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(
        ["commit", "-m", "Expand workstream document for update tests"], cwd=continuity_repo
    )
    assert commit_result.returncode == 0, commit_result.stderr
    head = _run_git(["rev-parse", "HEAD"], cwd=continuity_repo).stdout.strip()

    started = _start(continuity_repo, Scope=[".ai"])
    assert started.returncode == 0, f"stdout={started.stdout!r} stderr={started.stderr!r}"
    claim_id = parse_json_stdout(started)["claim_id"]

    return UpdateFixture(repo_dir=continuity_repo, claim_id=claim_id, head=head, workstream_path=workstream_path)


def _update(repo_dir: Path, claim_id: str, **overrides) -> HelperResult:
    """Call `update` with reasonable defaults for the fields under test to
    override individually."""
    parameters = {
        "ClaimId": claim_id,
        "Agent": "codex",
        "SessionId": "session-1",
        "Summary": "Did a thing.",
        "VerificationResult": "not-run",
        "NotRunReason": "not run yet",
        "Json": True,
    }
    parameters.update(overrides)
    return run_helper(repo_dir, "update", **parameters)


# ---------------------------------------------------------------------------
# Step 1: identity, branch, and document-shape validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["ClaimId", "Agent", "SessionId", "Summary"])
def test_update_rejects_missing_required_parameter(update_repo, missing):
    """Each required `update` parameter is validated before any filesystem
    mutation; omitting one returns the stable exit `2`."""
    result = _update(update_repo.repo_dir, update_repo.claim_id, **{missing: ""})

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False


def test_update_rejects_unknown_claim_id(update_repo):
    """A claim ID matching no live claim is rejected without renewing the
    real claim or changing the workstream file."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )

    result = _update(update_repo.repo_dir, "00000000-0000-4000-8000-000000000000")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_wrong_agent(update_repo):
    """A different, otherwise-valid agent than the claim's own recorded
    agent is an identity mismatch: exit `2`, no lease renewal, no Markdown
    change (design spec, "Claim ownership identity")."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )

    result = _update(update_repo.repo_dir, update_repo.claim_id, Agent="claude")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_wrong_session(update_repo):
    """A different session ID than the claim's own recorded session is an
    identity mismatch: exit `2`, no lease renewal, no Markdown change."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )

    result = _update(update_repo.repo_dir, update_repo.claim_id, SessionId="session-2")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_wrong_branch(update_repo):
    """Calling `update` from a branch that does not match the claim's own
    workstream is rejected: exit `2`, no lease renewal, no Markdown
    change."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    _checkout_new_branch(update_repo.repo_dir, "codex/other-workstream")

    result = _update(update_repo.repo_dir, update_repo.claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_wrong_canonical_worktree(update_repo):
    """Calling `update` for the claim's own workstream from a different,
    linked worktree -- rather than the exact canonical worktree recorded on
    the claim -- is an identity mismatch: exit `2`, no lease renewal, no
    Markdown change in the original worktree."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    linked_worktree = update_repo.repo_dir.parent / "linked-worktree"
    worktree_result = _run_git(
        ["worktree", "add", "-b", "claude/continuity-pilot", str(linked_worktree)],
        cwd=update_repo.repo_dir,
    )
    assert worktree_result.returncode == 0, worktree_result.stderr

    result = _update(linked_worktree, update_repo.claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_when_claim_scope_excludes_workstream_doc(continuity_repo):
    """Whole-branch review Fix I2: `WORKFLOW.md` requires every write claim
    to include its own `.ai/workstreams/<workstream-id>.md` path in scope,
    but `update` always writes that file. A claim scoped to a disjoint
    prefix that does NOT cover the owned workstream document must be
    rejected at the `update` boundary before any mutation: exit `2`, no
    Markdown change, no lease renewal."""
    workstream_path = continuity_repo / ".ai" / "workstreams" / "continuity-pilot.md"
    workstream_path.write_text(
        _full_workstream_document("continuity-pilot", "0" * 40), encoding="utf-8"
    )
    add_result = _run_git(["add", "."], cwd=continuity_repo)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(
        ["commit", "-m", "Expand workstream document for scope-exclusion test"], cwd=continuity_repo
    )
    assert commit_result.returncode == 0, commit_result.stderr

    started = _start(continuity_repo, Scope=["shared"])
    assert started.returncode == 0, f"stdout={started.stdout!r} stderr={started.stderr!r}"
    claim_id = parse_json_stdout(started)["claim_id"]

    before_doc = workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8")
    )

    result = _update(continuity_repo, claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_when_claim_is_not_active(update_repo):
    """`update` requires the claim to currently be `active`; any other
    recorded state is rejected without mutation."""
    claim_path = _claim_file_for(update_repo.repo_dir, "continuity-pilot")
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["state"] = "handoff-ready"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")

    result = _update(update_repo.repo_dir, update_repo.claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc


def test_update_rejects_missing_managed_marker(update_repo):
    """A workstream file missing the managed milestone marker pair returns
    exit `3` without any mutation (Task 4 brief, Step 1)."""
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    text = _full_workstream_document("continuity-pilot", update_repo.head).replace(
        f"{MARKER_START}\n{MARKER_END}\n", ""
    )
    update_repo.workstream_path.write_text(text, encoding="utf-8")

    result = _update(update_repo.repo_dir, update_repo.claim_id)

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == text
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_duplicate_managed_marker(update_repo):
    """A workstream file with a duplicated managed marker pair returns exit
    `3` without any mutation."""
    text = _full_workstream_document("continuity-pilot", update_repo.head).replace(
        f"{MARKER_END}\n", f"{MARKER_END}\n{MARKER_START}\n{MARKER_END}\n"
    )
    update_repo.workstream_path.write_text(text, encoding="utf-8")

    result = _update(update_repo.repo_dir, update_repo.claim_id)

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == text


MALFORMED_WORKSTREAM_MUTATIONS = {
    "missing-workstream-id": lambda text, head: text.replace(
        "- **workstream_id:** `continuity-pilot`\n", ""
    ),
    "mismatched-workstream-id": lambda text, head: text.replace(
        "- **workstream_id:** `continuity-pilot`\n",
        "- **workstream_id:** `other-workstream`\n",
    ),
    "missing-state": lambda text, head: text.replace("- **State:** active\n", ""),
    "missing-head-commit": lambda text, head: text.replace(f"- **Head commit:** `{head}`\n", ""),
    "malformed-head-commit": lambda text, head: text.replace(f"`{head}`", "`abc123`"),
    "missing-last-milestone": lambda text, head: text.replace("- **Last milestone:** none yet\n", ""),
    "missing-next-action-heading": lambda text, head: text.replace("## Next action\n\n", ""),
    "empty-next-action-body": lambda text, head: text.replace("Do the first thing.\n\n", ""),
}


@pytest.mark.parametrize(
    "mutation_name", sorted(MALFORMED_WORKSTREAM_MUTATIONS), ids=sorted(MALFORMED_WORKSTREAM_MUTATIONS)
)
def test_update_rejects_malformed_workstream_document(update_repo, mutation_name):
    """Every missing or malformed required workstream field returns exit `3`
    without any claim mutation (Task 4 brief, Step 1)."""
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    text = _full_workstream_document("continuity-pilot", update_repo.head)
    mutated = MALFORMED_WORKSTREAM_MUTATIONS[mutation_name](text, update_repo.head)
    assert mutated != text, f"mutation {mutation_name!r} did not change the document"
    update_repo.workstream_path.write_text(mutated, encoding="utf-8")

    result = _update(update_repo.repo_dir, update_repo.claim_id)

    assert result.returncode == 3, (
        f"mutation={mutation_name!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == mutated
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_rejects_state_field_after_next_action_heading(update_repo):
    """`Write-WorkstreamDocument` assumes the `State`/`Head commit`/`Last
    milestone` field lines precede the `## Next action` heading -- its
    "Next action" body rewrite removes every line between that heading and
    the milestone marker, which would silently delete one of these fields
    (and desynchronize the line index recorded for it) if it were relocated
    into that span. `Read-WorkstreamDocument` must reject that ordering
    explicitly, before any mutation, rather than relying on the assumption
    silently (Task 4 review Fix 4)."""
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    text = _full_workstream_document("continuity-pilot", update_repo.head)
    state_line = "- **State:** active\n"
    assert state_line in text
    mutated = text.replace(state_line, "", 1).replace(
        "## Next action\n\n", f"## Next action\n\n{state_line}\n", 1
    )
    assert mutated != text
    update_repo.workstream_path.write_text(mutated, encoding="utf-8")

    result = _update(update_repo.repo_dir, update_repo.claim_id)

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == mutated
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


# ---------------------------------------------------------------------------
# Step 2: verification matrix
# ---------------------------------------------------------------------------


def test_update_rejects_invalid_verification_result_value(update_repo):
    result = _update(
        update_repo.repo_dir, update_repo.claim_id, VerificationResult="maybe", NotRunReason=""
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_passed_requires_command_and_commit(update_repo):
    """`passed`/`failed` verification requires both `-VerificationCommand`
    and a full `HEAD` commit SHA (design spec, verification matrix)."""
    result = _update(
        update_repo.repo_dir, update_repo.claim_id, VerificationResult="passed", NotRunReason=""
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_rejects_stale_commit(update_repo):
    """A `-VerificationCommit` that is not the current `HEAD` is rejected as
    stale evidence, without renewing the claim or changing the workstream
    document."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    stale_commit = "1" * 40
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        NotRunReason="",
        VerificationCommand="pytest -q",
        VerificationCommit=stale_commit,
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_verification_rejects_malformed_commit_sha(update_repo):
    """A `-VerificationCommit` that is not a full 40-character SHA is
    rejected even if it happens to be a prefix of the real `HEAD`."""
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        NotRunReason="",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head[:7],
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_not_run_requires_reason(update_repo):
    """`not-run` verification without `-NotRunReason` is rejected."""
    result = _update(
        update_repo.repo_dir, update_repo.claim_id, VerificationResult="not-run", NotRunReason=""
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_not_run_forbids_command_and_commit(update_repo):
    """`not-run` verification must not also supply command/commit evidence."""
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="not-run",
        NotRunReason="skipped for this test",
        VerificationCommand="pytest -q",
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_passed_forbids_not_run_reason(update_repo):
    """`passed`/`failed` verification must not also supply `-NotRunReason`."""
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="should not be here",
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_rejects_literal_dirty_placeholder(update_repo):
    """The literal `dirty` placeholder is always invalid for
    `-VerificationDirtyPath` (design spec, verification matrix), and the
    rejection leaves the workstream document and claim unmutated."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="",
        VerificationDirtyPath=["dirty"],
        ChangedPath=["dirty"],
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_verification_dirty_path_must_be_currently_dirty(update_repo):
    """A supplied dirty path that is not actually dirty is rejected."""
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="",
        VerificationDirtyPath=[".ai/workstreams/continuity-pilot.md"],
        ChangedPath=[".ai/workstreams/continuity-pilot.md"],
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_dirty_path_must_be_in_scope(update_repo):
    """A currently-dirty path outside the claim's own scope is rejected as
    verification-dirty-path evidence, without renewing the claim or changing
    the workstream document."""
    (update_repo.repo_dir / "outside.txt").write_text("dirty\n", encoding="utf-8")
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="",
        VerificationDirtyPath=["outside.txt"],
        ChangedPath=["outside.txt"],
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_verification_dirty_path_must_be_in_changed_path(update_repo):
    """A currently-dirty, in-scope path omitted from `-ChangedPath` is
    rejected as verification-dirty-path evidence, since it would not then be
    listed in the owned workstream file."""
    update_repo.workstream_path.write_text(
        update_repo.workstream_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="",
        VerificationDirtyPath=[".ai/workstreams/continuity-pilot.md"],
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_verification_passed_with_dirty_path_succeeds(update_repo):
    """`passed` verification with a currently-dirty, in-scope,
    changed-path-covered dirty path succeeds and records it in the
    milestone (design spec, verification matrix)."""
    update_repo.workstream_path.write_text(
        update_repo.workstream_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="passed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="",
        VerificationDirtyPath=[".ai/workstreams/continuity-pilot.md"],
        ChangedPath=[".ai/workstreams/continuity-pilot.md"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is True
    text = update_repo.workstream_path.read_text(encoding="utf-8")
    assert ".ai/workstreams/continuity-pilot.md" in text
    assert "passed" in text


def test_update_verification_failed_requires_command_and_commit(update_repo):
    """`failed` verification requires the same `-VerificationCommand` and
    full `HEAD` commit SHA evidence as `passed` (design spec, verification
    matrix); omitting them is rejected without mutation."""
    before_doc = update_repo.workstream_path.read_text(encoding="utf-8")
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )

    result = _update(
        update_repo.repo_dir, update_repo.claim_id, VerificationResult="failed", NotRunReason=""
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert update_repo.workstream_path.read_text(encoding="utf-8") == before_doc
    after_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert after_claim == before_claim


def test_update_verification_failed_with_command_and_commit_succeeds(update_repo):
    """`failed` verification with valid command/commit evidence succeeds and
    the milestone records the `failed` result (design spec, verification
    matrix)."""
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="failed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="",
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is True
    text = update_repo.workstream_path.read_text(encoding="utf-8")
    assert "Verification: failed" in text
    assert "pytest -q" in text


# ---------------------------------------------------------------------------
# Step 3: mutation boundary, milestone shape, and lease renewal
# ---------------------------------------------------------------------------


def test_update_happy_path_appends_milestone_and_renews_lease(update_repo):
    """A valid `update` call renews the claim's lease and appends exactly
    one milestone entry recording the summary, state, changed paths,
    verification, and next action (Task 4 brief, Steps 3 and 5)."""
    before_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        Summary="Implemented the update operation.",
        ChangedPath=["scripts/ai-handoff.ps1"],
        State="active",
        NextAction="Write more tests.",
        VerificationResult="passed",
        VerificationCommand="pytest -q",
        VerificationCommit=update_repo.head,
        NotRunReason="",
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["operation"] == "update"
    assert parsed["claim_id"] == update_repo.claim_id
    assert len(parsed["claims"]) == 1

    renewed = parsed["claims"][0]
    assert renewed["claim_id"] == before_claim["claim_id"]
    assert renewed["heartbeat_utc"] != before_claim["heartbeat_utc"]
    assert renewed["lease_until_utc"] != before_claim["lease_until_utc"]
    for key in before_claim:
        if key in ("heartbeat_utc", "lease_until_utc"):
            continue
        assert renewed[key] == before_claim[key], key

    on_disk_claim = json.loads(
        _claim_file_for(update_repo.repo_dir, "continuity-pilot").read_text(encoding="utf-8")
    )
    assert on_disk_claim == renewed

    text = update_repo.workstream_path.read_text(encoding="utf-8")
    assert "Implemented the update operation." in text
    assert "scripts/ai-handoff.ps1" in text
    assert "Write more tests." in text
    assert "passed" in text
    assert f"- **Head commit:** `{update_repo.head}`" in text
    assert text.count(MARKER_START) == 1
    assert text.count(MARKER_END) == 1
    assert text.index(MARKER_START) < text.index(MARKER_END)


def test_update_without_changed_path_omits_changed_paths_line(update_repo):
    """Omitting `-ChangedPath` entirely omits the "Changed paths:" line from
    the recorded milestone -- rather than emitting it with an empty list --
    locking down `Invoke-Update`'s current no-`ChangedPath` contract (Task 4
    review Fix 5)."""
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        Summary="Milestone recorded without any changed paths.",
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    text = update_repo.workstream_path.read_text(encoding="utf-8")
    assert "Milestone recorded without any changed paths." in text
    assert "Changed paths:" not in text


def test_update_preserves_bytes_outside_managed_section_and_explicit_fields(update_repo):
    """`update` changes only the managed milestone section plus the
    explicit `Last milestone`, `Head commit`, `State`, and `Next action`
    fields; every other line is preserved exactly (Task 4 brief, Step 3)."""
    before_text = update_repo.workstream_path.read_text(encoding="utf-8")
    before_lines = before_text.splitlines(keepends=True)

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        Summary="Milestone for byte-preservation test.",
        VerificationResult="not-run",
        NotRunReason="not run for this test",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    after_lines = update_repo.workstream_path.read_text(encoding="utf-8").splitlines(keepends=True)

    def is_always_changeable(line: str) -> bool:
        return (
            line.startswith("- **State:**")
            or line.startswith("- **Head commit:**")
            or line.startswith("- **Last milestone:**")
        )

    marker_region = False
    for index, before_line in enumerate(before_lines):
        if before_line.rstrip("\n") == MARKER_START:
            marker_region = True
        if marker_region:
            continue
        if is_always_changeable(before_line):
            continue
        assert after_lines[index] == before_line, (
            f"line {index} changed unexpectedly: {before_line!r} -> {after_lines[index]!r}"
        )


def test_update_preserves_bytes_outside_next_action_span_and_managed_section(update_repo):
    """Supplying `-NextAction` rewrites only the 'Next action' body span
    (plus the always-changeable explicit fields and the managed milestone
    section); every other line -- including the boilerplate before and after
    the 'Next action' section -- is preserved exactly (Task 4 brief, Step 3;
    review Fix 6, which supplies `-NextAction` where the original
    byte-preservation test above never does)."""
    before_lines = update_repo.workstream_path.read_text(encoding="utf-8").splitlines(keepends=True)
    heading_index = next(
        i for i, line in enumerate(before_lines) if line.rstrip("\n") == "## Next action"
    )
    marker_index = next(
        i for i, line in enumerate(before_lines) if line.rstrip("\n") == MARKER_START
    )

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        Summary="Milestone with an updated next action.",
        NextAction="Do the next thing instead.",
        VerificationResult="not-run",
        NotRunReason="not run for this test",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    after_lines = update_repo.workstream_path.read_text(encoding="utf-8").splitlines(keepends=True)

    def is_always_changeable(line: str) -> bool:
        return (
            line.startswith("- **State:**")
            or line.startswith("- **Head commit:**")
            or line.startswith("- **Last milestone:**")
        )

    for index, before_line in enumerate(before_lines):
        if heading_index < index < marker_index:
            continue  # the rewritten "Next action" body span
        if index >= marker_index:
            continue  # the managed milestone section, not checked here
        if is_always_changeable(before_line):
            continue
        assert after_lines[index] == before_line, (
            f"line {index} changed unexpectedly: {before_line!r} -> {after_lines[index]!r}"
        )

    after_text = "".join(after_lines)
    assert "Do the next thing instead." in after_text
    assert "Do the first thing." not in after_text


def test_update_preserves_original_newline_style(update_repo):
    """`update` preserves the owned workstream document's own newline style
    -- including on the lines it rewrites or appends -- rather than always
    emitting the platform default (design spec, Helper Contract)."""
    lf_text = _full_workstream_document("continuity-pilot", "0" * 40)
    assert "\r\n" not in lf_text
    update_repo.workstream_path.write_bytes(lf_text.encode("utf-8"))

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        VerificationResult="not-run",
        NotRunReason="not run for this test",
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    after_bytes = update_repo.workstream_path.read_bytes()
    assert not after_bytes.startswith(b"\xef\xbb\xbf"), "expected UTF-8 without a BOM"
    after_text = after_bytes.decode("utf-8")
    assert "\r\n" not in after_text, (
        "expected every line -- including the new milestone -- to keep the document's "
        "original LF-only style"
    )


def test_update_rejects_invalid_state_value(update_repo):
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        State="merged",
        VerificationResult="not-run",
        NotRunReason="n/a",
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_state_blocked_records_blockers_line(update_repo):
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        Summary="Hit a blocker.",
        State="blocked",
        VerificationResult="not-run",
        NotRunReason="blocked before verification",
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    text = update_repo.workstream_path.read_text(encoding="utf-8")
    assert "- **State:** blocked" in text
    assert "Blockers: Hit a blocker." in text


def test_update_state_handoff_requires_next_action(update_repo):
    """`-State handoff` requires a non-empty `-NextAction` (design spec,
    Helper Contract: "state `handoff` requires a non-empty `-NextAction`")."""
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        State="handoff",
        VerificationResult="not-run",
        NotRunReason="handing off",
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_update_state_handoff_with_next_action_succeeds(update_repo):
    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        State="handoff",
        NextAction="Hand off to the other agent.",
        VerificationResult="not-run",
        NotRunReason="handing off",
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    text = update_repo.workstream_path.read_text(encoding="utf-8")
    assert "- **State:** handoff" in text
    assert "Hand off to the other agent." in text


# ---------------------------------------------------------------------------
# Step 6: claim-rewrite failure after a successful durable milestone write
# ---------------------------------------------------------------------------


def test_update_claim_rewrite_failure_leaves_old_claim_authoritative_and_retry_via_start_renews_it(
    update_repo,
):
    """When the durable workstream write succeeds but the claim lease
    rewrite afterward fails, the milestone remains applied, the existing
    claim stays authoritative, and the reported recovery is an
    identical-scope `start` retry -- never re-running `update`, so the
    milestone can never be duplicated (Task 4 brief, Step 6).

    The claim-rewrite failure is injected via the fixed test-fault hook
    (`update-after-workstream-write`) rather than a real OS file lock on the
    claim file: `update` reads every live claim (including this workstream's
    own) from the same claim file before it ever attempts to rewrite it, so
    an exclusive `FileShare.None` lock on that single path would also block
    the earlier read the test needs to succeed, not just the later write.
    """
    claim_path = _claim_file_for(update_repo.repo_dir, "continuity-pilot")

    result = _update(
        update_repo.repo_dir,
        update_repo.claim_id,
        Summary="Milestone written despite a claim-rewrite failure.",
        VerificationResult="not-run",
        NotRunReason="not run for this test",
        env={"AI_CONTINUITY_TEST_FAULT": "update-after-workstream-write"},
    )

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert "update-after-workstream-write" not in result.stdout
    assert "update-after-workstream-write" not in result.stderr
    assert parsed.get("recovery"), "expected recovery fields naming the authoritative claim"
    recovery = parsed["recovery"]
    assert recovery.get("durable_update_applied") is True
    assert recovery.get("claim_id") == update_repo.claim_id
    assert recovery.get("authoritative_owner") == "existing-claim"
    retry_command = recovery.get("retry_command", "")
    assert "start" in retry_command
    assert "continuity-pilot" in retry_command

    text = update_repo.workstream_path.read_text(encoding="utf-8")
    assert "Milestone written despite a claim-rewrite failure." in text

    on_disk_claim = json.loads(claim_path.read_text(encoding="utf-8"))
    assert on_disk_claim["claim_id"] == update_repo.claim_id

    # `start` unconditionally rejects any dirty path inside the requested
    # scope -- even an identical-identity/identical-scope renewal of the
    # claim that owns it (design spec: "`start`... Reject... any dirty path
    # inside the requested scope"). The durable milestone `update` just wrote
    # is itself an in-scope dirty path, so the recovery retry only succeeds
    # once that milestone is committed, matching how an agent would actually
    # commit in-progress work before renewing a lease.
    add_result = _run_git(["add", "--", ".ai"], cwd=update_repo.repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(
        ["commit", "-m", "Record milestone despite claim-rewrite failure"],
        cwd=update_repo.repo_dir,
    )
    assert commit_result.returncode == 0, commit_result.stderr

    retry = _start(update_repo.repo_dir, Scope=[".ai"])
    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["claim_id"] == update_repo.claim_id
    assert len(retry_parsed["claims"]) == 1


# ---------------------------------------------------------------------------
# Task 5: committed handoffs and exact Git reconciliation
# ---------------------------------------------------------------------------


class HandoffFixture(NamedTuple):
    repo_dir: Path
    claim_id: str
    workstream_path: Path
    scope: list
    next_action: str


def _start_handoff_claim(repo_dir: Path, scope) -> str:
    result = _start(repo_dir, Scope=list(scope))
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    return parse_json_stdout(result)["claim_id"]


def _make_handoff_fixture(repo_dir: Path) -> HandoffFixture:
    """Commit a fully-shaped owned workstream document, then start one
    active `continuity-pilot` claim scoped to both the workstream file
    itself (design spec: "every write claim includes its own
    `.ai/workstreams/<workstream-id>.md` path in scope") and a `shared/`
    working directory (Task 5 brief, Steps 1-2)."""
    workstream_path = repo_dir / ".ai" / "workstreams" / "continuity-pilot.md"
    workstream_path.write_text(
        _full_workstream_document("continuity-pilot", "0" * 40), encoding="utf-8"
    )
    add_result = _run_git(["add", "."], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(
        ["commit", "-m", "Expand workstream document for handoff tests"], cwd=repo_dir
    )
    assert commit_result.returncode == 0, commit_result.stderr

    scope = [".ai/workstreams/continuity-pilot.md", "shared"]
    claim_id = _start_handoff_claim(repo_dir, scope)

    return HandoffFixture(
        repo_dir=repo_dir,
        claim_id=claim_id,
        workstream_path=workstream_path,
        scope=scope,
        next_action="Hand off to the other agent.",
    )


@pytest.fixture()
def handoff_repo(continuity_repo: Path) -> HandoffFixture:
    return _make_handoff_fixture(continuity_repo)


def _handoff(repo_dir: Path, claim_id: str, next_action: str, **overrides) -> HelperResult:
    """Call `handoff` with reasonable defaults for the fields under test to
    override individually."""
    parameters = {
        "ClaimId": claim_id,
        "Agent": "codex",
        "SessionId": "session-1",
        "NextAction": next_action,
        "Json": True,
    }
    parameters.update(overrides)
    return run_helper(repo_dir, "handoff", **parameters)


def _commit_handoff_update(
    fixture: HandoffFixture,
    *,
    changed_paths: Sequence[str],
    add_paths: Optional[Sequence[str]] = None,
    summary: str = "Ready to hand off.",
) -> None:
    """Run the handoff-triggering `update -State handoff` call listing
    `changed_paths`, then stage and commit `add_paths` (defaulting to
    `changed_paths`) together with that milestone -- the exact committed-
    handoff workflow the design's Agent Workflow section describes."""
    result = _update(
        fixture.repo_dir,
        fixture.claim_id,
        Summary=summary,
        ChangedPath=list(changed_paths),
        State="handoff",
        NextAction=fixture.next_action,
        VerificationResult="not-run",
        NotRunReason="not run for this test",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    paths_to_add = list(add_paths) if add_paths is not None else list(changed_paths)
    add_result = _run_git(["add", "--", *paths_to_add], cwd=fixture.repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", summary], cwd=fixture.repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr


def _current_claim(repo_dir: Path, workstream_id: str = "continuity-pilot") -> dict:
    return json.loads(_claim_file_for(repo_dir, workstream_id).read_text(encoding="utf-8"))


def _snapshot_repo_tree(repo_dir: Path) -> dict:
    """Snapshot every worktree file's bytes (excluding `.git`, which is
    where a non-linked-worktree repository's own Git-common claim file
    lives), plus `HEAD`, all refs, and the complete index, so a caller can
    assert the repository is byte-identical before/after an operation that
    must never mutate a tracked file (Task 5 brief, Step 8: "Except for the
    Git-common claim file, the worktree, index, refs, and tracked files must
    be byte-identical")."""
    files = {}
    for path in repo_dir.rglob("*"):
        if ".git" in path.relative_to(repo_dir).parts:
            continue
        if path.is_file():
            files[str(path.relative_to(repo_dir))] = path.read_bytes()
    head = _run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
    show_ref = _run_git(["show-ref"], cwd=repo_dir).stdout
    index = _run_git(["ls-files", "--stage"], cwd=repo_dir).stdout
    return {"files": files, "head": head, "show_ref": show_ref, "index": index}


# ---------------------------------------------------------------------------
# Step 1: committed-state validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["ClaimId", "Agent", "SessionId", "NextAction"])
def test_handoff_rejects_missing_required_parameter(continuity_repo, missing):
    """Each required `handoff` parameter is validated before any lock or
    mutation; omitting one returns the stable exit `2`."""
    parameters = {
        "ClaimId": "00000000-0000-4000-8000-000000000000",
        "Agent": "codex",
        "SessionId": "session-1",
        "NextAction": "Do the next thing.",
    }
    parameters[missing] = ""
    result = run_helper(continuity_repo, "handoff", Json=True, **parameters)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False


def test_handoff_rejects_unknown_claim_id(continuity_repo):
    result = _handoff(continuity_repo, "00000000-0000-4000-8000-000000000000", "Do the next thing.")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_handoff_rejects_wrong_agent(handoff_repo):
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action, Agent="claude")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_rejects_wrong_session(handoff_repo):
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    result = _handoff(
        handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action, SessionId="session-2"
    )

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_rejects_wrong_branch(handoff_repo):
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    _checkout_new_branch(handoff_repo.repo_dir, "codex/other-workstream")

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_handoff_rejects_protected_main_branch(handoff_repo):
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    checkout = _run_git(["checkout", "main"], cwd=handoff_repo.repo_dir)
    assert checkout.returncode == 0, checkout.stderr

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_handoff_rejects_dirty_claimed_scope(handoff_repo):
    """An uncommitted edit inside the claim's own scope -- including to the
    workstream file itself -- blocks handoff with exit `2`; the claim stays
    active (design spec: "Any dirty in-scope path... makes handoff fail
    with exit 2; the claim stays active")."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    handoff_repo.workstream_path.write_text(
        handoff_repo.workstream_path.read_text(encoding="utf-8") + "\nuncommitted edit\n",
        encoding="utf-8",
    )

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_workstream_never_committed_at_head(handoff_repo):
    """Task 5 review Fix B: this locks the intended behavior for the two
    distinct ways the owned workstream file can fail to be "the committed
    state at HEAD". (1) An uncommitted edit to an EXISTING committed version
    is caught earlier, as fresh dirty-in-scope evidence, at exit `2`
    (`test_handoff_rejects_dirty_claimed_scope` above). (2) The file
    genuinely does not exist at `HEAD` at all -- here, deleted and that
    deletion itself committed, so the current tree is clean -- and
    `Read-CommittedWorkstream`'s `git show HEAD:<path>` fails outright before
    any dirty-scope check ever runs. That is malformed/missing durable
    state, not a working-tree validation failure, so it is a deliberate
    stable exit `3` (ContinuityStateException); the claim is left
    unchanged."""
    rm_result = _run_git(
        ["rm", "--", ".ai/workstreams/continuity-pilot.md"], cwd=handoff_repo.repo_dir
    )
    assert rm_result.returncode == 0, rm_result.stderr
    commit_result = _run_git(
        ["commit", "-m", "Remove the owned workstream file entirely"], cwd=handoff_repo.repo_dir
    )
    assert commit_result.returncode == 0, commit_result.stderr

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_rejects_committed_state_not_handoff(handoff_repo):
    """The committed workstream file at `HEAD` must record state `handoff`;
    a well-formed but still-`active` committed document is rejected."""
    result = _update(
        handoff_repo.repo_dir,
        handoff_repo.claim_id,
        Summary="Still working.",
        ChangedPath=[".ai/workstreams/continuity-pilot.md"],
        State="active",
        VerificationResult="not-run",
        NotRunReason="still going",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    add_result = _run_git(["add", "--", ".ai"], cwd=handoff_repo.repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Still active"], cwd=handoff_repo.repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_rejects_next_action_mismatch(handoff_repo):
    """The exact `-NextAction` supplied to `handoff` must match the
    committed workstream's recorded next action byte-for-byte (after
    trimming incidental surrounding whitespace)."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, "A completely different next action.")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_head_commit_field_is_not_self_referential(handoff_repo):
    """The committed workstream document's own `Head commit` field records
    Git state BEFORE the status-file mutation that wrote it, and is
    deliberately "not self-referential" (design spec, "Workstream status"):
    it can never equal the hash of the very commit that carries it, since a
    commit cannot know its own hash in advance. A further, unrelated empty
    commit that advances `HEAD` past that recorded value -- without
    touching any tracked path -- is therefore harmless and does not block
    handoff."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    empty_commit = _run_git(
        ["commit", "--allow-empty", "-m", "advance HEAD without touching any tracked path"],
        cwd=handoff_repo.repo_dir,
    )
    assert empty_commit.returncode == 0, empty_commit.stderr

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["claims"][0]["state"] == "handoff-ready"


def test_handoff_rejects_committed_in_scope_path_omitted_from_changed_paths(handoff_repo):
    """A path committed inside the claim's own scope alongside the
    handoff-triggering `update` call, but omitted from that call's own
    `-ChangedPath` evidence, blocks handoff (design spec: "omitted committed
    in-scope change... makes handoff fail with exit 2")."""
    (handoff_repo.repo_dir / "shared").mkdir(parents=True, exist_ok=True)
    (handoff_repo.repo_dir / "shared" / "extra.txt").write_text("more\n", encoding="utf-8")

    result = _update(
        handoff_repo.repo_dir,
        handoff_repo.claim_id,
        Summary="Omit a changed path.",
        ChangedPath=[".ai/workstreams/continuity-pilot.md"],
        State="handoff",
        NextAction=handoff_repo.next_action,
        VerificationResult="not-run",
        NotRunReason="not run for this test",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    add_result = _run_git(["add", "--", ".ai", "shared"], cwd=handoff_repo.repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Omit a changed path"], cwd=handoff_repo.repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_happy_path_marks_claim_handoff_ready(handoff_repo):
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["operation"] == "handoff"
    assert len(parsed["claims"]) == 1
    claim = parsed["claims"][0]
    assert claim["state"] == "handoff-ready"
    assert claim["durable_status_path"] == ".ai/workstreams/continuity-pilot.md"
    assert re.fullmatch(r"[0-9a-f]{64}", claim["durable_status_sha256"])
    assert claim["durable_status_blob_oid"]
    head = _run_git(["rev-parse", "HEAD"], cwd=handoff_repo.repo_dir).stdout.strip()
    assert claim["handoff_commit"] == head
    assert claim["claim_id"] == handoff_repo.claim_id

    assert _current_claim(handoff_repo.repo_dir) == claim


def test_handoff_never_edits_a_tracked_file_or_git_state(handoff_repo):
    """Except for the Git-common claim file, the worktree, index, refs, and
    tracked files are byte-identical before and after a successful
    `handoff` (Task 5 brief, Step 8)."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    before = _snapshot_repo_tree(handoff_repo.repo_dir)

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    after = _snapshot_repo_tree(handoff_repo.repo_dir)
    assert after == before


# ---------------------------------------------------------------------------
# Step 2: Git reconciliation -- committed and fresh dirty-state paths
# ---------------------------------------------------------------------------


def _setup_copied(repo_dir: Path, prefix: str) -> dict:
    src_rel = f"{prefix}/copy-src.txt"
    dst_rel = f"{prefix}/copy-dst.txt"
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    (repo_dir / src_rel).write_text(
        "copy me\nwith enough content\nto be detected as a copy\n", encoding="utf-8"
    )
    _run_git(["add", "--", src_rel], cwd=repo_dir)
    _run_git(["commit", "-m", f"add {src_rel}"], cwd=repo_dir)
    shutil.copyfile(repo_dir / src_rel, repo_dir / dst_rel)
    _run_git(["add", "--", dst_rel], cwd=repo_dir)
    return {"path": dst_rel, "original_path": src_rel}


def _setup_committed_modified(repo_dir: Path, prefix: str) -> dict:
    setup = _setup_modified(repo_dir, prefix)
    return {"paths": [setup["path"]]}


def _setup_committed_added(repo_dir: Path, prefix: str) -> dict:
    setup = _setup_added(repo_dir, prefix)
    return {"paths": [setup["path"]]}


def _setup_committed_deleted(repo_dir: Path, prefix: str) -> dict:
    setup = _setup_deleted(repo_dir, prefix)
    return {"paths": [setup["path"]]}


def _setup_committed_renamed(repo_dir: Path, prefix: str) -> dict:
    setup = _setup_renamed(repo_dir, prefix)
    return {"paths": [setup["original_path"], setup["path"]]}


def _setup_committed_copied(repo_dir: Path, prefix: str) -> dict:
    setup = _setup_copied(repo_dir, prefix)
    return {"paths": [setup["original_path"], setup["path"]]}


COMMITTED_KIND_SETUPS = {
    "modified": _setup_committed_modified,
    "added": _setup_committed_added,
    "deleted": _setup_committed_deleted,
    "renamed": _setup_committed_renamed,
    "copied": _setup_committed_copied,
}


@pytest.mark.parametrize("kind", sorted(COMMITTED_KIND_SETUPS))
def test_handoff_accepts_committed_in_scope_change_of_every_kind(handoff_repo, kind):
    """Every committed-diff kind the design's reconciliation rule names --
    modified, added, deleted, renamed, and copied -- succeeds when properly
    covered by the handoff-triggering `update` call's `-ChangedPath`
    evidence, with both sides of a rename/copy listed and in scope (test
    list item 16)."""
    setup = COMMITTED_KIND_SETUPS[kind](handoff_repo.repo_dir, "shared")
    changed_paths = [".ai/workstreams/continuity-pilot.md", *setup["paths"]]

    _commit_handoff_update(handoff_repo, changed_paths=changed_paths, add_paths=[".ai", "shared"])

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 0, f"kind={kind!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["claims"][0]["state"] == "handoff-ready"


def test_handoff_accepts_paths_recorded_across_multiple_milestones(handoff_repo):
    """Task 5 review Fix A: an agent following the design's normal milestone
    cadence may call `update` more than once before `handoff` -- e.g. an
    earlier milestone recording a changed path for its own commit, followed
    by the handoff-triggering `update -State handoff` call recording only
    the workstream file itself. `Test-HandoffCoverage` validates the ENTIRE
    committed `base_commit..HEAD` diff, so the changed-path coverage must be
    the UNION across ALL committed milestone bullets, not just the most
    recently appended one, or the earlier milestone's own committed path is
    wrongly rejected as "not listed"."""
    (handoff_repo.repo_dir / "shared").mkdir(parents=True, exist_ok=True)
    (handoff_repo.repo_dir / "shared" / "path-a.txt").write_text(
        "first milestone content\n", encoding="utf-8"
    )

    first_update = _update(
        handoff_repo.repo_dir,
        handoff_repo.claim_id,
        Summary="First milestone.",
        ChangedPath=["shared/path-a.txt"],
        VerificationResult="not-run",
        NotRunReason="not run for this test",
    )
    assert first_update.returncode == 0, f"stdout={first_update.stdout!r} stderr={first_update.stderr!r}"
    add_result = _run_git(["add", "--", ".ai", "shared"], cwd=handoff_repo.repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "First milestone"], cwd=handoff_repo.repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["claims"][0]["state"] == "handoff-ready"


def test_handoff_rejects_committed_out_of_scope_change(handoff_repo):
    """A change committed OUTSIDE the claim's own scope -- even when listed
    in `-ChangedPath` -- is a violation: a claim may only commit within its
    claimed scope (design spec: "every committed path must be in scope")."""
    outside_dir = handoff_repo.repo_dir / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "extra.txt").write_text("should not be touched\n", encoding="utf-8")

    result = _update(
        handoff_repo.repo_dir,
        handoff_repo.claim_id,
        Summary="Accidentally touch something outside scope.",
        ChangedPath=[".ai/workstreams/continuity-pilot.md", "outside/extra.txt"],
        State="handoff",
        NextAction=handoff_repo.next_action,
        VerificationResult="not-run",
        NotRunReason="not run for this test",
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    add_result = _run_git(["add", "--", ".ai", "outside"], cwd=handoff_repo.repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Touch outside scope"], cwd=handoff_repo.repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_handoff_rejects_new_out_of_scope_untracked_path(handoff_repo):
    """Untracked dirt discovered at handoff time, outside the claim's own
    scope, that was never captured as pre-existing at `start` is a new
    out-of-scope change and blocks handoff without being altered (design
    spec: "A new out-of-scope path discovered at handoff is a conflict and
    fails with exit 2"; test list item 21)."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    (handoff_repo.repo_dir / "outside.txt").write_text("new dirt not captured at start\n", encoding="utf-8")

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"
    assert (handoff_repo.repo_dir / "outside.txt").exists()


def test_handoff_allows_unchanged_preexisting_out_of_scope_dirt(tmp_path):
    """Pre-existing out-of-scope dirt captured at `start` remains untouched
    and does not block a later committed `handoff` (test list item 21)."""
    repo_dir = _make_repo_on_branch(tmp_path, "codex/continuity-pilot")
    workstream_path = repo_dir / ".ai" / "workstreams" / "continuity-pilot.md"
    workstream_path.write_text(_full_workstream_document("continuity-pilot", "0" * 40), encoding="utf-8")
    _run_git(["add", "."], cwd=repo_dir)
    commit_result = _run_git(["commit", "-m", "Expand workstream document"], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    (repo_dir / "keep").mkdir(parents=True, exist_ok=True)
    (repo_dir / "keep" / "untouched.txt").write_text("leave me alone\n", encoding="utf-8")

    scope = [".ai/workstreams/continuity-pilot.md", "shared"]
    claim_id = _start_handoff_claim(repo_dir, scope)
    fixture = HandoffFixture(
        repo_dir=repo_dir, claim_id=claim_id, workstream_path=workstream_path,
        scope=scope, next_action="Hand off to the other agent.",
    )
    _commit_handoff_update(fixture, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    result = _handoff(repo_dir, claim_id, fixture.next_action)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert (repo_dir / "keep" / "untouched.txt").read_text(encoding="utf-8") == "leave me alone\n"


@pytest.mark.parametrize("kind", ["modified", "untracked", "deleted"])
def test_handoff_rejects_dirty_path_inside_claimed_scope_of_every_kind(handoff_repo, kind):
    """A dirty path inside the claim's own scope blocks handoff regardless
    of which kind of dirt it is, mirroring `start`'s equivalent rule (test
    list item 7)."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    DIRTY_KIND_SETUPS[kind](handoff_repo.repo_dir, "shared")

    result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert result.returncode == 2, f"kind={kind!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


# ---------------------------------------------------------------------------
# Step 3: pre-existing dirty fingerprint drift
# ---------------------------------------------------------------------------


def _build_repo_with_preexisting(tmp_path: Path, setup_fn) -> tuple:
    """Build a disposable repository with a committed workstream document,
    run `setup_fn(repo_dir, "keep")` to produce one out-of-scope dirty path
    BEFORE `start` captures it as pre-existing, start the claim, then run
    and commit the handoff-triggering `update` call. Returns
    `(repo_dir, claim_id, fixture, setup)`."""
    repo_dir = _make_repo_on_branch(tmp_path, "codex/continuity-pilot")
    workstream_path = repo_dir / ".ai" / "workstreams" / "continuity-pilot.md"
    workstream_path.write_text(_full_workstream_document("continuity-pilot", "0" * 40), encoding="utf-8")
    _run_git(["add", "."], cwd=repo_dir)
    commit_result = _run_git(["commit", "-m", "Expand workstream document"], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    setup = setup_fn(repo_dir, "keep")

    scope = [".ai/workstreams/continuity-pilot.md", "shared"]
    claim_id = _start_handoff_claim(repo_dir, scope)
    fixture = HandoffFixture(
        repo_dir=repo_dir, claim_id=claim_id, workstream_path=workstream_path,
        scope=scope, next_action="Hand off to the other agent.",
    )
    _commit_handoff_update(fixture, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    return repo_dir, claim_id, fixture, setup


def test_handoff_rejects_preexisting_dirty_status_drift(tmp_path):
    """A pre-existing untracked path that gets STAGED after `start` --
    content, kind, and its own path unchanged -- has its status drift from
    `??` to `A ` and blocks handoff (Task 5 brief, Step 3: "status")."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_untracked)

    add_result = _run_git(["add", "--", setup["path"]], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr

    result = _handoff(repo_dir, claim_id, fixture.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(repo_dir)["state"] == "active"


def test_handoff_rejects_preexisting_dirty_kind_drift(tmp_path):
    """A pre-existing untracked regular file replaced by a symbolic link at
    the SAME path -- status stays `??` throughout -- has its kind drift from
    `regular-file` to `symlink` and blocks handoff (Task 5 brief, Step 3:
    "kind")."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_untracked)

    (repo_dir / setup["path"]).unlink()
    target = repo_dir.parent / "kind-drift-target.txt"
    target.write_text("target\n", encoding="utf-8")
    _create_symlink_or_skip(repo_dir / setup["path"], target, target_is_directory=False)

    result = _handoff(repo_dir, claim_id, fixture.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(repo_dir)["state"] == "active"


def test_handoff_rejects_preexisting_dirty_content_drift(tmp_path):
    """A pre-existing modified-but-unstaged path edited AGAIN after `start`
    -- status, kind, and index entries unchanged -- has its content hash
    drift and blocks handoff (Task 5 brief, Step 3: "content")."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_modified)

    (repo_dir / setup["path"]).write_text("drifted content, never committed\n", encoding="utf-8")

    result = _handoff(repo_dir, claim_id, fixture.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(repo_dir)["state"] == "active"


def test_handoff_rejects_preexisting_dirty_index_object_id_drift(tmp_path):
    """A pre-existing staged-added path whose index entry is repointed at a
    different, already-existing blob object via `git update-index
    --cacheinfo` -- without touching the working-tree bytes
    Get-DirtyFingerprint hashes -- has its index object ID drift and blocks
    handoff (Task 5 brief, Step 3: "index... object ID")."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_added)

    hash_result = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo_dir,
        input="a completely different blob\n",
        capture_output=True,
        text=True,
        check=True,
    )
    new_blob_id = hash_result.stdout.strip()
    cacheinfo = f"100644,{new_blob_id},{setup['path']}"
    update_result = _run_git(["update-index", "--cacheinfo", cacheinfo], cwd=repo_dir)
    assert update_result.returncode == 0, update_result.stderr

    result = _handoff(repo_dir, claim_id, fixture.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(repo_dir)["state"] == "active"


def test_handoff_rejects_preexisting_dirty_index_mode_drift(tmp_path):
    """A pre-existing staged-added path whose index MODE flips from
    `100644` to `100755` while its object ID is left exactly as recorded --
    via `git update-index --cacheinfo` reusing the same blob -- blocks
    handoff (Task 5 brief, Step 3: "index mode")."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_added)

    ls_result = _run_git(["ls-files", "--stage", "--", setup["path"]], cwd=repo_dir)
    assert ls_result.returncode == 0, ls_result.stderr
    existing_object_id = ls_result.stdout.split()[1]
    cacheinfo = f"100755,{existing_object_id},{setup['path']}"
    update_result = _run_git(["update-index", "--cacheinfo", cacheinfo], cwd=repo_dir)
    assert update_result.returncode == 0, update_result.stderr

    result = _handoff(repo_dir, claim_id, fixture.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(repo_dir)["state"] == "active"


# NOTE: an "index stage count" drift test analogous to the object-ID/mode
# drift tests above -- a pre-existing unmerged conflict (three index
# stages) that collapses to one ordinary stage-0 entry after `start` -- is
# deliberately NOT included as a full committed-handoff scenario. Git
# itself refuses `git commit`, with or without a limiting pathspec, while
# ANY path in the index is unmerged ("cannot do a partial commit during a
# merge" / "Committing is not possible because you have unmerged files"),
# so the handoff-triggering `update`+commit step this fixture's flow
# requires can never succeed while the captured conflict remains
# unresolved -- and once it is resolved, it is no longer the same
# multi-stage entry `start` captured. The general index-entry-count and
# per-stage mode/object-ID comparison in Test-PreexistingDirtyUnchanged is
# still exercised directly by the object-ID and mode drift tests above.


def _setup_rename_source_pair(repo_dir: Path, prefix: str) -> dict:
    """Two committed files with identical content, so a later rename can be
    detected from either one, letting a test swap which committed path is
    reported as `original_path` for the SAME destination path (Task 5
    brief, Step 3: "rename source")."""
    (repo_dir / prefix).mkdir(parents=True, exist_ok=True)
    content = "identical content for rename-source ambiguity\nline two\nline three\n"
    src_a = f"{prefix}/rename-src-a.txt"
    src_b = f"{prefix}/rename-src-b.txt"
    (repo_dir / src_a).write_text(content, encoding="utf-8")
    (repo_dir / src_b).write_text(content, encoding="utf-8")
    _run_git(["add", "--", src_a, src_b], cwd=repo_dir)
    _run_git(["commit", "-m", "add rename source pair"], cwd=repo_dir)
    return {"src_a": src_a, "src_b": src_b, "content": content}


def test_handoff_rejects_preexisting_dirty_rename_source_drift(tmp_path):
    repo_dir = _make_repo_on_branch(tmp_path, "codex/continuity-pilot")
    workstream_path = repo_dir / ".ai" / "workstreams" / "continuity-pilot.md"
    workstream_path.write_text(_full_workstream_document("continuity-pilot", "0" * 40), encoding="utf-8")
    _run_git(["add", "."], cwd=repo_dir)
    commit_result = _run_git(["commit", "-m", "Expand workstream document"], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    pair = _setup_rename_source_pair(repo_dir, "keep")
    dst = "keep/rename-dst.txt"
    rm_result = _run_git(["rm", "--", pair["src_a"]], cwd=repo_dir)
    assert rm_result.returncode == 0, rm_result.stderr
    (repo_dir / dst).write_text(pair["content"], encoding="utf-8")
    add_result = _run_git(["add", "--", dst], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr

    scope = [".ai/workstreams/continuity-pilot.md", "shared"]
    claim_id = _start_handoff_claim(repo_dir, scope)
    started_claim = _current_claim(repo_dir)
    captured = [e for e in started_claim["preexisting_dirty"] if e["path"] == dst]
    assert len(captured) == 1, started_claim["preexisting_dirty"]
    assert captured[0]["original_path"] == pair["src_a"], captured[0]

    fixture = HandoffFixture(
        repo_dir=repo_dir, claim_id=claim_id, workstream_path=workstream_path,
        scope=scope, next_action="Hand off to the other agent.",
    )
    _commit_handoff_update(fixture, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    # Drift: restore src_a (making it clean again) and instead delete
    # src_b, so the SAME destination path is now detected as renamed from a
    # DIFFERENT committed source -- changing only `original_path`.
    (repo_dir / pair["src_a"]).write_text(pair["content"], encoding="utf-8")
    restore_result = _run_git(["add", "--", pair["src_a"]], cwd=repo_dir)
    assert restore_result.returncode == 0, restore_result.stderr
    rm_b_result = _run_git(["rm", "--", pair["src_b"]], cwd=repo_dir)
    assert rm_b_result.returncode == 0, rm_b_result.stderr

    result = _handoff(repo_dir, claim_id, fixture.next_action)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(repo_dir)["state"] == "active"


# ---------------------------------------------------------------------------
# Step 4: fault-boundary and idempotent-retry behavior
# ---------------------------------------------------------------------------


def test_handoff_after_validate_fault_preserves_active_claim_and_retry_succeeds(handoff_repo):
    """At `handoff-after-validate` (immediately before the atomic claim
    replace), no mutation has occurred: the claim stays `active`, and an
    idempotent retry of the exact same call succeeds (Task 5 brief, Step
    4)."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    faulted = _handoff(
        handoff_repo.repo_dir,
        handoff_repo.claim_id,
        handoff_repo.next_action,
        env={"AI_CONTINUITY_TEST_FAULT": "handoff-after-validate"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "handoff-after-validate" not in faulted.stdout
    assert "handoff-after-validate" not in faulted.stderr

    claim = _current_claim(handoff_repo.repo_dir)
    assert claim["state"] == "active"
    assert claim["durable_status_path"] is None
    assert claim["handoff_commit"] is None

    retry = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["state"] == "handoff-ready"


def test_handoff_after_claim_rewrite_fault_leaves_handoff_ready_authoritative_and_retry_reports_it(
    handoff_repo,
):
    """At `handoff-after-claim-rewrite` (immediately after the atomic claim
    replace completes), the claim IS already `handoff-ready` and
    authoritative: it blocks a new `start`, and retrying the exact same
    `handoff` call reports it without creating another claim (Task 5 brief,
    Step 4)."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])

    faulted = _handoff(
        handoff_repo.repo_dir,
        handoff_repo.claim_id,
        handoff_repo.next_action,
        env={"AI_CONTINUITY_TEST_FAULT": "handoff-after-claim-rewrite"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "handoff-after-claim-rewrite" not in faulted.stdout
    assert "handoff-after-claim-rewrite" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative handoff-ready claim"
    assert recovery.get("claim_id") == handoff_repo.claim_id
    assert recovery.get("authoritative_owner") == "handoff-ready-claim"

    claim = _current_claim(handoff_repo.repo_dir)
    assert claim["state"] == "handoff-ready"

    blocked_start = _start(handoff_repo.repo_dir, Scope=handoff_repo.scope)
    assert blocked_start.returncode == 2, f"stdout={blocked_start.stdout!r} stderr={blocked_start.stderr!r}"

    retry = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert len(retry_parsed["claims"]) == 1
    assert retry_parsed["claims"][0]["claim_id"] == handoff_repo.claim_id
    assert retry_parsed["claims"][0]["state"] == "handoff-ready"


def test_handoff_ready_retry_warns_on_next_action_mismatch(handoff_repo):
    """Task 5 review Fix C: an idempotent `handoff` retry against an
    already-`handoff-ready` claim still returns `ok: true` and makes no new
    mutation even when the resupplied `-NextAction` differs from the one
    recorded when the claim became handoff-ready -- but that mismatch is now
    surfaced as a warning rather than silently accepted."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    first = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    retry = _handoff(
        handoff_repo.repo_dir, handoff_repo.claim_id, "A completely different next action."
    )

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["state"] == "handoff-ready"
    warning_codes = {warning["code"] for warning in retry_parsed["warnings"]}
    assert "already-handoff-ready" in warning_codes
    assert "next-action-mismatch" in warning_codes
    assert _current_claim(handoff_repo.repo_dir)["state"] == "handoff-ready"


def test_handoff_ready_retry_no_warning_on_exact_next_action_match(handoff_repo):
    """The same idempotent retry with the EXACT SAME `-NextAction` as the one
    recorded when the claim became handoff-ready reports only the
    already-handoff-ready warning, never the mismatch warning (Task 5 review
    Fix C)."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    first = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    retry = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    warning_codes = {warning["code"] for warning in retry_parsed["warnings"]}
    assert "already-handoff-ready" in warning_codes
    assert "next-action-mismatch" not in warning_codes


# ---------------------------------------------------------------------------
# Task 6: acceptance transactions and successor-owned recovery
# ---------------------------------------------------------------------------


class AcceptFixture(NamedTuple):
    repo_dir: Path
    workstream_id: str
    predecessor_claim_id: str
    scope: list
    journal_path: Path


def _journal_path_for(repo_dir: Path, previous_claim_id: str) -> Path:
    return (
        _git_common_dir_for(repo_dir)
        / "ai-continuity"
        / "transactions"
        / f"accept-{previous_claim_id}.json"
    )


def _history_path_for(repo_dir: Path, claim_id: str) -> Path:
    return _git_common_dir_for(repo_dir) / "ai-continuity" / "history" / f"{claim_id}.json"


def _make_accept_fixture(fixture: HandoffFixture) -> AcceptFixture:
    """Drive a `HandoffFixture` all the way to `handoff-ready` -- committing
    the handoff-triggering milestone and calling `handoff` -- so `accept`
    tests start from a genuinely accept-eligible predecessor claim."""
    _commit_handoff_update(fixture, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    handoff_result = _handoff(fixture.repo_dir, fixture.claim_id, fixture.next_action)
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"

    return AcceptFixture(
        repo_dir=fixture.repo_dir,
        workstream_id="continuity-pilot",
        predecessor_claim_id=fixture.claim_id,
        scope=fixture.scope,
        journal_path=_journal_path_for(fixture.repo_dir, fixture.claim_id),
    )


@pytest.fixture()
def accept_repo(handoff_repo: HandoffFixture) -> AcceptFixture:
    return _make_accept_fixture(handoff_repo)


def _accept(repo_dir: Path, previous_claim_id: str, **overrides) -> HelperResult:
    """Call `accept` with reasonable defaults for the fields under test to
    override individually. Defaults to a DIFFERENT agent/session than the
    predecessor's own `codex`/`session-1` (handoff_repo's defaults), matching
    a genuine alternating handoff."""
    parameters = {
        "PreviousClaimId": previous_claim_id,
        "Agent": "claude",
        "SessionId": "claude-session-1",
        "Json": True,
    }
    parameters.update(overrides)
    return run_helper(repo_dir, "accept", **parameters)


# ---------------------------------------------------------------------------
# Step 1: acceptance validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["PreviousClaimId", "Agent", "SessionId"])
def test_accept_rejects_missing_required_parameter(accept_repo, missing):
    parameters = {
        "PreviousClaimId": accept_repo.predecessor_claim_id,
        "Agent": "claude",
        "SessionId": "claude-session-1",
    }
    parameters[missing] = ""
    result = run_helper(accept_repo.repo_dir, "accept", Json=True, **parameters)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_accept_rejects_unknown_claim_id(continuity_repo):
    result = _accept(continuity_repo, "00000000-0000-4000-8000-000000000000")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_accept_rejects_malformed_previous_claim_id(continuity_repo):
    """`-PreviousClaimId` is spliced directly into the transaction journal's
    filename, so it is validated as a GUID before any lock or mutation,
    closing off path-injection-shaped input (Task 6 brief, Step 1: "exact
    predecessor ID")."""
    result = _accept(continuity_repo, "../../etc/passwd")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_accept_rejects_predecessor_not_handoff_ready(handoff_repo):
    """A still-`active` claim (never handed off) is rejected; accept requires
    the exact `handoff-ready` state."""
    result = _accept(handoff_repo.repo_dir, handoff_repo.claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(handoff_repo.repo_dir)["state"] == "active"


def test_accept_rejects_different_branch(accept_repo):
    """`accept` requires the same canonical worktree and branch recorded on
    the handoff-ready predecessor claim."""
    _checkout_new_branch(accept_repo.repo_dir, "codex/other-workstream")

    result = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_accept_rejects_branch_differing_from_predecessor_recorded_branch(accept_repo):
    """`Test-AcceptPreActivationInvariants`'s OWN branch comparison rejects
    acceptance even when the coarser `Assert-WorkstreamBranchAllowed` naming
    check would allow the current branch: the predecessor was recorded on
    `codex/continuity-pilot`, and `accept` is invoked from
    `claude/continuity-pilot` -- a DIFFERENT branch, but still a valid
    `<codex|claude>/<workstream-id>` prefix for this exact workstream id, so
    the naming check alone would pass it through (Task 6 review Fix 3)."""
    predecessor = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert predecessor["branch"] == "codex/continuity-pilot"

    _checkout_new_branch(accept_repo.repo_dir, "claude/continuity-pilot")

    result = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False

    claim = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert claim["claim_id"] == accept_repo.predecessor_claim_id
    assert claim["state"] == "handoff-ready"


def test_accept_rejects_head_drift_since_handoff(accept_repo):
    """Unlike `handoff` (whose committed `Head commit` field is deliberately
    "not self-referential"), `accept` requires the CURRENT `HEAD` to exactly
    equal the recorded `handoff_commit`: an unrelated empty commit that
    advances `HEAD` after handoff-ready blocks acceptance, and the
    predecessor stays handoff-ready and authoritative (Task 6 brief, Step 1:
    "unchanged HEAD")."""
    empty_commit = _run_git(
        ["commit", "--allow-empty", "-m", "advance head after handoff-ready"],
        cwd=accept_repo.repo_dir,
    )
    assert empty_commit.returncode == 0, empty_commit.stderr

    result = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    claim = _current_claim(accept_repo.repo_dir)
    assert claim["claim_id"] == accept_repo.predecessor_claim_id
    assert claim["state"] == "handoff-ready"


def test_accept_rejects_tampered_durable_status_hash(accept_repo):
    """The committed workstream blob object ID and SHA-256 recorded at
    handoff must still match; a claim file tampered to record a different
    hash is rejected even though `HEAD` itself has not moved (Task 6 brief,
    Step 1: "committed blob and SHA-256")."""
    claim_path = _claim_file_for(accept_repo.repo_dir, accept_repo.workstream_id)
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["durable_status_sha256"] = "0" * 64
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    result = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_accept_rejects_dirty_claimed_scope(accept_repo):
    workstream_path = (
        accept_repo.repo_dir / ".ai" / "workstreams" / f"{accept_repo.workstream_id}.md"
    )
    workstream_path.write_text(
        workstream_path.read_text(encoding="utf-8") + "\nuncommitted edit\n", encoding="utf-8"
    )

    result = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(accept_repo.repo_dir)["state"] == "handoff-ready"


def test_accept_rejects_preexisting_dirty_content_drift(tmp_path):
    """A pre-existing modified-but-unstaged out-of-scope path edited AGAIN
    after `handoff` blocks `accept` (Task 6 brief, Step 1: "unchanged
    pre-existing dirt"), reusing the exact same
    `Test-PreexistingDirtyUnchanged` gate `handoff` already exercises
    exhaustively."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_modified)
    handoff_result = _handoff(repo_dir, claim_id, fixture.next_action)
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"

    (repo_dir / setup["path"]).write_text("drifted after handoff-ready\n", encoding="utf-8")

    result = _accept(repo_dir, claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(repo_dir)["state"] == "handoff-ready"


def test_accept_succeeds_after_lease_expiry_when_evidence_matches(accept_repo):
    """Acceptance after lease expiry is allowed precisely because every
    recorded Git invariant is revalidated (design spec, Helper Contract:
    "accept"; Task 6 brief, Step 1)."""
    claim_path = _claim_file_for(accept_repo.repo_dir, accept_repo.workstream_id)
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["lease_until_utc"] = "2000-01-01T00:00:00Z"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    result = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["claims"][0]["state"] == "active"


# ---------------------------------------------------------------------------
# Step 2: successor shape and immutable-history cross-references
# ---------------------------------------------------------------------------


def test_accept_happy_path_creates_successor_and_archives_predecessor(accept_repo):
    predecessor = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert predecessor["state"] == "handoff-ready"

    result = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id, Agent="claude", SessionId="claude-session-1"
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["operation"] == "accept"
    assert len(parsed["claims"]) == 1
    successor = parsed["claims"][0]

    assert re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", successor["claim_id"]
    )
    assert successor["claim_id"] != predecessor["claim_id"]
    assert successor["workstream_id"] == predecessor["workstream_id"]
    assert successor["worktree_path"] == predecessor["worktree_path"]
    assert successor["branch"] == predecessor["branch"]
    assert sorted(successor["scope_paths"]) == sorted(predecessor["scope_paths"])
    assert successor["preexisting_dirty"] == predecessor["preexisting_dirty"]
    assert successor["predecessor_claim_id"] == predecessor["claim_id"]
    assert successor["agent"] == "claude"
    assert successor["session_id"] == "claude-session-1"
    assert successor["base_commit"] == predecessor["handoff_commit"]
    assert successor["state"] == "active"
    assert successor["durable_status_path"] is None
    assert successor["durable_status_sha256"] is None
    assert successor["durable_status_blob_oid"] is None
    assert successor["handoff_commit"] is None
    assert successor["replaces_claim_id"] is None
    assert successor["replacement_reason"] is None
    assert successor["started_utc"] != predecessor["started_utc"]
    assert successor["lease_until_utc"] != predecessor["lease_until_utc"]

    on_disk = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert on_disk == successor

    history_path = _history_path_for(accept_repo.repo_dir, predecessor["claim_id"])
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["claim_id"] == predecessor["claim_id"]
    assert history["final_state"] == "handed-off"
    assert history["successor_claim_id"] == successor["claim_id"]
    assert history["successor_agent"] == "claude"
    assert history["successor_session_id"] == "claude-session-1"
    assert history["durable_status_path"] == predecessor["durable_status_path"]
    assert history["durable_status_sha256"] == predecessor["durable_status_sha256"]
    assert history.get("ended_utc")

    assert not accept_repo.journal_path.exists()


def test_accept_happy_path_preserves_nonempty_preexisting_dirty(tmp_path):
    """A predecessor claim's non-empty `preexisting_dirty` list survives the
    prepared-journal round-trip into the newly activated successor claim --
    verifying the array's exact CONTENT, not merely object identity (Task 6
    review Fix 4)."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_modified)
    handoff_result = _handoff(repo_dir, claim_id, fixture.next_action)
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"

    predecessor = _current_claim(repo_dir)
    assert len(predecessor["preexisting_dirty"]) == 1

    result = _accept(repo_dir, claim_id)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    successor = parsed["claims"][0]
    assert successor["preexisting_dirty"] == predecessor["preexisting_dirty"]

    on_disk = _current_claim(repo_dir)
    assert on_disk["preexisting_dirty"] == predecessor["preexisting_dirty"]


# ---------------------------------------------------------------------------
# Step 3: fault/retry at each fixed boundary
# ---------------------------------------------------------------------------


def test_accept_after_prepare_fault_leaves_predecessor_authoritative_and_retry_completes(accept_repo):
    """At `accept-after-prepare` (immediately after the journal finishes
    writing, before activation), the predecessor is still `handoff-ready` and
    authoritative; the journal is readable; and retrying the exact same
    `accept` call completes acceptance, reusing the ALREADY-prepared
    successor claim ID rather than generating a second one (Task 6 brief,
    Step 3)."""
    faulted = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-after-prepare"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "accept-after-prepare" not in faulted.stdout
    assert "accept-after-prepare" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative predecessor"
    assert recovery["authoritative_owner"] == "predecessor"
    assert recovery["claim_id"] == accept_repo.predecessor_claim_id
    assert accept_repo.predecessor_claim_id in recovery["retry_command"]

    assert accept_repo.journal_path.exists()
    journal = json.loads(accept_repo.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "prepared"
    prepared_successor_id = journal["successor"]["claim_id"]

    claim = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert claim["claim_id"] == accept_repo.predecessor_claim_id
    assert claim["state"] == "handoff-ready"

    retry = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["claim_id"] == prepared_successor_id
    assert not accept_repo.journal_path.exists()


def test_accept_resume_warns_on_identity_mismatch_with_journal(accept_repo):
    """Task 6 review Fix 2: a resumed `accept` retry that resupplies a
    DIFFERENT `-Agent`/`-SessionId` than the journal's already-recorded
    successor identity still completes using the RECORDED identity (no
    behavior change) but now surfaces the mismatch as a warning, mirroring
    `handoff`'s `next-action-mismatch` warning."""
    faulted = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-after-prepare"},
    )
    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"

    journal = json.loads(accept_repo.journal_path.read_text(encoding="utf-8"))
    recorded_agent = journal["successor"]["agent"]
    recorded_session_id = journal["successor"]["session_id"]
    assert recorded_agent == "claude"
    assert recorded_session_id == "claude-session-1"

    retry = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        Agent="codex", SessionId="codex-session-9",
    )

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["agent"] == recorded_agent
    assert retry_parsed["claims"][0]["session_id"] == recorded_session_id
    warning_codes = {warning["code"] for warning in retry_parsed["warnings"]}
    assert "accept-identity-mismatch" in warning_codes


def test_accept_resume_no_warning_on_exact_identity_match(accept_repo):
    """The same resumed retry with the EXACT SAME `-Agent`/`-SessionId` as
    the journal's recorded successor identity reports no identity-mismatch
    warning (Task 6 review Fix 2)."""
    faulted = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-after-prepare"},
    )
    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"

    retry = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    warning_codes = {warning["code"] for warning in retry_parsed["warnings"]}
    assert "accept-identity-mismatch" not in warning_codes


def test_accept_after_activate_fault_leaves_successor_authoritative_and_retry_completes(accept_repo):
    """At `accept-after-activate` (immediately after the successor claim
    replaces the active claim file), the successor IS already `active` and
    authoritative: it blocks a `start` from the predecessor's own identity,
    and retrying the exact same `accept` call completes acceptance without
    creating a second successor (Task 6 brief, Step 3)."""
    faulted = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-after-activate"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "accept-after-activate" not in faulted.stdout
    assert "accept-after-activate" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative successor"
    assert recovery["authoritative_owner"] == "successor"
    successor_claim_id = recovery["claim_id"]
    assert successor_claim_id != accept_repo.predecessor_claim_id

    claim = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert claim["claim_id"] == successor_claim_id
    assert claim["state"] == "active"
    assert accept_repo.journal_path.exists()

    # The predecessor's own identity (codex/session-1, `handoff_repo`'s
    # defaults) can no longer `start` this workstream: the successor -- owned
    # by a different agent/session -- already blocks it, proving the
    # predecessor never becomes active again.
    blocked_start = _start(accept_repo.repo_dir, Scope=accept_repo.scope)
    assert blocked_start.returncode == 2, f"stdout={blocked_start.stdout!r} stderr={blocked_start.stderr!r}"

    retry = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["claim_id"] == successor_claim_id
    assert not accept_repo.journal_path.exists()


def test_accept_after_revalidate_fault_leaves_successor_authoritative_and_retry_completes(accept_repo):
    """At `accept-after-revalidate` (immediately after post-activation Git
    revalidation passes, before the predecessor is archived), the successor
    is already active and authoritative, the predecessor is NOT yet archived
    to history, and retrying completes acceptance without creating a second
    successor (Task 6 brief, Step 3)."""
    faulted = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-after-revalidate"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "accept-after-revalidate" not in faulted.stdout
    assert "accept-after-revalidate" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative successor"
    assert recovery["authoritative_owner"] == "successor"
    successor_claim_id = recovery["claim_id"]

    claim = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert claim["claim_id"] == successor_claim_id
    assert claim["state"] == "active"
    assert accept_repo.journal_path.exists()

    history_path = _history_path_for(accept_repo.repo_dir, accept_repo.predecessor_claim_id)
    assert not history_path.exists()

    retry = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["claim_id"] == successor_claim_id
    assert not accept_repo.journal_path.exists()
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["final_state"] == "handed-off"
    assert history["successor_claim_id"] == successor_claim_id


def test_accept_after_archive_fault_leaves_successor_authoritative_and_retry_completes(accept_repo):
    """At `accept-after-archive` (immediately after the predecessor's history
    record finishes writing, before the journal is deleted), the predecessor
    IS already archived with `final_state: handed-off`, the successor is
    active and authoritative, and retrying only needs to validate the
    cross-references and delete the journal -- never creating a second
    successor or a second history record (Task 6 brief, Step 3)."""
    faulted = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-after-archive"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "accept-after-archive" not in faulted.stdout
    assert "accept-after-archive" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative successor"
    assert recovery["authoritative_owner"] == "successor"
    successor_claim_id = recovery["claim_id"]

    claim = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert claim["claim_id"] == successor_claim_id
    assert accept_repo.journal_path.exists()

    history_path = _history_path_for(accept_repo.repo_dir, accept_repo.predecessor_claim_id)
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["final_state"] == "handed-off"
    assert history["successor_claim_id"] == successor_claim_id

    retry = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["claim_id"] == successor_claim_id
    assert not accept_repo.journal_path.exists()


def test_accept_after_archive_retry_does_not_rewrite_history_record(accept_repo):
    """Task 6 review Fix 1: the predecessor's history record must be
    write-once. A resumed retry AFTER the `accept-after-archive` fault (the
    history record already finished writing before the fault interrupted the
    journal delete) must NEVER recompute `ended_utc` or rewrite any other
    field of the already-archived, immutable record."""
    faulted = _accept(
        accept_repo.repo_dir, accept_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-after-archive"},
    )
    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"

    history_path = _history_path_for(accept_repo.repo_dir, accept_repo.predecessor_claim_id)
    assert history_path.exists()
    first_write = json.loads(history_path.read_text(encoding="utf-8"))
    assert first_write.get("ended_utc")

    # A fresh, distinguishable timestamp between the first write and the
    # retry makes an accidental overwrite observable rather than relying on
    # sub-second timer luck (the recorded format has one-second resolution).
    time.sleep(1.1)

    retry = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    assert history_path.exists()
    second_write = json.loads(history_path.read_text(encoding="utf-8"))

    assert second_write == first_write
    assert second_write["ended_utc"] == first_write["ended_utc"]


# ---------------------------------------------------------------------------
# Step 4: deterministic concurrent-drift recovery
# ---------------------------------------------------------------------------


def _spawn_accept(cwd: Path, env: Optional[dict] = None, **parameters) -> subprocess.Popen:
    """Launch `accept` as a background process through the same fixed pwsh
    wrapper `run_helper` uses, WITHOUT blocking for it to exit -- the
    concurrent-drift test needs to perform a Git mutation while this process
    is deliberately paused mid-transaction. The caller is responsible for
    eventually draining/closing this process (`communicate`)."""
    pwsh = _find_pwsh()
    payload = {"ScriptPath": str(SCRIPT_PATH), "Operation": "accept", **parameters}
    run_env = {**os.environ, **(env or {})}
    process = subprocess.Popen(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", _HELPER_WRAPPER_SCRIPT],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=run_env,
    )
    process.stdin.write(json.dumps(payload))
    process.stdin.close()
    return process


def _wait_for_path(path: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for '{path}' to appear")


def test_accept_recovers_from_post_activation_git_drift(accept_repo):
    """The deterministic concurrent-drift scenario (Task 6 brief, Step 4):
    `accept` is paused at `accept-pause-after-activate` -- exactly after the
    successor claim is activated, before Git is revalidated -- while the
    TEST (never the helper) commits an unrelated empty commit in the same
    disposable worktree. `accept` resumes, detects the drift, and returns
    exit `5` with full recovery evidence; the predecessor never becomes
    active again, and the exact retry command is idempotent."""
    journal_path = accept_repo.journal_path
    ready_path = journal_path.with_name(journal_path.name + ".ready")
    continue_path = journal_path.with_name(journal_path.name + ".continue")

    process = _spawn_accept(
        accept_repo.repo_dir,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-pause-after-activate"},
        PreviousClaimId=accept_repo.predecessor_claim_id,
        Agent="claude",
        SessionId="claude-session-1",
        Json=True,
    )
    try:
        _wait_for_path(ready_path, timeout=15)

        # The TEST performs the concurrent Git mutation; the helper itself
        # never runs a Git mutation.
        commit_result = _run_git(
            ["commit", "--allow-empty", "-m", "concurrent drift during accept"],
            cwd=accept_repo.repo_dir,
        )
        assert commit_result.returncode == 0, commit_result.stderr

        continue_path.write_text("go", encoding="utf-8")

        stdout, stderr = process.communicate(timeout=20)
        returncode = process.returncode
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    assert returncode == 5, f"stdout={stdout!r} stderr={stderr!r}"
    parsed = parse_json_stdout(HelperResult(returncode, stdout, stderr))
    assert parsed["ok"] is False

    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields for the exit-5 drift"
    assert recovery["authoritative_owner"] == "successor"
    successor_claim_id = recovery["claim_id"]
    assert successor_claim_id != accept_repo.predecessor_claim_id
    assert recovery["transaction_path"].endswith(
        f"transactions/accept-{accept_repo.predecessor_claim_id}.json"
    )
    assert recovery["transaction_state"] == "activated"
    assert "accept" in recovery["retry_command"]
    assert accept_repo.predecessor_claim_id in recovery["retry_command"]
    assert "claude" in recovery["retry_command"]

    # The predecessor never becomes active again: the live claim is the
    # successor, active, and the journal is still retained.
    live_claim = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert live_claim["claim_id"] == successor_claim_id
    assert live_claim["state"] == "active"
    assert journal_path.exists()

    # The test-only control files are always cleaned up, whether the
    # continue signal arrived or the bounded wait timed out.
    assert not ready_path.exists()
    assert not continue_path.exists()

    # The concurrent drift committed above is permanent (a real commit now
    # sits ahead of the successor's recorded `base_commit`), so the exact
    # retry command is idempotent in the sense the design promises -- it
    # never creates a second successor or corrupts the transaction -- but it
    # keeps reporting the SAME exit-5 recovery rather than silently
    # completing over unresolved drift (design spec: "requires the recipient
    # to resolve or renew the handoff; it never restores an old owner over
    # the successor").
    retry = run_helper(
        accept_repo.repo_dir, "accept",
        PreviousClaimId=accept_repo.predecessor_claim_id, Agent="claude", SessionId="claude-session-1",
        Json=True,
    )
    assert retry.returncode == 5, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is False
    retry_recovery = retry_parsed["recovery"]
    assert retry_recovery["authoritative_owner"] == "successor"
    assert retry_recovery["claim_id"] == successor_claim_id
    assert journal_path.exists()

    live_claim_after_retry = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert live_claim_after_retry["claim_id"] == successor_claim_id
    assert live_claim_after_retry["state"] == "active"

    history_path = _history_path_for(accept_repo.repo_dir, accept_repo.predecessor_claim_id)
    assert not history_path.exists()


def test_accept_post_activation_dirty_claimed_scope_triggers_exit5(accept_repo):
    """Whole-branch review Fix C2: `accept`'s post-activation revalidation
    must re-check the claimed scope is still clean, not just that `HEAD`
    has not moved. An UNCOMMITTED in-scope edit that appears during the
    `accept-pause-after-activate` window -- the SAME claimed scope the
    successor now owns -- is genuine drift: exit `5`, the successor stays
    the live claim, and the predecessor is never archived."""
    journal_path = accept_repo.journal_path
    ready_path = journal_path.with_name(journal_path.name + ".ready")
    continue_path = journal_path.with_name(journal_path.name + ".continue")
    workstream_path = (
        accept_repo.repo_dir / ".ai" / "workstreams" / f"{accept_repo.workstream_id}.md"
    )

    process = _spawn_accept(
        accept_repo.repo_dir,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-pause-after-activate"},
        PreviousClaimId=accept_repo.predecessor_claim_id,
        Agent="claude",
        SessionId="claude-session-1",
        Json=True,
    )
    try:
        _wait_for_path(ready_path, timeout=15)

        # An UNCOMMITTED in-scope edit -- never a commit -- appears during
        # the pause window, directly in the file the successor's own
        # inherited scope covers.
        workstream_path.write_text(
            workstream_path.read_text(encoding="utf-8") + "\nuncommitted in-scope edit during pause\n",
            encoding="utf-8",
        )

        continue_path.write_text("go", encoding="utf-8")

        stdout, stderr = process.communicate(timeout=20)
        returncode = process.returncode
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    assert returncode == 5, f"stdout={stdout!r} stderr={stderr!r}"
    parsed = parse_json_stdout(HelperResult(returncode, stdout, stderr))
    assert parsed["ok"] is False

    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields for the exit-5 drift"
    assert recovery["authoritative_owner"] == "successor"
    successor_claim_id = recovery["claim_id"]
    assert successor_claim_id != accept_repo.predecessor_claim_id

    # The predecessor never becomes active again: the live claim is the
    # successor, active, and the journal is still retained -- the
    # predecessor was NEVER archived.
    live_claim = _current_claim(accept_repo.repo_dir, accept_repo.workstream_id)
    assert live_claim["claim_id"] == successor_claim_id
    assert live_claim["state"] == "active"
    assert journal_path.exists()

    history_path = _history_path_for(accept_repo.repo_dir, accept_repo.predecessor_claim_id)
    assert not history_path.exists()


def test_accept_post_activation_preexisting_dirty_drift_triggers_exit5(tmp_path):
    """Whole-branch review Fix C2: post-activation revalidation must also
    re-check unchanged pre-existing dirty fingerprints, not just `HEAD`. A
    captured out-of-scope dirty path that drifts DURING the
    `accept-pause-after-activate` window is genuine concurrent drift: exit
    `5`, the successor stays the live claim, and the predecessor is never
    archived."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_modified)
    handoff_result = _handoff(repo_dir, claim_id, fixture.next_action)
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"

    journal_path = _journal_path_for(repo_dir, claim_id)
    ready_path = journal_path.with_name(journal_path.name + ".ready")
    continue_path = journal_path.with_name(journal_path.name + ".continue")

    process = _spawn_accept(
        repo_dir,
        env={"AI_CONTINUITY_TEST_FAULT": "accept-pause-after-activate"},
        PreviousClaimId=claim_id,
        Agent="claude",
        SessionId="claude-session-1",
        Json=True,
    )
    try:
        _wait_for_path(ready_path, timeout=15)

        # The captured pre-existing dirty path drifts DURING the pause --
        # uncommitted content only, never a commit.
        (repo_dir / setup["path"]).write_text("drifted during accept pause\n", encoding="utf-8")

        continue_path.write_text("go", encoding="utf-8")

        stdout, stderr = process.communicate(timeout=20)
        returncode = process.returncode
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    assert returncode == 5, f"stdout={stdout!r} stderr={stderr!r}"
    parsed = parse_json_stdout(HelperResult(returncode, stdout, stderr))
    assert parsed["ok"] is False

    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields for the exit-5 drift"
    assert recovery["authoritative_owner"] == "successor"
    successor_claim_id = recovery["claim_id"]
    assert successor_claim_id != claim_id

    live_claim = _current_claim(repo_dir, "continuity-pilot")
    assert live_claim["claim_id"] == successor_claim_id
    assert live_claim["state"] == "active"
    assert journal_path.exists()

    history_path = _history_path_for(repo_dir, claim_id)
    assert not history_path.exists()


# ---------------------------------------------------------------------------
# Task 7: `takeover` -- explicit expired-claim takeover and immutable history
# ---------------------------------------------------------------------------


def _takeover_journal_path_for(repo_dir: Path, previous_claim_id: str) -> Path:
    return (
        _git_common_dir_for(repo_dir)
        / "ai-continuity"
        / "transactions"
        / f"takeover-{previous_claim_id}.json"
    )


def _expire_claim(repo_dir: Path, workstream_id: str = "continuity-pilot") -> None:
    """Directly rewrite a claim's `lease_until_utc` into the past, simulating
    real lease expiry without waiting (mirrors
    `test_accept_succeeds_after_lease_expiry_when_evidence_matches`)."""
    claim_path = _claim_file_for(repo_dir, workstream_id)
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["lease_until_utc"] = "2000-01-01T00:00:00Z"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")


def _takeover(repo_dir: Path, previous_claim_id: str, **overrides) -> HelperResult:
    """Call `takeover` with reasonable defaults for the fields under test to
    override individually. Defaults to a DIFFERENT agent/session than the
    typical `codex`/`session-1` claim owner this module's fixtures create,
    matching a genuine replacement by another agent after the original
    owner's session is gone."""
    parameters = {
        "Agent": "claude",
        "SessionId": "claude-session-1",
        "Workstream": "continuity-pilot",
        "PreviousClaimId": previous_claim_id,
        "Reason": "Original owner's session was lost; picking up the workstream.",
        "Scope": ["shared"],
        "Json": True,
    }
    parameters.update(overrides)
    return run_helper(repo_dir, "takeover", **parameters)


# ---------------------------------------------------------------------------
# Step 1: takeover validation -- a live claim cannot be taken over; an
# expired claim requires the exact ID, non-empty reason, valid identity/
# scope/branch, clean requested scope, and unchanged pre-existing dirt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["Agent", "SessionId", "Workstream", "PreviousClaimId", "Reason"])
def test_takeover_rejects_missing_required_parameter(continuity_repo, missing):
    start_result = _start(continuity_repo, Scope=["shared"])
    assert start_result.returncode == 0, f"stdout={start_result.stdout!r} stderr={start_result.stderr!r}"
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    parameters = {
        "Agent": "claude",
        "SessionId": "claude-session-1",
        "Workstream": "continuity-pilot",
        "PreviousClaimId": claim_id,
        "Reason": "a reason",
    }
    parameters[missing] = ""
    result = run_helper(continuity_repo, "takeover", Json=True, Scope=["shared"], **parameters)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(continuity_repo)["claim_id"] == claim_id
    assert not _takeover_journal_path_for(continuity_repo, claim_id).exists()


def test_takeover_rejects_empty_scope_list(continuity_repo):
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    result = _takeover(continuity_repo, claim_id, Scope=[])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(continuity_repo)["claim_id"] == claim_id


def test_takeover_rejects_live_active_claim_within_lease(continuity_repo):
    """A live (unexpired) `active` claim cannot be taken over, even with the
    exact claim ID and a valid reason (Task 7 brief, Step 1)."""
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]

    result = _takeover(continuity_repo, claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    claim = _current_claim(continuity_repo)
    assert claim["claim_id"] == claim_id
    assert claim["state"] == "active"
    assert not _takeover_journal_path_for(continuity_repo, claim_id).exists()
    assert not _history_path_for(continuity_repo, claim_id).exists()


def test_takeover_rejects_live_handoff_ready_claim_within_lease(handoff_repo):
    """A live (unexpired) `handoff-ready` claim cannot be taken over either;
    only `accept` may claim it while its lease has not expired."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    handoff_result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"

    result = _takeover(handoff_repo.repo_dir, handoff_repo.claim_id, Scope=handoff_repo.scope)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    claim = _current_claim(handoff_repo.repo_dir)
    assert claim["claim_id"] == handoff_repo.claim_id
    assert claim["state"] == "handoff-ready"


def test_takeover_rejects_unknown_claim_id(continuity_repo):
    result = _takeover(continuity_repo, "00000000-0000-4000-8000-000000000000")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_takeover_rejects_malformed_previous_claim_id(continuity_repo):
    """`-PreviousClaimId` is spliced directly into the transaction journal's
    filename, so it is validated as a GUID before any lock or mutation,
    closing off path-injection-shaped input (mirrors accept's identical
    guard)."""
    result = _takeover(continuity_repo, "../../etc/passwd")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_takeover_rejects_stale_claim_id_when_real_expired_claim_exists(continuity_repo):
    """A guessed/stale ID that does not match the current (expired) claim for
    the workstream fails without mutation, even though a real expired claim
    exists under a DIFFERENT claim ID (Task 7 brief, Step 1: "a stale or
    guessed ID fails without mutation")."""
    start_result = _start(continuity_repo, Scope=["shared"])
    real_claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    result = _takeover(continuity_repo, "00000000-0000-4000-8000-000000000000")

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    claim = _current_claim(continuity_repo)
    assert claim["claim_id"] == real_claim_id
    assert claim["state"] == "active"


def test_takeover_rejects_protected_main_branch(continuity_repo):
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    checkout_result = _run_git(["checkout", "main"], cwd=continuity_repo)
    assert checkout_result.returncode == 0, checkout_result.stderr

    result = _takeover(continuity_repo, claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_takeover_rejects_detached_head(continuity_repo):
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    head = _run_git(["rev-parse", "HEAD"], cwd=continuity_repo).stdout.strip()
    checkout_result = _run_git(["checkout", head], cwd=continuity_repo)
    assert checkout_result.returncode == 0, checkout_result.stderr

    result = _takeover(continuity_repo, claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_takeover_rejects_wrong_branch(continuity_repo):
    """A workstream's claim was recorded on `codex/continuity-pilot`;
    `takeover` invoked from a DIFFERENT (but still validly-prefixed) branch
    for the SAME workstream id is rejected by
    Test-TakeoverPreActivationInvariants's own branch comparison, even though
    the coarser Assert-WorkstreamBranchAllowed naming check alone would allow
    it (mirrors Task 6 review Fix 3 for accept)."""
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    _checkout_new_branch(continuity_repo, "claude/continuity-pilot")

    result = _takeover(continuity_repo, claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    claim = _current_claim(continuity_repo)
    assert claim["claim_id"] == claim_id
    assert claim["state"] == "active"


INVALID_TAKEOVER_SCOPE_FORMS = [
    "",
    ".",
    "..",
    "/",
    "../escape",
    "shared/../../escape",
    "shared/*",
    "C:/absolute",
    "//server/share",
    ".git",
    "shared/.git",
]


@pytest.mark.parametrize("scope_value", INVALID_TAKEOVER_SCOPE_FORMS, ids=repr)
def test_takeover_rejects_invalid_scope_forms(continuity_repo, scope_value):
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    result = _takeover(continuity_repo, claim_id, Scope=[scope_value])

    assert result.returncode == 2, (
        f"scope={scope_value!r} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(continuity_repo)["claim_id"] == claim_id


def test_takeover_rejects_dirty_path_inside_requested_scope(continuity_repo):
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    (continuity_repo / "shared").mkdir(parents=True, exist_ok=True)
    (continuity_repo / "shared" / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    result = _takeover(continuity_repo, claim_id, Scope=["shared"])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    claim = _current_claim(continuity_repo)
    assert claim["claim_id"] == claim_id
    assert claim["state"] == "active"
    assert not _takeover_journal_path_for(continuity_repo, claim_id).exists()


def test_takeover_rejects_scope_overlapping_other_live_claim(continuity_repo):
    start_result = _start(continuity_repo, Scope=["shared"])
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(continuity_repo)

    _checkout_new_branch(continuity_repo, "codex/other-workstream")
    other_start = _start(
        continuity_repo, Workstream="other-workstream", SessionId="session-2", Scope=["other"]
    )
    assert other_start.returncode == 0, f"stdout={other_start.stdout!r} stderr={other_start.stderr!r}"

    checkout_back = _run_git(["checkout", "codex/continuity-pilot"], cwd=continuity_repo)
    assert checkout_back.returncode == 0, checkout_back.stderr

    result = _takeover(continuity_repo, claim_id, Scope=["other"])

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_takeover_rejects_preexisting_dirty_content_drift(tmp_path):
    """A pre-existing modified-but-unstaged out-of-scope path edited AGAIN
    after the claim expires blocks takeover, reusing the exact same
    `Test-PreexistingDirtyUnchanged` gate `handoff`/`accept` already exercise
    exhaustively (Task 7 brief, Step 1: "unchanged pre-existing dirty
    fingerprints from the prior claim")."""
    repo_dir, claim_id, fixture, setup = _build_repo_with_preexisting(tmp_path, _setup_modified)
    _expire_claim(repo_dir)

    (repo_dir / setup["path"]).write_text("drifted after expiry\n", encoding="utf-8")

    result = _takeover(repo_dir, claim_id, Scope=fixture.scope)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    claim = _current_claim(repo_dir)
    assert claim["claim_id"] == claim_id
    assert claim["state"] == "active"


# ---------------------------------------------------------------------------
# Step 2: replacement shape and immutable-history cross-references
# ---------------------------------------------------------------------------


def test_takeover_happy_path_replaces_expired_active_claim(continuity_repo):
    start_result = _start(continuity_repo, Scope=["shared"])
    predecessor = parse_json_stdout(start_result)["claims"][0]
    _expire_claim(continuity_repo)

    result = _takeover(
        continuity_repo, predecessor["claim_id"],
        Agent="claude", SessionId="claude-session-1", Reason="Predecessor session crashed.",
        Scope=["other-shared"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["operation"] == "takeover"
    assert len(parsed["claims"]) == 1
    replacement = parsed["claims"][0]

    assert re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", replacement["claim_id"]
    )
    assert replacement["claim_id"] != predecessor["claim_id"]
    assert replacement["workstream_id"] == predecessor["workstream_id"]
    assert replacement["worktree_path"] == predecessor["worktree_path"]
    assert replacement["branch"] == predecessor["branch"]
    assert replacement["scope_paths"] == ["other-shared"]
    assert replacement["scope_paths"] != predecessor["scope_paths"]
    assert replacement["agent"] == "claude"
    assert replacement["session_id"] == "claude-session-1"
    assert replacement["state"] == "active"
    assert replacement["predecessor_claim_id"] is None
    assert replacement["replaces_claim_id"] == predecessor["claim_id"]
    assert replacement["replacement_reason"] == "Predecessor session crashed."
    assert replacement["durable_status_path"] is None
    assert replacement["durable_status_sha256"] is None
    assert replacement["durable_status_blob_oid"] is None
    assert replacement["handoff_commit"] is None
    assert replacement["preexisting_dirty"] == []
    assert replacement["started_utc"] != predecessor["started_utc"]
    assert replacement["lease_until_utc"] != predecessor["lease_until_utc"]

    on_disk = _current_claim(continuity_repo)
    assert on_disk == replacement

    history_path = _history_path_for(continuity_repo, predecessor["claim_id"])
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["claim_id"] == predecessor["claim_id"]
    assert history["final_state"] == "replaced"
    assert history["scope_paths"] == predecessor["scope_paths"]
    assert history["preexisting_dirty"] == predecessor["preexisting_dirty"]
    assert history["successor_claim_id"] == replacement["claim_id"]
    assert history["successor_agent"] == "claude"
    assert history["successor_session_id"] == "claude-session-1"
    assert history["takeover_reason"] == "Predecessor session crashed."
    assert history.get("ended_utc")

    assert not _takeover_journal_path_for(continuity_repo, predecessor["claim_id"]).exists()


def test_takeover_happy_path_replaces_expired_handoff_ready_claim(handoff_repo):
    """Design spec Testing item 27 / Local Claim Model: an expired
    `handoff-ready` claim remains blocking and can be taken over only with
    its exact ID, a reason, and normal safety validation."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    handoff_result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"
    predecessor = _current_claim(handoff_repo.repo_dir)
    assert predecessor["state"] == "handoff-ready"

    _expire_claim(handoff_repo.repo_dir)

    result = _takeover(
        handoff_repo.repo_dir, handoff_repo.claim_id,
        Agent="claude", SessionId="claude-session-1", Reason="Recipient never accepted.",
        Scope=handoff_repo.scope,
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    replacement = parsed["claims"][0]
    assert replacement["state"] == "active"
    assert replacement["replaces_claim_id"] == handoff_repo.claim_id

    history_path = _history_path_for(handoff_repo.repo_dir, handoff_repo.claim_id)
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["final_state"] == "replaced"
    assert history["state"] == "handoff-ready"
    assert history["durable_status_path"] == predecessor["durable_status_path"]
    assert history["durable_status_sha256"] == predecessor["durable_status_sha256"]


def test_takeover_fresh_dirty_baseline_captures_new_out_of_scope_dirt_beyond_predecessor(tmp_path):
    """The replacement's `preexisting_dirty` baseline is captured FRESH at
    takeover time: unlike `handoff` (which treats any NEW out-of-scope dirt
    as a blocking conflict), `takeover` -- like `start` -- simply captures
    it. A path outside the predecessor's own scope is preserved unchanged in
    the predecessor's immutable history, while an ADDITIONAL out-of-scope
    path that appeared only AFTER the predecessor's claim started is
    captured fresh in the replacement's baseline even though the predecessor
    never recorded it (Task 7 brief, Step 2: "capture a fresh complete
    out-of-scope dirty baseline for the successor")."""
    repo_dir = _make_repo_on_branch(tmp_path, "codex/continuity-pilot")
    (repo_dir / "outside").mkdir(parents=True, exist_ok=True)
    (repo_dir / "outside" / "before-start.txt").write_text("pre-existing\n", encoding="utf-8")

    start_result = _start(repo_dir, Scope=["scope-a"])
    predecessor = parse_json_stdout(start_result)["claims"][0]
    assert len(predecessor["preexisting_dirty"]) == 1
    assert predecessor["preexisting_dirty"][0]["path"] == "outside/before-start.txt"
    _expire_claim(repo_dir)

    # A new out-of-scope dirty path appears only AFTER the claim started;
    # takeover captures it fresh instead of treating it as a conflict.
    (repo_dir / "outside" / "after-start.txt").write_text("appeared later\n", encoding="utf-8")

    result = _takeover(
        repo_dir, predecessor["claim_id"], Reason="Replacement continues with a new scope.",
        Scope=["scope-b"],
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    replacement = parse_json_stdout(result)["claims"][0]
    assert replacement["scope_paths"] == ["scope-b"]
    replacement_dirty_paths = {entry["path"] for entry in replacement["preexisting_dirty"]}
    assert replacement_dirty_paths == {"outside/before-start.txt", "outside/after-start.txt"}

    history = json.loads(_history_path_for(repo_dir, predecessor["claim_id"]).read_text(encoding="utf-8"))
    assert history["scope_paths"] == ["scope-a"]
    assert history["preexisting_dirty"] == predecessor["preexisting_dirty"]


# ---------------------------------------------------------------------------
# Step 3: fault/retry at each fixed boundary
# ---------------------------------------------------------------------------


class TakeoverFixture(NamedTuple):
    repo_dir: Path
    workstream_id: str
    predecessor_claim_id: str
    scope: list
    journal_path: Path


def _make_takeover_fixture(repo_dir: Path, *, scope=("shared",)) -> TakeoverFixture:
    start_result = _start(repo_dir, Scope=list(scope))
    assert start_result.returncode == 0, f"stdout={start_result.stdout!r} stderr={start_result.stderr!r}"
    claim_id = parse_json_stdout(start_result)["claim_id"]
    _expire_claim(repo_dir)
    return TakeoverFixture(
        repo_dir=repo_dir,
        workstream_id="continuity-pilot",
        predecessor_claim_id=claim_id,
        scope=list(scope),
        journal_path=_takeover_journal_path_for(repo_dir, claim_id),
    )


@pytest.fixture()
def takeover_repo(continuity_repo: Path) -> TakeoverFixture:
    return _make_takeover_fixture(continuity_repo)


def test_takeover_after_prepare_fault_leaves_old_claim_authoritative_and_retry_completes(takeover_repo):
    """At `takeover-after-prepare` (immediately after the journal finishes
    writing, before activation), the old claim is still `active` and
    authoritative; the journal is readable; and retrying the exact same
    `takeover` call completes it, reusing the ALREADY-prepared replacement
    claim ID rather than generating a second one (Task 7 brief, Step 3)."""
    faulted = _takeover(
        takeover_repo.repo_dir, takeover_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "takeover-after-prepare"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "takeover-after-prepare" not in faulted.stdout
    assert "takeover-after-prepare" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative old claim"
    assert recovery["authoritative_owner"] == "predecessor"
    assert recovery["claim_id"] == takeover_repo.predecessor_claim_id
    assert takeover_repo.predecessor_claim_id in recovery["retry_command"]

    assert takeover_repo.journal_path.exists()
    journal = json.loads(takeover_repo.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "prepared"
    prepared_replacement_id = journal["new_claim"]["claim_id"]

    claim = _current_claim(takeover_repo.repo_dir)
    assert claim["claim_id"] == takeover_repo.predecessor_claim_id
    assert claim["state"] == "active"

    retry = _takeover(takeover_repo.repo_dir, takeover_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["claim_id"] == prepared_replacement_id
    assert not takeover_repo.journal_path.exists()


def test_takeover_resume_warns_on_identity_mismatch_with_journal(takeover_repo):
    """A resumed `takeover` retry that resupplies a DIFFERENT `-Agent`/
    `-SessionId` than the journal's already-recorded replacement identity
    still completes using the RECORDED identity (no behavior change) but now
    surfaces the mismatch as a warning, mirroring accept's
    `accept-identity-mismatch` warning (Task 6 review Fix 2)."""
    faulted = _takeover(
        takeover_repo.repo_dir, takeover_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "takeover-after-prepare"},
    )
    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"

    journal = json.loads(takeover_repo.journal_path.read_text(encoding="utf-8"))
    recorded_agent = journal["new_claim"]["agent"]
    recorded_session_id = journal["new_claim"]["session_id"]
    assert recorded_agent == "claude"
    assert recorded_session_id == "claude-session-1"

    retry = _takeover(
        takeover_repo.repo_dir, takeover_repo.predecessor_claim_id,
        Agent="codex", SessionId="codex-session-9",
    )

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["agent"] == recorded_agent
    assert retry_parsed["claims"][0]["session_id"] == recorded_session_id
    warning_codes = {warning["code"] for warning in retry_parsed["warnings"]}
    assert "takeover-identity-mismatch" in warning_codes


def test_takeover_resume_rechecks_scope_overlap_before_activation(takeover_repo):
    """During the prepare-to-retry window an injected `takeover-after-
    prepare` fault opens, another agent can legitimately `start` a DIFFERENT
    workstream whose scope overlaps the one this pending, not-yet-activated
    journal recorded -- that `start`'s own overlap check cannot see a
    journal that never wrote a claim file. The FRESH path already checks the
    supplied `-Scope` against every OTHER live claim before writing the
    journal, but a resumed retry must re-run that SAME check immediately
    before activating; otherwise it activates anyway, producing two live
    claims with overlapping scope. The predecessor (still expired, but still
    the recorded owner) must remain authoritative on conflict, and the
    journal must be left intact for a later legitimate retry once the
    conflict clears (Task 7 review Fix 1)."""
    faulted = _takeover(
        takeover_repo.repo_dir, takeover_repo.predecessor_claim_id,
        Scope=["other"],
        env={"AI_CONTINUITY_TEST_FAULT": "takeover-after-prepare"},
    )
    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    assert takeover_repo.journal_path.exists()

    _checkout_new_branch(takeover_repo.repo_dir, "codex/other-workstream")
    other_start = _start(
        takeover_repo.repo_dir, Workstream="other-workstream", SessionId="session-2", Scope=["other"]
    )
    assert other_start.returncode == 0, f"stdout={other_start.stdout!r} stderr={other_start.stderr!r}"
    other_claim_id = parse_json_stdout(other_start)["claim_id"]

    checkout_back = _run_git(["checkout", "codex/continuity-pilot"], cwd=takeover_repo.repo_dir)
    assert checkout_back.returncode == 0, checkout_back.stderr

    retry = _takeover(takeover_repo.repo_dir, takeover_repo.predecessor_claim_id, Scope=["other"])

    assert retry.returncode == 2, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is False

    assert takeover_repo.journal_path.exists()
    journal = json.loads(takeover_repo.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "prepared"

    predecessor_claim = _current_claim(takeover_repo.repo_dir, "continuity-pilot")
    assert predecessor_claim["claim_id"] == takeover_repo.predecessor_claim_id
    assert predecessor_claim["state"] == "active"

    other_claim = _current_claim(takeover_repo.repo_dir, "other-workstream")
    assert other_claim["claim_id"] == other_claim_id
    assert other_claim["state"] == "active"


def test_takeover_after_activate_fault_leaves_replacement_authoritative_and_retry_completes(takeover_repo):
    """At `takeover-after-activate` (immediately after the replacement claim
    replaces the active claim file), the replacement IS already `active` and
    authoritative: it blocks a `start` from the predecessor's own identity,
    and retrying the exact same `takeover` call completes it without
    creating a second replacement (Task 7 brief, Step 3)."""
    faulted = _takeover(
        takeover_repo.repo_dir, takeover_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "takeover-after-activate"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "takeover-after-activate" not in faulted.stdout
    assert "takeover-after-activate" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative replacement"
    assert recovery["authoritative_owner"] == "successor"
    replacement_claim_id = recovery["claim_id"]
    assert replacement_claim_id != takeover_repo.predecessor_claim_id

    claim = _current_claim(takeover_repo.repo_dir)
    assert claim["claim_id"] == replacement_claim_id
    assert claim["state"] == "active"
    assert takeover_repo.journal_path.exists()

    blocked_start = _start(takeover_repo.repo_dir, Scope=takeover_repo.scope)
    assert blocked_start.returncode == 2, f"stdout={blocked_start.stdout!r} stderr={blocked_start.stderr!r}"

    retry = _takeover(takeover_repo.repo_dir, takeover_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["claim_id"] == replacement_claim_id
    assert not takeover_repo.journal_path.exists()


def test_takeover_after_archive_fault_leaves_replacement_authoritative_and_retry_completes(takeover_repo):
    """At `takeover-after-archive` (immediately after the old claim's history
    record finishes writing, before the journal is deleted), the old claim
    IS already archived with `final_state: replaced`, the replacement is
    active and authoritative, and retrying only needs to validate the
    cross-references and delete the journal -- never creating a second
    replacement or a second history record (Task 7 brief, Step 3)."""
    faulted = _takeover(
        takeover_repo.repo_dir, takeover_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "takeover-after-archive"},
    )

    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"
    parsed = parse_json_stdout(faulted)
    assert parsed["ok"] is False
    assert "takeover-after-archive" not in faulted.stdout
    assert "takeover-after-archive" not in faulted.stderr
    recovery = parsed.get("recovery")
    assert recovery, "expected recovery fields naming the authoritative replacement"
    assert recovery["authoritative_owner"] == "successor"
    replacement_claim_id = recovery["claim_id"]

    claim = _current_claim(takeover_repo.repo_dir)
    assert claim["claim_id"] == replacement_claim_id
    assert takeover_repo.journal_path.exists()

    history_path = _history_path_for(takeover_repo.repo_dir, takeover_repo.predecessor_claim_id)
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["final_state"] == "replaced"
    assert history["successor_claim_id"] == replacement_claim_id

    retry = _takeover(takeover_repo.repo_dir, takeover_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    retry_parsed = parse_json_stdout(retry)
    assert retry_parsed["ok"] is True
    assert retry_parsed["claims"][0]["claim_id"] == replacement_claim_id
    assert not takeover_repo.journal_path.exists()


def test_takeover_after_archive_retry_does_not_rewrite_history_record(takeover_repo):
    """The old claim's history record must be write-once. A resumed retry
    AFTER the `takeover-after-archive` fault (the history record already
    finished writing before the fault interrupted the journal delete) must
    NEVER recompute `ended_utc` or rewrite any other field of the
    already-archived, immutable record (mirrors accept's Task 6 review
    Fix 1)."""
    faulted = _takeover(
        takeover_repo.repo_dir, takeover_repo.predecessor_claim_id,
        env={"AI_CONTINUITY_TEST_FAULT": "takeover-after-archive"},
    )
    assert faulted.returncode == 3, f"stdout={faulted.stdout!r} stderr={faulted.stderr!r}"

    history_path = _history_path_for(takeover_repo.repo_dir, takeover_repo.predecessor_claim_id)
    assert history_path.exists()
    first_write = json.loads(history_path.read_text(encoding="utf-8"))
    assert first_write.get("ended_utc")

    # A fresh, distinguishable timestamp between the first write and the
    # retry makes an accidental overwrite observable rather than relying on
    # sub-second timer luck (the recorded format has one-second resolution).
    time.sleep(1.1)

    retry = _takeover(takeover_repo.repo_dir, takeover_repo.predecessor_claim_id)

    assert retry.returncode == 0, f"stdout={retry.stdout!r} stderr={retry.stderr!r}"
    assert history_path.exists()
    second_write = json.loads(history_path.read_text(encoding="utf-8"))

    assert second_write == first_write
    assert second_write["ended_utc"] == first_write["ended_utc"]


# ---------------------------------------------------------------------------
# Task 7 review Fix 2/Fix 3: direct internal-function coverage for
# `ConvertFrom-ContinuityTimestamp`, the shared helper `status`/`accept`/
# `takeover` all use to compare a claim's recorded timestamps against
# `[DateTimeOffset]::UtcNow`.
#
# `scripts/ai-handoff.ps1` always runs its single top-level try/catch/
# finally and ends with `exit $exitCode` the instant it is invoked (or
# dot-sourced) at all -- there is no guard that skips this when the file is
# loaded only to define functions -- so it cannot be dot-sourced as a whole
# to call one internal function afterward without the process exiting first.
# Instead, this extracts exactly the `ContinuityStateException` class and
# the `ConvertFrom-ContinuityTimestamp` function definitions from the real
# script by AST (never copying or re-implementing their logic), defines
# only those two nodes in an isolated pwsh process, and calls the real
# function directly -- the "internal-function hook" the review calls for.
# ---------------------------------------------------------------------------


_CONVERT_TIMESTAMP_WRAPPER_SCRIPT = textwrap.dedent(
    """
    $ErrorActionPreference = 'Stop'
    $raw = [Console]::In.ReadToEnd()
    # `-DateKind String` is required on THIS outer JSON deserialization: an
    # RFC3339-shaped JSON string value is otherwise auto-detected and
    # deserialized as `[DateTime]` here too (the exact same
    # `ConvertFrom-Json` quirk `ConvertFrom-ContinuityTimestamp`'s own
    # docstring documents), which would silently corrupt the raw test input
    # string itself before this wrapper ever calls the real function under
    # test.
    $parameters = $raw | ConvertFrom-Json -AsHashtable -DateKind String
    $scriptPath = $parameters['ScriptPath']
    $valueKind = $parameters['ValueKind']
    $rawValue = [string] $parameters['Value']

    $source = [System.IO.File]::ReadAllText($scriptPath, [System.Text.UTF8Encoding]::new($false))
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref] $tokens, [ref] $parseErrors)

    $classAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.TypeDefinitionAst] -and $node.Name -ceq 'ContinuityStateException'
    }, $true)
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'ConvertFrom-ContinuityTimestamp'
    }, $true)
    if ($null -eq $classAst -or $null -eq $functionAst) {
        throw "Could not locate ContinuityStateException/ConvertFrom-ContinuityTimestamp in '$scriptPath'."
    }

    # Defining ONLY these two extracted AST nodes -- never the rest of the
    # script's top-level statements -- is what lets this call the real
    # internal function without ever reaching the script's trailing
    # `exit $exitCode`.
    . ([scriptblock]::Create($classAst.Extent.Text + "`n" + $functionAst.Extent.Text))

    $value = if ($valueKind -eq 'datetime-utc') {
        # Mirrors exactly what `ConvertFrom-Json` itself produces for a
        # claim's RFC3339 UTC string field: a `[DateTime]` with `Kind=Utc`
        # (documented Newtonsoft.Json ISO-8601 auto-detection quirk the
        # function's own docstring describes).
        [DateTime]::Parse(
            $rawValue, [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind
        )
    }
    else {
        # The documented `[string]` fallback branch: a raw RFC3339 string
        # that never passed through JSON deserialization at all.
        $rawValue
    }

    $resultOffset = ConvertFrom-ContinuityTimestamp -Value $value -FieldName 'lease_until_utc' `
        -ClaimId 'internal-test-claim'

    [PSCustomObject]@{
        offset_iso = $resultOffset.ToString('yyyy-MM-ddTHH:mm:sszzz', [System.Globalization.CultureInfo]::InvariantCulture)
    } | ConvertTo-Json -Compress
    """
).strip()


def _convert_from_continuity_timestamp(value: str, value_kind: str) -> dict:
    """Directly invoke the real `ConvertFrom-ContinuityTimestamp` function
    extracted from `scripts/ai-handoff.ps1` by AST (see the module comment
    above), passing `value` either as a `[DateTime]` with `Kind=Utc`
    (``value_kind="datetime-utc"``, the shape every claim timestamp field
    actually arrives as after `ConvertFrom-Json`) or as a raw `[string]`
    (``value_kind="string"``, the documented fallback branch)."""
    pwsh = _find_pwsh()
    payload = {"ScriptPath": str(SCRIPT_PATH), "Value": value, "ValueKind": value_kind}
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", _CONVERT_TIMESTAMP_WRAPPER_SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"internal ConvertFrom-ContinuityTimestamp call failed: "
        f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
    )
    return json.loads(completed.stdout)


def test_convert_from_continuity_timestamp_pins_utc_offset_independent_of_runner_timezone():
    """`ConvertFrom-ContinuityTimestamp`'s `[DateTime]` branch --
    `[DateTimeOffset]::new([DateTime]::SpecifyKind($Value, [DateTimeKind]::Utc))`
    -- forces the incoming value's `Kind` to `Utc` before constructing the
    `[DateTimeOffset]`, which is a fixed .NET guarantee: the result's
    `Offset` is always exactly `+00:00`, regardless of what local time zone
    the calling process happens to be configured with. The bug this
    replaced declared `$Value` as `[string]`, which forced PowerShell to
    implicitly re-stringify the already-`ConvertFrom-Json`-parsed
    `[DateTime]` using the current culture's default format BEFORE the
    function ever ran -- silently discarding the "Z" UTC marker. The
    downstream `[DateTimeOffset]::Parse` call then had no zone information
    left to work from, so it fell back to interpreting the value as LOCAL
    time and stamped it with whatever `+HH:mm` offset the CURRENT PROCESS's
    system time zone happened to be.

    The existing near-boundary expiry tests (for example
    `test_accept_succeeds_after_lease_expiry_when_evidence_matches`, and
    this module's own `_expire_claim`-based takeover tests) only exercise
    that reverted bug observably when the test runner's own local UTC
    offset happens to be nonzero -- on a UTC-zoned runner the buggy
    re-stringify-then-local-parse path coincidentally reproduces the correct
    instant too, so those tests would pass with or without the fix. This
    test instead asserts the fixed behavior's invariant directly (offset
    pinned at `+00:00`), which the fix guarantees unconditionally and does
    not itself depend on this machine's configured time zone, even though a
    hypothetical regression could still only be OBSERVED failing here on a
    non-UTC-zoned host (Task 7 review Fix 2)."""
    result = _convert_from_continuity_timestamp("2026-07-19T01:14:34Z", "datetime-utc")

    assert result["offset_iso"] == "2026-07-19T01:14:34+00:00"


def test_convert_from_continuity_timestamp_handles_string_input_directly():
    """`ConvertFrom-ContinuityTimestamp`'s documented `[string]` fallback
    branch (`[DateTimeOffset]::Parse([string] $Value, InvariantCulture)`) is
    reachable and exercised directly here -- not dead code -- by calling the
    real internal function (extracted by AST; see the module comment above)
    with a raw RFC3339 string that never passed through `ConvertFrom-Json`
    at all. It parses to the identical UTC instant and `+00:00` offset as
    the `[DateTime]` branch a real claim read exercises (Task 7 review
    Fix 3)."""
    via_datetime = _convert_from_continuity_timestamp("2026-07-19T01:14:34Z", "datetime-utc")
    via_string = _convert_from_continuity_timestamp("2026-07-19T01:14:34Z", "string")

    assert via_string["offset_iso"] == via_datetime["offset_iso"] == "2026-07-19T01:14:34+00:00"


# ---------------------------------------------------------------------------
# Task 8: complete contract and failure matrix
#
# Design-test traceability table (docs/superpowers/specs/2026-07-16-claude-
# codex-continuity-design.md, "Testing", numbered items 1-27). Every row
# names pytest test(s) that actually invoke the helper (or, for item 19, the
# staging audit) and assert the durable filesystem/Git result -- never a
# test that merely exercises unrelated code incidentally -- with two
# intentional exceptions for requirements that are inherently STATIC and
# have no helper behavior to invoke: item 1 (instruction-reference
# resolution -- AGENTS.md/CLAUDE.md load order and file existence) and item
# 12 (a source-layout scan proving the complete absence of prohibited Git
# mutation commands). Those two rows are covered by direct static
# file/source assertions instead of a helper invocation, which is the
# correct and complete way to test these two requirement types; every
# other row still names a test that invokes the helper (or the staging
# audit).
#
#  1. Root instruction files resolve shared documents.
#     -> test_root_instruction_references_resolve_once
#  2. A linked worktree sees claims through the Git common directory.
#     -> test_start_shares_claims_through_linked_worktree,
#        test_status_reports_shared_git_common_directory,
#        test_start_concurrent_disjoint_claims_in_linked_worktrees_both_persist,
#        test_start_concurrent_overlapping_claims_in_linked_worktrees_exactly_one_succeeds
#  3. `start` is idempotent for the same claim/worktree.
#     -> test_start_is_idempotent_for_same_identity_and_scope
#  4. Overlapping scopes fail and preserve the original claim.
#     -> test_start_rejects_overlapping_scope_with_other_workstreams_claim,
#        test_start_concurrent_overlapping_claims_in_linked_worktrees_exactly_one_succeeds
#  5. Disjoint scopes can be claimed concurrently.
#     -> test_start_allows_disjoint_scope_concurrent_claims,
#        test_start_concurrent_disjoint_claims_in_linked_worktrees_both_persist
#  6. Pre-existing out-of-scope dirty files are captured and never changed.
#     -> test_start_captures_out_of_scope_dirty_entry_with_full_fingerprint,
#        test_handoff_allows_unchanged_preexisting_out_of_scope_dirt
#  7. `start`/`takeover`/`handoff` reject dirty claimed scope; active
#     `update` may record explicitly dirty in-progress verification.
#     -> test_start_rejects_dirty_path_inside_requested_scope,
#        test_takeover_rejects_dirty_path_inside_requested_scope,
#        test_handoff_rejects_dirty_path_inside_claimed_scope_of_every_kind,
#        test_update_verification_passed_with_dirty_path_succeeds
#  8. Lease expiry does not delete a claim; takeover records replacement.
#     -> test_status_warns_on_expired_lease,
#        test_takeover_happy_path_replaces_expired_active_claim
#  9. Malformed JSON and lock contention fail closed without corrupting
#     prior state.
#     -> test_status_reports_malformed_claim_as_exit_3,
#        test_status_reports_duplicate_active_workstream_file_as_exit_3,
#        test_start_times_out_on_lock_contention_without_mutation
# 10. `update` changes only the owned workstream status.
#     -> test_update_preserves_bytes_outside_managed_section_and_explicit_fields,
#        test_update_preserves_bytes_outside_next_action_span_and_managed_section
# 11. `handoff` requires committed state/next action/full commit/unchanged
#     dirty fingerprints/clean scope; never edits tracked files.
#     -> test_handoff_rejects_committed_state_not_handoff,
#        test_handoff_rejects_next_action_mismatch,
#        test_handoff_head_commit_field_is_not_self_referential,
#        test_handoff_rejects_dirty_claimed_scope,
#        test_handoff_rejects_preexisting_dirty_status_drift,
#        test_handoff_never_edits_a_tracked_file_or_git_state,
#        test_handoff_happy_path_marks_claim_handoff_ready
# 12. The helper never invokes prohibited Git mutation commands.
#     -> test_helper_never_invokes_prohibited_git_mutation_commands
# 13. Every operation/condition returns the specified exit code and JSON
#     warning/error shape.
#     -> test_status_json_has_stable_top_level_shape (0),
#        test_status_reports_overlapping_live_claims_as_exit_2 (2),
#        test_status_reports_malformed_claim_as_exit_3 (3),
#        test_status_returns_exit_4_outside_git (4),
#        test_start_returns_exit_4_outside_git (4),
#        test_accept_recovers_from_post_activation_git_drift (5)
# 14. Wrong agent/session/worktree/branch cannot update/hand off/accept.
#     -> test_update_rejects_wrong_agent, test_update_rejects_wrong_session,
#        test_update_rejects_wrong_branch, test_update_rejects_wrong_canonical_worktree,
#        test_handoff_rejects_wrong_agent, test_handoff_rejects_wrong_session,
#        test_handoff_rejects_wrong_branch, test_accept_rejects_different_branch,
#        test_accept_rejects_wrong_canonical_worktree
# 15. Unsafe IDs/paths (traversal, rooted/UNC, `.git`, reparse escapes) fail
#     before filesystem mutation.
#     -> test_start_rejects_invalid_workstream_id_forms,
#        test_start_rejects_invalid_session_id_forms,
#        test_start_rejects_invalid_scope_forms,
#        test_start_rejects_scope_through_directory_junction,
#        test_start_rejects_scope_through_symbolic_link
# 16. Handoff reconciles modified/renamed/deleted/untracked/committed/
#     pre-existing/out-of-scope paths against durable workstream state.
#     -> test_handoff_accepts_committed_in_scope_change_of_every_kind,
#        test_handoff_rejects_committed_out_of_scope_change,
#        test_handoff_rejects_new_out_of_scope_untracked_path,
#        test_handoff_rejects_committed_in_scope_path_omitted_from_changed_paths,
#        test_handoff_allows_unchanged_preexisting_out_of_scope_dirt
# 17. Verification parameter combinations follow the result-dependent matrix
#     and include complete dirty-path evidence.
#     -> test_update_verification_passed_requires_command_and_commit,
#        test_update_verification_not_run_requires_reason,
#        test_update_verification_dirty_path_must_be_currently_dirty,
#        test_update_verification_dirty_path_must_be_in_scope,
#        test_update_verification_dirty_path_must_be_in_changed_path,
#        test_update_verification_passed_with_dirty_path_succeeds,
#        test_update_verification_failed_requires_command_and_commit
# 18. Failure injection after each handoff/accept/takeover write boundary
#     produces the documented recoverable state and an idempotent retry.
#     -> test_handoff_after_validate_fault_preserves_active_claim_and_retry_succeeds,
#        test_handoff_after_claim_rewrite_fault_leaves_handoff_ready_authoritative_and_retry_reports_it,
#        test_accept_after_prepare_fault_leaves_predecessor_authoritative_and_retry_completes,
#        test_accept_after_activate_fault_leaves_successor_authoritative_and_retry_completes,
#        test_accept_after_revalidate_fault_leaves_successor_authoritative_and_retry_completes,
#        test_accept_after_archive_fault_leaves_successor_authoritative_and_retry_completes,
#        test_takeover_after_prepare_fault_leaves_old_claim_authoritative_and_retry_completes,
#        test_takeover_after_activate_fault_leaves_replacement_authoritative_and_retry_completes,
#        test_takeover_after_archive_fault_leaves_replacement_authoritative_and_retry_completes
# 19. Candidate staged paths outside the exact portable allowlist fail the
#     audit.
#     -> test_candidate_staged_path_outside_allowlist_fails_audit,
#        test_candidate_missing_allowlisted_path_fails_audit
# 20. Reserved Windows device-name workstream IDs are rejected
#     case-insensitively.
#     -> test_start_rejects_reserved_device_name_workstream_case_insensitively
# 21. Pre-existing out-of-scope dirt remains allowed; new out-of-scope dirt
#     blocks handoff and preserves the active claim.
#     -> test_handoff_allows_unchanged_preexisting_out_of_scope_dirt,
#        test_handoff_rejects_new_out_of_scope_untracked_path
# 22. Every mutating operation rejects protected `main`; read-only `status`
#     remains available.
#     -> test_start_rejects_protected_main_branch,
#        test_update_rejects_protected_main_branch,
#        test_handoff_rejects_protected_main_branch,
#        test_accept_rejects_protected_main_branch,
#        test_takeover_rejects_protected_main_branch,
#        test_status_is_available_on_protected_main
# 23. Alternating agents can hand off one non-protected branch only after
#     all in-scope paths and the workstream status are committed.
#     -> test_handoff_rejects_dirty_claimed_scope,
#        test_handoff_workstream_never_committed_at_head,
#        test_handoff_happy_path_marks_claim_handoff_ready
# 24. Same-path status/kind/content/index mode/stage/object-ID/rename-source
#     changes to pre-existing dirt block handoff and accept.
#     -> test_handoff_rejects_preexisting_dirty_status_drift,
#        test_handoff_rejects_preexisting_dirty_kind_drift,
#        test_handoff_rejects_preexisting_dirty_content_drift,
#        test_handoff_rejects_preexisting_dirty_index_object_id_drift,
#        test_handoff_rejects_preexisting_dirty_index_mode_drift,
#        test_handoff_rejects_preexisting_dirty_rename_source_drift,
#        test_accept_rejects_preexisting_dirty_content_drift
# 25. Mutating operations enforce the branch-prefix/workstream-ID rule and
#     the documented backfill-branch exception; either agent can accept the
#     other tool's prefix.
#     -> test_start_rejects_wrong_branch_prefix, test_start_rejects_wrong_branch_suffix,
#        test_start_accepts_both_valid_branch_prefixes,
#        test_start_either_agent_can_operate_the_other_prefixs_branch,
#        test_start_bootstrap_exception_allows_documented_branch_and_workstream,
#        test_start_bootstrap_branch_rejects_other_workstream,
#        test_accept_happy_path_creates_successor_and_archives_predecessor
#        (claude accepts a codex-prefixed branch),
#        test_accept_codex_can_accept_predecessor_claim_on_claude_prefixed_branch
#        (the reverse direction)
# 26. Concurrent Git mutation before/after recipient activation never
#     creates an unowned scope; produces the documented recovery state.
#     -> test_accept_rejects_head_drift_since_handoff (before activation),
#        test_accept_recovers_from_post_activation_git_drift (after activation)
# 27. An expired `handoff-ready` claim remains blocking, can be accepted
#     when evidence matches, and can be taken over only with its exact ID,
#     a reason, and normal safety validation.
#     -> test_start_rejects_expired_handoff_ready_claim_remains_blocking,
#        test_accept_succeeds_after_lease_expiry_when_evidence_matches,
#        test_takeover_happy_path_replaces_expired_handoff_ready_claim
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task 8 carry-in gap 1: "duplicate active workstream file" (implementation
# plan, Task 2 Step 6) is a malformed-state condition distinct from the
# scope-based `overlapping-claims` conflict `status` already reports at exit
# 2. Claims live one file per workstream, named `<workstream-id>.json`
# (design spec, Helper Contract); two files that both declare the SAME
# internal `workstream_id` -- for example a stray copy left under a
# different filename -- violate that invariant even when their scope
# prefixes are disjoint, which the existing scope-overlap check alone would
# never catch.
# ---------------------------------------------------------------------------


def test_status_reports_duplicate_active_workstream_file_as_exit_3(continuity_repo):
    """Two claim files declaring the SAME `workstream_id` with DISJOINT
    scope prefixes make `status` fail closed with the stable exit `3`,
    `ok: false`, and a `malformed-state` error -- proving this is caught
    independently of (and distinctly from) the `overlapping-claims` exit-2
    conflict, which a same-scope duplicate would also trigger but a
    disjoint-scope one never would."""
    claims_dir = _claims_dir_for(continuity_repo)
    head = _run_git(["rev-parse", "HEAD"], cwd=continuity_repo).stdout.strip()

    _write_claim(
        claims_dir,
        "continuity-pilot.json",
        claim_id="55555555-5555-4555-8555-555555555555",
        workstream_id="continuity-pilot",
        scope_paths=["area-a"],
        base_commit=head,
        state="active",
    )
    _write_claim(
        claims_dir,
        "continuity-pilot-stray-copy.json",
        claim_id="66666666-6666-4666-8666-666666666666",
        workstream_id="continuity-pilot",
        scope_paths=["area-b"],
        base_commit=head,
        state="active",
    )

    result = run_helper(continuity_repo, "status", Json=True)

    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"], "expected a malformed-state error"
    assert any(error["code"] == "malformed-state" for error in parsed["errors"])
    assert not any(error["code"] == "overlapping-claims" for error in parsed["errors"])


# ---------------------------------------------------------------------------
# Task 8 carry-in gap 2: a dedicated `git-drift` test. The warning itself has
# existed since Task 2 (`test_status_warns_on_expired_lease` incidentally
# exercises it too, since `_write_claim`'s default `base_commit` never
# matches a real repository's HEAD), but no test names and isolates it.
# ---------------------------------------------------------------------------


def test_status_warns_on_git_drift(continuity_repo):
    """A live claim's recorded `base_commit` no longer matching the current
    `HEAD` makes `status` report a `git-drift` warning at the stable exit
    `0` -- never an error, never a non-zero exit -- isolated here from
    `expired-lease` by keeping the claim's lease far in the future (design
    spec, Local Claim Model: "Status/Git drift is reported, not silently
    corrected")."""
    claims_dir = _claims_dir_for(continuity_repo)
    stale_commit = "1" * 40

    _write_claim(
        claims_dir,
        "continuity-pilot.json",
        claim_id="44444444-4444-4444-8444-444444444444",
        workstream_id="continuity-pilot",
        scope_paths=[".ai"],
        base_commit=stale_commit,
        lease_until_utc="2999-01-01T00:00:00Z",
        state="active",
    )

    result = run_helper(continuity_repo, "status", Workstream="continuity-pilot", Json=True)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is True
    assert parsed["errors"] == []
    assert parsed["claim_id"] == "44444444-4444-4444-8444-444444444444"
    assert parsed["warnings"], "expected a git-drift warning"
    assert any(warning["code"] == "git-drift" for warning in parsed["warnings"])
    assert not any(warning["code"] == "expired-lease" for warning in parsed["warnings"])


# ---------------------------------------------------------------------------
# Step 2: remaining uncovered matrix cells
# ---------------------------------------------------------------------------


def test_start_returns_exit_4_outside_git(tmp_path):
    """The shared repository-context resolution (`Get-RepositoryContext`) is
    called exactly once, before the operation dispatch `switch`, for EVERY
    operation -- so a mutating operation like `start` returns the identical
    stable exit `4` outside any Git working tree that read-only `status`
    already does, never a stray parameter-validation exit `2` and never
    exit `0` (Task 8 brief, Step 2: "non-worktree at exit 4")."""
    outside_dir = tmp_path / "not-a-repo"
    outside_dir.mkdir()

    result = run_helper(outside_dir, "start", Json=True)

    assert result.returncode == 4, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["operation"] == "start"
    assert parsed["repo_root"] is None
    assert parsed["git_common_dir"] is None
    assert parsed["errors"], "expected at least one error describing the missing Git context"


def test_start_rejects_expired_handoff_ready_claim_remains_blocking(handoff_repo):
    """Design test 27's "remains blocking" facet, distinct from the already-
    covered accept/takeover-when-expired happy paths: EVEN AFTER its lease
    has expired, a `handoff-ready` claim is never simply available to a
    plain `start` retry -- only `accept` or `takeover` may act on it (design
    spec, Local Claim Model: "An expired handoff-ready claim remains
    blocking... it is never silently deleted or downgraded to active")."""
    _commit_handoff_update(handoff_repo, changed_paths=[".ai/workstreams/continuity-pilot.md"])
    handoff_result = _handoff(handoff_repo.repo_dir, handoff_repo.claim_id, handoff_repo.next_action)
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"

    _expire_claim(handoff_repo.repo_dir)

    blocked = _start(handoff_repo.repo_dir, Scope=handoff_repo.scope)

    assert blocked.returncode == 2, f"stdout={blocked.stdout!r} stderr={blocked.stderr!r}"
    assert parse_json_stdout(blocked)["ok"] is False
    claim = _current_claim(handoff_repo.repo_dir)
    assert claim["claim_id"] == handoff_repo.claim_id
    assert claim["state"] == "handoff-ready"


def test_update_rejects_protected_main_branch(update_repo):
    """`update` enforces the same protected-`main` rejection every mutating
    operation shares through `Assert-WorkstreamBranchAllowed` (design test
    22), closing the one operation the existing per-operation
    protected-main tests (`start`/`handoff`/`takeover`) had not yet named
    directly."""
    checkout = _run_git(["checkout", "main"], cwd=update_repo.repo_dir)
    assert checkout.returncode == 0, checkout.stderr

    result = _update(update_repo.repo_dir, update_repo.claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False


def test_accept_rejects_protected_main_branch(accept_repo):
    """`accept` enforces the same protected-`main` rejection every mutating
    operation shares (design test 22); the predecessor stays `handoff-ready`
    and no successor is created."""
    checkout = _run_git(["checkout", "main"], cwd=accept_repo.repo_dir)
    assert checkout.returncode == 0, checkout.stderr

    result = _accept(accept_repo.repo_dir, accept_repo.predecessor_claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert not _history_path_for(accept_repo.repo_dir, accept_repo.predecessor_claim_id).exists()


def test_accept_rejects_wrong_canonical_worktree(accept_repo):
    """Accepting a `handoff-ready` claim from a DIFFERENT, linked worktree --
    rather than the exact canonical worktree recorded on the claim -- is an
    identity mismatch: exit `2`, the predecessor remains `handoff-ready`,
    and no successor or history record is created (design test 14: "Wrong
    agent, session, worktree, or branch cannot ... accept a valid claim";
    mirrors `test_update_rejects_wrong_canonical_worktree`)."""
    linked_worktree = accept_repo.repo_dir.parent / "accept-linked-worktree"
    worktree_result = _run_git(
        ["worktree", "add", "-b", "claude/continuity-pilot", str(linked_worktree)],
        cwd=accept_repo.repo_dir,
    )
    assert worktree_result.returncode == 0, worktree_result.stderr

    result = _accept(linked_worktree, accept_repo.predecessor_claim_id)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert parse_json_stdout(result)["ok"] is False
    assert _current_claim(accept_repo.repo_dir)["state"] == "handoff-ready"
    assert not _history_path_for(accept_repo.repo_dir, accept_repo.predecessor_claim_id).exists()


def test_accept_codex_can_accept_predecessor_claim_on_claude_prefixed_branch(tmp_path):
    """Design test 25 ("either agent can accept the other tool's prefix")
    requires BOTH directions. The existing happy-path accept tests already
    exercise a CLAUDE agent accepting a claim recorded on `continuity_repo`'s
    default `codex/`-prefixed branch; this is the missing REVERSE direction:
    a CODEX agent accepting a claim started, updated, and handed off
    entirely by CLAUDE on a `claude/<workstream-id>` branch. The
    branch-prefix rule is never tied to the ACCEPTING agent's own identity
    (design spec, "Branch validation": "The prefix identifies the branch's
    creator, not its current owner")."""
    repo_dir = _make_repo_on_branch(tmp_path, "claude/continuity-pilot", dir_name="claude-branch-repo")
    workstream_path = repo_dir / ".ai" / "workstreams" / "continuity-pilot.md"
    workstream_path.write_text(_full_workstream_document("continuity-pilot", "0" * 40), encoding="utf-8")
    add_result = _run_git(["add", "."], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Expand workstream document"], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    started = _start(
        repo_dir, Agent="claude", SessionId="claude-session-1",
        Scope=[".ai/workstreams/continuity-pilot.md", "shared"],
    )
    assert started.returncode == 0, f"stdout={started.stdout!r} stderr={started.stderr!r}"
    claim_id = parse_json_stdout(started)["claim_id"]
    next_action = "Hand off to Codex."

    update_result = _update(
        repo_dir, claim_id,
        Agent="claude", SessionId="claude-session-1",
        Summary="Ready to hand off.",
        ChangedPath=[".ai/workstreams/continuity-pilot.md"],
        State="handoff", NextAction=next_action,
        VerificationResult="not-run", NotRunReason="not run for this test",
    )
    assert update_result.returncode == 0, f"stdout={update_result.stdout!r} stderr={update_result.stderr!r}"
    add_result = _run_git(["add", "--", ".ai/workstreams/continuity-pilot.md"], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Ready to hand off."], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr

    handoff_result = _handoff(repo_dir, claim_id, next_action, Agent="claude", SessionId="claude-session-1")
    assert handoff_result.returncode == 0, f"stdout={handoff_result.stdout!r} stderr={handoff_result.stderr!r}"

    accept_result = _accept(repo_dir, claim_id, Agent="codex", SessionId="codex-session-1")

    assert accept_result.returncode == 0, f"stdout={accept_result.stdout!r} stderr={accept_result.stderr!r}"
    parsed = parse_json_stdout(accept_result)
    assert parsed["ok"] is True
    successor = parsed["claims"][0]
    assert successor["agent"] == "codex"
    assert successor["session_id"] == "codex-session-1"
    assert successor["branch"] == "claude/continuity-pilot"
    assert successor["state"] == "active"

    history_path = _history_path_for(repo_dir, claim_id)
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["final_state"] == "handed-off"
    assert history["successor_agent"] == "codex"


# ---------------------------------------------------------------------------
# Step 3: deterministic linked-worktree concurrency.
#
# Both tests force GENUINE, PROVEN contention rather than merely launching
# two processes back-to-back and hoping the OS interleaves them usefully:
# an external holder acquires the SAME common lock `Use-ContinuityLock`
# uses (reusing `_spawn_lock_holder`, already established for the Task 3
# lock-contention tests), THEN both `start` processes are launched -- each
# with its OWN distinct `AI_CONTINUITY_TEST_LOCK_BARRIER` marker path (Task
# 8 review Fix 2) -- while the external holder still holds the lock.
# `Use-ContinuityLock` writes that exact marker file IMMEDIATELY BEFORE its
# first exclusive-open attempt, so `_run_concurrent_start_round` waits
# (bounded, generous timeout) until BOTH marker files exist -- proving both
# `start` processes have actually reached `Use-ContinuityLock` and are now
# genuinely BLOCKED retrying against the identical OS-level lock the
# external holder still owns, never merely that both processes happened to
# be launched within some fixed window and got lucky with scheduling. Only
# after both markers are confirmed present (and asserted so) is the
# external holder released, by killing it -- which drops the OS handle
# immediately, exactly like
# `test_start_reacquires_lock_promptly_after_owner_process_is_killed`
# demonstrates -- guaranteeing both processes were genuinely contending for
# the lock at the instant it releases. For BOTH outcomes below (disjoint
# persistence, exactly-one-overlap-conflict) the assertion holds regardless
# of which of the two processes the OS happens to let acquire the released
# lock first. If the barrier hook were bypassed, disabled, or the helper
# stopped genuinely blocking on the shared lock, the marker files would
# never both appear and `_run_concurrent_start_round`'s own assertion below
# fails BEFORE either process's result is even inspected, instead of the
# test silently passing on a sequential (non-concurrent) execution.
# ---------------------------------------------------------------------------


def _spawn_start(cwd: Path, env: Optional[dict] = None, **parameters) -> subprocess.Popen:
    """Launch `start` as a background process through the same fixed pwsh
    wrapper `run_helper` uses, WITHOUT blocking for it to exit (mirrors
    `_spawn_accept`)."""
    pwsh = _find_pwsh()
    payload = {
        "ScriptPath": str(SCRIPT_PATH), "Operation": "start",
        "Agent": "codex", "SessionId": "session-1", "Json": True,
        **parameters,
    }
    run_env = {**os.environ, **(env or {})}
    process = subprocess.Popen(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", _HELPER_WRAPPER_SCRIPT],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=run_env,
    )
    process.stdin.write(json.dumps(payload))
    process.stdin.close()
    return process


@pytest.fixture()
def linked_worktree_pair(continuity_repo: Path):
    """Two linked worktrees of the SAME disposable repository sharing one
    Git common directory (design test 2), used to drive genuinely
    concurrent `start` invocations that must contend for the identical
    common lock from two different worktrees."""
    linked_worktree = continuity_repo.parent / "concurrency-linked-worktree"
    result = _run_git(
        ["worktree", "add", "-b", "codex/concurrency-bootstrap", str(linked_worktree), "main"],
        cwd=continuity_repo,
    )
    assert result.returncode == 0, result.stderr
    return continuity_repo, linked_worktree


def _await_both_lock_barrier_markers(
    marker_a: Path, marker_b: Path, timeout_seconds: float = 15.0
) -> bool:
    """Poll (bounded, generous) until BOTH lock-barrier marker files exist,
    proving both concurrent `start` processes have reached
    `Use-ContinuityLock` and are now genuinely blocking on the still-held
    external lock (Task 8 review Fix 2). Returns whether both appeared
    before the timeout; never raises itself, so the caller can assert with
    a clear, test-specific failure message instead of a bare poll timeout."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if marker_a.exists() and marker_b.exists():
            return True
        time.sleep(0.05)
    return marker_a.exists() and marker_b.exists()


def _run_concurrent_start_round(
    worktree_a: Path, worktree_b: Path, params_a: dict, params_b: dict, tmp_path: Path
) -> tuple:
    """Run one round of genuinely concurrent `start` calls (see the module
    comment above for the external-lock-holder-plus-barrier rendezvous
    technique) and return both results. Each child process is given its own
    distinct `AI_CONTINUITY_TEST_LOCK_BARRIER` marker path so the caller can
    prove -- rather than assume -- that both children were genuinely
    contending for the shared lock before it releases."""
    lock_path = _lock_path_for(worktree_a)
    holder = _spawn_lock_holder(lock_path, hold_seconds=20)
    marker_a = tmp_path / "lock-barrier-a.marker"
    marker_b = tmp_path / "lock-barrier-b.marker"
    process_a = None
    process_b = None
    try:
        process_a = _spawn_start(
            worktree_a, env={"AI_CONTINUITY_TEST_LOCK_BARRIER": str(marker_a)}, **params_a
        )
        process_b = _spawn_start(
            worktree_b, env={"AI_CONTINUITY_TEST_LOCK_BARRIER": str(marker_b)}, **params_b
        )

        both_contending = _await_both_lock_barrier_markers(marker_a, marker_b)
        assert both_contending, (
            "expected both `start` processes to write their lock-barrier "
            "marker file -- proving they reached and are genuinely blocking "
            "on the still-held common lock -- before this bounded wait "
            f"elapsed (marker_a exists={marker_a.exists()}, "
            f"marker_b exists={marker_b.exists()}); without this proof the "
            "concurrency assertions below would be vacuous"
        )

        # Both children are now proven to be genuinely contending for the
        # identical lock the external holder still owns. Release it now, by
        # killing the holder process rather than waiting for its own fixed
        # sleep to elapse: killing drops the OS file handle immediately
        # (see `test_start_reacquires_lock_promptly_after_owner_process_is_killed`),
        # so this round's correctness never depends on any fixed sleep
        # window resolving a particular way.
        holder.kill()
        holder.wait(timeout=15)
        stdout_a, stderr_a = process_a.communicate(timeout=20)
        stdout_b, stderr_b = process_b.communicate(timeout=20)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
        for process in (process_a, process_b):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    return (
        HelperResult(process_a.returncode, stdout_a, stderr_a),
        HelperResult(process_b.returncode, stdout_b, stderr_b),
    )


@pytest.mark.parametrize("round_index", range(3))
def test_start_concurrent_disjoint_claims_in_linked_worktrees_both_persist(
    linked_worktree_pair, round_index, tmp_path
):
    """Two DIFFERENT workstreams with non-overlapping scope, started
    GENUINELY concurrently from two linked worktrees sharing one Git common
    directory, both persist as live claims (design tests 2 and 5; Task 8
    brief, Step 3). Parametrized across multiple rounds with fresh
    workstream IDs each time to exercise the common lock's contention path
    more than once. `_run_concurrent_start_round` proves both processes
    were genuinely blocking on the shared lock before releasing it (Task 8
    review Fix 2)."""
    worktree_a, worktree_b = linked_worktree_pair
    workstream_a = f"disjoint-a-{round_index}"
    workstream_b = f"disjoint-b-{round_index}"
    _checkout_new_branch(worktree_a, f"codex/{workstream_a}")
    _checkout_new_branch(worktree_b, f"codex/{workstream_b}")

    result_a, result_b = _run_concurrent_start_round(
        worktree_a, worktree_b,
        params_a={"Workstream": workstream_a, "SessionId": "session-a", "Scope": [f"area-a-{round_index}"]},
        params_b={"Workstream": workstream_b, "SessionId": "session-b", "Scope": [f"area-b-{round_index}"]},
        tmp_path=tmp_path,
    )

    assert result_a.returncode == 0, f"stdout={result_a.stdout!r} stderr={result_a.stderr!r}"
    assert result_b.returncode == 0, f"stdout={result_b.stdout!r} stderr={result_b.stderr!r}"
    assert parse_json_stdout(result_a)["ok"] is True
    assert parse_json_stdout(result_b)["ok"] is True

    status = run_helper(worktree_a, "status", Json=True)
    assert status.returncode == 0, status.stderr
    workstream_ids = {claim["workstream_id"] for claim in parse_json_stdout(status)["claims"]}
    assert workstream_ids == {workstream_a, workstream_b}


@pytest.mark.parametrize("round_index", range(3))
def test_start_concurrent_overlapping_claims_in_linked_worktrees_exactly_one_succeeds(
    linked_worktree_pair, round_index, tmp_path
):
    """Two DIFFERENT workstreams whose scope prefixes overlap, started
    GENUINELY concurrently from two linked worktrees sharing one Git common
    directory: regardless of which process the OS lets acquire the shared
    lock first, EXACTLY ONE succeeds and the other returns the stable exit
    `2`, and the single surviving claim is whichever workstream actually won
    (design test 4; Task 8 brief, Step 3). `_run_concurrent_start_round`
    proves both processes were genuinely blocking on the shared lock before
    releasing it (Task 8 review Fix 2)."""
    worktree_a, worktree_b = linked_worktree_pair
    workstream_a = f"overlap-a-{round_index}"
    workstream_b = f"overlap-b-{round_index}"
    _checkout_new_branch(worktree_a, f"codex/{workstream_a}")
    _checkout_new_branch(worktree_b, f"codex/{workstream_b}")

    result_a, result_b = _run_concurrent_start_round(
        worktree_a, worktree_b,
        params_a={"Workstream": workstream_a, "SessionId": "session-a", "Scope": ["shared"]},
        params_b={"Workstream": workstream_b, "SessionId": "session-b", "Scope": ["shared/nested"]},
        tmp_path=tmp_path,
    )

    outcomes = {result_a.returncode, result_b.returncode}
    assert outcomes == {0, 2}, (
        f"expected exactly one success and one exit-2 conflict; got "
        f"a={result_a.returncode} stdout={result_a.stdout!r} stderr={result_a.stderr!r}; "
        f"b={result_b.returncode} stdout={result_b.stdout!r} stderr={result_b.stderr!r}"
    )

    status = run_helper(worktree_a, "status", Json=True)
    assert status.returncode == 0, status.stderr
    claims = parse_json_stdout(status)["claims"]
    assert len(claims) == 1
    winner_workstream = claims[0]["workstream_id"]
    assert winner_workstream in {workstream_a, workstream_b}

    winning_result = result_a if result_a.returncode == 0 else result_b
    winning_parsed = parse_json_stdout(winning_result)
    assert winning_parsed["claims"][0]["workstream_id"] == winner_workstream


# ---------------------------------------------------------------------------
# Step 4: serialization and security assertions
# ---------------------------------------------------------------------------


def test_start_claim_json_never_contains_injected_environment_value(continuity_repo):
    """Claims must never contain environment values (design spec, Local
    Claim Model: "Claims must not contain credentials, environment values,
    file contents..., or arbitrary command output"). Injecting a
    distinctive marker into the CHILD PROCESS's actual environment and
    asserting it is absent from both the returned JSON and the on-disk
    claim file proves the helper never serializes ambient environment
    state, not merely that it doesn't happen to today by coincidence."""
    marker = "SUPER-SECRET-ENV-MARKER-3f9c1e7b"
    result = _start(continuity_repo, env={"AI_CONTINUITY_TEST_SECRET_PROBE": marker})

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert marker not in result.stdout
    assert marker not in result.stderr

    claim_path = _claim_file_for(continuity_repo, "continuity-pilot")
    assert marker not in claim_path.read_text(encoding="utf-8")


def test_start_captures_preexisting_dirty_content_as_hash_only_never_raw_bytes(tmp_path):
    """Pre-existing out-of-scope dirty files are fingerprinted by SHA-256
    only (design spec, Local Claim Model: "No file contents are stored").
    Writing a distinctive marker string into an untracked out-of-scope file
    and asserting it never appears anywhere in the returned JSON or the
    on-disk claim proves content is never captured, not merely that the
    schema happens to lack a content field today."""
    repo_dir = tmp_path / "content-leak-repo"
    _init_repo(repo_dir)
    _write_minimal_ai_records(repo_dir)
    add_result = _run_git(["add", "."], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Initial commit"], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr
    checkout_result = _run_git(["checkout", "-b", "codex/continuity-pilot"], cwd=repo_dir)
    assert checkout_result.returncode == 0, checkout_result.stderr

    marker = "TOP-SECRET-FILE-CONTENT-MARKER-8b2e"
    (repo_dir / "out-of-scope.txt").write_text(marker, encoding="utf-8")

    result = _start(repo_dir, Scope=["shared"])

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert marker not in result.stdout
    assert marker not in result.stderr

    claim_path = _claim_file_for(repo_dir, "continuity-pilot")
    assert marker not in claim_path.read_text(encoding="utf-8")

    parsed = parse_json_stdout(result)
    preexisting = parsed["claims"][0]["preexisting_dirty"]
    assert any(entry["path"] == "out-of-scope.txt" for entry in preexisting)


def test_error_message_control_characters_are_json_escaped_not_raw(continuity_repo):
    """A validation error's message may legitimately quote invalid input
    verbatim for diagnostics, but the JSON transport itself must never leak
    a literal, UNESCAPED control character onto stdout/stderr --
    `ConvertTo-Json` escapes it as `\\u00XX`, so terminal/log injection
    through a crafted workstream ID is impossible even though the message
    text quotes the offending value (Task 8 brief, Step 4: "Error output
    never echoes unsafe raw input containing control characters")."""
    unsafe_workstream = "bad\x07id\x1bmore"
    result = _start(continuity_repo, Workstream=unsafe_workstream, Json=True)

    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "\x07" not in result.stdout
    assert "\x1b" not in result.stdout
    assert "\x07" not in result.stderr
    assert "\x1b" not in result.stderr
    # The escaped forms ARE present -- the value is not silently dropped,
    # only safely transported.
    assert "\\u0007" in result.stdout
    assert "\\u001b" in result.stdout

    parsed = parse_json_stdout(result)
    assert parsed["ok"] is False
    assert parsed["errors"], "expected a validation error"


def test_claim_json_key_order_is_deterministic_across_independent_claims(continuity_repo):
    """Claim JSON's top-level key order must be deterministic run-to-run --
    two INDEPENDENTLY constructed claims (different workstreams, same
    repository) have the IDENTICAL key order, proving it never depends on
    unstable hashtable/property enumeration (Task 8 brief, Step 4:
    "Claim/history/journal JSON is deterministic")."""
    first = _start(continuity_repo, Workstream="continuity-pilot", Scope=["area-a"])
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

    _checkout_new_branch(continuity_repo, "codex/other-workstream")
    second = _start(continuity_repo, Workstream="other-workstream", SessionId="session-2", Scope=["area-b"])
    assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"

    first_keys = list(json.loads(
        _claim_file_for(continuity_repo, "continuity-pilot").read_text(encoding="utf-8")
    ).keys())
    second_keys = list(json.loads(
        _claim_file_for(continuity_repo, "other-workstream").read_text(encoding="utf-8")
    ).keys())

    assert first_keys == second_keys
    assert set(first_keys) == START_CLAIM_KEYS


def test_accept_history_json_key_order_is_deterministic_across_independent_transactions(tmp_path):
    """Two INDEPENDENT `accept` transactions, each built from its own fresh
    disposable repository, produce history records with the IDENTICAL
    top-level key order -- proving the serialization is deterministic
    rather than dependent on unstable hashtable/property enumeration order.
    A same-repository idempotent-retry comparison (already covered by
    `test_accept_after_archive_retry_does_not_rewrite_history_record`)
    could not show this on its own, since plain dict `==` in Python ignores
    key order entirely (Task 8 brief, Step 4)."""
    first_repo = _make_repo_on_branch(tmp_path, "codex/continuity-pilot", dir_name="history-order-repo-1")
    second_repo = _make_repo_on_branch(tmp_path, "codex/continuity-pilot", dir_name="history-order-repo-2")

    first_fixture = _make_accept_fixture(_make_handoff_fixture(first_repo))
    second_fixture = _make_accept_fixture(_make_handoff_fixture(second_repo))

    first_accept = _accept(first_fixture.repo_dir, first_fixture.predecessor_claim_id)
    assert first_accept.returncode == 0, f"stdout={first_accept.stdout!r} stderr={first_accept.stderr!r}"
    second_accept = _accept(second_fixture.repo_dir, second_fixture.predecessor_claim_id)
    assert second_accept.returncode == 0, f"stdout={second_accept.stdout!r} stderr={second_accept.stderr!r}"

    first_history_keys = list(json.loads(
        _history_path_for(first_fixture.repo_dir, first_fixture.predecessor_claim_id).read_text(encoding="utf-8")
    ).keys())
    second_history_keys = list(json.loads(
        _history_path_for(second_fixture.repo_dir, second_fixture.predecessor_claim_id).read_text(encoding="utf-8")
    ).keys())

    assert first_history_keys == second_history_keys
