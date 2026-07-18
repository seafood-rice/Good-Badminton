# Claude-Codex Continuity Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install a portable, tested continuity layer that lets Claude and Codex safely
alternate on one Good Badminton branch or work in parallel linked worktrees while preserving
unrelated dirty files and requiring committed handoffs.

**Architecture:** Tracked Markdown files hold shared workflow and durable status, while live
claims and transaction journals live under the clone's absolute Git common directory. One
PowerShell 7 helper provides read-only status plus validated `start`, `update`, `handoff`,
`accept`, and `takeover` operations. Pytest drives the helper as a black box in disposable Git
repositories and linked worktrees, including deterministic fault injection at transaction
boundaries. Only `update` may edit the bounded fields of the owned workstream document; every
operation leaves Git refs, history, and index unchanged.

**Tech Stack:** PowerShell 7.5, Git, JSON, Markdown, the repository `.venv` (currently Python
3.12.4), and pytest 8+ (currently 9.1.1); no new dependency.

## Global Constraints

- Execute this plan on `codex/good-badminton-development`, the approved backfill exception.
  Never write, commit, merge, rebase, force-update, or push `main`.
- Tasks 1-8 are an installation bootstrap. The helper cannot govern its own creation, so use
  exact path staging and disposable repositories until it is committed and verified. Create
  the first real claim only in Task 9 after the claimed project scope is clean.
- Preserve the existing deleted assets and all unrelated untracked files. Never run
  `git add -A`, `git add -u`, `git add .`, `git stash`, `git reset`, `git clean`, or a wildcard
  pathspec. Never change a file merely to make the repository globally clean.
- The implementation may stage only the exact portable allowlist in the approved design.
  `.claude/settings.local.json`, `.claude/.ccteams-manifest.json`, `.codex/config.toml`,
  `.tokensave/`, logs, model weights, caches, and absolute-path manifests remain local.
- Before each commit, require an initially empty index, stage explicit `$TaskPaths`, compare
  the complete staged set to `$TaskPaths`, and run `git diff --cached --check`:

