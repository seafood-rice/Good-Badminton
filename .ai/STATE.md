# Good Badminton - Project State

The one file any AI tool (Claude, Codex, other) reads first. Facts here are a starting point:
Git and the filesystem win over this file if they disagree.

## How we work (5 rules)

1. **`main` is protected.** Work on a branch (`claude/<feature>` or `codex/<feature>`), open a PR
   against the fork's `main`; the owner merges. Never push or commit to `main`.
2. **One feature = one branch (+ worktree if others are active).** Nothing to claim or hand over:
   to take over, check out the branch and read its row below.
3. **Before editing, find overlap:** `python scripts/ai_overlap.py <path-prefix> ...`. It lists
   other unmerged branches and worktrees touching those paths. It only warns, never blocks. If
   it reports overlap, rebase on or coordinate with that branch (or pick other files).
4. **Keep your row current** in "Active work" below: feature, branch, paths, next action. Update
   it in the same commit as the work. Delete the row when the PR merges. Long detail goes in the
   feature's spec/plan under `docs/superpowers/`, not here.
5. **Record verification honestly:** command, result, commit. If not run, say so.

## Project

Badminton match video analysis (Flask app + Kestrel UI, `badminton_analysis/` package): court
detection, player/shuttle tracking, rally and stroke recognition, posture report.

- **Run the app:** `python app.py` (port 5050; `--host` defaults to `0.0.0.0`).
- **Tests:** `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`
  (about 10 min; 1357 passed, 3 skipped at `33c86be`; the 3 skips need Windows symlink privilege).
- **Remotes:** `origin` = fork `seafood-rice/Good-Badminton` (push here); `upstream` =
  `qwpyyx/Good-Badminton` (fetch-only; nothing proposed upstream).
- **Never commit:** `BirdEye Prototype.html` (untracked, repo root); `data/` (gitignored).

## Direction

Per-player scoring of 7 techniques is the goal; detection comes first, full coverage, ranges
calibrated on pro footage. Sub-project B (full-match stroke recognition, spec
`docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md`): B1 and B11 (rally
detection) merged; B2-B10 not yet planned. Learned hit detection (part 1 of technique scoring)
merged as #13; its dead-time gap builder (`--gaps`) and the `/` -> Kestrel serving tweak are merged too; retraining continues on its branch.

## Active work

| Feature | Branch | Paths it touches | State / next action |
|---|---|---|---|
| learned-hit-detection | `claude/learned-hit-detection` | `badminton_analysis/stroke/`, `system.py`, `stroke_recog/recognizer.py`, `shuttle_track/tracknet.py`, `third_party/tracknet/`, `static/kestrel/legacy/kestrel-legacy.js`, `main.py`, `app.py`, `scripts/*hit*`, `scripts/shuttleset_labels.py`, `tests/test_hit_*` | First model on owner's Axelsen labels: recall 0.80, precision 0.67 (bar 0.60 / 0.70), hitter 0.88. Dead-time rebuild runs detached (`data/hit_dataset_gaps/build.log`; `stop_build.ps1` / `resume_build.ps1` there). Next: when it shows BUILD DONE, retrain on `data/hit_dataset` + `data/hit_dataset_gaps` (val Sindhu), re-score the 4 Axelsen stretches. Spec/plan: `docs/superpowers/*/2026-10-04-learned-hit-detection*`. `app.py` now serves `kestrel.html` at `/` and binds `0.0.0.0` (merged). Note: claude/stroke-timeline-mismatch-2bfb29 deletes stroke_recog/; rebase before touching it. |
| split-match-linking | `claude/match-split-side-change-58a409` | `badminton_analysis/library/athletes.py`, `insights/aggregate.py`, `web/athletes_routes.py`, `app.py` (`_forget_match_record`), `static/kestrel/views/matches.js`, `static/kestrel/core/i18n.js` | Spec `docs/superpowers/specs/2026-10-21-split-match-linking-design.md`. Built: link a video as part N of a match (manual, sides-swapped flag); one expandable Matches row. Verified 2026-10-21: full pytest 2047 passed / 2 skipped, `node --test tests/js/*.test.mjs` 280 passed, live UI + API check on seeded data. Next: owner review/merge of the PR; then delete this row. Follow-ups: explicit `{part_of: null}` unlink (stale-tab safety), `has_record` flag on overview rows (UI guesses today). |
| concurrent-analysis-jobs | `claude/analysis-pipeline-parallel-jobs-8705aa` | `app.py` (job routes), `badminton_analysis/web/job_slots.py`, `static/kestrel/views/pipeline.js`, `static/kestrel/widgets/pipeline.js`, `static/kestrel/core/{api,i18n}.js`, `static/kestrel/legacy/kestrel-legacy.js`, `docker-compose.yml`, `tests/conftest.py` | Analyses (match + posture) share a cap (`GB_MAX_JOBS`, default from host: 2 on the 4090 box) with a FIFO queue; same video twice = 409; queued jobs can be cancelled from the Pipeline view. Verified 2026-10-25: full pytest 2139 passed / 1 skipped, node 401 passed, live check on a local server (GB_MAX_JOBS=1: queue, move-up, 409, cancel). Next: merge, rebuild container, delete this row. |
| docker-migration-compose | `claude/docker-migration-compose-a48190` | `Dockerfile`, `.dockerignore`, `docker-compose*.yml`, `README.md`, `README_EN.md` | Image + compose (GPU default, CPU override) and README Docker sections done. Verified on RTX 4090 host: both images build; GPU image sees CUDA (torch + onnxruntime CUDA session); stack healthy; `pytest` in GPU image 1936 passed, 3 skipped (`test_ai_overlap.py` excluded, needs `git`). Not run: `fetch-weights` download, a real analysis on a video with weights. Delete this row when merged. |

## Decisions that still matter

- B11 owner decisions: spec `docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md`
  section 12a, as built in section 15. Rally counts are only claimed when the signal supports
  them; with no usable signal, emit degraded coarse windows and withhold stroke labels.
- The court-view gate threshold is calibrated per video (median - 4 MAD, never stricter than 0.75).
- Library tags live in `data/library_tags.json` (gitignored); every read-modify-write holds
  `_TAGS_LOCK` in `app.py`.
- Hit detector: no weights present means output identical to before; weight-mode tracking is the
  inference default, nonoverlap is for dataset builds.

## Blockers and loose ends

- Stroke classifier (spec `docs/superpowers/specs/2026-10-22-unified-stroke-classifier-design.md`): owner
  accuracy bar not yet measured. Owner labels `outputs/dji-stroke-labels/LABELS.md` (0014 = test; add 1-2
  more test videos so every group has >= 20 test hits), then retrain with `--dji-labels`, relabel, and run
  `scripts/score_strokes.py --split test`. First model (`weights/stroke-classifier.pt`, ShuttleSet only): Sindhu
  group accuracy 0.867, drive recall 0.49. After merge, relabel existing results with
  `python -m badminton_analysis.stroke.relabel <result_dir>`.
- B11 shuttle-segmentation path is unvalidated on footage (needs an owner-run full Axelsen run).
- `stroke/shot_boundary.py` is standalone, not wired in; no real broadcast sample exists.
- B11 follow-ups: CLI flags in `main.py` for gate/signal pins; legacy `web_ui.html` clip button;
  clip padding 1.5 s vs recommended 2 s or more.

## History

Git log and merged PRs (#1-#13). Older ledger: `.superpowers/sdd/progress.md`. The previous
claim/handoff system (`.ai/WORKFLOW.md`, `.ai/workstreams/`, `scripts/ai-handoff.ps1`) was retired
because it blocked work; its files remain in Git history.
