# Claude-Codex Project Continuity - Design Spec

**Date:** 2026-07-16
**Status:** Design approved; awaiting written-spec review before implementation planning.

## Goal

Make Good Badminton a pilot repository that Claude and Codex can safely continue from
one another, whether they alternate in one working tree or work concurrently in linked
Git worktrees. Both tools must see the same durable project intent, progress, decisions,
verification evidence, blockers, and next actions without replacing their tool-specific
instructions.

The pilot must preserve the existing dirty working tree and Superpowers history. It must
not stage, commit, revert, move, or delete unrelated user files.

## Rollout Scope

This design applies only to Good Badminton. If it remains reliable through Sub-project B,
its templates and audit logic can be adapted for the other 12 repositories under
`D:/Dev/Claude/Projects` that currently contain `CLAUDE.md`.

Supporting all 45 Git repositories immediately is out of scope.

## Current State

- Integration branch: `master`.
- Current recorded integration commit: `5d2ae2c`.
- Match Analysis UX Sub-project A is complete.
- Last recorded verification for Sub-project A: 356 tests plus a runtime smoke test.
- The existing session work is unpushed.
- `.superpowers/sdd/progress.md` is the detailed historical ledger.
- Sub-project B, full-match stroke recognition, is next but its product completion bar
  remains unresolved. That decision is outside this continuity pilot and does not block
  implementing the pilot.
- The working tree contains pre-existing deleted and untracked files. They belong to the
  user and must not be swept into continuity commits.

Implementation must refresh these facts from Git and the progress ledger rather than
assuming this snapshot is still current.

## Design Principles

1. **One durable intent, two tool entry points.** Claude and Codex retain separate root
   instruction files but load the same continuity workflow and project status.
2. **Separate coordination from evidence.** Status records intent; Git records actual
   changes; fresh command output records verification; specs and plans record approved
   requirements.
3. **Parallel work is isolated.** Concurrent agents use separate branches/worktrees and
   non-overlapping path-prefix claims.
4. **No hidden mutation.** Continuity tooling never creates branches/worktrees or runs
   `stash`, `reset`, `clean`, `add`, `commit`, `push`, or destructive filesystem commands.
5. **Milestone updates, not activity logs.** Durable status changes after meaningful
   decisions, edits, tests, commits, blockers, and before a stop or handoff.
6. **Preserve existing systems.** The large Superpowers ledger remains historical evidence;
   the continuity layer links to it instead of copying it.
7. **No new dependency.** Use Markdown, JSON, PowerShell, Git, and the existing pytest
   stack.

## Sources Of Truth

When two artifacts disagree, agents use this division of authority:

| Question | Authority |
|---|---|
| What should be built? | Approved spec and implementation plan |
| What code/files currently exist? | Filesystem and fresh Git inspection |
| What has been verified? | Fresh command output tied to a commit or dirty state |
| Who owns current work and what happens next? | Live claim plus durable workstream status |
| What is the integrated project state? | `.ai/PROJECT_STATUS.md` |
| What happened historically? | `.superpowers/sdd/progress.md` and Git history |

Status text must never override contradictory Git or verification evidence. Agents update
stale status instead of treating it as fact.

## Repository Layout

### Shared tracked files

- `.ai/WORKFLOW.md` - stable tool-neutral operating contract.
- `.ai/PROJECT_STATUS.md` - concise integration-branch snapshot.
- `.ai/workstreams/<workstream-id>.md` - durable branch-owned workstream state.
- `scripts/ai-handoff.ps1` - idempotent continuity helper.
- `tests/test_ai_handoff.py` - isolated Git/worktree behavior tests.
- `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md` - this design.

### Existing portable instruction files

- `AGENTS.md` - Codex entry point.
- `CLAUDE.md` - Claude entry point.
- `RTK.md` - shared imported RTK instructions required by the current project setup.
- `.codex/active-team.md` and the five Codex agent definitions - portable Codex team
  configuration.
- `.claude/active-team.md` and the five Claude agent definitions - portable Claude team
  configuration.

Use lowercase `.codex` in repository references because that is the actual directory and
case-sensitive clones must resolve it. Include `RTK.md` once in `AGENTS.md`; remove the
current duplicate include blocks.

### Local-only state

Live claims are stored under the absolute Git common directory:

