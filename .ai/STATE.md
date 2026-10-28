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
| docker-migration-compose | `claude/docker-migration-compose-a48190` | `Dockerfile`, `.dockerignore`, `docker-compose*.yml`, `README.md`, `README_EN.md` | Image + compose (GPU default, CPU override) and README Docker sections done. Verified on RTX 4090 host: both images build; GPU image sees CUDA (torch + onnxruntime CUDA session); stack healthy; `pytest` in GPU image 1936 passed, 3 skipped (`test_ai_overlap.py` excluded, needs `git`). Not run: `fetch-weights` download, a real analysis on a video with weights. Delete this row when merged. |
| rally-winner-learning | `claude/rally-winner-learning` | `badminton_analysis/outcomes/`, `badminton_analysis/models/store.py`, `stroke/rallies.py`, `stroke/model_eval.py`, `system.py`, `library/athletes.py`, `app.py`, `web/`, `scripts/score_rally_model.py`, `static/kestrel/` (matches, pipeline, rallymodel, i18n) | Spec `docs/superpowers/specs/2026-10-28-rally-winner-learning-design.md` written, awaiting owner review. Next: owner review, then the implementation plan. Nothing built or tested yet. |

## Decisions that still matter

- Analyses (match + posture) share a concurrency cap: `GB_MAX_JOBS`, else sized from the host
  (`badminton_analysis/web/job_slots.py`: 2 on the RTX 4090 box); extra jobs queue FIFO, the same
  video twice is a 409, queued jobs can be cancelled from the Pipeline view.
- A running analysis can be paused from the Pipeline view: its process tree is frozen in place
  (`web/proc_control.py`, psutil) and its slot goes to the next job; resume continues from the same
  frame, ahead of the queue. A frozen job keeps its RAM/VRAM, so at most `max_jobs` can be paused;
  a container restart kills paused jobs like running ones.

- B11 owner decisions: spec `docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md`
  section 12a, as built in section 15. Rally counts are only claimed when the signal supports
  them; with no usable signal, emit degraded coarse windows and withhold stroke labels.
- The court-view gate threshold is calibrated per video (median - 4 MAD, never stricter than 0.75).
- Library tags live in `data/library_tags.json` (gitignored); every read-modify-write holds
  `_TAGS_LOCK` in `app.py`.
- Hit detector: no weights present means output identical to before; weight-mode tracking is the
  inference default, nonoverlap is for dataset builds.

- Strategy report (spec `docs/superpowers/specs/2026-10-27-match-strategy-report-design.md`): every completed
  match queues `strategy_<stem>` in the shared job slots; local Ollama `qwen3.8:27b` writes en+zh from a facts
  pack; every claim cites fact ids and every number must be held by a cited fact, else retry then plain-text
  fallback. Config `GB_STRATEGY_*` env > `data/settings.json` `strategy` > defaults (`GB_STRATEGY_REPORT=off`).
- Stroke correction (spec `docs/superpowers/specs/2026-10-28-stroke-correction-and-rally-tactics-design.md`): owner
  labels in `data/stroke_labels/<stem>.json` overlay `strokes.json` reversibly (`stroke/owner_labels.py`; relabel and
  re-analysis keep them). Retrain (`train_strokes` job, Pipeline card) keeps a candidate only if its 7-group accuracy
  >= the live model's on the same owner test shots and unknown <= 15 %; history and roll back in `weights/`. The
  strategy report adds an effectiveness tally, openings and overview facts from `strategy/shots.py`.

## Blockers and loose ends
- Stroke correction: no owner stroke labels exist yet, so Retrain has never run on real labels (smoke-tested on
  placeholder labels: 90 s on the RTX 4090, reject path). Position check fails 0004 (both sides) and 0003's far side,
  so zone/rushed facts are thin; only 3 of 30 openings in 0003 are complete until missed shots are added. Doubles
  tactics untested live. Owner next: label strokes on 0003/0004 (one match as test), then Retrain.
- To-do (owner, 2026-10-28; not planned): forehand/backhand detection; shuttle landing depth; a per-game split of
  rallies; feeding owner not-a-hit / added shots into hit-detector training; career-wide tactical report; classifier
  relabel for doubles.
- Strategy report: doubles not yet tried against the live model (no doubles run in `outputs/`). A live singles run
  took 832 s for en+zh (cold model load, full test suite running beside it); a first run timed out at 300 s, so the
  default timeout is 600 s per language.

- Rally outcomes (spec `docs/superpowers/specs/2026-10-27-rally-outcome-labelling-design.md`, merged #36):
  `outputs/<stem>/rally_outcomes.json` is the contract the LLM report reads. Auto labeller precision not yet
  measured: the owner labels 0009 + 0010 in the match details page (Point block), then run
  `python scripts/score_rally_outcomes.py outputs/DJI_20261024092943_0009_D outputs/DJI_20261024093729_0010_D`.
  Auto coverage on DJI runs is ~3-7 %. Backfill older runs with `python -m badminton_analysis.outcomes.build
  outputs/<stem>`. Next step after labels: a score-sequence decoder (HMM).
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
