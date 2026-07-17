# Claude-Codex Project Continuity - Design Spec

**Date:** 2026-07-16
**Status:** Design revised for protected-branch, commit-based handoffs; awaiting review.

## Goal

Make Good Badminton a pilot repository that Claude and Codex can safely continue from
one another, whether they alternate in one working tree or work concurrently in linked
Git worktrees. All write work must occur on a non-`master` branch, and every handoff must
reference committed work. Both tools must see the same durable project intent, progress,
decisions, verification evidence, blockers, and next actions without replacing their
tool-specific instructions.

The pilot must preserve the existing dirty working tree and Superpowers history. It must
not stage, commit, revert, move, or delete unrelated user files.

## Rollout Scope

This design applies only to Good Badminton. If it remains reliable through Sub-project B,
its templates and audit logic can be adapted for the other 12 repositories under
`D:/Dev/Claude/Projects` that currently contain `CLAUDE.md`.

Supporting all 45 Git repositories immediately is out of scope.

## Current State

- Protected integration branch: `master`; agents must not commit directly to it.
- Current development/backfill branch: `codex/good-badminton-development`, created from
  `e4d2a79`; fresh Git inspection remains authoritative for its current `HEAD`.
- No Git remote or upstream is configured.
- `master` still points at `e4d2a79` pending an explicit baseline decision. The locally
  evidenced import baseline candidate is `962e1f9`; moving `master` is a separate guarded
  migration step, not part of editing this specification.
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
4. **Protect integration history.** Agents never write or commit directly on `master`.
   Feature work is integrated only by an explicit user-authorized merge or pull request.
5. **Committed handoffs.** Before ownership changes, all in-scope changes and the durable
   next action are committed on the workstream branch. Unrelated pre-existing dirt remains
   classified and untouched.
6. **No hidden mutation.** Continuity tooling never creates branches/worktrees or runs
   `stash`, `reset`, `clean`, `add`, `commit`, `push`, or destructive filesystem commands.
7. **Milestone updates, not activity logs.** Durable status changes after meaningful
   decisions, edits, tests, commits, blockers, and before a stop or handoff.
8. **Preserve existing systems.** The large Superpowers ledger remains historical evidence;
   the continuity layer links to it instead of copying it.
9. **No new dependency.** Use Markdown, JSON, PowerShell, Git, and the existing pytest
   stack.

## Sources Of Truth

When two artifacts disagree, agents use this division of authority:

| Question | Authority |
|---|---|
| What should be built? | Approved spec and implementation plan |
| What code/files currently exist? | Filesystem and fresh Git inspection |
| What has been verified? | Fresh command output tied to a commit, or explicitly dirty active work before handoff |
| Who owns current work and what happens next? | Live claim plus durable workstream status |
| What is the integrated project state? | `.ai/PROJECT_STATUS.md` |
| What happened historically? | `.superpowers/sdd/progress.md` and Git history |

Status text must never override contradictory Git or verification evidence. Agents update
stale status instead of treating it as fact.

## Repository Layout

### Shared tracked files