```text
<git-common-dir>/ai-continuity/claims/<workstream-id>.json
```

An atomic coordination lock also lives under `<git-common-dir>/ai-continuity/`. These files
are shared by linked worktrees and are not committed.

Local settings, absolute-path manifests, caches, logs, secrets, `.claude/settings.local.json`,
`.claude/.ccteams-manifest.json`, and `.codex/config.toml` are excluded from the portable
pilot commit.

### Portable implementation allowlist

The pilot implementation may stage only these paths:

```text
AGENTS.md
CLAUDE.md
RTK.md
.ai/WORKFLOW.md
.ai/PROJECT_STATUS.md
.ai/workstreams/continuity-pilot.md
.ai/workstreams/match-stroke-recognition-b.md
.claude/active-team.md
.claude/agents/architect.md
.claude/agents/builder.md
.claude/agents/qa-reviewer.md
.claude/agents/scope-planner.md
.claude/agents/shipper.md
.codex/active-team.md
.codex/agents/architect.toml
.codex/agents/builder.toml
.codex/agents/qa-reviewer.toml
.codex/agents/scope-planner.toml
.codex/agents/shipper.toml
scripts/ai-handoff.ps1
tests/test_ai_handoff.py
```

The design-spec commit is separate and may stage only this specification. Before every
pilot commit, compare `git diff --cached --name-only` against the applicable exact
allowlist and fail the audit if any extra path appears. Adding another path requires an
explicit user-approved scope update; wildcard pathspecs are prohibited.

## Instruction Entry Points

The root files retain tool-specific content but load the same continuity documents.

`AGENTS.md`:

```text
@RTK.md
@.ai/WORKFLOW.md
@.ai/PROJECT_STATUS.md
@.codex/active-team.md
```

`CLAUDE.md`:

```text
@.ai/WORKFLOW.md
@.ai/PROJECT_STATUS.md
@.claude/active-team.md
```

The shared workflow instructs both tools to inspect the relevant workstream file and fresh
Git state before changing files. The project status links workstream files rather than
embedding their complete histories.

## Durable Status Model

### Project status

`.ai/PROJECT_STATUS.md` is updated only at integration milestones. Concurrent feature
branches do not repeatedly edit it, avoiding a permanent merge-conflict hotspot.

Required fields and sections:

- Schema version and last-updated UTC timestamp.
- Integration branch, integration commit, and remote state: `pushed`, `unpushed`,
  `diverged`, or `no-upstream`.
- Current objective, phase, and acceptance criteria.
- Workstream table containing ID, state, branch, base/head commits, status-file link,
  exact next action, and updated UTC timestamp.
- Durable decisions with links to governing specs or plans.
- Verification records containing exact command, result, tested commit or dirty-state
  description, and UTC timestamp.
- Blockers and deferred work.
- Immediate integration next actions.
- Links to `.superpowers/sdd/progress.md` and relevant specs/plans.

### Workstream status

Each parallel workstream owns one `.ai/workstreams/<workstream-id>.md` file. Other active
workstreams do not edit it.

Required fields and sections:

- `workstream_id` and objective.
- Repository-relative path-prefix scope.
- State: `planned`, `active`, `blocked`, `handoff`, or `merged`.
- Branch, worktree path, base commit, and head commit.
- Last material milestone timestamp in UTC.
- Acceptance criteria or a link to the approved spec/plan.
- Decisions and rationale.
- Changed paths.
- Verification evidence using exact commands and results.
- Blockers and deferred work.
- Exact next action that another agent can execute without reconstructing intent.

Live ownership is not stored in tracked Markdown because it becomes stale across branches.

## Local Claim Model

Each claim JSON contains:

```json
{
  "schema_version": 1,
  "claim_id": "unique-id",
  "workstream_id": "continuity-pilot",
  "agent": "codex",
  "session_id": "tool-session-id-or-user-supplied-id",
  "worktree_path": "absolute-path",
  "branch": "branch-name",
  "base_commit": "full-commit-sha",
  "scope_paths": [".ai/", "scripts/ai-handoff.ps1"],
  "started_utc": "RFC3339 timestamp",
  "heartbeat_utc": "RFC3339 timestamp",
  "lease_until_utc": "RFC3339 timestamp",
  "state": "active",
  "preexisting_dirty_paths": ["repository-relative-path"],
  "replaces_claim_id": null,
  "replacement_reason": null,
  "durable_status_path": null,
  "durable_status_sha256": null
}
```

