# AI Continuity Workflow

Stable, tool-neutral operating contract for Claude and Codex on Good Badminton. This
document does not change per branch; branch- and task-specific state lives in
`.ai/PROJECT_STATUS.md` and `.ai/workstreams/<workstream-id>.md`.

## Sources of truth

| Question | Authority |
|---|---|
| What should be built? | Approved spec and implementation plan |
| What code/files currently exist? | Filesystem and fresh Git inspection |
| What has been verified? | Fresh command output tied to a commit, or explicitly dirty active work before handoff |
| Who owns current work and what happens next? | Live claim plus durable workstream status |
| What is the integrated project state? | `.ai/PROJECT_STATUS.md` |
| What happened historically? | `.superpowers/sdd/progress.md` and Git history |

Status text never overrides contradictory Git or verification evidence. Update stale
status instead of treating it as fact.

## Startup sequence

1. Resolve repository root, current worktree, branch, `HEAD`, Git common directory, and
   the protected integration branch.
2. Read this file, `.ai/PROJECT_STATUS.md`, and the relevant workstream file.
3. Inspect fresh `git status`, recent commits, the relevant spec/plan, and active claims
   (`scripts/ai-handoff.ps1 status`).
4. For write work, verify or create a non-protected workstream branch before editing.
5. Acquire a scoped claim (`scripts/ai-handoff.ps1 start`). A dirty path inside that scope
   fails startup; pre-existing dirt outside the scope is reported and left untouched.

Read-only questions and diagnostics do not require a claim.

## Protected-branch rules

- `main` is the protected integration branch. Read-only inspection is allowed there;
  `start`, `update`, `handoff`, `accept`, and `takeover` reject it with exit `2` and no
  mutation.
- Only the user or a reviewed pull-request service updates `main`. Agents never execute a
  merge, rebase, direct commit, ref update, force-update, or push that changes `main`.

## Branch naming and the bootstrap exception

- New branches use `codex/<workstream-id>` when created by Codex and
  `claude/<workstream-id>` when created by Claude. An alternating agent continues the
  existing branch regardless of its prefix; the prefix identifies the branch creator, not
  its current owner.
- `codex/good-badminton-development` is a documented bootstrap exception for the
  `continuity-pilot` workstream only, used to implement and land this pilot while
  preserving imported project history. New workstreams created after the pilot use an
  exact `codex/<workstream-id>` or `claude/<workstream-id>` name; no other workstream may
  reuse this exception.

## Alternating and parallel modes

- **Alternating:** Claude and Codex may share one worktree and branch sequentially. The
  current owner updates the workstream, commits all in-scope changes, and completes
  `handoff`. The receiving agent must successfully `accept` the recorded branch and commit
  before starting write work. There is no unowned interval between agents.
- **Parallel:** concurrent agents use separate branches/worktrees and disjoint path-prefix
  claims. Each agent edits only its own workstream status. Integration updates project
  status only after a workstream is merged or otherwise incorporated.

## Claim requirement

Every write claim includes its own `.ai/workstreams/<workstream-id>.md` path in scope, so
the final status update is covered by the clean-scope and commit checks. A live claim
(`active` or `handoff-ready`) blocks any new or overlapping claim; disjoint scopes may be
claimed concurrently.

## Dirty-file protections

- Pre-existing dirty paths outside the claim scope are captured at `start`, reported, and
  never staged, changed, or swept merely to make the repository globally clean.
- Dirty paths inside scope block `start`, `takeover`, and `handoff`; they may exist
  temporarily during an active claim (recorded through `update`) and must be committed
  before handoff.
- A new out-of-scope change discovered at handoff, or any change to a captured
  pre-existing fingerprint, is a conflict and fails with exit `2`.

## Milestone triggers

Update the owned workstream file after:

- A material implementation change.
- A design or scope decision.
- A test, build, lint, benchmark, or smoke result.
- A commit or branch-base change.
- Discovery of a blocker or deferred dependency.
- Any pause, stop, agent switch, or user-requested handoff.

Record milestones, not command-by-command activity. Verification records identify the
tested commit; if the tree is dirty, they identify the relevant changed paths. If
verification was not run, record `not run` and the reason.

## Committed handoff and accept flow

1. Before handoff, update the owned workstream file with state `handoff`, changed paths
   (including the workstream file itself), verification evidence, and the exact next
   action; commit it with all remaining in-scope work so the claimed scope is clean.
2. `handoff` verifies claim identity, the non-protected branch, the clean claimed scope,
   the committed workstream state/next action at `HEAD`, and unchanged pre-existing dirty
   fingerprints, then marks the claim `handoff-ready`. It never edits a tracked file and
   remains the blocking owner until accepted.
3. `accept` revalidates the same evidence under the common lock, then atomically activates
   a successor claim and archives the predecessor as `handed-off`. Git drift before
   activation leaves the predecessor blocking; drift after activation leaves the successor
   active and returns exit `5` with recovery details.

## Takeover rules

- A live claim (`active` or `handoff-ready` within its lease) cannot be taken over.
- An expired `active` or `handoff-ready` claim may be replaced by `takeover` using its
  exact claim ID, a non-empty reason, and the same protected-branch and clean claimed-scope
  checks as `start`. It requires unchanged pre-existing dirty fingerprints from the prior
  claim and preserves prior audit evidence as immutable `replaced` history.
- Expired claims are reported, never silently deleted or downgraded to `active`.

## Stable helper exits

- `0` - success, including read-only `status` with warnings.
- `2` - validation error, protected-branch use, dirty claimed scope, or claim conflict.
- `3` - malformed continuity state, lock timeout, or atomic-write failure.
- `4` - not a Git worktree or Git common directory unavailable.
- `5` - `accept` activated the successor but post-activation Git drift requires recovery.

## No hidden mutation

`scripts/ai-handoff.ps1` never creates a branch or worktree, and never runs `add`,
`commit`, `checkout`, `switch`, `branch`, `worktree add/remove`, `stash`, `reset`, `clean`,
`merge`, `rebase`, or `push`. Agents perform all Git mutations explicitly and only when
authorized by the user and repository instructions.
