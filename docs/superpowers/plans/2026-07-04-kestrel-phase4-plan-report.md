# Kestrel Phase 4 — Training Plan + Coach Report + Enriched Exercise Content — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Training Plan panel (both results modes, with per-exercise bilingual detail and video guides) and the Coach Report panel (posture, EN/繁體/簡體 + downloads) to the Kestrel results screens, backed by an enriched bilingual exercise library that `plan_generator` carries through into plan sessions.

**Architecture:** Backend is additive-only: `exercise_library.json` gains bilingual detail fields per exercise (`name_zh`, `description_zh`, `instructions{zh,en}`, `coaching_cues{zh,en}`, `common_mistakes{zh,en}`, `video_url`), and `_session_from_exercise` copies them into a `detail` sub-object on each plan session — existing session keys, routes, and cached plans stay untouched (the UI degrades gracefully when `detail` is absent and offers Regenerate). Frontend extends `static/kestrel.js` with three renderers wired to existing endpoints: `renderPlanPanel(stem, mode)` (GET/POST `/api/training-plan/<stem>` or `/api/posture-plan/<stem>`), an expandable per-session detail card with a video guide (embed for `watch?v=` URLs when online, link-out otherwise), and `renderCoachReport(stem)` (GET `/api/posture-report/<stem>?lang=`, downloads via `/api/output/...`).

**Tech Stack:** Python 3 / Flask (backend), vanilla JS + CSS custom properties (frontend), pytest (backend tests).

## Global Constraints

- Bilingual **zh/en**, default `zh`; every user-facing UI string localized in the frontend. The Coach Report keeps its own **EN/繁體/簡體** switcher independent of the UI language. (Spec.)
- **Offline-safe app shell:** no CDN/external references in HTML/CSS/JS assets. **Exception locked by spec:** exercise **video guides are curated external links** — embedded when online, with a graceful localized "video unavailable offline" fallback; links open in a new tab.
- **Additive backend only:** existing routes, session keys, and the match/posture pipelines keep working; `exercise_library.json` gains keys, `_session_from_exercise` gains one `detail` key. Old UI at `/` stays functional (it reads only `name/sets/reps/frequency_per_week/duration_min/location` from sessions — untouched).
- Kestrel stays parallel at `/kestrel`; only `static/kestrel.js`, `static/kestrel.css`, `badminton_analysis/training/exercise_library.json`, `badminton_analysis/training/plan_generator.py`, and test files are touched.
- Theme tokens only: light `:root` + `[data-theme="dark"]`; accent `#FF5A36` and score hues constant across themes; no per-component dark CSS. (Spec.)
- **Scope boundary (locked):** Phase 4 = Training Plan panel (match + posture) + exercise detail/video guides + Coach Report panel (posture). **Notes and Export remain deferred to Phase 5.**
- **All existing tests stay green** (baseline **161**). No JS test framework — frontend tasks verify with `node --check static/kestrel.js` + running the app + curl; backend tasks are TDD with pytest via `./.venv/Scripts/python.exe -m pytest` (Windows; `PYTHONUTF8=1` when launching `app.py`).
- **Backward compatibility with cached plans:** `outputs/*/training_plan.json` and `outputs/*/posture/training_plan.json` already on disk have sessions WITHOUT `detail`. The UI must render them (rows fine, expansion shows a localized "regenerate to see details" hint), never throw.
- Data contracts (verified against app.py + modules): `GET /api/training-plan/<stem>` → plan JSON or `{error}` 404 (auto-generates from `technique_summary.json` when missing); `POST /api/training-plan/<stem>` body `{weeks:4}` → regenerated plan JSON; same pair at `/api/posture-plan/<stem>` (from `drill_summary.json`). Plan JSON = `{weeks:[{week:int, phase:'Foundation'|'Progression', sessions:[{exercise_id,name,category,location:'court'|'home',frequency_per_week,reps,sets,duration_min,targets[, detail]}]}], targeted_weaknesses:[], on_court:[], at_home:[]}`. `GET /api/posture-report/<stem>?lang=<en|zh-Hant|zh-Hans>` → `{lang, header:{stroke,stroke_label,rep_count,dominant_hand,date,pose_family}, summary:{mean_score,consistency,verdict_text}, strengths:[{metric,metric_label,measured,impact_label,text}], weaknesses:[{metric,metric_label,measured,ideal_range,direction,severity,impact_label,mechanism_text,drill_text}], per_rep:[...], training_plan:{...}, section_labels:{...}}` or 400 invalid lang / 404 no report. Report downloads: `/api/output/<stem>/posture/coach_report_<lang>.html` (exists) and `.pdf` (best-effort).
- **Dogfood samples:** `IMG_1270` (posture: cached plan WITHOUT detail + full tri-lingual coach reports on disk → exercises graceful path, regenerate, and the report panel). `IMG_1537` (match: `technique_summary.json` with `stroke_count 0` → plan auto-generates from `_baseline` exercises).

---

### Task 1: Enrich the exercise library with bilingual detail + video guides

**Files:**
- Modify: `badminton_analysis/training/exercise_library.json` (all 15 exercises gain 6 fields)
- Test: `tests/test_exercise_library.py` (append 1 schema test)

**Interfaces:**
- Produces: every exercise object in the library additionally carries:
  - `name_zh`: non-empty string
  - `description_zh`: non-empty string
  - `instructions`: `{"en": [3+ strings], "zh": [3+ strings]}`
  - `coaching_cues`: `{"en": [2+ strings], "zh": [2+ strings]}`
  - `common_mistakes`: `{"en": [2+ strings], "zh": [2+ strings]}`
  - `video_url`: string starting `https://www.youtube.com/` — either a specific `watch?v=` tutorial (curated, real URLs found via web research: Badminton Insight clear `xRv1JLg4NMM`, Badminton Insight smash `H7kpZ9inc10`, split-step `gy4YZS5tGxE`, 4-corner footwork `fBa08o5GEqw`) or a `results?search_query=` curated search link for generic strength/mobility movements (deliberate: no fabricated video IDs, no link rot; the schema accepts any URL so specific videos can be swapped in later).
- Existing fields and ordering are preserved; the file stays a JSON array of 15 objects.

- [ ] **Step 1: Write the failing schema test**

Append to `tests/test_exercise_library.py`:

```python
def test_library_detail_schema():
    lib = load_library()
    assert len(lib) == 15
    for ex in lib:
        assert isinstance(ex.get("name_zh"), str) and ex["name_zh"].strip(), ex["id"]
        assert isinstance(ex.get("description_zh"), str) and ex["description_zh"].strip(), ex["id"]
        for field, minimum in (("instructions", 3), ("coaching_cues", 2), ("common_mistakes", 2)):
            block = ex.get(field)
            assert isinstance(block, dict), (ex["id"], field)
            for lang in ("en", "zh"):
                items = block.get(lang)
                assert isinstance(items, list) and len(items) >= minimum, (ex["id"], field, lang)
                assert all(isinstance(s, str) and s.strip() for s in items), (ex["id"], field, lang)
        assert isinstance(ex.get("video_url"), str), ex["id"]
        assert ex["video_url"].startswith("https://www.youtube.com/"), ex["id"]
```