Scope entries are normalized repository-relative path prefixes, not globs. Two claims
overlap when either normalized prefix contains the other at a path boundary. Case
comparison follows the filesystem behavior while serialized paths use forward slashes.

Claims must not contain credentials, environment values, generated logs, or arbitrary
command output.

### Identifier and path validation

- Workstream IDs must match `[a-z0-9][a-z0-9-]{0,63}`. They cannot contain separators,
  dots, whitespace, control characters, or reserved Windows filename characters. Also
  reject the case-insensitive Windows device names `CON`, `PRN`, `AUX`, `NUL`, `COM1`
  through `COM9`, and `LPT1` through `LPT9`.
- Claim IDs are generated GUIDs. Session IDs must match
  `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`.
- Scope paths must be non-empty repository-relative prefixes. Reject empty/root scope,
  `.`, `..`, traversal segments, wildcards, drive-qualified paths, absolute paths, UNC
  paths, NUL/control characters, `.git`, and any `.git/` descendant.
- Resolve the repository root and each scope's nearest existing ancestor to canonical
  absolute paths. Reject a scope unless the canonical path remains below the repository
  root at a path boundary.
- Reject any scope whose existing path components contain a symbolic link, junction, or
  other reparse point. The pilot does not attempt to prove containment through links.
- Validate IDs and paths before creating directories, lock files, claims, or workstream
  files. Invalid input returns exit `2` with no filesystem mutation.

### Claim ownership identity

A claim ID is not an authentication token. `update` and `handoff` require the recorded
agent and session ID, and the helper derives the current canonical worktree path, branch,
and Git common directory. All values must match the active claim. A mismatch returns exit
`2` and does not renew, mutate, or release the claim.

### Claim lifecycle and history

Active claim files use `state: active` or `state: handoff-ready`. Expiry is a derived
lease condition reported by `status`; the read-only command never rewrites `state`.

Completed local audit records are retained indefinitely for the pilot under:

```text
<git-common-dir>/ai-continuity/history/<claim-id>.json
```

History records copy the claim and add `final_state` (`released` or `replaced`),
`ended_utc`, `durable_status_path`, `durable_status_sha256`, and, for replacement,
`replacement_claim_id` and `replacement_reason`.

| From | Trigger | Durable result | Active-file result |
|---|---|---|---|
| none | successful `start` | none | new `active` claim |
| `active` live | same identity runs `start` or `update` | optional milestone | lease renewed |
| `active` expired | `status` | none | unchanged; reported expired |
| `active` expired | successful `takeover` | takeover transaction removed after old claim is archived `replaced` | new `active` claim replaces old claim |
| `active` | handoff status written | workstream hash recorded | remains `active` until next step |
| `active` | handoff marker written | unchanged | becomes `handoff-ready` |
| `handoff-ready` | release archived | history record `released` | remains blocking until deletion |
| `handoff-ready` | release completes | history retained | active file removed |

No other transition is valid. A `handoff-ready` claim blocks new claims exactly like an
active claim. History is never used as live ownership.

## Helper Contract

`scripts/ai-handoff.ps1` provides these operations:

- `status [-Workstream <id>] [-Json]` - read-only report of Git state,
  project/workstream state, claims, malformed claims, overlaps, and expired leases.
- `start -Agent <claude|codex> -SessionId <id> -Workstream <id>
  -Scope <path-prefix[]> [-AcknowledgeDirty] [-LeaseHours <1..24>] [-Json]` - validate
  startup state and create a scoped lease. The default lease is eight hours. Capture
  existing dirty paths. Repeating the same agent/session/workstream claim in the same
  worktree with the identical normalized scope is idempotent and renews it. A scope change
  returns exit `2` and requires a handoff/release or expired-claim takeover.
- `update -ClaimId <id> -Agent <claude|codex> -SessionId <id> -Summary <text>
  [-ChangedPath <path[]>]
  -VerificationResult <passed|failed|not-run> [-VerificationCommand <text>]
  [-VerificationCommit <sha>] [-VerificationDirtyPath <path[]>]
  [-NotRunReason <text>] [-Json]` - renew the lease and
  append a material milestone to only the owned workstream file. `not-run` requires
  `-NotRunReason`.