- `.ai/WORKFLOW.md` - stable tool-neutral operating contract.
- `.ai/PROJECT_STATUS.md` - concise protected-integration snapshot.
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
- Branch, worktree path, base commit, and last observed head commit. The head value records
  Git state before the status-file mutation and is not self-referential; handoff history
  records the final committed `HEAD` that contains the status update.
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
  "preexisting_dirty": [
    {
      "path": "repository-relative-path",
      "original_path": null,
      "status": "??",
      "kind": "regular-file",
      "worktree_sha256": "full-content-sha256",
      "index_entries": []
    }
  ],
  "predecessor_claim_id": null,
  "replaces_claim_id": null,
  "replacement_reason": null,
  "durable_status_path": null,
  "durable_status_sha256": null,
  "durable_status_blob_oid": null,
  "handoff_commit": null
}
```

Scope entries are normalized repository-relative path prefixes, not globs. Two claims
overlap when either normalized prefix contains the other at a path boundary. Case
comparison follows the filesystem behavior while serialized paths use forward slashes.

Claims must not contain credentials, environment values, file contents (including generated
logs), or arbitrary command output. Content hashes and repository-relative paths are
allowed.

Pre-existing dirt is enumerated with `git status --porcelain=v1 -z -uall`. Each entry
records the two-character Git status, destination path, rename/copy source path when
present, filesystem kind, and a SHA-256 fingerprint of the working-tree bytes. It also
records the complete `git ls-files --stage -z` tuples for that path: stage, mode, and object
ID, including stages 1-3 for unmerged entries. Missing tracked paths use kind `absent` and
a null worktree hash; links or reparse points are fingerprinted from their link metadata
without traversal. Untracked paths have an empty index-entry array. No file contents are
stored. `handoff` and `accept` require every pre-existing out-of-scope entry to retain the
same status, paths, kind, worktree hash, and index tuples. Same-path working-tree or staged
content changes are conflicts, not proof that the agent preserved the file.

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

### Branch validation

- Mutating operations reject detached `HEAD`, `master`, and any branch that is not named
  `codex/<workstream-id>` or `claude/<workstream-id>`.
- The existing `codex/good-badminton-development` backfill branch is a documented exception
  for implementing the continuity pilot and preserving imported history. New workstreams
  created after the pilot use an exact workstream-ID suffix.
- The prefix identifies the branch creator, not its current owner. Claude may accept a
  `codex/` branch and Codex may accept a `claude/` branch.

### Claim ownership identity

A claim ID is not an authentication token. `update` and `handoff` require the recorded
agent and session ID, and the helper derives the current canonical worktree path, branch,
and Git common directory. All values must match the active claim. A mismatch returns exit
`2` and does not renew, mutate, or transfer the claim.

### Claim lifecycle and history

Active claim files use `state: active` or `state: handoff-ready`. Expiry is a derived
lease condition reported by `status`; the read-only command never rewrites `state`.
An expired `handoff-ready` claim remains blocking. It may be accepted after expiry when all
recorded Git evidence still matches, or explicitly replaced through `takeover` using its
exact claim ID and a reason; it is never silently deleted or downgraded to `active`.

Completed local audit records are retained indefinitely for the pilot under:

```text
<git-common-dir>/ai-continuity/history/<claim-id>.json
```

History records copy the claim and add `final_state` (`handed-off` or `replaced`),
`ended_utc`, `durable_status_path`, `durable_status_sha256`, and the successor or replacement
claim ID, agent, session ID, and reason where applicable.

| From | Trigger | Durable result | Active-file result |
|---|---|---|---|
| none | successful `start` | none | new `active` claim |
| `active` live | same identity runs `start` or `update` | optional milestone | lease renewed |
| `active` expired | `status` | none | unchanged; reported expired |
| `active` expired | successful `takeover` | takeover transaction removed after old claim is archived `replaced` | new `active` claim replaces old claim |
| `active` | committed handoff status validated | none; workstream hash and `HEAD` commit derived in memory | remains `active` until next step |
| `active` | handoff marker written | hash and commit copied into claim | becomes `handoff-ready` |
| `handoff-ready` | recipient `accept` prepared | accept transaction records predecessor and successor | remains blocking |
| `handoff-ready` | recipient activated | transaction retained | atomically replaced by successor `active` claim |
| successor `active` | predecessor archived | history record `handed-off` | successor remains active |
| successor `active` | accept completes | history retained; transaction removed | successor remains active |
| `handoff-ready` expired | successful `accept` with unchanged evidence | normal accept transaction | successor becomes active |
| `handoff-ready` expired | successful `takeover` with exact prior ID/reason | takeover transaction and `replaced` history | new active claim replaces old claim |

No other transition is valid. A `handoff-ready` claim blocks new claims exactly like an
active claim. History is never used as live ownership.

## Helper Contract

`scripts/ai-handoff.ps1` provides these operations:

- `status [-Workstream <id>] [-Json]` - read-only report of Git state,
  project/workstream state, claims, malformed claims, overlaps, and expired leases.
- `start -Agent <claude|codex> -SessionId <id> -Workstream <id>
  -Scope <path-prefix[]> [-LeaseHours <1..24>] [-Json]` - validate
  startup state and create a scoped lease. The default lease is eight hours. Reject the
  protected integration branch and any dirty path inside the requested scope; capture and
  report pre-existing dirt outside the scope without changing it. Repeating the same
  agent/session/workstream claim in the same worktree with the identical normalized scope
  is idempotent and renews it. A scope change returns exit `2` and requires a
  completed handoff/accept or expired-claim takeover.
- `update -ClaimId <id> -Agent <claude|codex> -SessionId <id> -Summary <text>
  [-ChangedPath <path[]>]
  [-State <active|blocked|handoff>] [-NextAction <text>]
  -VerificationResult <passed|failed|not-run> [-VerificationCommand <text>]
  [-VerificationCommit <sha>] [-VerificationDirtyPath <path[]>]
  [-NotRunReason <text>] [-Json]` - renew the lease and
  append a material milestone to only the owned workstream file. `not-run` requires
  `-NotRunReason`; state `handoff` requires a non-empty `-NextAction`.
- `handoff -ClaimId <id> -Agent <claude|codex> -SessionId <id> -NextAction <text>
  [-Json]` - verify claim identity, non-protected branch, clean claimed scope, and the
  current full commit SHA. Confirm that the workstream file at `HEAD` is committed with
  state `handoff` and the exact next action, verify pre-existing dirty fingerprints, then
  mark the claim handoff-ready. It remains the blocking owner until accepted. `handoff`
  never edits a tracked file.
- `accept -PreviousClaimId <id> -Agent <claude|codex> -SessionId <id> [-Json]` - accept a
  `handoff-ready` claim in the same canonical worktree and branch. Revalidate the recorded
  full commit, clean claimed scope, committed status hash, and pre-existing dirty
  fingerprints under the common lock. Atomically activate a successor claim and archive
  the predecessor as `handed-off`. Git drift before activation leaves the predecessor
  blocking; drift after activation leaves the successor active and returns exit `5`.
  Acceptance is allowed after lease expiry because every recorded Git invariant is
  revalidated.
- `takeover -Agent <claude|codex> -SessionId <id> -Workstream <id>
  -PreviousClaimId <id> -Reason <text> -Scope <path-prefix[]>
  [-LeaseHours <1..24>] [-Json]` - replace an expired claim and record its identity and
  replacement reason. A live claim cannot be taken over. An expired claim may be `active`
  or `handoff-ready`; both require the exact prior ID. Takeover has the same protected
  branch and clean claimed-scope checks as `start`, requires unchanged pre-existing dirty
  fingerprints from the prior claim, and preserves prior audit evidence.

PowerShell array parameters accept a normal comma-separated array. Repository paths are
normalized before comparison and serialization.

Workstream templates contain a helper-managed section bounded by:

```text
<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
```

`update` may edit only that bounded section plus the template's explicit `Last milestone`,
`Head commit`, `State`, and `Next action` fields. `handoff` only validates the committed
file. Missing or duplicate markers fail validation; the helper never guesses where to
write.

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
`claim_id`, `git`, `claims`, `warnings`, `errors`, and `recovery` when an operation ends in
a recoverable successor-owned state. Secrets and environment values are never included.

Exit codes are stable:

- `0` - success, including read-only status with warnings.
- `2` - validation error, protected-branch use, dirty claimed scope, or claim conflict.
- `3` - malformed continuity state, lock timeout, or atomic-write failure.
- `4` - not a Git worktree or Git common directory unavailable.
- `5` - `accept` activated the successor but post-activation Git drift requires recovery.

Every mutating operation returns non-zero without mutation on validation failure detected
before its documented transaction boundary. Exit `5` and write failures after a transaction
boundary preserve the explicit authoritative recovery state described below.

Operation outcomes are unambiguous:

| Condition | Exit | JSON | Mutation |
|---|---:|---|---|
| Healthy operation | 0 | `ok: true` | documented mutation only |
| `status` finds expiry, Git drift, or ordinary dirty paths | 0 | `ok: true`, item in `warnings` | none |
| Any operation finds malformed claim JSON, duplicate/missing managed markers, or malformed required durable state | 3 | `ok: false`, item in `errors` | none |
| Live overlap, identity mismatch, protected-branch or invalid branch-name use, dirty claimed scope, dirty fingerprint drift, unsafe input, or invalid verification parameters | 2 | `ok: false`, item in `errors` | none |
| `status` finds overlapping live claims | 2 | `ok: false`, conflict in `errors` | none |
| Lock timeout or atomic-write failure | 3 | `ok: false`, item in `errors` | prior or recoverable state described below |
| Git root/common directory unavailable | 4 | `ok: false`, item in `errors` | none |
| `accept` detects Git drift after successor activation | 5 | `ok: false`; `recovery` contains successor claim ID, `authoritative_owner: successor`, transaction path/state, and exact retry command | successor remains active; accept transaction retained |

Human-readable output reports the same warnings/errors as JSON. Malformed state is never
reported as a successful warning.

The helper does not create branches or commit status files. Agents use normal explicit Git
operations only when authorized by the user and repository instructions, and never commit
on the protected integration branch.

## Agent Workflow

### Branch and commit policy

- `master` is the protected integration branch. Read-only inspection is allowed there;
  `start`, `update`, `handoff`, `accept`, and `takeover` reject it with exit `2` and no
  mutation.
- Before write work, create or switch to a non-protected branch. New branches use
  `codex/<workstream-id>` when created by Codex and `claude/<workstream-id>` when created
  by Claude. An alternating agent continues the existing branch regardless of its prefix.
- Parallel agents use distinct branches and linked worktrees. Alternating agents may share
  one feature branch and worktree sequentially through a completed handoff.
- Before handoff, update the owned workstream file with state `handoff`, changed paths,
  verification evidence, and the exact next action. Commit that file together with all
  remaining in-scope work. Changed paths includes the owned workstream file itself. The
  claimed scope must then be clean.
- Every write claim includes its `.ai/workstreams/<workstream-id>.md` path so the final
  status update is covered by the clean-scope and commit checks.
- Pre-existing unrelated dirty paths outside the claim scope may remain and must never be
  staged merely to make the repository globally clean.
- Only the user or a reviewed pull-request service may update `master`. Agents prepare and
  report the feature branch, verification evidence, pull request, or exact integration
  commands; they never execute a merge, rebase, direct commit, ref update, force-update,
  or push that changes `master`.

### Startup

1. Resolve repository root, worktree, branch, HEAD, Git common directory, and protected
   integration branch.
2. Read `.ai/WORKFLOW.md`, `.ai/PROJECT_STATUS.md`, and the relevant workstream file.
3. Inspect fresh `git status`, recent commits, relevant spec/plan, and active claims.
4. For write work, verify or create a non-protected workstream branch before editing.
5. Acquire a scoped claim. A dirty path inside that scope fails startup; pre-existing dirt
   outside the scope is reported and left untouched.

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
workstream, commit all in-scope changes on the shared non-protected branch, and complete
`handoff`. The receiving agent must successfully `accept` the recorded branch and commit
before starting write work. Uncommitted in-scope handoffs are prohibited, and there is no
unowned interval between agents.

At handoff, the helper reads fresh `git status --porcelain=v1 -z` and the committed
`base_commit..HEAD` name-status diff, including both sides of renames. Every observed path
must be either:

- A path captured as pre-existing outside the claim scope at `start` and still classified
  as pre-existing; or
- A committed path inside the live claim scope and listed in the committed workstream's
  changed paths.

Any dirty in-scope path, new out-of-scope change, omitted committed in-scope change, or
unsafe path makes handoff fail with exit `2`; the claim stays active. Changed status,
kind, or fingerprint for a pre-existing out-of-scope path also fails. The helper requires
`HEAD` to contain the workstream file with state `handoff` and the exact supplied next
action. `accept` repeats these Git and fingerprint checks before transferring ownership.

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
- Dirty paths inside scope block `start`, `takeover`, and `handoff` and are never altered
  by the helper. They may exist temporarily during an active claim and must be committed
  before handoff.
- A crash leaves a claim until its lease expires. Expired claims are reported and never
  silently deleted.
- Takeover requires the exact prior claim ID and records which claim it replaced and why.
- A failed handoff keeps the claim active. A successful handoff remains `handoff-ready`
  and blocking until a recipient passes `accept`, so Git drift cannot create an unowned
  stale handoff.
- Status/Git drift is reported, not silently corrected.
- Separate clones or machines cannot see local claims. This pilot coordinates one clone
  plus its linked worktrees only.

### Handoff write ordering and recovery

Handoff holds the common lock while it validates Git and rewrites the active claim. Because
the tracked workstream state is already committed before handoff starts, the operation
never mutates the Git working tree:

1. Validate identity, scope, clean claimed scope, non-protected branch, current full commit
   SHA, pre-existing dirty fingerprints, and committed workstream state/next action at
   `HEAD`.
2. Compute the committed workstream SHA-256, Git blob object ID, and handoff commit in
   memory.
3. Atomically rewrite the active claim as `handoff-ready` with that path, both hashes, and
   commit.

| Failure boundary | Authoritative ownership and retry behavior |
|---|---|
| Before step 2 | Committed workstream and active claim remain authoritative. |
| After step 2, before step 3 | No mutation has occurred; rerun recomputes the committed content/hash. |
| After step 3 | `handoff-ready` claim remains authoritative and blocks all writers until `accept`. |

`accept` uses an explicit transaction journal under
`<git-common-dir>/ai-continuity/transactions/accept-<old-claim-id>.json`:

The proposed successor receives a new GUID claim ID and lease timestamps, the accepting
agent/session identity, `predecessor_claim_id` set to the handoff claim, and
`base_commit = handoff_commit`. It inherits the workstream ID, canonical worktree, branch,
normalized scope, and unchanged pre-existing dirty fingerprints. Handoff-only status/hash
fields are cleared after their values are copied into predecessor history.

1. Under the lock, revalidate the recorded branch, `HEAD`, committed status blob, clean
   claimed scope, and pre-existing dirty fingerprints.
2. Atomically write a `prepared` transaction containing the complete predecessor and
   proposed successor claims.
3. Atomically replace the active file with the successor claim. The active file remains
   the authority for current ownership.
4. Revalidate Git after activation. Drift leaves the successor active, returns exit `5`,
   and requires the recipient to resolve or renew the handoff; it never restores an old
   owner over the successor.
5. Archive the predecessor to immutable history with `final_state: handed-off`.
6. Delete the transaction only after the successor and history record are readable and
   cross-reference each other.

If Git changes before step 3, acceptance fails and the `handoff-ready` predecessor remains
blocking. If Git changes or failure occurs after step 3, the successor is authoritative
and retry uses the transaction to revalidate or finish archival. There is never a point at
which no active claim owns the scope. An exit-`5` retry runs the same `accept
-PreviousClaimId <id> -Agent <agent> -SessionId <id>` command. The helper recognizes the
prepared transaction and successor claim, reports that claim ID as authoritative, and
continues from post-activation validation rather than creating another successor.

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
6. Pre-existing out-of-scope dirty files are captured and never changed.
7. `start`, `takeover`, and `handoff` reject dirty claimed scope; active `update` may record
   explicitly dirty in-progress verification.
8. Lease expiry does not delete a claim; explicit takeover records replacement metadata.
9. Malformed JSON and lock contention fail closed without corrupting prior state.
10. `update` changes only the owned workstream status.
11. `handoff` requires a committed workstream state, exact next action, full handoff commit,
    unchanged dirty fingerprints, and clean claimed scope before marking ownership ready;
    it never edits tracked files.
12. The helper never invokes prohibited Git mutation commands.
13. Every operation/condition returns the specified exit code and JSON warning/error shape.
14. Wrong agent, session, worktree, or branch cannot update, hand off, or accept a valid
    claim.
15. Unsafe IDs and paths, including traversal, rooted/UNC paths, `.git`, and reparse-point
    escapes, fail before filesystem mutation.
16. Handoff reconciles modified, renamed, deleted, untracked, committed, pre-existing, and
    out-of-scope paths against durable workstream state.
17. Verification parameter combinations follow the result-dependent matrix and include
    complete dirty-path evidence.
18. Failure injection after each handoff, accept, and takeover write boundary produces the
    documented recoverable state and an idempotent retry.
19. Candidate staged paths outside the exact portable allowlist fail the audit.
20. Reserved Windows device-name workstream IDs are rejected case-insensitively.
21. Pre-existing out-of-scope dirt remains allowed, while new out-of-scope dirt blocks
    handoff and preserves the active claim.
22. Every mutating operation rejects `master`, while read-only `status` remains available.
23. Alternating agents can hand off one non-protected branch only after all in-scope paths
    and the workstream status are committed.
24. Same-path status, kind, worktree content, index mode/stage/object IDs, or rename source
    changes to pre-existing dirt block handoff and accept.
25. Mutating operations enforce the branch-prefix/workstream-ID rule and the documented
    backfill-branch exception; either agent can accept the other tool's prefix.
26. Concurrent Git mutation before and after recipient activation never creates an unowned
    scope and produces the documented blocking or successor-owned recovery state.
27. An expired `handoff-ready` claim remains blocking, can be accepted when all evidence
    matches, and can be taken over only with its exact ID, a reason, and normal safety
    validation.

Verification gates:

- Focused continuity tests.
- `git diff --check` over the pilot changes.
- Existing full pytest suite.
- Manual two-worktree smoke test covering claim visibility, conflict, update, handoff,
  accept, takeover reporting, and concurrent Git drift.

## Pilot Rollout

1. Refresh current project evidence from Git and the Superpowers ledger.
2. Verify that the current checkout is a non-protected branch before any write.
3. Add the shared workflow, integration status, and initial workstream records.
4. Add the PowerShell helper and focused tests.
5. Normalize `AGENTS.md` and `CLAUDE.md` without discarding tool-specific team guidance.
6. Include portable team definitions required by those entry points.
7. Exclude machine-local settings, manifests with absolute paths, caches, logs, and secrets.
8. Run all verification gates.
9. Commit in narrow logical commits on the workstream branch without staging unrelated
   user changes and without pushing.
10. Complete a commit-based handoff/accept cycle. Prepare integration evidence for the user
    or reviewed pull-request service; the agent does not update `master`.
11. Use the pilot during Sub-project B and record friction or drift.
12. Only after a stable pilot, create a separate design/plan for the remaining 12
    Claude-enabled repositories.

## Existing-History Backfill

The repository was initialized locally and has no configured remote or upstream. Repository
documentation identifies `https://github.com/qwpyyx/Good-Badminton` as the imported project
and `https://github.com/yo-WASSUP/Good-Badminton` as its upstream, but local Git metadata
does not prove which remote commit should anchor `master`.

