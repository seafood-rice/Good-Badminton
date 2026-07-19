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
    hidden mutation"; test list item 12)."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    calls = INVOKE_GIT_CALL_PATTERN.findall(source)
    assert calls, "expected at least one Invoke-Git call site to scan"
    for call_arguments in calls:
        tokens = QUOTED_TOKEN_PATTERN.findall(call_arguments)
        prohibited_hits = PROHIBITED_GIT_TOKENS.intersection(tokens)
        assert not prohibited_hits, (
            f"Invoke-Git call arguments {call_arguments!r} include prohibited "
            f"mutating token(s): {prohibited_hits}"
        )