(`load_library` is already imported at the top of this test file; if not, add `from badminton_analysis.training.exercise_library import load_library`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_exercise_library.py::test_library_detail_schema -v`
Expected: FAIL — `assert isinstance(ex.get("name_zh"), str)` (name_zh missing).

- [ ] **Step 3: Rewrite `exercise_library.json` with the enriched content**

Replace the entire file with exactly this (existing fields preserved, 6 new fields per exercise — content is deliberately plain coaching voice):

```json
[
  {"id": "shadow_clear", "name": "Shadow clear swings", "name_zh": "高远球挥拍练习", "category": "on_court",
   "target_weaknesses": ["elbow_extension", "wrist_flexion"],
   "description": "Full overhead clear motion without a shuttle, focusing on full arm extension and wrist snap at the top.",
   "description_zh": "不用球做完整的高远球挥拍动作，重点体会手臂充分伸展和击球点的手腕发力。",
   "instructions": {
     "en": ["Stand side-on with your racket arm back, elbow bent about 90 degrees and non-racket arm pointing up.",
            "Swing up and rotate through, reaching as high as you can at the imaginary contact point.",
            "Snap the wrist at the top, follow through across your body, and reset to the start position."],
     "zh": ["侧身站立，持拍手后引，肘部约成90度，非持拍手指向上方。",
            "向上挥拍并转体，在想象的击球点尽量伸高手臂。",
            "在最高点抖腕发力，顺势收拍到身体另一侧，然后回到起始姿势。"]},
   "coaching_cues": {
     "en": ["Reach for the ceiling at contact.", "Hips turn before the arm swings."],
     "zh": ["击球瞬间像去摸天花板一样伸高。", "先转髋，再挥臂。"]},
   "common_mistakes": {
     "en": ["Hitting with a bent elbow, which cuts power and reach.", "Facing the net square-on instead of starting side-on."],
     "zh": ["击球时手肘弯曲，力量和高度都打了折扣。", "正对球网站位，没有先侧身。"]},
   "video_url": "https://www.youtube.com/watch?v=xRv1JLg4NMM",
   "equipment": "racket", "difficulty": "beginner", "duration_min": 10, "reps": 20, "sets": 3, "location": "court"},

  {"id": "overhead_band_press", "name": "Resistance band overhead press", "name_zh": "弹力带过头推举", "category": "strength",
   "target_weaknesses": ["elbow_extension"],
   "description": "Press a resistance band overhead to full lockout to build extension strength.",
   "description_zh": "用弹力带向头顶上方推举至手臂完全伸直，增强伸肘力量。",
   "instructions": {
     "en": ["Stand on the middle of the band with feet shoulder-width apart, holding an end in each hand at shoulder height.",
            "Press both hands straight up until your elbows fully lock out overhead.",
            "Lower back to shoulder height with control and repeat."],
     "zh": ["双脚与肩同宽踩住弹力带中段，双手各握一端置于肩部高度。",
            "双手竖直向上推举，直到手肘在头顶完全伸直。",
            "有控制地放回肩部高度，再重复。"]},
   "coaching_cues": {
     "en": ["Finish with biceps beside your ears.", "Keep your ribs down — don't arch the lower back."],
     "zh": ["推到最高点时大臂贴近耳朵。", "肋骨下沉，不要塌腰。"]},
   "common_mistakes": {
     "en": ["Stopping short of full lockout, which skips the range this drill exists to train.", "Leaning back and pressing forward instead of straight up."],
     "zh": ["没有推到完全伸直，恰恰错过了这个动作要练的幅度。", "身体后仰把带子往前推，而不是竖直向上。"]},
   "video_url": "https://www.youtube.com/results?search_query=resistance+band+overhead+press+form",
   "equipment": "resistance band", "difficulty": "beginner", "duration_min": 8, "reps": 12, "sets": 3, "location": "home"},

  {"id": "wall_throws", "name": "Overhead medicine ball wall throws", "name_zh": "药球过头掷墙", "category": "strength",
   "target_weaknesses": ["elbow_extension", "weight_transfer"],
   "description": "Explosively throw a light medicine ball overhead into a wall, extending fully and driving weight forward.",
   "description_zh": "将轻药球从头顶爆发式掷向墙面，手臂充分伸展，重心向前压。",
   "instructions": {
     "en": ["Stand about two meters from a solid wall, ball held behind your head with both hands.",
            "Step forward and throw the ball hard at a spot high on the wall, extending your arms fully.",
            "Catch the rebound (or pick the ball up), reset your stance, and repeat."],
     "zh": ["距离结实的墙面约两米，双手将球举到脑后。",
            "上步并将球用力掷向墙面高处，双臂完全伸展。",
            "接住反弹（或捡回球），恢复站位后重复。"]},
   "coaching_cues": {
     "en": ["Throw comes from the step, not just the arms.", "Finish with your chest over your front foot."],
     "zh": ["力量来自上步，不只是手臂。", "掷完时胸口压在前脚上方。"]},
   "common_mistakes": {
     "en": ["Throwing flat-footed with arms only.", "Using a ball so heavy the movement turns slow — this drill is about speed."],
     "zh": ["站着不动只用手臂扔。", "球太重导致动作变慢——这个练习追求的是速度。"]},
   "video_url": "https://www.youtube.com/results?search_query=overhead+medicine+ball+wall+throw+technique",
   "equipment": "medicine ball", "difficulty": "intermediate", "duration_min": 10, "reps": 10, "sets": 3, "location": "home"},

  {"id": "trunk_rotation_drill", "name": "Cable/band trunk rotations", "name_zh": "弹力带转体", "category": "strength",
   "target_weaknesses": ["trunk_rotation", "hip_shoulder_separation"],
   "description": "Rotate the trunk against band resistance to build rotational power.",
   "description_zh": "对抗弹力带阻力做躯干旋转，提升转体力量。",
   "instructions": {
     "en": ["Anchor a band at chest height and stand side-on to the anchor, both hands on the handle, arms extended.",
            "Rotate your trunk away from the anchor, keeping your arms straight and hips driving the turn.",
            "Return slowly to the start and finish the set before switching sides."],
     "zh": ["把弹力带固定在胸口高度，侧对固定点站立，双手握住把手，手臂伸直。",
            "以髋部带动，躯干向远离固定点的方向旋转，手臂保持伸直。",
            "缓慢转回起始位置，完成一组后换边。"]},
   "coaching_cues": {
     "en": ["Turn your belt buckle, not just your shoulders.", "Slow back, fast out."],
     "zh": ["转动腰带扣的方向，而不是只转肩。", "回来慢，转出去快。"]},
   "common_mistakes": {
     "en": ["Arms bend and do the work instead of the trunk.", "Feet stay glued so the hips never rotate."],
     "zh": ["手臂弯曲代替躯干发力。", "双脚钉在地上，髋部完全没有转动。"]},
   "video_url": "https://www.youtube.com/results?search_query=standing+band+trunk+rotation+exercise",
   "equipment": "resistance band", "difficulty": "beginner", "duration_min": 8, "reps": 15, "sets": 3, "location": "home"},

  {"id": "thoracic_rotation_stretch", "name": "Thoracic spine rotation stretch", "name_zh": "胸椎旋转拉伸", "category": "mobility",
   "target_weaknesses": ["trunk_rotation"],
   "description": "Open-book thoracic rotations to improve mid-back rotational range.",
   "description_zh": "“翻书式”胸椎旋转，改善中背部的旋转幅度。",
   "instructions": {
     "en": ["Lie on your side with knees stacked and bent to 90 degrees, arms extended together in front of your chest.",
            "Keeping the knees glued together, open your top arm like a book toward the floor behind you, following the hand with your eyes.",
            "Pause two breaths at your end range, close back up, and repeat, then switch sides."],
     "zh": ["侧卧，双膝并拢弯曲90度，双臂并拢伸直放在胸前。",
            "双膝保持贴紧，上侧手臂像翻书一样向身后打开，眼睛跟着手走。",
            "在最大幅度停留两次呼吸，合回原位重复，然后换边。"]},
   "coaching_cues": {
     "en": ["Knees stay glued together.", "Follow your hand with your eyes."],
     "zh": ["双膝始终贴紧。", "眼睛跟着手走。"]},
   "common_mistakes": {
     "en": ["Top knee lifts, turning it into a hip stretch instead of a mid-back stretch.", "Rushing through instead of pausing at the end range."],
     "zh": ["上侧膝盖抬起，变成了拉髋而不是拉中背。", "动作太快，没有在最大幅度停留。"]},
   "video_url": "https://www.youtube.com/results?search_query=open+book+thoracic+rotation+stretch",
   "equipment": "none", "difficulty": "beginner", "duration_min": 5, "reps": 12, "sets": 2, "location": "home"},

  {"id": "rotation_smash_drill", "name": "Multi-shuttle rotational smash", "name_zh": "多球转体杀球", "category": "on_court",
   "target_weaknesses": ["trunk_rotation", "hip_shoulder_separation", "weight_transfer"],
   "description": "Feed multi-shuttle smashes emphasizing hip-shoulder separation and forward drive.",
   "description_zh": "多球连续杀球练习，强调髋肩分离和向前的重心驱动。",
   "instructions": {
     "en": ["Have a partner feed high lifts to your rear court, one shuttle every few seconds.",
            "For each feed, turn side-on, load the back leg, then rotate hips first and shoulders after into the smash.",
            "Land with your weight coming forward onto the front leg and recover to base before the next feed."],
     "zh": ["请同伴向你的后场连续喂高球，每隔几秒一个。",
            "每一拍都先侧身、后腿蓄力，然后先转髋、后转肩完成杀球。",
            "落地时重心压向前腿，回位到中心后再接下一个球。"]},
   "coaching_cues": {
     "en": ["Hips lead, shoulders follow.", "Land forward, not underneath yourself."],
     "zh": ["髋先动，肩随后。", "向前落地，不要原地落下。"]},
   "common_mistakes": {
     "en": ["Arm-only smashes with a square chest — no separation, no power.", "Falling backward after contact instead of driving through."],
     "zh": ["正对球网只用手臂杀球——没有转体分离，也就没有力量。", "击球后身体向后倒，而不是向前压。"]},
   "video_url": "https://www.youtube.com/watch?v=H7kpZ9inc10",
   "equipment": "racket, shuttles", "difficulty": "advanced", "duration_min": 15, "reps": 30, "sets": 2, "location": "court"},

  {"id": "wrist_snap_drill", "name": "Wrist snap shadow drill", "name_zh": "手腕发力挥拍练习", "category": "on_court",
   "target_weaknesses": ["wrist_flexion"],
   "description": "Short overhead snaps focusing on wrist pronation at contact.",
   "description_zh": "短促的头顶挥拍，专注体会击球瞬间的手腕内旋发力。",
   "instructions": {
     "en": ["Hold the racket overhead in a relaxed forehand grip, elbow high and slightly bent.",
            "Do a short, sharp snap forward — forearm rotates and the wrist whips the racket head through.",
            "Stop the racket head just after the imaginary contact point, reset, and repeat in rhythm."],
     "zh": ["放松地正手握拍举过头顶，抬肘并略微弯曲。",
            "做短促有力的向前抖动——前臂旋转，手腕把拍头“甩”出去。",
            "拍头在想象的击球点稍前方停住，回位后按节奏重复。"]},
   "coaching_cues": {
     "en": ["Grip relaxed until the moment of the snap.", "Let the racket head do the traveling, not the arm."],
     "zh": ["抖腕前握拍保持放松。", "让拍头走远，而不是整条手臂。"]},
   "common_mistakes": {
     "en": ["Squeezing the grip the whole time, which kills the whip.", "Swinging from the shoulder instead of snapping from the forearm and wrist."],
     "zh": ["全程紧握球拍，鞭打效果全无。", "用肩膀抡大臂，而不是靠前臂和手腕发力。"]},
   "video_url": "https://www.youtube.com/results?search_query=badminton+wrist+action+forearm+rotation+tutorial",
   "equipment": "racket", "difficulty": "beginner", "duration_min": 8, "reps": 25, "sets": 3, "location": "court"},

  {"id": "wrist_curls", "name": "Weighted wrist curls", "name_zh": "负重腕弯举", "category": "strength",
   "target_weaknesses": ["wrist_flexion"],
   "description": "Wrist flexion/extension curls with a light dumbbell to build forearm control.",
   "description_zh": "用轻哑铃做手腕屈伸弯举，增强前臂控制力。",
   "instructions": {
     "en": ["Sit with your forearm resting on your thigh, wrist and hand hanging past the knee, dumbbell in hand.",
            "Curl the wrist up as far as it goes, then lower with control to a full stretch.",
            "Finish the set, then flip your palm down and repeat as extensions."],
     "zh": ["坐姿，前臂放在大腿上，手腕和手悬出膝盖，握住哑铃。",
            "手腕向上卷起到最大幅度，再有控制地放下到完全伸展。",
            "完成一组后手心翻转朝下，做反向的伸腕练习。"]},
   "coaching_cues": {
     "en": ["Forearm stays glued to the thigh.", "Full range beats heavy weight."],
     "zh": ["前臂始终贴住大腿。", "幅度做满比重量更重要。"]},
   "common_mistakes": {
     "en": ["Lifting the forearm off the thigh to cheat the curl.", "Going too heavy and shrinking the range to a wiggle."],
     "zh": ["前臂离开大腿借力。", "重量太大，动作缩成了小幅度晃动。"]},
   "video_url": "https://www.youtube.com/results?search_query=dumbbell+wrist+curl+extension+form",
   "equipment": "dumbbell", "difficulty": "intermediate", "duration_min": 8, "reps": 15, "sets": 3, "location": "home"},

  {"id": "split_step_lunge", "name": "Split-step into lunge", "name_zh": "分腿垫步接弓步", "category": "on_court",
   "target_weaknesses": ["knee_flexion", "weight_transfer"],
   "description": "Split-step then deep lunge to the net, loading the knees and transferring weight forward.",
   "description_zh": "先做分腿垫步，再向网前做深弓步，充分屈膝并把重心送向前方。",
   "instructions": {
     "en": ["Start at the base position; do a small hop landing on both feet at the same time, knees soft.",
            "Push off immediately into a long lunge toward the net, front knee bending over the foot.",
            "Push back off the front leg to return to base, and repeat in rhythm."],
     "zh": ["从中心位置开始，小跳后双脚同时落地，膝盖保持弹性。",
            "落地后立刻蹬地，向网前跨出一个大弓步，前膝在脚面上方弯曲。",
            "用前腿蹬地退回中心位置，按节奏重复。"]},
   "coaching_cues": {
     "en": ["Land the split-step as the opponent hits.", "Front knee tracks over the toes, chest stays up."],
     "zh": ["对手击球瞬间正好落地。", "前膝对准脚尖，上身保持挺直。"]},
   "common_mistakes": {
     "en": ["Split-step too high and slow — it's a quick, low hop.", "Lunging with a straight front leg and letting the knee cave inward."],
     "zh": ["垫步跳得太高太慢——它应该又快又低。", "弓步时前腿伸直、膝盖内扣。"]},
   "video_url": "https://www.youtube.com/watch?v=gy4YZS5tGxE",
   "equipment": "none", "difficulty": "intermediate", "duration_min": 10, "reps": 12, "sets": 3, "location": "court"},

  {"id": "single_leg_squat", "name": "Single-leg squats", "name_zh": "单腿深蹲", "category": "strength",
   "target_weaknesses": ["knee_flexion"],
   "description": "Controlled single-leg squats to build stance stability and leg drive.",
   "description_zh": "有控制的单腿下蹲，增强站位稳定性和腿部蹬伸力量。",
   "instructions": {
     "en": ["Stand on one leg next to a chair or wall for balance if needed, other leg out in front.",
            "Bend the standing knee and sit your hips back as low as you can control.",
            "Drive back up through the whole foot without letting the knee collapse inward; finish the set, then switch legs."],
     "zh": ["单腿站立，需要时可扶椅背或墙保持平衡，另一条腿伸向前方。",
            "支撑腿屈膝、臀部向后坐，下蹲到自己能控制的最低点。",
            "全脚掌发力站起，膝盖不要内扣；完成一组后换腿。"]},
   "coaching_cues": {
     "en": ["Sit back, don't dip forward.", "Knee points the same way as the toes."],
     "zh": ["臀部向后坐，不要往前栽。", "膝盖方向始终对准脚尖。"]},
   "common_mistakes": {
     "en": ["Knee caving inward on the way up.", "Dropping fast and bouncing out of the bottom instead of controlling it."],
     "zh": ["站起时膝盖内扣。", "快速下坠再借反弹站起，失去控制。"]},
   "video_url": "https://www.youtube.com/results?search_query=single+leg+squat+progression+tutorial",
   "equipment": "none", "difficulty": "intermediate", "duration_min": 10, "reps": 10, "sets": 3, "location": "home"},

  {"id": "hip_flexor_stretch", "name": "Hip flexor stretch", "name_zh": "髋屈肌拉伸", "category": "flexibility",
   "target_weaknesses": ["hip_shoulder_separation", "knee_flexion"],
   "description": "Half-kneeling hip flexor stretch to open the hips for rotation and lunging.",
   "description_zh": "半跪姿髋屈肌拉伸，打开髋部，为转体和弓步创造空间。",
   "instructions": {
     "en": ["Kneel on one knee with the other foot planted in front, both knees at 90 degrees.",
            "Tuck your tailbone slightly and shift your weight forward until you feel a stretch at the front of the kneeling-side hip.",
            "Hold and breathe, then ease off; finish the set and switch sides."],
     "zh": ["单膝跪地，另一只脚踩在身前，两膝都约成90度。",
            "骨盆微微后收，重心向前移，直到跪侧髋部前方有拉伸感。",
            "保持并深呼吸，然后放松；完成一组后换边。"]},
   "coaching_cues": {
     "en": ["Tuck the tailbone before you shift forward.", "Stretch should be felt in the hip, not the lower back."],
     "zh": ["先收骨盆，再前移重心。", "拉伸感应该在髋前，而不是腰上。"]},
   "common_mistakes": {
     "en": ["Arching the lower back instead of tucking, which fakes the stretch.", "Lunging so far forward the front knee passes way beyond the toes."],
     "zh": ["塌腰代替收骨盆，拉伸变成了假动作。", "前移过猛，前膝远远超过脚尖。"]},
   "video_url": "https://www.youtube.com/results?search_query=half+kneeling+hip+flexor+stretch",
   "equipment": "none", "difficulty": "beginner", "duration_min": 5, "reps": 4, "sets": 2, "location": "home"},

  {"id": "shoulder_dislocates", "name": "Band shoulder dislocates", "name_zh": "弹力带肩部环绕", "category": "mobility",
   "target_weaknesses": ["wrist_flexion", "elbow_extension"],
   "description": "Pass a band overhead and behind to improve shoulder/arm mobility for the overhead action.",
   "description_zh": "双手持弹力带从体前经头顶绕到身后，改善肩臂在头顶动作中的灵活性。",
   "instructions": {
     "en": ["Hold a band in front of you with a wide grip, arms straight.",
            "Keeping both arms straight, lift the band up, over your head, and down behind your back in one slow arc.",
            "Reverse the arc back to the front; widen your grip if the shoulders pinch."],
     "zh": ["双手宽距握住弹力带置于体前，手臂伸直。",
            "双臂保持伸直，将带子缓慢地举过头顶、绕到身后，画一个完整的弧线。",
            "沿原弧线转回体前；如果肩部有夹挤感就把握距加宽。"]},
   "coaching_cues": {
     "en": ["Arms stay straight the whole way around.", "Slower is better — no rushing through the sticky point."],
     "zh": ["整个过程手臂保持伸直。", "越慢越好——不要冲过最紧的位置。"]},
   "common_mistakes": {
     "en": ["Bending the elbows to sneak past the tight spot.", "Grip too narrow, forcing the shoulders to shrug and strain."],
     "zh": ["弯曲手肘偷偷绕过最紧的位置。", "握距太窄，迫使耸肩硬掰。"]},
   "video_url": "https://www.youtube.com/results?search_query=band+shoulder+dislocates+tutorial",
   "equipment": "resistance band", "difficulty": "beginner", "duration_min": 5, "reps": 12, "sets": 2, "location": "home"},

  {"id": "lunge_footwork", "name": "Four-corner lunge footwork", "name_zh": "四角弓步步法", "category": "on_court",
   "target_weaknesses": ["weight_transfer", "knee_flexion"],
   "description": "Lunge to four court corners and recover, driving weight into and out of each lunge.",
   "description_zh": "依次向场地四个角做弓步并回位，每一步都把重心压进弓步再蹬回。",
   "instructions": {
     "en": ["Start at the base position in the middle of the court.",
            "Move to each corner in turn — front two with lunges, rear two with side-on steps — touching the floor or a marker at each corner.",
            "Push hard off the outside leg to recover to base between corners; keep the rhythm steady."],
     "zh": ["从场地中央的中心位置出发。",
            "依次移动到四个角——前场两点用弓步，后场两点侧身移动——每个角触地或触碰标志物。",
            "每个角之后用外侧腿用力蹬地回中；保持稳定节奏。"]},
   "coaching_cues": {
     "en": ["Push off the outside leg to come back.", "Stay low between corners — don't stand up in the middle."],
     "zh": ["回中靠外侧腿蹬地。", "移动全程保持低重心——中途不要站直。"]},
   "common_mistakes": {
     "en": ["Standing tall at base before each move, wasting the first step.", "Small shuffles into the corner instead of one committed lunge."],
     "zh": ["每次出发前在中心站得笔直，浪费了第一步。", "到角落前碎步蹭过去，而不是一个果断的弓步。"]},
   "video_url": "https://www.youtube.com/watch?v=fBa08o5GEqw",
   "equipment": "none", "difficulty": "intermediate", "duration_min": 12, "reps": 16, "sets": 2, "location": "court"},

  {"id": "serve_target_practice", "name": "Serve target practice", "name_zh": "发球落点练习", "category": "on_court",
   "target_weaknesses": ["wrist_flexion", "trunk_rotation"],
   "description": "Serve to marked targets to refine controlled wrist and trunk action on the serve.",
   "description_zh": "向标记好的目标区发球，打磨发球时手腕和躯干的控制。",
   "instructions": {
     "en": ["Place targets (cones, shuttle tubes, or paper) in the front corners and back tramlines of the service box.",
            "Serve in sets of ten to one target at a time — low serves skimming the tape, high serves dropping steeply on the back target.",
            "Count your hits per set and try to beat that number on the next set."],
     "zh": ["在发球区前角和后发球线附近摆放目标（雪糕筒、球筒或纸张都可以）。",
            "每十个为一组，只打同一个目标——低发球贴网带飞过，高发球在后场目标上方陡直下落。",
            "记录每组命中数，下一组尝试打破自己的纪录。"]},
   "coaching_cues": {
     "en": ["Same relaxed swing every time — let the target do the coaching.", "Low serve: guide it, don't hit it."],
     "zh": ["每一次都用同样放松的挥拍——让目标来当教练。", "低发球是“送”过去的，不是“打”过去的。"]},
   "common_mistakes": {
     "en": ["Changing the swing on misses instead of repeating the same motion.", "Gripping tight and stabbing at the shuttle on the low serve."],
     "zh": ["一没打中就乱改动作，而不是重复同一个挥拍。", "低发球时握死球拍去戳球。"]},
   "video_url": "https://www.youtube.com/results?search_query=badminton+backhand+low+serve+tutorial",
   "equipment": "racket, shuttles", "difficulty": "beginner", "duration_min": 12, "reps": 30, "sets": 2, "location": "court"},

  {"id": "med_ball_rotational_throw", "name": "Rotational medicine ball throw", "name_zh": "药球转体抛掷", "category": "strength",
   "target_weaknesses": ["hip_shoulder_separation", "trunk_rotation"],
   "description": "Explosive sideways medicine ball throws to develop hip-shoulder separation power.",
   "description_zh": "侧向爆发式抛掷药球，发展髋肩分离的旋转爆发力。",
   "instructions": {
     "en": ["Stand side-on to a wall, about two meters away, ball held at your back hip with both hands.",
            "Load into the back leg, then rip the hips toward the wall and let the shoulders and ball whip through after.",
            "Release the ball into the wall at chest height, catch the rebound, reset, and repeat; switch sides each set."],
     "zh": ["侧对墙站立，距离约两米，双手将球持于后侧髋旁。",
            "重心压进后腿蓄力，然后髋部猛然转向墙面，让肩和球随后甩出。",
            "在胸口高度把球掷向墙面，接住反弹后回位重复；每组换一边。"]},
   "coaching_cues": {
     "en": ["Hips fire first, ball leaves last.", "Throw through the wall, not at it."],
     "zh": ["髋先启动，球最后出手。", "把球“穿过”墙扔出去，而不是碰到墙就算。"]},
   "common_mistakes": {
     "en": ["Arms swing the ball while the hips stay still — no separation being trained.", "Standing too close to the wall to finish the rotation."],
     "zh": ["只有手臂在抡球，髋部纹丝不动——完全没练到分离。", "离墙太近，转体还没做完就撞墙了。"]},
   "video_url": "https://www.youtube.com/results?search_query=rotational+medicine+ball+wall+throw+tutorial",
   "equipment": "medicine ball", "difficulty": "advanced", "duration_min": 10, "reps": 8, "sets": 3, "location": "home"}
]
```

- [ ] **Step 4: Run the schema test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_exercise_library.py -v`
Expected: PASS (all tests in the file, including the new one).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `162 passed` (161 baseline + 1 new). Existing library/plan tests read only the original keys, which are all preserved.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/training/exercise_library.json tests/test_exercise_library.py
git commit -m "feat(training): bilingual exercise detail + curated video guides in library"
```

---

### Task 2: `plan_generator` carries exercise detail into sessions (TDD)

**Files:**
- Modify: `badminton_analysis/training/plan_generator.py` (`_session_from_exercise` gains a `detail` key)
- Test: `tests/test_plan_generator.py` (append 2 tests)

**Interfaces:**
- Consumes: enriched library fields from Task 1 (all read with `.get()` so un-enriched libraries still work).
- Produces: every session dict additionally carries `"detail"`: `{name_zh, description, description_zh, equipment, difficulty, instructions, coaching_cues, common_mistakes, video_url}` (values `None` when the library entry lacks the field). All existing session keys unchanged. Task 4's frontend reads exactly these `detail` keys.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_plan_generator.py` (match its existing imports — it already imports `generate_plan`):

```python
def test_sessions_carry_detail():
    plan = generate_plan({"recurring_weaknesses": [{"metric": "elbow_extension", "count": 3}]})
    sessions = [s for wk in plan["weeks"] for s in wk["sessions"]]
    assert sessions
    for s in sessions:
        d = s["detail"]
        assert d["name_zh"]
        assert isinstance(d["instructions"], dict) and d["instructions"]["zh"]
        assert isinstance(d["coaching_cues"], dict) and d["coaching_cues"]["en"]
        assert isinstance(d["common_mistakes"], dict)
        assert d["video_url"].startswith("https://")
        assert d["difficulty"] in ("beginner", "intermediate", "advanced")


def test_detail_none_for_unenriched_library():
    bare = [{"id": "x", "name": "X", "category": "strength", "target_weaknesses": ["elbow_extension"],
             "difficulty": "beginner", "duration_min": 5, "reps": 5, "sets": 2, "location": "home"}]
    plan = generate_plan({"recurring_weaknesses": [{"metric": "elbow_extension", "count": 1}]}, library=bare)
    s = plan["weeks"][0]["sessions"][0]
    assert s["detail"]["name_zh"] is None
    assert s["detail"]["instructions"] is None
    assert s["detail"]["video_url"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_plan_generator.py::test_sessions_carry_detail tests/test_plan_generator.py::test_detail_none_for_unenriched_library -v`
Expected: FAIL — `KeyError: 'detail'`.

- [ ] **Step 3: Extend `_session_from_exercise`**

In `badminton_analysis/training/plan_generator.py`, replace the whole `_session_from_exercise` function with:

```python
def _session_from_exercise(ex, frequency):
    return {
        "exercise_id": ex["id"],
        "name": ex["name"],
        "category": ex["category"],
        "location": ex["location"],
        "frequency_per_week": frequency,
        "reps": ex["reps"],
        "sets": ex["sets"],
        "duration_min": ex["duration_min"],
        "targets": list(ex["target_weaknesses"]),
        "detail": {
            "name_zh": ex.get("name_zh"),
            "description": ex.get("description"),
            "description_zh": ex.get("description_zh"),
            "equipment": ex.get("equipment"),
            "difficulty": ex.get("difficulty"),
            "instructions": ex.get("instructions"),
            "coaching_cues": ex.get("coaching_cues"),
            "common_mistakes": ex.get("common_mistakes"),
            "video_url": ex.get("video_url"),
        },
    }
```

(Note the bare-library test passes a fixture without `description`/`equipment` — `.get()` covers every detail field. `reps`/`sets`/`duration_min`/`target_weaknesses` stay required as before.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_plan_generator.py -v`
Expected: PASS (all tests in the file, including the 2 new ones).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `164 passed` (162 + 2 new). The coach-report pipeline embeds `generate_plan` output (`training_plan` key in coach_report JSON) — additive `detail` keys are carried along harmlessly.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/training/plan_generator.py tests/test_plan_generator.py
git commit -m "feat(training): carry bilingual exercise detail into plan sessions"
```

---

### Task 3: Training Plan panel on both results screens

**Files:**
- Modify: `static/kestrel.js` (`planCtx` holder; `renderPlanPanel`, `renderPlanWeeks`, `regeneratePlan`; mount points in `renderMatchResults` + `renderPostureResults`)
- Modify: `static/kestrel.css` (plan panel styles)

**Interfaces:**
- Consumes: `state`, `setScreen` ecosystem from Phases 1–3; `GET/POST /api/training-plan/<stem>` and `/api/posture-plan/<stem>` (contract in Global Constraints).
- Produces: module-scoped `var planCtx = { stem: null, mode: 'match', plan: null, loc: 'court' };` `renderPlanPanel(stem, mode)` paints into `#plan-box`; `renderPlanWeeks()` re-paints the week list for the active location tab; session rows carry `data-si` (session index within `planCtx.flat`) that Task 4's expansion consumes; `planCtx.flat` is the flattened `[{week, phase, session}]` list for the active location.

- [ ] **Step 1: Add the plan context + renderers** (place after `selectRep`/`downloadRep`, before `openResults`)

```js
  var planCtx = { stem: null, mode: 'match', plan: null, loc: 'court' };
  var PHASE_LABELS = { Foundation: ['基础期', 'Foundation'], Progression: ['进阶期', 'Progression'] };
  function phaseLabel(p) { var m = PHASE_LABELS[p]; return m ? (state.lang==='zh'?m[0]:m[1]) : p; }
  function sessionName(s) {
    if (state.lang === 'zh' && s.detail && s.detail.name_zh) { return s.detail.name_zh; }
    return s.name;
  }
  function renderPlanPanel(stem, mode) {
    planCtx = { stem: stem, mode: mode, plan: null, loc: 'court' };
    var box = document.getElementById('plan-box'); if (!box) { return; }
    var zh = state.lang === 'zh';
    box.innerHTML = '<h2>' + (zh?'训练计划':'Training Plan') + '</h2><p class="muted">' + (zh?'加载中…':'Loading…') + '</p>';
    var url = (mode === 'posture' ? '/api/posture-plan/' : '/api/training-plan/') + stem;
    fetch(url).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var b = document.getElementById('plan-box'); if (!b) { return; }
      if (!d || !d.weeks) {
        b.innerHTML = '<h2>' + (zh?'训练计划':'Training Plan') + '</h2><p class="muted">' + (zh?'暂无训练计划（需先完成分析）':'No training plan yet (run an analysis first)') + '</p>';
        return;
      }
      planCtx.plan = d;
      b.innerHTML =
        '<div class="plan-head"><h2>' + (zh?'训练计划':'Training Plan') + '</h2>' +
          '<div class="seg" role="tablist" aria-label="location">' +
            '<button class="seg-btn" data-loc="court" role="tab">' + (zh?'场上':'On-Court') + '</button>' +
            '<button class="seg-btn" data-loc="home" role="tab">' + (zh?'居家':'At-Home') + '</button>' +
          '</div>' +
          '<button class="btn-ghost" id="plan-regen">↻ ' + (zh?'重新生成':'Regenerate') + '</button></div>' +
        '<div id="plan-weeks"></div>';
      b.querySelectorAll('[data-loc]').forEach(function (t) {
        t.onclick = function () { planCtx.loc = t.getAttribute('data-loc'); renderPlanWeeks(); };
      });
      document.getElementById('plan-regen').onclick = regeneratePlan;
      renderPlanWeeks();
    }).catch(function () {
      var b = document.getElementById('plan-box');
      if (b) { b.innerHTML = '<h2>' + (zh?'训练计划':'Training Plan') + '</h2><p class="muted">' + (zh?'加载失败':'Failed to load') + '</p>'; }
    });
  }
  function renderPlanWeeks() {
    var wrap = document.getElementById('plan-weeks'); if (!wrap || !planCtx.plan) { return; }
    var zh = state.lang === 'zh';
    document.querySelectorAll('#plan-box [data-loc]').forEach(function (t) {
      t.classList.toggle('on', t.getAttribute('data-loc') === planCtx.loc);
      t.setAttribute('aria-selected', t.getAttribute('data-loc') === planCtx.loc ? 'true' : 'false');
    });
    planCtx.flat = [];
    var html = '';
    (planCtx.plan.weeks || []).forEach(function (wk) {
      var rows = (wk.sessions || []).filter(function (s) { return s.location === planCtx.loc; });
      if (!rows.length) { return; }
      html += '<div class="plan-week"><div class="plan-week-head mono">' +
        (zh ? ('第 ' + wk.week + ' 周') : ('Week ' + wk.week)) + ' · ' + phaseLabel(wk.phase) + '</div>';
      rows.forEach(function (s) {
        var si = planCtx.flat.length;
        planCtx.flat.push({ week: wk.week, phase: wk.phase, session: s });
        html += '<button class="plan-row" data-si="' + si + '">' +
          '<span class="plan-name">' + sessionName(s) + '</span>' +
          '<span class="plan-meta mono">' + s.sets + '×' + s.reps +
            ' · ' + s.frequency_per_week + '×/' + (zh?'周':'wk') +
            (s.duration_min ? ' · ' + s.duration_min + 'min' : '') + '</span></button>' +
          '<div class="plan-detail" id="plan-detail-' + si + '" hidden></div>';
      });
      html += '</div>';
    });
    wrap.innerHTML = html || '<p class="muted">' + (zh?'本视图暂无训练项目':'Nothing scheduled for this view') + '</p>';
    wrap.querySelectorAll('.plan-row').forEach(function (r) {
      r.onclick = function () { togglePlanDetail(Number(r.getAttribute('data-si'))); };
    });
  }
  function togglePlanDetail(si) {
    var el = document.getElementById('plan-detail-' + si);
    if (el) { el.hidden = !el.hidden; }
  }
  function regeneratePlan() {
    var zh = state.lang === 'zh';
    var btn = document.getElementById('plan-regen'); if (!btn) { return; }
    btn.disabled = true; btn.textContent = zh?'生成中…':'Regenerating…';
    var url = (planCtx.mode === 'posture' ? '/api/posture-plan/' : '/api/training-plan/') + planCtx.stem;
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ weeks: 4 }) })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        btn.disabled = false; btn.textContent = '↻ ' + (zh?'重新生成':'Regenerate');
        if (d && d.weeks) { planCtx.plan = d; renderPlanWeeks(); }
        else { alert(zh?'生成失败':'Failed to regenerate'); }
      }).catch(function () {
        btn.disabled = false; btn.textContent = '↻ ' + (zh?'重新生成':'Regenerate');
        alert(zh ? '网络错误，请重试' : 'Network error — please try again');
      });
  }
```

(`togglePlanDetail` is minimal here; Task 4 replaces it with the full detail renderer. `planCtx.flat` is rebuilt on every `renderPlanWeeks`, so `data-si` indexes always match the visible rows.)

- [ ] **Step 2: Mount the panel on both results screens**

In `renderMatchResults`, the `body.innerHTML` assignment currently ends with:
```js
        '<div id="tech-summary"></div><div id="tech-strokes" class="stroke-list"></div><div id="tech-detail" class="rep-detail"></div></div>';
```
Change that ending to:
```js
        '<div id="tech-summary"></div><div id="tech-strokes" class="stroke-list"></div><div id="tech-detail" class="rep-detail"></div></div>' +
      '<div class="res-section" id="plan-box"></div>';
```
Then, on the next line after the `body.innerHTML = ...;` statement (before the heatmap/scatter `onerror` wiring), add:
```js
    renderPlanPanel(stem, 'match');
```

In `renderPostureResults`, the `body.innerHTML` assignment currently ends with:
```js
      '<div class="res-section rep-detail" id="rep-detail"></div>';
```
Change that ending to:
```js
      '<div class="res-section rep-detail" id="rep-detail"></div>' +
      '<div class="res-section" id="plan-box"></div>';
```
Then, immediately after the `body.innerHTML = ...;` statement (before the `/api/posture/` fetch), add:
```js
    renderPlanPanel(stem, 'posture');
```

- [ ] **Step 3: Add plan CSS to `kestrel.css`**

```css
.plan-head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:14px;}
.plan-head h2{margin:0 auto 0 0;}
.plan-week{margin-bottom:16px;max-width:720px;}
.plan-week-head{font-size:12px;color:var(--faint);margin-bottom:8px;}
.plan-row{width:100%;display:flex;align-items:center;justify-content:space-between;gap:12px;background:var(--card);
  border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px 14px;font:inherit;cursor:pointer;
  margin-bottom:6px;transition:border-color .15s;text-align:left;}
.plan-row:hover{border-color:var(--faint);}
.plan-name{font-size:14px;font-weight:600;color:var(--ink);}
.plan-meta{font-size:12px;color:var(--muted);white-space:nowrap;}
```

- [ ] **Step 4: Verify + commit**

Run: `node --check static/kestrel.js` → exit 0.
Start `PYTHONUTF8=1 ./.venv/Scripts/python.exe app.py --port 5096` (background); `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5096/kestrel` → 200; `curl -s http://127.0.0.1:5096/static/kestrel.js | grep -c "renderPlanPanel\|regeneratePlan\|planCtx"` → non-zero; confirm the data endpoints: `curl -s http://127.0.0.1:5096/api/posture-plan/IMG_1270 | head -c 200` → JSON with `weeks` (cached, sessions have NO `detail`), and `curl -s -X POST http://127.0.0.1:5096/api/posture-plan/IMG_1270 -H "Content-Type: application/json" -d "{\"weeks\":4}" | grep -c "detail"` → non-zero (regenerated plan carries detail). Kill the server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): training plan panel with location tabs + regenerate on results screens"
```

---

### Task 4: Expandable exercise detail cards + video guides

**Files:**
- Modify: `static/kestrel.js` (replace Task 3's `togglePlanDetail` stub with the full detail renderer + helpers)
- Modify: `static/kestrel.css` (detail card + video styles)

**Interfaces:**
- Consumes: `planCtx.flat[si].session.detail` (Task 2 shape: `{name_zh, description, description_zh, equipment, difficulty, instructions, coaching_cues, common_mistakes, video_url}`, possibly absent on cached plans), `#plan-detail-<si>` containers and `data-si` rows (Task 3).
- Produces: clicking a plan row toggles a detail card: localized description, equipment/difficulty chips, numbered instructions, coaching cues, common mistakes, and a video guide (embedded iframe for `watch?v=` URLs when online; a link-out row always; localized offline note when `navigator.onLine` is false). Cached sessions without `detail` show a localized "regenerate" hint.

- [ ] **Step 1: Replace `togglePlanDetail` with the full renderer**

Replace the Task 3 `togglePlanDetail` function entirely with:

```js
  var EQUIP_LABELS = { 'racket': '球拍', 'racket, shuttles': '球拍、羽毛球', 'resistance band': '弹力带',
    'medicine ball': '药球', 'dumbbell': '哑铃', 'none': '无器械' };
  var DIFF_LABELS = { beginner: ['入门', 'Beginner'], intermediate: ['进阶', 'Intermediate'], advanced: ['高级', 'Advanced'] };
  function equipLabel(e) { return state.lang === 'zh' ? (EQUIP_LABELS[e] || e) : e; }
  function diffLabel(d) { var m = DIFF_LABELS[d]; return m ? (state.lang==='zh'?m[0]:m[1]) : d; }
  function localList(block) {
    if (!block) { return []; }
    var zh = state.lang === 'zh';
    return (zh ? (block.zh || block.en) : (block.en || block.zh)) || [];
  }
  function videoGuideHTML(url) {
    var zh = state.lang === 'zh';
    var watch = /youtube\.com\/watch\?v=([\w-]{6,})/.exec(url || '');
    var openLink = '<a class="video-link" href="' + url + '" target="_blank" rel="noopener">▶ ' +
      (watch ? (zh?'在 YouTube 打开':'Open on YouTube') : (zh?'查找视频教学':'Find video guides')) + '</a>';
    if (!url) { return ''; }
    if (watch && navigator.onLine) {
      return '<div class="video-wrap"><iframe class="video-embed" src="https://www.youtube-nocookie.com/embed/' + watch[1] + '" ' +
        'title="video guide" loading="lazy" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div>' + openLink;
    }
    if (watch && !navigator.onLine) {
      return '<div class="video-offline muted">' + (zh?'离线状态，视频暂不可用':'Video unavailable offline') + '</div>' + openLink;
    }
    return openLink;
  }
  function togglePlanDetail(si) {
    var el = document.getElementById('plan-detail-' + si); if (!el) { return; }
    if (!el.hidden) { el.hidden = true; return; }
    var zh = state.lang === 'zh';
    var entry = planCtx.flat && planCtx.flat[si]; if (!entry) { return; }
    var d = entry.session.detail;
    if (!d) {
      el.innerHTML = '<p class="muted">' + (zh?'此计划是旧版本生成的——点击「重新生成」查看动作详解。':'This plan was generated before exercise details existed — hit Regenerate to see them.') + '</p>';
      el.hidden = false; return;
    }
    var desc = zh ? (d.description_zh || d.description) : (d.description || d.description_zh);
    var ins = localList(d.instructions).map(function (s) { return '<li>' + s + '</li>'; }).join('');
    var cues = localList(d.coaching_cues).map(function (s) { return '<li>' + s + '</li>'; }).join('');
    var mis = localList(d.common_mistakes).map(function (s) { return '<li>' + s + '</li>'; }).join('');
    el.innerHTML =
      '<div class="chip-row">' +
        (d.difficulty ? '<span class="chip-metric">' + diffLabel(d.difficulty) + '</span>' : '') +
        (d.equipment ? '<span class="chip-metric">' + equipLabel(d.equipment) + '</span>' : '') + '</div>' +
      (desc ? '<p class="plan-desc">' + desc + '</p>' : '') +
      (ins ? '<div class="plan-sub">' + (zh?'怎么做':'How to do it') + '</div><ol class="plan-list">' + ins + '</ol>' : '') +
      (cues ? '<div class="plan-sub">' + (zh?'要点提示':'Coaching cues') + '</div><ul class="plan-list">' + cues + '</ul>' : '') +
      (mis ? '<div class="plan-sub">' + (zh?'常见错误':'Common mistakes') + '</div><ul class="plan-list">' + mis + '</ul>' : '') +
      videoGuideHTML(d.video_url);
    el.hidden = false;
  }
```

- [ ] **Step 2: Add detail CSS to `kestrel.css`**

```css
.plan-detail{background:var(--fill);border:1px solid var(--border);border-radius:var(--radius-sm);
  padding:14px 16px;margin:-2px 0 10px;}
.plan-desc{font-size:13.5px;color:var(--ink);margin:8px 0 0;line-height:1.6;}
.plan-sub{font-size:12px;font-weight:700;color:var(--faint);margin:12px 0 4px;text-transform:uppercase;letter-spacing:.04em;}
.plan-list{margin:0;padding-left:20px;font-size:13.5px;color:var(--muted);line-height:1.7;}
.video-wrap{position:relative;max-width:560px;aspect-ratio:16/9;margin-top:12px;}
.video-embed{position:absolute;inset:0;width:100%;height:100%;border:0;border-radius:var(--radius-sm);}
.video-link{display:inline-block;margin-top:10px;font-size:13px;color:var(--accent);text-decoration:none;}
.video-link:hover{text-decoration:underline;}
.video-offline{margin-top:12px;padding:16px;border:1px dashed var(--border);border-radius:var(--radius-sm);
  text-align:center;font-size:13px;}
```

- [ ] **Step 3: Verify + commit**

Run: `node --check static/kestrel.js` → exit 0.
Start app on 5096; `curl -s http://127.0.0.1:5096/static/kestrel.js | grep -c "videoGuideHTML\|togglePlanDetail\|EQUIP_LABELS"` → non-zero; confirm a regenerated posture plan session carries `detail.video_url`: `curl -s http://127.0.0.1:5096/api/posture-plan/IMG_1270 | grep -c "video_url"` → non-zero (Task 3's POST already regenerated it; if 0, POST again per Task 3 Step 4). Kill the server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): expandable exercise detail cards with bilingual content + video guides"
```

---

### Task 5: Coach Report panel (posture results)

**Files:**
- Modify: `static/kestrel.js` (`renderCoachReport` + mount in `renderPostureResults`)
- Modify: `static/kestrel.css` (report card styles)

**Interfaces:**
- Consumes: `GET /api/posture-report/<stem>?lang=<en|zh-Hant|zh-Hans>` (contract in Global Constraints), downloads at `/api/output/<stem>/posture/coach_report_<lang>.html` and `.pdf`.
- Produces: `renderCoachReport(stem)` paints into `#report-box`: a 3-language tab row (EN / 繁體 / 簡體 — independent of UI language), header line, verdict, strength cards (green left border), weakness cards (red left border, measured vs ideal + mechanism + drill), and HTML/PDF download links. 404 → localized "no report yet" note. Module-scoped `reportCtx = { stem, lang }`.

- [ ] **Step 1: Add the report renderer** (place after the Task 4 helpers, before `openResults`)

```js
  var reportCtx = { stem: null, lang: 'zh-Hans' };
  var REPORT_LANG_TABS = [['en', 'EN'], ['zh-Hant', '繁體'], ['zh-Hans', '簡體']];
  function renderCoachReport(stem) {
    reportCtx.stem = stem;
    var box = document.getElementById('report-box'); if (!box) { return; }
    var zh = state.lang === 'zh';
    box.innerHTML =
      '<div class="plan-head"><h2>' + (zh?'教练报告':'Coach Report') + '</h2>' +
        '<div class="seg" role="tablist" aria-label="report language">' + REPORT_LANG_TABS.map(function (t) {
          return '<button class="seg-btn" data-rlang="' + t[0] + '" role="tab">' + t[1] + '</button>'; }).join('') + '</div>' +
        '<span id="report-dl"></span></div>' +
      '<div id="report-body"><p class="muted">' + (zh?'加载中…':'Loading…') + '</p></div>';
    box.querySelectorAll('[data-rlang]').forEach(function (b) {
      b.onclick = function () { loadCoachReport(b.getAttribute('data-rlang')); };
    });
    loadCoachReport(reportCtx.lang);
  }
  function loadCoachReport(lang) {
    reportCtx.lang = lang;
    var zh = state.lang === 'zh';
    document.querySelectorAll('#report-box [data-rlang]').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-rlang') === lang);
      b.setAttribute('aria-selected', b.getAttribute('data-rlang') === lang ? 'true' : 'false');
    });
    fetch('/api/posture-report/' + reportCtx.stem + '?lang=' + lang)
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        var body = document.getElementById('report-body'); if (!body) { return; }
        if (!d) {
          body.innerHTML = '<p class="muted">' + (zh?'暂无教练报告（完成姿态分析后生成）':'No coach report yet (produced by a posture analysis)') + '</p>';
          var dl0 = document.getElementById('report-dl'); if (dl0) { dl0.innerHTML = ''; }
          return;
        }
        var h = d.header || {}, s = d.summary || {};
        var html = '<div class="report-head mono">' + (h.stroke_label || h.stroke || '') +
          ' · ' + (h.rep_count || 0) + ' reps' + (h.date ? ' · ' + h.date : '') + '</div>';
        if (s.verdict_text) { html += '<p class="report-verdict">' + s.verdict_text + '</p>'; }
        (d.strengths || []).forEach(function (x) {
          html += '<div class="report-card good"><b>' + (x.metric_label || x.metric) + '</b>' +
            (x.impact_label ? ' <span class="chip-metric">' + x.impact_label + '</span>' : '') +
            '<div>' + (x.text || '') + '</div></div>';
        });
        (d.weaknesses || []).forEach(function (x) {
          var ideal = (Array.isArray(x.ideal_range) && x.ideal_range.length >= 2) ? (x.ideal_range[0] + '–' + x.ideal_range[1]) : '';
          html += '<div class="report-card bad"><b>' + (x.metric_label || x.metric) + '</b>' +
            (x.impact_label ? ' <span class="chip-metric">' + x.impact_label + '</span>' : '') +
            '<div class="mono report-nums">' + (x.measured !== null && x.measured !== undefined ? x.measured : '—') +
              (ideal ? ' (' + ideal + ')' : '') + '</div>' +
            (x.mechanism_text ? '<div>' + x.mechanism_text + '</div>' : '') +
            (x.drill_text ? '<div class="report-drill">' + x.drill_text + '</div>' : '') + '</div>';
        });
        body.innerHTML = html;
        var dl = document.getElementById('report-dl');
        if (dl) {
          dl.innerHTML =
            '<a class="video-link" href="/api/output/' + reportCtx.stem + '/posture/coach_report_' + lang + '.html" target="_blank" rel="noopener">HTML</a> ' +
            '<a class="video-link" href="/api/output/' + reportCtx.stem + '/posture/coach_report_' + lang + '.pdf" target="_blank" rel="noopener">PDF</a>';
        }
      }).catch(function () {
        var body = document.getElementById('report-body');
        if (body) { body.innerHTML = '<p class="muted">' + (zh?'加载失败':'Failed to load') + '</p>'; }
      });
  }
```

- [ ] **Step 2: Mount it on the posture results screen**

In `renderPostureResults`, the `body.innerHTML` ending (after Task 3's edit) is:
```js
      '<div class="res-section rep-detail" id="rep-detail"></div>' +
      '<div class="res-section" id="plan-box"></div>';
```
Change it to:
```js
      '<div class="res-section rep-detail" id="rep-detail"></div>' +
      '<div class="res-section" id="plan-box"></div>' +
      '<div class="res-section" id="report-box"></div>';
```
And directly after the existing `renderPlanPanel(stem, 'posture');` line add:
```js
    renderCoachReport(stem);
```

- [ ] **Step 3: Add report CSS to `kestrel.css`**

```css
.report-head{font-size:12.5px;color:var(--faint);margin-bottom:8px;}
.report-verdict{font-size:14px;color:var(--ink);max-width:720px;line-height:1.6;margin:0 0 14px;}
.report-card{background:var(--card);border:1px solid var(--border);border-left-width:4px;border-radius:var(--radius-sm);
  padding:12px 16px;margin-bottom:8px;max-width:720px;font-size:13.5px;color:var(--muted);line-height:1.6;}
.report-card b{color:var(--ink);font-size:14px;}
.report-card.good{border-left-color:var(--good);}
.report-card.bad{border-left-color:var(--bad);}
.report-nums{font-size:12.5px;margin:4px 0;}
.report-drill{font-style:italic;margin-top:6px;}
```

- [ ] **Step 4: Verify + commit**

Run: `node --check static/kestrel.js` → exit 0.
Start app on 5096; `curl -s http://127.0.0.1:5096/static/kestrel.js | grep -c "renderCoachReport\|loadCoachReport\|data-rlang"` → non-zero; data checks: `curl -s "http://127.0.0.1:5096/api/posture-report/IMG_1270?lang=zh-Hans" | head -c 200` → JSON with `header`; `curl -s "http://127.0.0.1:5096/api/posture-report/IMG_1270?lang=nope" -o /dev/null -w "%{http_code}"` → 400; `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5096/api/output/IMG_1270/posture/coach_report_en.html` → 200. Kill the server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): coach report panel with tri-lingual tabs + downloads on posture results"
```

---

## Self-Review

- **Spec coverage (Phase 4):** enriched `exercise_library.json` with `instructions[]`, `coaching_cues[]`, `common_mistakes[]`, `video_url`, bilingual zh/en in plain coaching voice (Task 1) ✓; `generate_plan` carries the enriched detail into plan session items (Task 2) ✓; Training Plan panel with On-Court/At-Home tabs + regenerate on BOTH results layouts (Task 3) ✓; exercise detail rendering + curated external video guides, embedded when online with a graceful offline fallback and link-out (Task 4) ✓; Coach Report panel with EN/繁體/簡體 switcher + HTML/PDF downloads (Task 5) ✓. **Notes + Export stay deferred to Phase 5** per the locked scope.
- **Deviation noted:** the spec imagines every video guide embedded; 4 badminton-specific exercises got real curated `watch?v=` URLs (embeddable, researched from reputable sources), while 11 generic strength/mobility movements ship curated YouTube **search** links (link-out only) — deliberately avoiding fabricated video IDs and link rot. The schema treats `video_url` as an opaque string, so specific videos can be swapped in without code changes; `videoGuideHTML` auto-upgrades any `watch?v=` URL to an embed.
- **Placeholder scan:** Task 3 ships a 3-line `togglePlanDetail` that Task 4 replaces (named, sequenced, harmless — the row click simply un-hides an empty container until Task 4 lands). All other steps carry complete code/content. No TBD/TODO.
- **Type consistency:** `detail` keys identical between Task 2 (producer) and Task 4 (consumer: `name_zh/description/description_zh/equipment/difficulty/instructions/coaching_cues/common_mistakes/video_url`); `planCtx {stem, mode, plan, loc, flat}` consistent across Tasks 3–4; `data-si` ↔ `planCtx.flat` indexes rebuilt together; `renderPlanPanel(stem, mode)` mode values `'match'|'posture'` map to the two route pairs; `reportCtx {stem, lang}` and `data-rlang` values match `_REPORT_LANGS` (`en`, `zh-Hant`, `zh-Hans`); plan JSON field names (`weeks/week/phase/sessions/location/sets/reps/frequency_per_week/duration_min`) match `plan_generator.py` exactly; report field names match the on-disk `coach_report_en.json` shape (`header.stroke_label/rep_count/date`, `summary.verdict_text`, strengths `text`, weaknesses `mechanism_text/drill_text/measured/ideal_range/impact_label`).
- **Backward compatibility verified in-plan:** Task 2's bare-library test locks the `.get()` behavior; Task 4's no-`detail` branch + Task 3's cached-plan curl check cover the on-disk `IMG_1270` plan generated before enrichment.

## Notes for the executor

- Base commit for Phase 4 is `d8d55df` (Phase 3 tip + gitignore chore).
- Backend tasks are TDD with pytest (`161` baseline → `162` after Task 1 → `164` after Task 2). Frontend tasks verify with `node --check` + curl; the real-browser dogfood pass happens at the controller level after Task 5.
- Task 1's JSON is the single source of content — transcribe it exactly (it contains escaped `“`/`”` quotes in three zh strings; keep them escaped or use the literal characters, both are valid JSON).
- The old UI (`web_ui.html`) reads plans too; it ignores unknown session keys, so `detail` is invisible to it — do not touch `web_ui.html`.
- Video embeds use `youtube-nocookie.com` and only render when `navigator.onLine` — the app shell itself stays CDN-free.
