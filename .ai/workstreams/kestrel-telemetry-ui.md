# Workstream: kestrel-telemetry-ui

- **workstream_id:** `kestrel-telemetry-ui`
- **Objective:** replace the Kestrel shell with the telemetry-style dashboard, match workspace,
  players and pipeline views, and build the insights, athlete/match and jobs backends they need, per
  `docs/superpowers/specs/2026-10-19-kestrel-telemetry-ui-design.md`.
- **Scope paths:** `.ai/workstreams/kestrel-telemetry-ui.md`, `badminton_analysis/insights`,
  `badminton_analysis/library/athletes.py`, `badminton_analysis/web`, `app.py`, `static/kestrel`,
  `static/kestrel.js`, `static/fonts`, `static/img/ground`, `kestrel.html`, `tests`,
  `docs/superpowers/specs/2026-10-19-kestrel-telemetry-ui-design.md`,
  `docs/superpowers/plans/2026-10-19-kestrel-telemetry-ui.md`
- **State:** handoff (implementation complete; awaiting owner review and merge)
- **Branch:** `claude/kestral-sports-ui-design-015bc4` (worktree `.claude/worktrees/kestral-sports-ui-design-015bc4`)
- **Base commit:** `71cee7b` (`main` after PR #13)
- **Pull request:** opened against the fork's `main` (see the PR list); owner merges
- **Claim:** none taken. The owner's rule is that claims are a record, not a gate; this branch was built in a
  linked worktree by parallel agents and its state is recorded here and in `.superpowers/sdd/` (git-ignored).

## Acceptance criteria

See spec section 9. Plan: `docs/superpowers/plans/2026-10-19-kestrel-telemetry-ui.md` (11 tasks plus integration).

## Decisions and rationale

- **Pieces 1-4 only (owner decision).** Automatic point outcome (rally win rate, unforced errors),
  net/lift/drive scoring and pro-calibrated ranges stay "not yet available" with a reason; the reference
  range is the literature range (score >= 70), labelled "reference range".
- **Athletes are assigned to both sides per video** (`upper`, `lower`) in `data/athletes.json`
  (gitignored, outside `outputs/`).
- **Insights are cached per video** in `outputs/<stem>/insights.json` (derived, regenerable; version 3).
- **Shots per rally come only from the learned hit detector's `hits.json`.** The BST stroke-contact
  fallback is too sparse to count shots (a real library showed "1.4 shots per rally").
- **The old `static/kestrel.js` moved to `static/kestrel/legacy/kestrel-legacy.js`** with minimal edits;
  the library, wizard and results screens behave as before inside the new shell.
- **Fonts and two stock photographs are vendored** (`static/fonts/FONTS.md`, `static/img/ground/CREDITS.md`).

## Verification

| Command | Result | Tested commit |
|---|---|---|
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` | 1824 passed, 5 skipped (720 s) | `10fb623` (docs-only after `20d74e2`) |
| `node --test 'tests/js/*.test.mjs'` | 197 passed, 0 failed | `10fb623` |
| Browser pass (Browser pane, real runs in `outputs/`, server on port 5071) | Overview in 3 lenses, peek, container transform to the workspace (real video and inspector), Matches + record editor, Players, Pipeline, legacy Library, palette (Ctrl+K), day theme, zh, 375 px layout; console clean | `20d74e2` |
| API round trip (athlete + match record -> win rate 1/1, oriented sets), then cleaned up | pass | `pre-final` |

Baseline before this work: 1246 passed, 3 skipped. Not browser-checked: reduced-motion rendering, the "approx." marker and "as <Technique>" cell after the final fix wave, the lane offset.

## Next action

Owner reviews and merges the PR. Follow-ups worth planning (not done): match-record import from
filename conventions, the Compare and Reports destinations, shot-landing on the ground, point-outcome
detection (unlocks rally win rate and unforced errors), pro-calibrated reference ranges.