- `handoff -ClaimId <id> -Agent <claude|codex> -SessionId <id> -NextAction <text>
  [-Json]` - verify claim identity, reconcile fresh Git changes with durable workstream
  state, write the exact next action, mark the claim handoff-ready, and release it only
  after the durable write succeeds.
- `takeover -Agent <claude|codex> -SessionId <id> -Workstream <id>
  -PreviousClaimId <id> -Reason <text> -Scope <path-prefix[]> [-AcknowledgeDirty]
  [-LeaseHours <1..24>] [-Json]` - replace an expired claim and record its identity and
  replacement reason. A live claim cannot be taken over.

PowerShell array parameters accept a normal comma-separated array. Repository paths are
normalized before comparison and serialization.

Workstream templates contain a helper-managed section bounded by:

```text
<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
```

`update` and `handoff` may edit only that bounded section plus the template's explicit
`Last milestone`, `Head commit`, `State`, and `Next action` fields. Missing or duplicate
markers fail validation; the helper never guesses where to write.

Verification parameters follow this matrix:

| Result | Required | Forbidden |
|---|---|---|
| `passed` or `failed`, clean tree | command and full tested commit SHA | not-run reason, dirty paths |
| `passed` or `failed`, relevant dirty tree | command, full HEAD SHA, and one or more repository-relative dirty paths | not-run reason |
| `not-run` | reason | command, commit, dirty paths |

The helper verifies that supplied dirty paths are currently dirty, within the claim scope,
and listed in the owned workstream file. A literal `dirty` placeholder is invalid.

Default output is a concise human-readable report. `-Json` emits one JSON object with:
`schema_version`, `ok`, `operation`, `repo_root`, `git_common_dir`, `workstream_id`,
`claim_id`, `git`, `claims`, `warnings`, and `errors`. Secrets and environment values are
never included.

Exit codes are stable:

- `0` - success, including read-only status with warnings.
- `2` - validation error, dirty-scope acknowledgement required, or claim conflict.
- `3` - malformed continuity state, lock timeout, or atomic-write failure.
- `4` - not a Git worktree or Git common directory unavailable.

Every mutating operation returns non-zero without partial mutation on validation failure.

Operation outcomes are unambiguous:

| Condition | Exit | JSON | Mutation |
|---|---:|---|---|
| Healthy operation | 0 | `ok: true` | documented mutation only |
| `status` finds expiry, Git drift, or ordinary dirty paths | 0 | `ok: true`, item in `warnings` | none |
| Any operation finds malformed claim JSON, duplicate/missing managed markers, or malformed required durable state | 3 | `ok: false`, item in `errors` | none |
| Live overlap, identity mismatch, unsafe input, invalid verification parameters, or missing dirty acknowledgement | 2 | `ok: false`, item in `errors` | none |
| `status` finds overlapping live claims | 2 | `ok: false`, conflict in `errors` | none |
| Lock timeout or atomic-write failure | 3 | `ok: false`, item in `errors` | prior or recoverable state described below |
| Git root/common directory unavailable | 4 | `ok: false`, item in `errors` | none |

Human-readable output reports the same warnings/errors as JSON. Malformed state is never
reported as a successful warning.

The helper does not automatically commit status files. Agents use normal explicit Git
operations only when authorized by the user and repository instructions.

## Agent Workflow

### Startup

1. Resolve repository root, worktree, branch, HEAD, and Git common directory.
2. Read `.ai/WORKFLOW.md`, `.ai/PROJECT_STATUS.md`, and the relevant workstream file.
3. Inspect fresh `git status`, recent commits, relevant spec/plan, and active claims.
4. For write work, acquire a scoped claim before editing.
5. Report pre-existing dirty paths and require explicit acknowledgement for dirty paths
   inside the requested scope.

Read-only questions and diagnostics do not require a claim.

### Milestones

Update durable workstream state after:

- A material implementation change.
- A design or scope decision.
- A test, build, lint, benchmark, or smoke result.
- A commit or branch-base change.
- Discovery of a blocker or deferred dependency.
- Any pause, stop, agent switch, or user-requested handoff.

Do not append command-by-command activity. Verification records identify the tested commit;
if the tree is dirty, they identify the relevant changed paths. If verification was not
run, record `not run` and the reason.