The safe first backfill step is complete: `codex/good-badminton-development` was created at
`e4d2a79`, preserving the current history, including the continuity design commit, before
any protected-branch repair. Current dirty user paths remain in the worktree and were not
staged or changed.

Moving the `master` reference or creating a remote fork requires a separate explicit
decision:

1. Verify the intended original repository and exact baseline commit.
2. Capture the expected old `master` SHA and inspect `git worktree list --porcelain`.
   Abort if any worktree has `master` checked out or if a concurrent ref change occurs.
3. Verify that the complete development history and current revision commit are reachable
   from the backfill branch.
4. If the user chooses local repair, the user moves only the local ref with compare-and-swap:
   `git update-ref refs/heads/master <approved-baseline> <expected-old-master-sha>`. Do not
   use `branch -f`, `reset --hard`, checkout-based restoration, or any operation that
   rewrites the dirty worktree.
5. Re-read both refs, worktrees, status, and reachability immediately afterward. A mismatch
   is a failed migration, not a condition to force through.
6. Alternatively, create a user-selected remote fork, preserve the development branch, and
   integrate through pull requests. Do not infer a GitHub account or destination.
7. Record the chosen baseline, remote, and result in `.ai/PROJECT_STATUS.md` before the
   first integration.

The backfill checklist is manually verified in a disposable repository: a checked-out
`master` and an expected-old-SHA mismatch must both fail without moving the ref.

