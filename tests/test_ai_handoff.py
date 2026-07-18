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

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import NamedTuple, Sequence

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


def run_helper(cwd: Path, operation: str, **parameters) -> HelperResult:
    """Invoke scripts/ai-handoff.ps1 as a black box through the fixed pwsh
    wrapper above.

    All parameters (including PowerShell switches like ``Json=True`` and
    arrays like ``Scope=[".ai/", "scripts/"]``) travel as one JSON object on
    stdin, so the real array/splat mechanics are exercised without building
    any PowerShell source from test-controlled strings.
    """
    pwsh = _find_pwsh()
    payload = {"ScriptPath": str(SCRIPT_PATH), "Operation": operation, **parameters}
    completed = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-Command", _HELPER_WRAPPER_SCRIPT],
        cwd=cwd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
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


@pytest.fixture()
def continuity_repo(tmp_path: Path) -> Path:
    """A disposable Git repository shaped like the real project: `main` with
    minimal `.ai` records committed, then checked out on the
    `codex/continuity-pilot` workstream branch (Task 2 brief, Step 1)."""
    repo_dir = tmp_path / "continuity-repo"
    _init_repo(repo_dir)
    _write_minimal_ai_records(repo_dir)

    add_result = _run_git(["add", "."], cwd=repo_dir)
    assert add_result.returncode == 0, add_result.stderr
    commit_result = _run_git(["commit", "-m", "Initial commit"], cwd=repo_dir)
    assert commit_result.returncode == 0, commit_result.stderr
    checkout_result = _run_git(["checkout", "-b", "codex/continuity-pilot"], cwd=repo_dir)
    assert checkout_result.returncode == 0, checkout_result.stderr

    return repo_dir


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