### Alternating work

Claude and Codex may use the same worktree sequentially. The current owner must update the
workstream, complete `handoff`, and release its claim before the next agent starts write
work. Uncommitted changes are allowed only when they are explicitly listed in the handoff.

At handoff, the helper reads fresh `git status --porcelain=v1 -z` and the committed
`base_commit..HEAD` name-status diff, including both sides of renames. Every observed path
must be either:

- A path captured as pre-existing at `start` and still classified as pre-existing; or
- Inside the live claim scope and listed in the workstream's changed paths.

A pre-existing in-scope path requires startup acknowledgement and must be listed in the
workstream if it remains dirty. Any new out-of-scope change, omitted in-scope change, or
unsafe path makes handoff fail with exit `2`; the claim stays active.

### Parallel work

Concurrent agents use separate branches/worktrees and disjoint path-prefix claims. Each
agent edits only its own workstream status. Integration updates project status after the
workstream is merged or otherwise incorporated.

## Failure And Conflict Handling

- Claim writes use an atomic common-directory lock and temporary-file rename.
- A lock timeout, malformed claim, unavailable Git common directory, or failed durable
  handoff returns non-zero and preserves either prior state or the recoverable monotonic
  handoff state defined below.
- Overlapping live path claims fail without modification.
- Dirty paths that were already outside scope at `start` are reported and allowed. A new
  out-of-scope path discovered at handoff is a conflict and fails with exit `2`.
- Dirty paths inside scope require explicit acknowledgement and are never altered by the
  helper.
- A crash leaves a claim until its lease expires. Expired claims are reported and never
  silently deleted.
- Takeover requires the exact prior claim ID and records which claim it replaced and why.
- A failed handoff keeps the claim active so ownership is not lost before durable state is
  valid.
- Status/Git drift is reported, not silently corrected.
- Separate clones or machines cannot see local claims. This pilot coordinates one clone
  plus its linked worktrees only.

### Handoff write ordering and recovery

Handoff holds the common lock through local claim/history writes. The tracked workstream
file cannot share that filesystem transaction, so handoff is monotonic and idempotently
recoverable rather than pretending to be one atomic transaction:

1. Validate identity, scope, Git reconciliation, and the proposed workstream content.
2. Atomically replace the tracked workstream file and compute its SHA-256.
3. Atomically rewrite the active claim as `handoff-ready` with that path and hash.
4. Atomically create the immutable `released` history record without deleting the active
   claim.
5. Delete the active claim only after the history record is readable and hash-consistent.

| Failure boundary | Authoritative ownership and retry behavior |
|---|---|
| Before step 2 | Prior workstream and active claim remain authoritative. |
| After step 2, before step 3 | Updated workstream exists, but active claim still owns the scope. Rerun verifies the content/hash and resumes. |
| After step 3, before step 4 | `handoff-ready` claim still blocks others. Rerun archives it. |
| After step 4, before step 5 | Released history exists, but active file still blocks others. Rerun verifies IDs/hashes and removes only that active file. |
| After step 5 | Handoff is complete; history is the audit record and no live owner remains. |

Takeover uses an explicit transaction journal under
`<git-common-dir>/ai-continuity/transactions/takeover-<old-claim-id>.json`:

1. Under the lock, atomically write a `prepared` transaction containing the complete old
   and proposed new claims.
2. Atomically replace the active file with the new claim. The active file is always the
   authority for current ownership.
3. Archive the old claim to immutable history with `final_state: replaced`.
4. Delete the transaction only after the active claim and history record are readable and
   cross-reference each other.

If failure occurs before step 2, the old active claim remains authoritative and retry uses
the prepared transaction. If failure occurs after step 2, the new active claim is
authoritative and retry uses the transaction's old-claim snapshot to finish the archive.
If failure occurs after step 3, retry only validates the cross-references and removes the
transaction. A pending transaction is never interpreted as completed history.

## Testing

Use pytest to invoke the PowerShell helper against isolated temporary Git repositories and
linked worktrees. No real project claim state is mutated by automated tests.

Required tests:

1. Both root instruction files resolve all shared referenced documents.
2. A linked worktree sees claims written from the main worktree through the Git common
   directory.