Until that decision is made, `master` and the backfill branch may point to the same commit,
but all new commits must be made only on the backfill or another non-protected branch.

## Initial Workstreams

- `continuity-pilot` - active until the helper, shared files, and verification gates land.
- `match-stroke-recognition-b` - planned and paused while the continuity pilot is installed.
  Its completion bar must be resolved before implementation planning resumes.

## Acceptance Criteria

1. Starting Claude or Codex in the repository exposes the same current objective, durable
   decisions, verification evidence, blockers, and next actions.
2. An alternating agent can continue committed work from an explicit handoff without
   guessing ownership, branch, commit, scope, or next action.
3. Two linked worktrees can claim disjoint scopes concurrently and cannot claim overlapping
   scopes.
4. A crash or malformed local claim cannot silently erase ownership or durable status.
5. Project status remains concise while detailed history remains available through the
   existing ledger and per-workstream files.
6. Existing user changes are preserved and excluded from continuity commits.
7. Focused tests, `git diff --check`, the full suite, and manual two-worktree smoke all pass.
8. No local settings, secrets, caches, absolute-path manifests, or unrelated files are
   committed.
9. No agent writes or commits directly on `master`; all handoffs identify a committed
   non-protected branch and full commit SHA.
10. Mutating helper operations enforce `codex/<workstream-id>` or
    `claude/<workstream-id>`, except for the documented existing backfill branch.
11. A handoff remains blocking until the recipient accepts the unchanged commit, status
    blob, clean claimed scope, and pre-existing dirty fingerprints.

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
- The repository currently has no remote-derived protected baseline. The backfill branch
  prevents history loss, but moving `master` or creating a fork remains blocked on an
  explicit baseline/destination decision.
- Milestone records can drift after a crash; lease expiry plus fresh Git inspection makes
  that drift visible rather than hiding it.
- RTK is currently not available on Codex's reduced PATH. The continuity design preserves
  the required RTK reference but does not install or configure the RTK executable.