```powershell
if ((git diff --cached --name-only).Count -ne 0) {
    throw 'Index must be empty before staging this task.'
}

git add -- $TaskPaths
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$staged = @(git diff --cached --name-only)
$difference = @(Compare-Object ($TaskPaths | Sort-Object) ($staged | Sort-Object))
if ($difference.Count -ne 0) {
    git diff --cached --name-status
    throw 'Staged paths differ from the exact task allowlist.'
}

git diff --cached --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

- Do not push implementation commits during Tasks 1-9. After implementation and review, use
  the repository shipper flow to present the branch and exact push command for user approval.
- Use UTF-8 without BOM, forward slashes in serialized repository paths, full 40-character Git
  object IDs, RFC3339 UTC timestamps, and case-aware comparisons matching the filesystem.
- Every public operation returns one stable exit code and, with `-Json`, exactly one JSON
  object. Never emit secrets, arbitrary command output, claim file contents, or environment
  values.
- The only test hook is `AI_CONTINUITY_TEST_FAULT`. It is inactive unless explicitly set by a
  test, is never serialized or printed, and accepts only the fixed boundary names listed in
  Tasks 3 and 5-7. Unknown values fail before mutation.
- Run tests with a unique writable `--basetemp`, disabled pytest cache, and the repository
  virtual environment. Use `AI_CONTINUITY_TEST_TMP` when the managed environment provides a
  specific writable root; otherwise preflight the system temp directory before running:

```powershell
$env:PYTHONUTF8 = '1'
$env:WINDIR = 'C:\Windows'
$testTmpRoot = if ($env:AI_CONTINUITY_TEST_TMP) {
    [IO.Path]::GetFullPath($env:AI_CONTINUITY_TEST_TMP)
} else {
    [IO.Path]::GetTempPath()
}
[IO.Directory]::CreateDirectory($testTmpRoot) | Out-Null
$probe = Join-Path $testTmpRoot ("write-probe-" + [guid]::NewGuid())
[IO.File]::WriteAllText($probe, 'ok')
[IO.File]::Delete($probe)
$tmp = Join-Path $testTmpRoot ("ai-continuity-" + [guid]::NewGuid())
.\.venv\Scripts\python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp $tmp
```

---

## File Structure

- `AGENTS.md` - Codex entry point with one RTK include and lowercase `.codex` reference.
- `CLAUDE.md` - Claude entry point loading the same shared workflow and project status.
- `RTK.md` - existing imported portable RTK instructions, tracked without machine-local data.
- `.ai/WORKFLOW.md` - stable tool-neutral operating contract.
- `.ai/PROJECT_STATUS.md` - integration milestone snapshot, not an activity log.
- `.ai/workstreams/continuity-pilot.md` - helper-managed continuity workstream record.
- `.ai/workstreams/match-stroke-recognition-b.md` - planned Sub-project B record.
- `.claude/active-team.md`, `.claude/agents/*.md` - portable Claude team configuration.
- `.codex/active-team.md`, `.codex/agents/*.toml` - portable Codex team configuration.
- `scripts/ai-handoff.ps1` - single continuity CLI and all internal state logic.
- `tests/test_ai_handoff.py` - isolated black-box contract, Git, worktree, and fault tests.

---

### Task 1: Install the portable shared records and entry points

**Files:**
- Create: `.ai/WORKFLOW.md`
- Create: `.ai/PROJECT_STATUS.md`
- Create: `.ai/workstreams/continuity-pilot.md`
- Create: `.ai/workstreams/match-stroke-recognition-b.md`
- Create: `tests/test_ai_handoff.py`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Track unchanged after portability review: `RTK.md`
- Track portable team files: `.claude/active-team.md`, `.claude/agents/architect.md`,
  `.claude/agents/builder.md`, `.claude/agents/qa-reviewer.md`,
  `.claude/agents/scope-planner.md`, `.claude/agents/shipper.md`,
  `.codex/active-team.md`, `.codex/agents/architect.toml`, `.codex/agents/builder.toml`,
  `.codex/agents/qa-reviewer.toml`, `.codex/agents/scope-planner.toml`,
  `.codex/agents/shipper.toml`

**Interfaces:**
- `AGENTS.md` contains exactly `@RTK.md`, `@.ai/WORKFLOW.md`,
  `@.ai/PROJECT_STATUS.md`, and `@.codex/active-team.md`, once each and in that order.
- `CLAUDE.md` contains exactly `@.ai/WORKFLOW.md`, `@.ai/PROJECT_STATUS.md`, and
  `@.claude/active-team.md`, once each and in that order.
- Both workstream files contain one managed marker pair and all durable fields from the
  approved design. The marker text is byte-exact.
- `PROJECT_STATUS.md` records fresh execution-time Git evidence, the fork and protected
  integration state, the two initial workstreams, links to the approved spec and this plan,
  and the historical ledger. It does not claim that an uncommitted snapshot is pushed.

- [ ] **Step 1: Record the bootstrap baseline.** Run `git status --short --branch`,
  `git rev-parse HEAD`, `git rev-parse main`, `git rev-parse '@{upstream}'`, and
  `git remote -v`. Save the command output in the implementation session notes, not in a new
  repository file. Confirm the index is empty and the branch is not `main`.

- [ ] **Step 2: Write failing reference and portability tests.** Start
  `tests/test_ai_handoff.py` with `PROJECT_ROOT`, the exact 21-path portable implementation
  allowlist from the spec, and tests named
  `test_root_instruction_references_resolve_once`,
  `test_instruction_files_use_lowercase_codex_reference`,
  `test_workstream_files_have_one_managed_marker_pair`,
  `test_portable_team_files_contain_no_absolute_project_path`, and
  `test_candidate_staged_path_outside_allowlist_fails_audit`.

  The staged-path test initializes a temporary repository, stages one allowlisted file plus
  `server.log`, runs the same set-comparison audit used before commits, and asserts a non-zero
  result naming only `server.log` as extra.

- [ ] **Step 3: Run the focused tests and confirm RED.** Use the Global Constraints pytest
  command. Expected: failures for missing `.ai` files, duplicate/wrong-case `AGENTS.md`
  references, and absent tracked portable records; the audit helper test itself passes.

- [ ] **Step 4: Normalize the root entry points.** Replace the current duplicate
  `AGENTS.md` blocks with the exact four includes above. Add the two shared includes before
  the existing Claude team include. Do not copy Claude-only instructions into `AGENTS.md` or
  Codex-only instructions into `CLAUDE.md`.

- [ ] **Step 5: Create the shared workflow.** Write `.ai/WORKFLOW.md` with the sources of
  truth, startup sequence, protected-branch rules, branch naming, bootstrap exception,
  alternating and parallel modes, claim requirement, dirty-file protections, milestone
  triggers, committed handoff/accept flow, takeover rules, stable helper exits, and the rule
  that the helper never creates worktrees or runs Git mutation commands.

- [ ] **Step 6: Create durable status records.** Populate `PROJECT_STATUS.md` from the fresh
  Step 1 evidence. Set `continuity-pilot` to `active` with the next action "Implement the
  read-only helper core from Task 2". Set `match-stroke-recognition-b` to `planned` and paused
  until the continuity pilot is installed and Sub-project B's product completion bar is
  approved. Include this exact managed section in each workstream file:

```text
<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
```

  Each workstream also has explicit `State`, `Branch`, `Worktree`, `Base commit`, `Head
  commit`, `Last milestone`, `Scope paths`, `Changed paths`, `Verification`, `Blockers`, and
  `Next action` fields. Record the head observed before the file mutation, per the design.

- [ ] **Step 7: Audit the portable team files.** Confirm all ten role definitions and both
  team files contain role behavior only, no secret, no local username, and no absolute
  project path. Preserve role semantics. Track those exact files, `RTK.md`, and no local
  settings or manifests.

- [ ] **Step 8: Run tests GREEN.** Expected: all Task 1 tests pass.

- [ ] **Step 9: Commit the portable foundation.** Set `$TaskPaths` to the exact files listed
  by this task, including `tests/test_ai_handoff.py`; run the global staging audit, inspect
  `git diff --cached`, then commit:

```powershell
git commit -m "Install shared Claude-Codex continuity records"
```

---

### Task 2: Build the read-only helper core and black-box harness

**Files:**
- Create: `scripts/ai-handoff.ps1`
- Modify: `tests/test_ai_handoff.py`

**Interfaces:**
- Script invocation is `pwsh -NoProfile -File scripts/ai-handoff.ps1
  status|start|update|handoff|accept|takeover [named parameters]`.
- For multiple array values in an interactive PowerShell session, invoke the script directly
  and pass a real array, for example
  `& ./scripts/ai-handoff.ps1 start -Scope @('.ai/', 'scripts/')`. The Python black-box
  harness passes a JSON object on stdin to a fixed `pwsh -Command` wrapper, converts it with
  `ConvertFrom-Json -AsHashtable`, and splats that hashtable into the script. This avoids shell
  interpolation and must be tested with two values for `Scope`, `ChangedPath`, and
  `VerificationDirtyPath`.
- The script declares all public parameters from the approved helper contract, with the
  operation as required position 0 and `-Json` as a switch.
- Internal boundaries are named `Invoke-Git`, `Get-RepositoryContext`, `Normalize-Id`,
  `Normalize-ScopePath`, `Read-ContinuityState`, `New-OperationResult`, `Write-Result`, and
  `Invoke-Status`. Later tasks extend these boundaries instead of adding another script.
- `status` never acquires a write lock and never creates `.git/ai-continuity`.

- [ ] **Step 1: Add the disposable repository harness.** Add pytest fixtures that locate
  `pwsh`, initialize `git init -b main`, configure a local test identity, create and commit
  minimal `.ai` records, create `codex/continuity-pilot`, and invoke the project helper with
  `cwd` set to the disposable worktree. For subprocess-safe arrays, send a JSON object on
  stdin to a fixed wrapper that removes `ScriptPath` and `Operation`, then invokes
  `& $scriptPath $operation @parameters`; do not construct PowerShell source from test input.
  Capture raw stdout, stderr, and return code. A JSON helper must reject extra non-JSON stdout.

- [ ] **Step 2: Write failing read-only tests.** Add tests named
  `test_status_returns_exit_4_outside_git`, `test_status_json_has_stable_top_level_shape`,
  `test_status_is_available_on_protected_main`,
  `test_status_does_not_create_continuity_directory`,
  `test_status_reports_shared_git_common_directory`, and
  `test_status_reports_malformed_claim_as_exit_3`.

  Assert the exact top-level JSON keys from the design, `operation == "status"`, full Git
  SHAs, canonical absolute `repo_root` and `git_common_dir`, and the required error/warning
  distinction.

- [ ] **Step 3: Run and confirm RED.** Expected: helper-not-found failures.

- [ ] **Step 4: Implement parameter binding and stable output.** Use one top-level
  `try/catch/finally` that maps known validation, malformed-state, and Git-context exceptions
  to exits 2, 3, and 4. Default output is concise human-readable text; `-Json` serializes one
  object at sufficient depth. Do not let diagnostic PowerShell streams corrupt JSON stdout.

- [ ] **Step 5: Implement read-only Git discovery.** Resolve `--show-toplevel`,
  `--git-common-dir`, current worktree path, branch, `HEAD`, upstream, and protected branch
  through a `System.Diagnostics.Process` Git wrapper that preserves NUL bytes for later
  commands. Canonicalize relative common-dir output against the worktree. Detached `HEAD` is
  reportable by `status` and rejected only by mutating operations.

- [ ] **Step 6: Parse continuity state without mutation.** Read claims, history, and pending
  transactions when present. Missing directories mean empty state. Malformed JSON,
  duplicate active workstream files, or malformed required durable records produce exit 3.
  Expiry and ordinary Git drift are warnings; overlapping live claims make `status` exit 2.

- [ ] **Step 7: Parse-check and run GREEN.** Run:

```powershell
pwsh -NoProfile -Command '$null = [scriptblock]::Create((Get-Content -Raw "scripts/ai-handoff.ps1"))'
```

  Then run the focused pytest command. Expected: all Task 1-2 tests pass.

- [ ] **Step 8: Commit.** Set `$TaskPaths` to `scripts/ai-handoff.ps1` and
  `tests/test_ai_handoff.py`, run the global audit, inspect the diff, then commit:

```powershell
git commit -m "Add read-only AI continuity status helper"
```

---

### Task 3: Add safe claims, locking, path validation, and dirty fingerprints

**Files:**
- Modify: `scripts/ai-handoff.ps1`
- Modify: `tests/test_ai_handoff.py`

**Interfaces:**
- `start -Agent <claude|codex> -SessionId <id> -Workstream <id> -Scope <string[]>
  [-LeaseHours <1..24>] [-Json]` creates or idempotently renews one active claim.
- Active claims live at `<git-common-dir>/ai-continuity/claims/<workstream-id>.json`.
- Internal additions: `Use-ContinuityLock`, `Write-JsonAtomic`, `Get-GitStatusEntries`,
  `Get-IndexEntries`, `Get-DirtyFingerprint`, `Test-ScopeOverlap`, `Test-ClaimIdentity`, and
  `Invoke-Start`.
- Fixed fault names: `start-before-claim-replace` and `start-after-claim-replace`.

- [ ] **Step 1: Write failing validation and visibility tests.** Add tests for valid ID
  limits, every invalid workstream/session form, case-insensitive Windows device names,
  empty/root/traversal/wildcard/drive/UNC/`.git` scopes, and symlink/junction/reparse escapes.
  Assert invalid input returns 2 before creating any continuity directory. Always create an
  unprivileged directory junction with `New-Item -ItemType Junction`; capability-gate only the
  additional symbolic-link case when Windows developer mode or link privilege is unavailable.

- [ ] **Step 2: Write failing branch tests.** Cover protected `main`, detached `HEAD`, wrong
  prefix/suffix, both valid prefixes, either agent operating the other prefix, and the exact
  `codex/good-badminton-development` plus `continuity-pilot` exception. No other
  workstream may use that exception.

- [ ] **Step 3: Write failing claim tests.** Add idempotent same-identity start, scope-change
  rejection, wrong identity, linked-worktree common visibility, overlapping prefix failure,
  disjoint concurrent claims, lock contention, and atomic-write failure preserving prior
  state. Use the fixed start fault boundaries rather than platform-dependent permission
  changes for the atomic-write cases. Before claim replacement, the old/empty state is
  authoritative. After claim replacement, the new claim is authoritative, the operation
  returns 3 with its claim ID and exact same-identity `start` retry command, and retry renews
  that claim without creating another.

- [ ] **Step 4: Write failing dirty-state tests.** Cover modified, added, deleted, renamed,
  untracked, unmerged, executable-mode, absent tracked, symlink, and reparse entries. Assert
  in-scope dirt blocks start, while out-of-scope dirt is captured with status, destination,
  rename source, kind, SHA-256 or null, and all stage/mode/object-ID index tuples. Assert the
  helper never reads through a link target.

- [ ] **Step 5: Run and confirm RED.** Expected: `start` is not implemented.

- [ ] **Step 6: Implement validation before filesystem mutation.** Normalize IDs and scope
  prefixes exactly as the spec requires, resolve the nearest existing ancestor, reject all
  reparse components, and compare paths at component boundaries. Derive case sensitivity
  from the repository filesystem while always serializing forward slashes.

- [ ] **Step 7: Implement the common lock and atomic writes.** Keep one persistent lock path
  under the Git common directory and acquire ownership by opening it as an exclusive
  `FileStream` with bounded retry. File existence is never ownership; process termination
  releases the handle, so another process can reacquire without deleting a stale marker.
  Add a killed-lock-owner test that reacquires within the fixed timeout. Serialize to a
  same-directory unique temporary file, flush, then atomically replace the target. Failed
  writes retain either the readable old file or the complete new file, never a partial file.

- [ ] **Step 8: Implement exact dirty fingerprints.** Parse
  `git status --porcelain=v1 -z -uall` without losing rename sources. Query
  `git ls-files --stage -z -- <path>` for complete index tuples. Hash regular-file bytes;
  record `absent` plus null for missing tracked paths; fingerprint link metadata without
  traversal. Sort deterministically before JSON serialization.

- [ ] **Step 9: Implement `start`.** Under the common lock, re-read all claims, fail on live
  overlap, allow only identical idempotent renewal, capture all pre-existing out-of-scope
  dirt, reject in-scope dirt, and atomically write the schema-1 claim with an eight-hour
  default lease. `status` derives expiry without rewriting the claim.

- [ ] **Step 10: Run GREEN and verify no Git mutation.** Add a spy Git executable or wrapper
  assertion proving helper Git calls exclude `add`, `commit`, `checkout`, `switch`, `branch`,
  `worktree add/remove`, `stash`, `reset`, `clean`, `merge`, `rebase`, and `push`. Run focused
  tests. Expected: all Task 1-3 tests pass.

- [ ] **Step 11: Commit.** Audit and commit the two task paths:

```powershell
git commit -m "Add scoped AI continuity claims"
```

---

### Task 4: Add durable milestone updates and verification validation

**Files:**
- Modify: `scripts/ai-handoff.ps1`
- Modify: `tests/test_ai_handoff.py`

**Interfaces:**
- `update -ClaimId <id> -Agent <claude|codex> -SessionId <id> -Summary <text>
  [-ChangedPath <string[]>] [-State <active|blocked|handoff>] [-NextAction <text>]
  -VerificationResult <passed|failed|not-run> [-VerificationCommand <text>]
  [-VerificationCommit <sha>] [-VerificationDirtyPath <string[]>]
  [-NotRunReason <text>] [-Json]`.
- Internal additions: `Read-WorkstreamDocument`, `Write-WorkstreamDocument`,
  `Test-VerificationArguments`, `Test-DirtyVerificationPaths`, and `Invoke-Update`.
- Only the managed milestone section plus explicit `Last milestone`, `Head commit`, `State`,
  and `Next action` fields may change.

- [ ] **Step 1: Write failing identity and document-shape tests.** Wrong claim ID, agent,
  session, branch, or canonical worktree returns 2 without lease renewal or Markdown change.
  Missing/duplicate managed markers and malformed required fields return 3 without mutation.

- [ ] **Step 2: Write failing verification-matrix tests.** Parameterize every allowed and
  forbidden combination for `passed`, `failed`, and `not-run`. Require full `HEAD` SHA plus
  command for clean passed/failed evidence; require full `HEAD`, command, and currently dirty
  in-scope paths for dirty evidence; require only a reason for `not-run`. Reject literal
  `dirty`, non-dirty paths, out-of-scope paths, omitted dirty paths, and stale commits.

- [ ] **Step 3: Write failing mutation-boundary tests.** Snapshot the complete file before
  `update`, run a valid milestone, and assert all bytes outside the allowed fields and marker
  region are unchanged. Assert only the owned workstream file changes and the claim lease is
  renewed after the durable file succeeds.

- [ ] **Step 4: Run and confirm RED.** Expected: update-not-implemented failures.

- [ ] **Step 5: Implement strict workstream parsing.** Parse required top-level fields and
  exactly one marker pair. Preserve original newline style and untouched bytes. The helper
  appends one concise timestamped milestone containing summary, state, changed paths,
  verification, blockers when state is blocked, and exact next action when present.

- [ ] **Step 6: Implement validation and write ordering.** Validate identity, branch, inputs,
  verification evidence, and dirty paths before writing. Atomically update the workstream
  first; only then renew and atomically rewrite the claim. A Markdown failure leaves the claim
  unchanged. If the later claim renewal fails, the old claim remains authoritative and the
  durable milestone remains applied. Return exit 3 with `durable_update_applied: true`, the
  authoritative claim ID, and the exact identical-scope `start` renewal command. Recovery is
  `start`, never rerunning `update`, so the milestone cannot be duplicated. Test this boundary,
  the result shape, and successful lease recovery explicitly.

- [ ] **Step 7: Run GREEN.** Expected: all Task 1-4 tests pass.

- [ ] **Step 8: Commit.** Audit and commit the helper and test paths:

```powershell
git commit -m "Add durable AI continuity milestones"
```

---

### Task 5: Enforce committed handoffs and exact Git reconciliation

**Files:**
- Modify: `scripts/ai-handoff.ps1`
- Modify: `tests/test_ai_handoff.py`

**Interfaces:**
- `handoff -ClaimId <id> -Agent <claude|codex> -SessionId <id>
  -NextAction <text> [-Json]` validates tracked evidence and changes only the active claim
  from `active` to `handoff-ready`.
- Internal additions: `Get-CommittedNameStatus`, `Test-PreexistingDirtyUnchanged`,
  `Read-CommittedWorkstream`, `Test-HandoffCoverage`, and `Invoke-Handoff`.
- Fixed fault names: `handoff-after-validate` and `handoff-after-claim-rewrite`.

- [ ] **Step 1: Write failing committed-state tests.** Require the owned workstream file at
  `HEAD`, state `handoff`, exact next action, changed-path coverage including itself, full
  current commit, and clean claimed scope. Uncommitted status edits, stale status blobs,
  omitted paths, or a dirty claimed path return 2 and keep the active claim.

- [ ] **Step 2: Write failing reconciliation tests.** Cover modified, added, deleted,
  renamed, copied, untracked, pre-existing, committed in-scope, committed out-of-scope, and
  new out-of-scope paths. Parse `git diff --name-status -z <base>..HEAD` and include both sides
  of rename/copy records. Every committed in-scope path must be listed; every committed path
  must be in scope; unchanged pre-existing out-of-scope dirt remains allowed.

- [ ] **Step 3: Write failing fingerprint-drift tests.** Change only status, kind, content,
  index mode/stage/object ID, or rename source of captured pre-existing dirt. Each change must
  block handoff without changing ownership.

- [ ] **Step 4: Write failing boundary tests.** At `handoff-after-validate`, assert no state
  mutation and idempotent retry. At `handoff-after-claim-rewrite`, assert the
  `handoff-ready` claim is authoritative and blocks all starts; retry reports it without
  creating a new claim.

- [ ] **Step 5: Run and confirm RED.** Expected: handoff-not-implemented failures.

- [ ] **Step 6: Implement committed reconciliation.** Read the committed workstream through
  `git show HEAD:<path>`, obtain its blob OID through `git rev-parse HEAD:<path>`, compute its
  SHA-256 from those exact committed bytes, and capture full `HEAD`. Re-fingerprint all
  pre-existing dirt and reject any mismatch. Do not edit a tracked file.

- [ ] **Step 7: Implement the monotonic handoff transition.** Hold the lock through final
  validation and atomic active-claim rewrite. Populate `durable_status_path`,
  `durable_status_sha256`, `durable_status_blob_oid`, and `handoff_commit`; then set
  `state: handoff-ready`. Implement fixed fault hooks without printing their environment
  source or value.

- [ ] **Step 8: Run GREEN and compare repository tree before/after handoff.** Except for the
  Git-common claim file, the worktree, index, refs, and tracked files must be byte-identical.

- [ ] **Step 9: Commit.** Audit and commit:

```powershell
git commit -m "Require committed AI agent handoffs"
```

---

### Task 6: Add acceptance transactions and successor-owned recovery

**Files:**
- Modify: `scripts/ai-handoff.ps1`
- Modify: `tests/test_ai_handoff.py`

**Interfaces:**
- `accept -PreviousClaimId <id> -Agent <claude|codex> -SessionId <id> [-Json]` creates one
  successor, activates it, revalidates Git, archives its predecessor, and removes the journal
  only after cross-reference validation.
- Journal path:
  `<git-common-dir>/ai-continuity/transactions/accept-<old-claim-id>.json`.
- Fixed fault names: `accept-after-prepare`, `accept-after-activate`,
  `accept-pause-after-activate`, `accept-after-revalidate`, and `accept-after-archive`.
- `accept-pause-after-activate` uses fixed files beside the transaction:
  `<journal>.ready` and `<journal>.continue`; it times out safely and removes test controls.

- [ ] **Step 1: Write failing acceptance validation tests.** Require exact predecessor ID,
  `handoff-ready` state, same canonical worktree and branch, unchanged `HEAD`, committed blob
  and SHA-256, clean claimed scope, and unchanged pre-existing dirt. Acceptance after lease
  expiry is allowed only when all evidence still matches.

- [ ] **Step 2: Write failing successor tests.** Assert a new GUID claim inherits workstream,
  branch, worktree, normalized scope, and pre-existing dirt; sets predecessor, accepting
  identity, fresh lease, `base_commit == handoff_commit`, and active state; and clears
  handoff-only fields. Assert immutable history records `final_state: handed-off` and exact
  successor cross-references.

- [ ] **Step 3: Write failing fault/retry tests.** Inject each fixed boundary. Before
  activation, predecessor remains authoritative. After activation, successor remains
  authoritative. Re-running the exact accept command reuses the journal and never creates a
  second successor. History and active files must be readable at every boundary.

- [ ] **Step 4: Write the deterministic concurrent-drift test.** Launch accept with
  `accept-pause-after-activate`, wait for `<journal>.ready`, mutate and commit the disposable
  worktree from the test, write `<journal>.continue`, and assert exit 5. JSON recovery must
  contain the successor claim ID, `authoritative_owner: successor`, transaction path/state,
  and the exact retry command. The predecessor never becomes active again.

- [ ] **Step 5: Run and confirm RED.** Expected: accept-not-implemented failures.

- [ ] **Step 6: Implement the accept journal state machine.** Under the lock, resume an
  existing prepared transaction when present; otherwise revalidate and atomically prepare
  one complete predecessor/successor journal. Atomically activate the successor, revalidate
  Git, archive the predecessor, validate both cross-references, and only then delete the
  journal. Never create an unowned interval.

- [ ] **Step 7: Implement exit-5 recovery.** Post-activation Git drift retains the successor
  and journal, returns one `ok: false` result with exact recovery fields, and supports the
  exact idempotent retry. Ordinary post-activation atomic failures return 3 with the same
  authoritative-owner facts.

- [ ] **Step 8: Run GREEN.** Expected: all Task 1-6 tests pass, including genuine concurrent
  Git drift.

- [ ] **Step 9: Commit.** Audit and commit:

```powershell
git commit -m "Add recoverable AI handoff acceptance"
```

---

### Task 7: Add explicit expired-claim takeover and immutable history

**Files:**
- Modify: `scripts/ai-handoff.ps1`
- Modify: `tests/test_ai_handoff.py`

**Interfaces:**
- `takeover -Agent <claude|codex> -SessionId <id> -Workstream <id>
  -PreviousClaimId <id> -Reason <text> -Scope <string[]> [-LeaseHours <1..24>] [-Json]`.
- Journal path:
  `<git-common-dir>/ai-continuity/transactions/takeover-<old-claim-id>.json`.
- Fixed fault names: `takeover-after-prepare`, `takeover-after-activate`, and
  `takeover-after-archive`.

- [ ] **Step 1: Write failing takeover validation tests.** A live claim cannot be replaced.
  An expired active or handoff-ready claim requires the exact ID, non-empty reason, valid
  identity/scope/branch, clean requested scope, and unchanged pre-existing dirt. A stale or
  guessed ID fails without mutation.

- [ ] **Step 2: Write failing replacement/history tests.** Assert the successor gets a new
  claim ID, `replaces_claim_id`, replacement reason, new identity and lease, while preserving
  branch and worktree. Its normalized scope comes from the validated supplied `-Scope`, not
  the predecessor scope. First require every predecessor dirty fingerprint to remain
  unchanged; then reject dirt newly inside the supplied scope and capture a fresh complete
  out-of-scope dirty baseline for the successor. Preserve the predecessor's old scope and
  dirty evidence only in immutable `final_state: replaced` history, cross-referenced to the
  replacement.

- [ ] **Step 3: Write failing fault/retry tests.** Inject every fixed takeover boundary.
  Before activation the old claim remains authoritative; after activation the replacement
  remains authoritative. Exact retry finishes one archive and removes the journal. An expired
  handoff-ready claim remains blocking until valid accept or takeover.

- [ ] **Step 4: Run and confirm RED.** Expected: takeover-not-implemented failures.

- [ ] **Step 5: Implement the takeover state machine.** Reuse validation, locking,
  fingerprint, atomic-write, and journal primitives from earlier tasks. Resume prepared
  transactions idempotently. Never infer completion from a journal alone; the active file is
  current ownership and history is completed evidence.

- [ ] **Step 6: Run GREEN.** Expected: all Task 1-7 tests pass.

- [ ] **Step 7: Commit.** Audit and commit:

```powershell
git commit -m "Add explicit expired AI claim takeover"
```

---

### Task 8: Close the complete contract and failure matrix

**Files:**
- Modify: `scripts/ai-handoff.ps1`
- Modify: `tests/test_ai_handoff.py`

**Interfaces:**
- Every design test numbered 1-27 has one or more named black-box pytest cases.
- Every operation and documented condition has an asserted exit code and JSON shape.
- Human output carries the same warning/error meaning without leaking JSON internals.

- [ ] **Step 1: Add a traceability table in a test comment.** Map design tests 1-27 to exact
  pytest test names. Do not mark a requirement covered unless the test invokes the helper or
  staging audit and asserts the durable filesystem/Git result.

- [ ] **Step 2: Fill uncovered matrix cases.** In particular, cover ordinary status warnings
  at exit 0, overlapping live claims at exit 2, malformed markers/state and lock timeout at
  exit 3, non-worktree at exit 4, post-activation drift at exit 5, recipient cross-prefix
  acceptance, new out-of-scope dirt, expired handoff-ready accept/takeover, and all prohibited
  Git mutation commands.

- [ ] **Step 3: Add deterministic concurrency tests.** Start simultaneous disjoint claims in
  two linked worktrees and assert both persist. Start simultaneous overlapping claims and
  assert exactly one succeeds while the other returns 2. Repeat enough times to exercise the
  common lock without relying on sleeps for correctness.

- [ ] **Step 4: Add serialization and security assertions.** Claim/history/journal JSON is
  deterministic and contains no credentials, environment variables, file contents, log
  output, or control-file values. Error output never echoes unsafe raw input containing
  control characters.

- [ ] **Step 5: Parse-check and run the focused suite twice.** First run the whole focused
  suite. Then rerun transaction/concurrency tests alone to catch state leakage:

```powershell
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ai-continuity-repeat-" + [guid]::NewGuid())
.\.venv\Scripts\python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp $tmp -k "accept or takeover or concurrent or fault"
```

  Expected: both runs pass with no real project claim directory created or changed.

- [ ] **Step 6: Run `git diff --check` and inspect scope.** Confirm only the helper and test
  differ in this task and unrelated dirt matches the bootstrap snapshot.

- [ ] **Step 7: Commit.** Audit and commit:

```powershell
git commit -m "Complete AI continuity safety matrix"
```

---

### Task 9: Verify and activate the Good Badminton pilot

**Files:**
- Modify: `.ai/PROJECT_STATUS.md`
- Modify: `.ai/workstreams/continuity-pilot.md`

**Interfaces:**
- Project status records the tested implementation commit, exact verification commands and
  UTC results, fork branch state, blockers/deferred work, and the next Sub-project B action.
- The first real claim covers only `.ai/workstreams/continuity-pilot.md`, so it cannot block a
  future disjoint Sub-project B claim.

- [ ] **Step 1: Run the full focused gate.** Parse-check the PowerShell script and run all
  `tests/test_ai_handoff.py` tests with a unique temp directory. Record exact command, result,
  full tested commit, and UTC timestamp.

- [ ] **Step 2: Run the existing full suite.** Use:

```powershell
$testTmpRoot = if ($env:AI_CONTINUITY_TEST_TMP) {
    [IO.Path]::GetFullPath($env:AI_CONTINUITY_TEST_TMP)
} else {
    [IO.Path]::GetTempPath()
}
$tmp = Join-Path $testTmpRoot ("good-badminton-full-" + [guid]::NewGuid())
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp $tmp
```

  Expected: the existing 356 tests plus all new continuity tests pass; any optional-model
  skip is recorded exactly rather than hidden.

- [ ] **Step 3: Run a disposable two-worktree smoke.** In a temporary repository and linked
  worktree, manually invoke human and JSON forms of `status`, disjoint and overlapping
  `start`, `update`, committed `handoff`, `accept`, expired active takeover, expired
  handoff-ready accept/takeover, and post-activation drift recovery. Delete only the
  disposable test repository after resolving and verifying its absolute path.

- [ ] **Step 4: Review the implementation.** Delegate a whole-branch correctness and safety
  review to `qa-reviewer`, covering the approved spec, exact allowlist, transaction
  invariants, dirty-file preservation, and test genuineness. Resolve every blocking finding
  with another RED/GREEN cycle and rerun the affected gates.

- [ ] **Step 5: Record integration evidence before real claims.** Update project and
  continuity workstream status with all fresh evidence, the current implementation `HEAD`,
  fork remote state, exact changed paths, and next action. Set the next Sub-project B action
  to "Approve the full-match stroke-recognition completion bar, then create its implementation
  plan". Commit only the two status files after the exact audit:

```powershell
git commit -m "Record continuity pilot verification"
```

- [ ] **Step 6: Reverify the committed status snapshot.** The Task 4 verification contract
  rejects stale commit evidence, so rerun both gates after the Step 5 status commit and build
  the exact evidence string used by the real handoff:

```powershell
$verificationCommit = (git rev-parse HEAD).Trim()
$focusedTmp = Join-Path $testTmpRoot ("continuity-handoff-focused-" + [guid]::NewGuid())
$fullTmp = Join-Path $testTmpRoot ("continuity-handoff-full-" + [guid]::NewGuid())

.\.venv\Scripts\python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp $focusedTmp
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp $fullTmp
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$verificationCommand = "focused: .\.venv\Scripts\python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp `"$focusedTmp`"; full: .\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp `"$fullTmp`""
if ((git rev-parse HEAD).Trim() -ne $verificationCommit) {
    throw 'HEAD changed during verification.'
}
```

- [ ] **Step 7: Start the first real scoped claim.** Confirm the continuity workstream file
  is clean, then invoke it directly from PowerShell so `Scope` is a real array:

```powershell
$startJson = & .\scripts\ai-handoff.ps1 start `
  -Agent codex `
  -SessionId continuity-pilot-codex `
  -Workstream continuity-pilot `
  -Scope @('.ai/workstreams/continuity-pilot.md') `
  -Json
if ($LASTEXITCODE -ne 0) { throw $startJson }
$startResult = $startJson | ConvertFrom-Json
$codexClaimId = $startResult.claim_id
if (-not $codexClaimId) { throw 'Start returned no claim ID.' }
```

  Expected: exit 0, one active claim, and warnings listing but not changing all pre-existing
  out-of-scope dirt.

- [ ] **Step 8: Exercise a real committed Codex-to-Claude handoff.** Run the complete update,
  exact-path commit, handoff, and acceptance sequence:

```powershell
$nextToClaude = 'Accept the continuity pilot, verify shared status, then hand it back to Codex'
$updateJson = & .\scripts\ai-handoff.ps1 update `
  -ClaimId $codexClaimId `
  -Agent codex `
  -SessionId continuity-pilot-codex `
  -Summary 'Continuity pilot implementation and verification complete' `
  -ChangedPath @('.ai/workstreams/continuity-pilot.md') `
  -State handoff `
  -NextAction $nextToClaude `
  -VerificationResult passed `
  -VerificationCommand $verificationCommand `
  -VerificationCommit $verificationCommit `
  -Json
if ($LASTEXITCODE -ne 0) { throw $updateJson }

$TaskPaths = @('.ai/workstreams/continuity-pilot.md')
# Run the exact global staging audit here.
git commit -m "Prepare continuity pilot handoff to Claude"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$codexHandoffJson = & .\scripts\ai-handoff.ps1 handoff `
  -ClaimId $codexClaimId `
  -Agent codex `
  -SessionId continuity-pilot-codex `
  -NextAction $nextToClaude `
  -Json
if ($LASTEXITCODE -ne 0) { throw $codexHandoffJson }

$acceptClaudeJson = & .\scripts\ai-handoff.ps1 accept `
  -PreviousClaimId $codexClaimId `
  -Agent claude `
  -SessionId continuity-pilot-claude `
  -Json
if ($LASTEXITCODE -ne 0) { throw $acceptClaudeJson }
$acceptClaudeResult = $acceptClaudeJson | ConvertFrom-Json
$claudeClaimId = $acceptClaudeResult.claim_id
if (-not $claudeClaimId) { throw 'Claude acceptance returned no successor claim ID.' }
```

  Confirm predecessor history and the successor active claim cross-reference each other.

- [ ] **Step 9: Hand ownership back to Codex.** As the Claude identity, verify shared status,
  append and commit the return handoff, then accept it as Codex:

```powershell
$statusCommand = '& .\scripts\ai-handoff.ps1 status -Workstream continuity-pilot -Json'
$statusJson = & .\scripts\ai-handoff.ps1 status -Workstream continuity-pilot -Json
if ($LASTEXITCODE -ne 0) { throw $statusJson }
$null = $statusJson | ConvertFrom-Json
$claudeVerificationCommit = (git rev-parse HEAD).Trim()
$nextToCodex = "Approve Sub-project B's completion bar and plan the per-rally dense pre-pass"

$returnUpdateJson = & .\scripts\ai-handoff.ps1 update `
  -ClaimId $claudeClaimId `
  -Agent claude `
  -SessionId continuity-pilot-claude `
  -Summary 'Claude accepted and verified the shared continuity state' `
  -ChangedPath @('.ai/workstreams/continuity-pilot.md') `
  -State handoff `
  -NextAction $nextToCodex `
  -VerificationResult passed `
  -VerificationCommand $statusCommand `
  -VerificationCommit $claudeVerificationCommit `
  -Json
if ($LASTEXITCODE -ne 0) { throw $returnUpdateJson }

$TaskPaths = @('.ai/workstreams/continuity-pilot.md')
# Run the exact global staging audit here.
git commit -m "Return continuity pilot handoff to Codex"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$claudeHandoffJson = & .\scripts\ai-handoff.ps1 handoff `
  -ClaimId $claudeClaimId `
  -Agent claude `
  -SessionId continuity-pilot-claude `
  -NextAction $nextToCodex `
  -Json
if ($LASTEXITCODE -ne 0) { throw $claudeHandoffJson }

$acceptCodexJson = & .\scripts\ai-handoff.ps1 accept `
  -PreviousClaimId $claudeClaimId `
  -Agent codex `
  -SessionId continuity-pilot-codex-next `
  -Json
if ($LASTEXITCODE -ne 0) { throw $acceptCodexJson }
$acceptCodexResult = $acceptCodexJson | ConvertFrom-Json
if (-not $acceptCodexResult.claim_id) { throw 'Codex acceptance returned no successor claim ID.' }
```

  Confirm the final active claim is scoped only to the continuity workstream and both
  predecessor history records cross-reference correctly.

- [ ] **Step 10: Final audit.** Run `git diff --check`, focused tests, full pytest, fresh Git
  status, branch tracking, and exact staged-path checks. Confirm no local setting, log, model,
  cache, deleted asset, archive ref, or protected `main` update entered any commit.

- [ ] **Step 11: Prepare the reviewed branch for publication.** Delegate to `shipper` for
  logical commit review and the exact non-force push command for
  `origin/codex/good-badminton-development`. Do not push until the user explicitly approves
  that publication step.

---

## Requirement Traceability

| Design requirements | Primary plan coverage |
|---|---|
| Shared instruction resolution and portable allowlist | Task 1 |
| Git common-directory visibility, status, malformed state | Task 2 |
| ID/path safety, branch protection, locking, claims, dirt | Task 3 |
| Owned durable milestones and verification matrix | Task 4 |
| Committed clean-scope handoff and path reconciliation | Task 5 |
| Acceptance, successor authority, exit-5 recovery | Task 6 |
| Expiry, takeover, immutable replacement history | Task 7 |
| Stable exits, complete tests 1-27, concurrency, security | Task 8 |
| Full suite, two-worktree smoke, actual alternating cycle | Task 9 |

## Self-Review

**Spec coverage:** All helper operations, state schemas, stable exits, branch rules, dirty
fingerprints, workstream markers, transaction boundaries, failure recovery, the exact portable
scope, alternating work, parallel work, and tests 1-27 map to implementation tasks above.
The Good Badminton pilot is activated only after isolated verification, preserving the design's
bootstrap and no-hidden-mutation constraints.

**Completeness scan:** Every product field, algorithm, test boundary, and fixed fault name is
specified. Live commit IDs, upstream state, and UTC timestamps are intentionally read from Git
and the clock at execution time because stale hard-coded evidence would violate the spec.

**Type consistency:** Workstream IDs, session IDs, scope arrays, full commit SHAs, dirty path
arrays, claim/history/journal objects, operation outputs, and exit codes use the same names and
shapes across Tasks 2-9 and the approved design. `handoff` transfers no ownership,
`accept` creates one successor, `takeover` replaces only an expired exact predecessor, and the
active claim file remains authoritative at every transaction boundary.