3. `start` is idempotent for the same claim/worktree.
4. Overlapping scopes fail and preserve the original claim.
5. Disjoint scopes can be claimed concurrently.
6. Pre-existing dirty files are captured and never changed.
7. In-scope dirty files require explicit acknowledgement.
8. Lease expiry does not delete a claim; explicit takeover records replacement metadata.
9. Malformed JSON and lock contention fail closed without corrupting prior state.
10. `update` changes only the owned workstream status.
11. `handoff` persists and validates durable state before releasing ownership.
12. The helper never invokes prohibited Git mutation commands.
13. Every operation/condition returns the specified exit code and JSON warning/error shape.
14. Wrong agent, session, worktree, or branch cannot update or release a valid claim.
15. Unsafe IDs and paths, including traversal, rooted/UNC paths, `.git`, and reparse-point
    escapes, fail before filesystem mutation.
16. Handoff reconciles modified, renamed, deleted, untracked, committed, pre-existing, and
    out-of-scope paths against durable workstream state.
17. Verification parameter combinations follow the result-dependent matrix and include
    complete dirty-path evidence.
18. Failure injection after each handoff/takeover write boundary produces the documented
    recoverable state and an idempotent retry.
19. Candidate staged paths outside the exact portable allowlist fail the audit.
20. Reserved Windows device-name workstream IDs are rejected case-insensitively.
21. Pre-existing out-of-scope dirt remains allowed, while new out-of-scope dirt blocks
    handoff and preserves the active claim.

Verification gates:

- Focused continuity tests.
- `git diff --check` over the pilot changes.
- Existing full pytest suite.
- Manual two-worktree smoke test covering claim visibility, conflict, update, handoff, and
  takeover reporting.

## Pilot Rollout

1. Refresh current project evidence from Git and the Superpowers ledger.
2. Add the shared workflow, integration status, and initial workstream records.
3. Add the PowerShell helper and focused tests.
4. Normalize `AGENTS.md` and `CLAUDE.md` without discarding tool-specific team guidance.
5. Include portable team definitions required by those entry points.
6. Exclude machine-local settings, manifests with absolute paths, caches, logs, and secrets.
7. Run all verification gates.
8. Commit in narrow logical commits without staging unrelated user changes and without
   pushing.
9. Use the pilot during Sub-project B and record friction or drift.
10. Only after a stable pilot, create a separate design/plan for the remaining 12
    Claude-enabled repositories.

## Initial Workstreams

- `continuity-pilot` - active until the helper, shared files, and verification gates land.
- `match-stroke-recognition-b` - planned and paused while the continuity pilot is installed.
  Its completion bar must be resolved before implementation planning resumes.

## Acceptance Criteria

1. Starting Claude or Codex in the repository exposes the same current objective, durable
   decisions, verification evidence, blockers, and next actions.
2. An alternating agent can continue uncommitted work from an explicit handoff without
   guessing ownership or scope.
3. Two linked worktrees can claim disjoint scopes concurrently and cannot claim overlapping
   scopes.
4. A crash or malformed local claim cannot silently erase ownership or durable status.
5. Project status remains concise while detailed history remains available through the
   existing ledger and per-workstream files.
6. Existing user changes are preserved and excluded from continuity commits.
7. Focused tests, `git diff --check`, the full suite, and manual two-worktree smoke all pass.
8. No local settings, secrets, caches, absolute-path manifests, or unrelated files are
   committed.

## Non-Goals

- Real-time coordination across separate clones or machines.
- A daemon, web service, database, remote lock server, or external dependency.
- Automatic branch/worktree creation, Git staging, commits, pushes, merges, stashing,
  resetting, or cleaning.
- Capturing every command or conversational turn.
- Replacing Superpowers specs, plans, or progress history.
- Bulk rollout to other repositories before the Good Badminton pilot is proven.

## Tradeoffs And Risks

- Local claims are advisory. Agents that ignore the shared workflow can still collide.
- Claims do not coordinate separate clones or computers.
- Per-workstream files add documentation overhead but avoid a single status-file conflict
  hotspot.
- Restricting project-status edits to integration milestones creates a small coordinator
  responsibility.
- Milestone records can drift after a crash; lease expiry plus fresh Git inspection makes
  that drift visible rather than hiding it.
- RTK is currently not available on Codex's reduced PATH. The continuity design preserves
  the required RTK reference but does not install or configure the RTK executable.
