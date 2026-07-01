"""
coach_kb.py — Badminton Coach Knowledge Base
=============================================
Correlates per-rep biomechanical findings to shot-quality impact.
This module is pure data + i18n + lookups; no external dependencies.

Task 1: KB structure + English content.
Task 2: Traditional Chinese / Simplified Chinese translations.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_LANGS = ("en", "zh-Hant", "zh-Hans")

METRICS = (
    "elbow_extension",
    "trunk_rotation",
    "wrist_flexion",
    "knee_flexion",
    "hip_shoulder_separation",
    "weight_transfer",
)

IMPACT_CATEGORIES = ("power", "accuracy", "consistency", "injury_risk")

STROKES = ("high_clear", "smash", "drop_shot", "serve")

# ---------------------------------------------------------------------------
# Generic fallback — absolute last resort, never None
# ---------------------------------------------------------------------------

_GENERIC_FALLBACK = {
    "impact": "consistency",
    "severity_hint": "moderate",
    "mechanism_key": "generic_mech",
    "drill_key": "generic_drill",
}

_GENERIC_STRENGTH = {
    "impact": "consistency",
    "text_key": "generic_strength",
}

# ---------------------------------------------------------------------------
# COACH_KB — weakness entries
# Keys: (metric, stroke_or_"*", direction)
# Values: {"impact", "severity_hint", "mechanism_key", "drill_key"}
# ---------------------------------------------------------------------------

COACH_KB = {
    # ---- elbow_extension ----

    # Stroke-specific
    ("elbow_extension", "smash", "under"): {
        "impact": "power",
        "severity_hint": "high",
        "mechanism_key": "elbow_under_smash_mech",
        "drill_key":     "elbow_under_smash_drill",
    },
    ("elbow_extension", "smash", "over"): {
        "impact": "injury_risk",
        "severity_hint": "high",
        "mechanism_key": "elbow_over_smash_mech",
        "drill_key":     "elbow_over_smash_drill",
    },
    ("elbow_extension", "high_clear", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "elbow_under_clear_mech",
        "drill_key":     "elbow_under_clear_drill",
    },

    # Generic per-direction
    ("elbow_extension", "*", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "elbow_under_generic_mech",
        "drill_key":     "elbow_under_generic_drill",
    },
    ("elbow_extension", "*", "over"): {
        "impact": "injury_risk",
        "severity_hint": "moderate",
        "mechanism_key": "elbow_over_generic_mech",
        "drill_key":     "elbow_over_generic_drill",
    },

    # ---- trunk_rotation ----

    ("trunk_rotation", "smash", "under"): {
        "impact": "power",
        "severity_hint": "high",
        "mechanism_key": "trunk_under_smash_mech",
        "drill_key":     "trunk_under_smash_drill",
    },
    ("trunk_rotation", "smash", "over"): {
        "impact": "accuracy",
        "severity_hint": "moderate",
        "mechanism_key": "trunk_over_smash_mech",
        "drill_key":     "trunk_over_smash_drill",
    },

    ("trunk_rotation", "*", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "trunk_under_generic_mech",
        "drill_key":     "trunk_under_generic_drill",
    },
    ("trunk_rotation", "*", "over"): {
        "impact": "accuracy",
        "severity_hint": "moderate",
        "mechanism_key": "trunk_over_generic_mech",
        "drill_key":     "trunk_over_generic_drill",
    },

    # ---- wrist_flexion ----

    ("wrist_flexion", "smash", "under"): {
        "impact": "power",
        "severity_hint": "high",
        "mechanism_key": "wrist_under_smash_mech",
        "drill_key":     "wrist_under_smash_drill",
    },
    ("wrist_flexion", "smash", "over"): {
        "impact": "injury_risk",
        "severity_hint": "high",
        "mechanism_key": "wrist_over_smash_mech",
        "drill_key":     "wrist_over_smash_drill",
    },
    ("wrist_flexion", "drop_shot", "under"): {
        "impact": "accuracy",
        "severity_hint": "moderate",
        "mechanism_key": "wrist_under_drop_mech",
        "drill_key":     "wrist_under_drop_drill",
    },

    ("wrist_flexion", "*", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "wrist_under_generic_mech",
        "drill_key":     "wrist_under_generic_drill",
    },
    ("wrist_flexion", "*", "over"): {
        "impact": "injury_risk",
        "severity_hint": "moderate",
        "mechanism_key": "wrist_over_generic_mech",
        "drill_key":     "wrist_over_generic_drill",
    },

    # ---- knee_flexion ----

    ("knee_flexion", "serve", "under"): {
        "impact": "accuracy",
        "severity_hint": "moderate",
        "mechanism_key": "knee_under_serve_mech",
        "drill_key":     "knee_under_serve_drill",
    },
    ("knee_flexion", "serve", "over"): {
        "impact": "consistency",
        "severity_hint": "low",
        "mechanism_key": "knee_over_serve_mech",
        "drill_key":     "knee_over_serve_drill",
    },
    ("knee_flexion", "smash", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "knee_under_smash_mech",
        "drill_key":     "knee_under_smash_drill",
    },

    ("knee_flexion", "*", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "knee_under_generic_mech",
        "drill_key":     "knee_under_generic_drill",
    },
    ("knee_flexion", "*", "over"): {
        "impact": "injury_risk",
        "severity_hint": "moderate",
        "mechanism_key": "knee_over_generic_mech",
        "drill_key":     "knee_over_generic_drill",
    },

    # ---- hip_shoulder_separation ----

    ("hip_shoulder_separation", "smash", "under"): {
        "impact": "power",
        "severity_hint": "high",
        "mechanism_key": "hss_under_smash_mech",
        "drill_key":     "hss_under_smash_drill",
    },
    ("hip_shoulder_separation", "high_clear", "under"): {
        "impact": "power",
        "severity_hint": "high",
        "mechanism_key": "hss_under_clear_mech",
        "drill_key":     "hss_under_clear_drill",
    },

    ("hip_shoulder_separation", "*", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "hss_under_generic_mech",
        "drill_key":     "hss_under_generic_drill",
    },
    ("hip_shoulder_separation", "*", "over"): {
        "impact": "accuracy",
        "severity_hint": "low",
        "mechanism_key": "hss_over_generic_mech",
        "drill_key":     "hss_over_generic_drill",
    },

    # ---- weight_transfer ----

    ("weight_transfer", "high_clear", "under"): {
        "impact": "power",
        "severity_hint": "high",
        "mechanism_key": "wt_under_clear_mech",
        "drill_key":     "wt_under_clear_drill",
    },
    ("weight_transfer", "smash", "under"): {
        "impact": "power",
        "severity_hint": "high",
        "mechanism_key": "wt_under_smash_mech",
        "drill_key":     "wt_under_smash_drill",
    },
    ("weight_transfer", "serve", "under"): {
        "impact": "consistency",
        "severity_hint": "moderate",
        "mechanism_key": "wt_under_serve_mech",
        "drill_key":     "wt_under_serve_drill",
    },

    ("weight_transfer", "*", "under"): {
        "impact": "power",
        "severity_hint": "moderate",
        "mechanism_key": "wt_under_generic_mech",
        "drill_key":     "wt_under_generic_drill",
    },
    ("weight_transfer", "*", "over"): {
        "impact": "consistency",
        "severity_hint": "moderate",
        "mechanism_key": "wt_over_generic_mech",
        "drill_key":     "wt_over_generic_drill",
    },
}

# ---------------------------------------------------------------------------
# STRENGTH_KB — positive finding entries
# Keys: (metric, stroke_or_"*")
# Values: {"impact", "text_key"}
# ---------------------------------------------------------------------------

STRENGTH_KB = {
    # Stroke-specific
    ("elbow_extension", "smash"): {
        "impact": "power",
        "text_key": "strength_elbow_smash",
    },
    ("trunk_rotation", "smash"): {
        "impact": "power",
        "text_key": "strength_trunk_smash",
    },
    ("wrist_flexion", "smash"): {
        "impact": "power",
        "text_key": "strength_wrist_smash",
    },
    ("knee_flexion", "smash"): {
        "impact": "power",
        "text_key": "strength_knee_smash",
    },
    ("hip_shoulder_separation", "smash"): {
        "impact": "power",
        "text_key": "strength_hss_smash",
    },
    ("weight_transfer", "high_clear"): {
        "impact": "power",
        "text_key": "strength_wt_clear",
    },
    ("weight_transfer", "smash"): {
        "impact": "power",
        "text_key": "strength_wt_smash",
    },
    ("knee_flexion", "serve"): {
        "impact": "accuracy",
        "text_key": "strength_knee_serve",
    },

    # Generic per-metric
    ("elbow_extension", "*"): {
        "impact": "power",
        "text_key": "strength_elbow_generic",
    },
    ("trunk_rotation", "*"): {
        "impact": "power",
        "text_key": "strength_trunk_generic",
    },
    ("wrist_flexion", "*"): {
        "impact": "power",
        "text_key": "strength_wrist_generic",
    },
    ("knee_flexion", "*"): {
        "impact": "consistency",
        "text_key": "strength_knee_generic",
    },
    ("hip_shoulder_separation", "*"): {
        "impact": "power",
        "text_key": "strength_hss_generic",
    },
    ("weight_transfer", "*"): {
        "impact": "power",
        "text_key": "strength_wt_generic",
    },
}

# ---------------------------------------------------------------------------
# I18N — internationalisation strings
# "en" fully populated; "zh-Hant" and "zh-Hans" left empty for Task 2.
# ---------------------------------------------------------------------------

I18N = {
    "en": {
        # -- Generic fallback texts --
        "generic_mech": (
            "This movement is outside the range we'd expect from someone at your level. "
            "It will show up as errors on shots where you need control under pressure."
        ),
        "generic_drill": (
            "Shadow footwork without the racket: focus on returning to base after every "
            "shadow stroke, maintaining a quiet upper body while the legs do the work."
        ),
        "generic_strength": (
            "Good movement mechanics overall — keep building on this foundation."
        ),

        # -- Section / label / verdict keys --
        "section_weaknesses": "Areas to Improve",
        "section_strengths":  "What You're Doing Well",
        "label_impact":       "Shot impact",
        "label_severity":     "Priority",
        "label_mechanism":    "Why it hurts your game",
        "label_drill":        "Corrective drill",
        "verdict_high":       "Fix this first",
        "verdict_moderate":   "Worth addressing",
        "verdict_low":        "Minor adjustment",

        # ================================================================
        # elbow_extension
        # ================================================================

        # smash, under
        "elbow_under_smash_mech": (
            "You're hitting the smash with a bent elbow, cutting your lever arm short. "
            "A fully extended elbow at contact adds 15-20% racket-head speed — that "
            "missing extension is the difference between a shot that sits up and one "
            "that genuinely threatens the floor."
        ),
        "elbow_under_smash_drill": (
            "Wall-tap drill: stand an arm's length from a wall and tap the shuttlecock "
            "against it using only your forearm snap, consciously locking the elbow "
            "straight at the moment of contact. 3 sets of 20 reps. Once the pattern "
            "feels natural, move to a feeder drill at full court."
        ),

        # smash, over
        "elbow_over_smash_mech": (
            "Hyperextending the elbow past straight adds zero extra power but puts "
            "significant stress on the medial collateral ligament. Over time this "
            "pattern causes chronic elbow pain that sidelines players for weeks."
        ),
        "elbow_over_smash_drill": (
            "Light-resistance band shadow swing: loop a band around your wrist and "
            "anchor it just behind you. The band's pull prevents hyperextension at "
            "the end of the swing. 3 sets of 15 reps focusing on stopping exactly at "
            "full extension, not past it."
        ),

        # high_clear, under
        "elbow_under_clear_mech": (
            "A short-lever overhead clear relies entirely on wrist snap and shoulder "
            "rotation to travel the full court depth. That's asking too much of smaller "
            "muscle groups — the shot either falls short or you fatigue quickly and "
            "accuracy drops on later rallies."
        ),
        "elbow_under_clear_drill": (
            "Throwing drill: throw a shuttlecock overarm to a partner across the full "
            "court, focusing on full arm extension at the point of release. Translate "
            "that same throwing sensation to your overhead swing. 20 throws, then "
            "immediately hit 20 clears while keeping that mental image."
        ),

        # generic, under
        "elbow_under_generic_mech": (
            "Incomplete elbow extension shortens your effective reach and reduces the "
            "angular velocity of the racket head at contact. The result is less power "
            "and a restricted hitting zone that reduces shot options."
        ),
        "elbow_under_generic_drill": (
            "Full-extension shadow swings: in front of a mirror, take 50 shadow swings "
            "per stroke type, checking that the elbow is fully straight at the imaginary "
            "contact point before the follow-through begins."
        ),

        # generic, over
        "elbow_over_generic_mech": (
            "Consistent hyperextension creates a cumulative load on the elbow joint. "
            "Even without acute pain now, this pattern is one of the leading causes of "
            "lateral epicondylitis (tennis/badminton elbow) in recreational players."
        ),
        "elbow_over_generic_drill": (
            "Partner check drill: have your partner lightly place a finger behind your "
            "elbow during slow-motion swing rehearsals. Stop the swing the moment you "
            "feel pressure — that is your safe end-range. Groove 30 reps per session "
            "until muscle memory takes over."
        ),

        # ================================================================
        # trunk_rotation
        # ================================================================

        # smash, under
        "trunk_under_smash_mech": (
            "A smash driven purely by the arm is a short-hitter's smash. Your trunk is "
            "the biggest muscle group involved in overhead power; under-rotating means "
            "you're leaving 30-40% of your potential smash speed on the table. "
            "Opponents at the net level will read and block it easily."
        ),
        "trunk_under_smash_drill": (
            "Hip-shoulder coil drill: stand sideways on to a target, coil the hips fully "
            "away from it before each swing, then unwind hips first — shoulder follows — "
            "then arm. Use a foam ball so you can focus entirely on the rotation sequence "
            "without worrying about shuttle placement. 3 sets of 12."
        ),

        # smash, over
        "trunk_over_smash_mech": (
            "Over-rotating the trunk past the hitting position pulls the racket face "
            "across the shuttle at contact, imparting lateral spin that sends the smash "
            "wide. You'll see the shot drift to the tram lines under pressure."
        ),
        "trunk_over_smash_drill": (
            "Target cone drill: place a cone 1 metre in front of the net on the centre "
            "line. Every smash must land within 60 cm of the cone. The accuracy feedback "
            "will train you to stop rotation at the right moment — quality over speed for "
            "the first two weeks."
        ),

        # generic, under
        "trunk_under_generic_mech": (
            "Limited trunk rotation isolates power generation to the arm and shoulder. "
            "Those muscles fatigue in long matches, and the shots they produce lack the "
            "pace and depth that threaten a competent opponent."
        ),
        "trunk_under_generic_drill": (
            "Medicine ball rotational throw: stand sideways to a wall and throw a 2 kg "
            "medicine ball against it using only trunk rotation — no arm push at the end. "
            "3 sets of 10 each side. Translates directly to the hip-to-shoulder chain "
            "used in every overhead shot."
        ),

        # generic, over
        "trunk_over_generic_mech": (
            "Excessive trunk rotation past the ideal plane rotates the shoulders beyond "
            "the hitting position, pulling the racket face off-line. Consistency suffers "
            "most — even when power is fine, direction becomes unreliable."
        ),
        "trunk_over_generic_drill": (
            "Bounded rotation drill: tie a light elastic band between your elbows and "
            "hold them at 90 degrees. The band acts as a cue to stop rotation when it "
            "goes taut. Practice 25 shadow swings per stroke per session."
        ),

        # ================================================================
        # wrist_flexion
        # ================================================================

        # smash, under
        "wrist_under_smash_mech": (
            "The wrist snap at contact is your final speed multiplier — it adds roughly "
            "25% to racket-head velocity in the last few centimetres before impact. "
            "Hitting a smash without full wrist flexion is like revving a car in fourth "
            "gear from rest: the engine is working but you're not using the full "
            "transmission."
        ),
        "wrist_under_smash_drill": (
            "Wrist-snap feeder drill: hold the shaft of the racket mid-grip and have a "
            "partner hold the head still. Practice the isolated wrist-flick motion 20 "
            "times. Then move to a full swing, consciously initiating that same snap at "
            "the moment you feel the strings about to contact the shuttle. 4 sets of 15."
        ),

        # smash, over
        "wrist_over_smash_mech": (
            "Snapping the wrist beyond the neutral follow-through range strains the "
            "flexor tendons along the forearm. Repeatedly doing this under load — "
            "especially on jump smashes — is a direct path to wrist tendinitis."
        ),
        "wrist_over_smash_drill": (
            "Controlled finish rehearsal: in slow motion, take the wrist only to the "
            "natural follow-through position (roughly 45 degrees past neutral), then "
            "hold for two seconds. Repeat 20 times with eyes closed so proprioception "
            "builds the range limit into muscle memory."
        ),

        # drop_shot, under
        "wrist_under_drop_mech": (
            "A drop shot needs precise deceleration of the racket head at contact to "
            "die quickly over the net. Without enough wrist control — specifically, "
            "the ability to absorb rather than snap — the shuttle carries too far and "
            "sits up in mid-court, giving the opponent an easy attack."
        ),
        "wrist_under_drop_drill": (
            "Touch feeder drill: stand at the net and gently brush a stationary shuttle "
            "off a feeder's hand, aiming for it to fall within 50 cm of the net tape. "
            "Focus entirely on relaxing the wrist at contact. 3 sets of 20."
        ),

        # generic, under
        "wrist_under_generic_mech": (
            "Under-flexing the wrist reduces racket-head speed at contact and limits "
            "your ability to change shot angle late. Both hurt the breadth of your "
            "attacking repertoire."
        ),
        "wrist_under_generic_drill": (
            "Wrist isolation flicks: with a racket held at arm's length and the elbow "
            "locked, flick the racket head up and down using only wrist movement. "
            "3 sets of 30. Builds isolated wrist strength and range of motion."
        ),

        # generic, over
        "wrist_over_generic_mech": (
            "Excessive wrist flexion beyond the safe range loads the carpal tunnel and "
            "flexor tendons. Tendinitis and, in serious cases, carpal tunnel syndrome "
            "are the endpoint of this pattern if left uncorrected."
        ),
        "wrist_over_generic_drill": (
            "Range-awareness drill: tape a small marker on the back of your wrist. "
            "When the marker points straight back at you (not downward), you're at "
            "maximum safe flexion. Check this position 10 times before each practice "
            "session until your body knows the limit."
        ),

        # ================================================================
        # knee_flexion
        # ================================================================

        # serve, under
        "knee_under_serve_mech": (
            "A stiff-legged serve stance locks the hips and pelvis, preventing the "
            "slight forward lean that keeps the shuttle on a controlled trajectory. "
            "The result is a serve that tends to rise higher than intended, sitting "
            "up for the receiver to attack with a flat interception."
        ),
        "knee_under_serve_drill": (
            "Serve stance checkpoint: before each serve in practice, consciously sink "
            "into a slight knee bend (roughly 20-30 degrees) and hold that position for "
            "a full breath before initiating the swing. Over 50 practice serves, this "
            "becomes the automatic launch position."
        ),

        # serve, over
        "knee_over_serve_mech": (
            "Excessive knee bend on a serve shifts body weight forward too early, "
            "causing the swing to bottom out before the shuttle — the shuttle is "
            "clipped with a descending face and service height and length become "
            "inconsistent."
        ),
        "knee_over_serve_drill": (
            "Posture mirror drill: serve in front of a full-length mirror and check "
            "that the knee angle matches the target range (roughly hip-width stance, "
            "slight bend). Adjust until the mirror confirms it, then hit 30 serves "
            "maintaining that position."
        ),

        # smash, under
        "knee_under_smash_mech": (
            "Jumping into a smash with minimal knee load reduces jump height and "
            "shortens the time you spend at the apex — your ideal contact window. "
            "Lower contact means a flatter angle, which makes the smash easier to read "
            "and gives the opponent more time to react."
        ),
        "knee_under_smash_drill": (
            "Squat-to-jump smash drill: from a half-squat, drive upward and hit a full "
            "smash at the apex. The emphasis is on using the knee load as the launch "
            "pad, not the arm. 3 sets of 8."
        ),

        # generic, under
        "knee_under_generic_mech": (
            "Insufficient knee flexion reduces leg-drive contribution to the stroke. "
            "The legs are the body's largest muscle group; not recruiting them means "
            "relying on arm strength alone, which fatigues faster and generates less "
            "overall power."
        ),
        "knee_under_generic_drill": (
            "Lunge-to-stroke drill: feed yourself a shuttle while stepping into a full "
            "lunge; the rule is the stroke must complete before the back knee touches "
            "the floor. Forces maximal knee bend and leg engagement. 3 sets of 10."
        ),

        # generic, over
        "knee_over_generic_mech": (
            "Repeatedly loading the knee beyond the safe bend angle — especially on "
            "jump landings — increases patellofemoral joint stress. Persistent deep-bend "
            "landings are the main cause of patellar tendinitis in jumping sports."
        ),
        "knee_over_generic_drill": (
            "Controlled landing drill: step off a 20 cm box and land with a soft, "
            "controlled bend stopping at roughly 90 degrees. Hold that position for "
            "2 seconds before standing. 3 sets of 10. Builds landing proprioception "
            "that carries over to jump smash landings."
        ),

        # ================================================================
        # hip_shoulder_separation
        # ================================================================

        # smash, under
        "hss_under_smash_mech": (
            "Hip-shoulder separation is the 'X-factor' angle that stores elastic energy "
            "in the trunk musculature before you unleash it. Small separation on a smash "
            "means you release no elastic snap — the shot is all arm, giving up the "
            "single biggest free source of overhead power available to a badminton player."
        ),
        "hss_under_smash_drill": (
            "Coil and hold: from the smash ready position, rotate the hips to face the "
            "back fence while keeping the shoulders facing the net. Hold this stretched "
            "position for 3 seconds. Then swing. 4 sets of 10. The hold prevents "
            "premature shoulder rotation and trains the coil pattern."
        ),

        # high_clear, under
        "hss_under_clear_mech": (
            "Generating depth on a high clear without proper hip-shoulder separation "
            "forces the shoulder to do all the lifting. The shot often comes out flat "
            "rather than arching high, landing in the mid-court where it is easily read "
            "and attacked."
        ),
        "hss_under_clear_drill": (
            "Stagger-stance clear drill: hit clears from an exaggerated staggered "
            "stance (opposite foot well forward). The stance mechanically enforces "
            "hip turn before shoulder turn. Hit 30 clears per session from this stance "
            "before returning to normal footwork."
        ),

        # generic, under
        "hss_under_generic_mech": (
            "Without adequate hip-shoulder separation, the hips and shoulders fire "
            "together as a block instead of in sequence. That sequence matters: hips "
            "turn first, then trunk, then shoulder, then arm. Collapse it into one "
            "simultaneous move and you give up most of the rotational speed before "
            "the arm even gets involved."
        ),
        "hss_under_generic_drill": (
            "Seated rotation drill: sit on a bench with your feet flat on the floor. "
            "Rotate the shoulders as far right as possible while keeping the hips "
            "square. Hold for 2 seconds. 3 sets of 10 per side. Isolates trunk mobility "
            "separate from lower-body movement."
        ),

        # generic, over
        "hss_over_generic_mech": (
            "Too much hip-shoulder separation tilts the pelvis sideways during the stroke, "
            "shifting your weight mid-swing. Your body is still correcting its balance when "
            "the racket meets the shuttle, so the face angle is never quite where you aimed. "
            "The shots go in roughly the right direction, but the precision drops."
        ),
        "hss_over_generic_drill": (
            "Alignment check drill: place a strip of tape on the floor indicating the "
            "baseline hip direction. After each stroke, check that your hips returned to "
            "alignment within one step. 3 sets of 15 strokes with a partner calling out "
            "any drift."
        ),

        # ================================================================
        # weight_transfer
        # ================================================================

        # high_clear, under
        "wt_under_clear_mech": (
            "A high clear with no weight transfer is essentially a static lift. The "
            "shuttle has to travel to the back tramline — that's over 13 metres at a "
            "high arc. Without driving your body mass forward into the stroke, you are "
            "fighting court geometry with arm strength alone, and the shuttle will "
            "reliably fall short on your harder days."
        ),
        "wt_under_clear_drill": (
            "Step-through clear drill: after striking a clear, your front foot must "
            "cross the back foot by at least 30 cm before the shuttle lands. This "
            "forces the momentum to continue through the shot. Hit 40 clears per "
            "session with this constraint until the step-through is automatic."
        ),

        # smash, under
        "wt_under_smash_mech": (
            "Smashing from a stationary or backward-weighted base means the arms and "
            "trunk alone provide force. A proper smash loads from the ground up: "
            "push off the floor, drive through the hip, and let the arm be the final "
            "whip. Remove the ground-push and you've halved the kinetic chain."
        ),
        "wt_under_smash_drill": (
            "Jump-land smash drill: take a single two-foot jump before each smash and "
            "ensure you contact the shuttle on the descent, driving your weight downward "
            "into it. Constraint: if you land behind the hitting spot, the rep doesn't "
            "count. 3 sets of 10."
        ),

        # serve, under
        "wt_under_serve_mech": (
            "A serve without forward weight transfer tends to be inconsistent in length. "
            "Without the trunk moving forward to deliver the racket, small variations in "
            "wrist angle become amplified, scattering the serve between too short and "
            "just legal."
        ),
        "wt_under_serve_drill": (
            "Rocking serve drill: start with weight on the back foot, rock forward as "
            "you initiate the swing, and ensure weight has fully transferred to the "
            "front foot by contact. Hit 50 serves per session with a partner checking "
            "your weight is forward at the moment the shuttle leaves the racket."
        ),

        # generic, under
        "wt_under_generic_mech": (
            "Without weight transfer, every shot comes from isolated arm and shoulder "
            "movement. Those muscles are smaller and they tire faster. Against a strong "
            "defender you'll feel the difference by the third game: the shots that were "
            "landing deep start coming up short."
        ),
        "wt_under_generic_drill": (
            "Shadow lunge drill: for each stroke direction, step into the stroke with a "
            "deliberate weight transfer onto the lunging foot. Hold the end position for "
            "one count before recovering. 5 directions × 10 reps."
        ),

        # generic, over
        "wt_over_generic_mech": (
            "Over-committing weight in one direction leaves you off-balance after the "
            "shot. Recovery to base takes longer, giving opponents the time to exploit "
            "the gap. In particular, over-transferred smash weight makes cross-court "
            "net replies almost unreachable."
        ),
        "wt_over_generic_drill": (
            "Recovery check drill: after every stroke, call out your target base "
            "position aloud and take exactly two steps to reach it. If you're stumbling "
            "or taking three steps, the weight transfer was excessive. 30-shot shadow "
            "routine with partner feedback."
        ),

        # ================================================================
        # STRENGTH text keys
        # ================================================================

        "strength_elbow_smash": (
            "Full elbow extension at smash contact — you're using the full lever arm "
            "and generating maximum racket-head speed. Keep this."
        ),
        "strength_trunk_smash": (
            "Good trunk rotation on the smash. You're loading the kinetic chain correctly "
            "and the hips are driving ahead of the shoulders."
        ),
        "strength_wrist_smash": (
            "Strong wrist snap at contact on your smash. That final flick is where "
            "the real pace comes from, and you're using it well."
        ),
        "strength_knee_smash": (
            "Good knee load before the jump. You're using the legs as a power source, "
            "not just the arm."
        ),
        "strength_hss_smash": (
            "Solid hip-shoulder separation. The coil is there and you're releasing it "
            "in sequence — that's the mark of an efficient overhead striker."
        ),
        "strength_wt_clear": (
            "Weight driving forward through the clear — you're using full-body momentum "
            "to reach the back of the court without over-relying on arm strength."
        ),
        "strength_wt_smash": (
            "Good downward weight transfer on the smash. You're using gravity and "
            "body mass, not just muscle, to put pace through the shuttle."
        ),
        "strength_knee_serve": (
            "Consistent knee position on the serve. A stable base means a repeatable "
            "swing path and that shows in your serve accuracy."
        ),
        "strength_elbow_generic": (
            "Elbow extension is in the right range. The lever arm is working for you."
        ),
        "strength_trunk_generic": (
            "Trunk rotation looks controlled. You're rotating enough to contribute "
            "power without losing direction."
        ),
        "strength_wrist_generic": (
            "Wrist mechanics are sound. Good range without over-flexing."
        ),
        "strength_knee_generic": (
            "Knee bend is within the productive range. Leg drive is available and "
            "you're using it."
        ),
        "strength_hss_generic": (
            "Hip-shoulder separation is adequate. The trunk is contributing to "
            "stroke production."
        ),
        "strength_wt_generic": (
            "Weight transfer is well-timed. Ground reaction force is feeding into the "
            "stroke rather than being wasted."
        ),

        # ================================================================
        # stroke labels
        # ================================================================
        "stroke_smash": "Smash",
        "stroke_high_clear": "High Clear",
        "stroke_drop_shot": "Drop Shot",
        "stroke_serve": "Serve",

        # ================================================================
        # metric labels
        # ================================================================
        "metric_elbow_extension": "Elbow Extension",
        "metric_trunk_rotation": "Trunk Rotation",
        "metric_wrist_flexion": "Wrist Flexion",
        "metric_knee_flexion": "Knee Flexion",
        "metric_hip_shoulder_separation": "Hip-Shoulder Separation",
        "metric_weight_transfer": "Weight Transfer",

        # ================================================================
        # impact category labels
        # ================================================================
        "impact_power": "Power",
        "impact_accuracy": "Accuracy",
        "impact_consistency": "Consistency",
        "impact_injury_risk": "Injury Risk",

        # ================================================================
        # verdict texts — {score} and {consistency} placeholders available
        # ================================================================
        "verdict_insufficient": (
            "Not enough data to assess — no reps recorded."
        ),
        "verdict_strong": (
            "Strong execution (score {score}, consistency ±{consistency}). "
            "Keep reinforcing these patterns under match pressure."
        ),
        "verdict_developing": (
            "Developing (score {score}, consistency ±{consistency}). "
            "The foundation is there; focus on the priority corrections below."
        ),
        "verdict_needs_work": (
            "Needs work (score {score}, consistency ±{consistency}). "
            "Address the key weaknesses before moving on to advanced drills."
        ),
    },

    # Task 2: Traditional Chinese (Taiwan / Hong Kong coaching register)
    "zh-Hant": {
        # -- Generic fallback texts --
        "generic_mech": (
            "這個動作超出了我們預期的範圍。"
            "在壓力下需要精準控制的球路，這個問題會以失誤的形式暴露出來。"
        ),
        "generic_drill": (
            "無球步法訓練：每次陰影步伐結束後確實回到中心位，"
            "上半身保持放鬆穩定，讓腿部主導移動。"
        ),
        "generic_strength": (
            "整體動作協調不錯，繼續保持這個基礎。"
        ),

        # -- Section / label / verdict keys --
        "section_weaknesses": "需要改善的地方",
        "section_strengths":  "做得好的地方",
        "label_impact":       "對球路的影響",
        "label_severity":     "優先程度",
        "label_mechanism":    "為什麼會影響你的表現",
        "label_drill":        "改善訓練動作",
        "verdict_high":       "優先修正",
        "verdict_moderate":   "值得改善",
        "verdict_low":        "小幅調整",

        # ================================================================
        # elbow_extension
        # ================================================================

        # smash, under
        "elbow_under_smash_mech": (
            "你在殺球時手肘沒有完全伸直，槓桿臂縮短了。"
            "完全伸直的手肘在擊球瞬間可以增加約15至20%的球頭速度——"
            "少了這個伸展，殺球就只是把球送過網，而不是真正威脅地板的重殺。"
        ),
        "elbow_under_smash_drill": (
            "牆壁點擊訓練：距牆一臂之距站立，用前臂甩動把羽球打在牆上，"
            "在擊球瞬間有意識地將手肘鎖定打直。做3組，每組20次。"
            "動作熟練後改為全場餵球訓練。"
        ),

        # smash, over
        "elbow_over_smash_mech": (
            "手肘過度伸展超過打直位置，不會增加任何力量，"
            "反而對內側副韌帶造成明顯的壓力。長期下來這個習慣會引發慢性肘痛，"
            "讓你不得不休息好幾個星期。"
        ),
        "elbow_over_smash_drill": (
            "輕阻力彈力帶陰影揮拍：把彈力帶繞在手腕上，"
            "固定點在身後稍低處。帶子的拉力可以防止揮拍末端過度伸展。"
            "做3組，每組15次，專注在手肘剛好打直的位置停下來，不要再往後。"
        ),

        # high_clear, under
        "elbow_under_clear_mech": (
            "手肘彎曲的後場高球完全依靠手腕甩動和肩膀轉動來飛越整個場地，"
            "這對小肌群來說負擔太大——球要嘛飛不夠遠，"
            "要嘛你很快就會疲勞，後面幾局的準確度大幅下滑。"
        ),
        "elbow_under_clear_drill": (
            "拋球訓練：用過臂拋球的方式把羽球丟給對面的訓練夥伴，"
            "在放開球的瞬間專注於手臂完全伸直。"
            "把這個拋球的感覺帶入你的後場揮拍動作。"
            "連續拋20次，然後立刻打20球高球，保持同樣的身體感覺。"
        ),

        # generic, under
        "elbow_under_generic_mech": (
            "手肘伸展不足會縮短有效的觸球範圍，降低擊球瞬間球頭的角速度。"
            "結果是力量不夠，擊球區域受限，可以打的球路選擇也跟著變少。"
        ),
        "elbow_under_generic_drill": (
            "完全伸展陰影揮拍：在鏡子前對每種球路各做50次陰影揮拍，"
            "確認在假想的擊球點、跟隨動作開始之前，手肘已經完全打直。"
        ),

        # generic, over
        "elbow_over_generic_mech": (
            "持續的過度伸展會累積對肘關節的負荷。"
            "就算現在還沒有急性疼痛，這個動作習慣是業餘球員出現"
            "外上髁炎（網球肘／羽球肘）的主要原因之一。"
        ),
        "elbow_over_generic_drill": (
            "夥伴檢查訓練：請訓練夥伴在你做慢動作揮拍練習時，"
            "輕輕把手指放在你手肘後方。當你感覺到壓力時停下揮拍，"
            "那就是你安全的末端範圍。每次練習做30次，直到肌肉記憶建立起來。"
        ),

        # ================================================================
        # trunk_rotation
        # ================================================================

        # smash, under
        "trunk_under_smash_mech": (
            "單靠手臂的殺球是力量不足的殺球。軀幹是頭頂出力最大的肌肉群；"
            "轉體不足代表你白白放棄了30至40%的殺球潛在速度。"
            "對方在網前的球員很容易就能預判並攔截。"
        ),
        "trunk_under_smash_drill": (
            "髖肩纏繞訓練：側身對著目標站立，每次揮拍前先把髖部充分轉離目標方向，"
            "然後依序展開：先髖部，再肩膀，最後手臂。"
            "用泡棉球練習，這樣可以完全專注在轉體順序上，不用擔心球的位置。"
            "做3組，每組12次。"
        ),

        # smash, over
        "trunk_over_smash_mech": (
            "軀幹過度轉超過擊球位置，會使球拍面在擊球時橫向劃過球，"
            "賦予側旋，讓殺球偏向邊線。你會發現在壓力下球路會飄向邊線區。"
        ),
        "trunk_over_smash_drill": (
            "目標錐筒訓練：在網前中線放一個錐筒。每一球殺球都必須落在錐筒60公分以內。"
            "準確度的回饋會訓練你在正確時機停止轉體——頭兩週先求品質，不求速度。"
        ),

        # generic, under
        "trunk_under_generic_mech": (
            "軀幹轉動不足，出力完全集中在手臂和肩膀。"
            "這些肌群在長時間比賽中容易疲勞，"
            "打出來的球也缺乏讓對手難以應付的速度和深度。"
        ),
        "trunk_under_generic_drill": (
            "藥球旋轉拋擲：側身靠近牆壁，用2公斤的藥球只靠軀幹轉動拋向牆壁，"
            "末端不要加手臂推力。每側各做3組，每組10次。"
            "這個動作直接對應每次頭頂球使用的髖肩動力鏈。"
        ),

        # generic, over
        "trunk_over_generic_mech": (
            "軀幹過度轉體超過理想平面，會讓肩膀偏離擊球位置，球拍面也跟著偏移。"
            "穩定性受影響最大——就算力量沒問題，球路方向也會變得不可預測。"
        ),
        "trunk_over_generic_drill": (
            "有界轉體訓練：把輕彈力帶綁在兩手肘之間，手肘維持90度。"
            "彈力帶拉緊時就是轉體該停下來的提示。每次練習對每種球路做25次陰影揮拍。"
        ),

        # ================================================================
        # wrist_flexion
        # ================================================================

        # smash, under
        "wrist_under_smash_mech": (
            "手腕甩動是你最後的加速器——在擊球前幾公分能增加約25%的球頭速度。"
            "殺球時手腕不完全屈曲，就像從靜止起步用高速檔起跑的車："
            "引擎在運作，但你沒有用到完整的傳動系統。"
        ),
        "wrist_under_smash_drill": (
            "手腕甩動孤立訓練：握住球拍中段，由訓練夥伴扶住拍頭，"
            "單獨練習手腕甩動20次。然後換為完整揮拍，"
            "在感覺到即將觸球的瞬間有意識地啟動同樣的甩動。做4組，每組15次。"
        ),

        # smash, over
        "wrist_over_smash_mech": (
            "手腕甩動超過自然跟隨動作的範圍，會拉扯前臂的屈肌肌腱。"
            "在負重下反覆這樣做——尤其是跳躍殺球——是引發手腕肌腱炎的直接路徑。"
        ),
        "wrist_over_smash_drill": (
            "受控收拍練習：慢動作下，只把手腕帶到自然跟隨動作的位置（約中性位後45度），"
            "然後定住2秒。閉眼重複20次，讓本體感覺把安全範圍刻進肌肉記憶。"
        ),

        # drop_shot, under
        "wrist_under_drop_mech": (
            "吊球需要在擊球時精準地讓球頭減速，讓球緊貼網頂落下。"
            "如果手腕控制不夠——特別是收力而非甩動的能力——"
            "球會飛太遠停在中場，給對手容易的進攻機會。"
        ),
        "wrist_under_drop_drill": (
            "輕觸餵球訓練：站在網邊，輕輕把餵球者手上靜止的球刷過網，"
            "目標是讓球落在距網帶50公分以內。完全專注在擊球時放鬆手腕。做3組，每組20次。"
        ),

        # generic, under
        "wrist_under_generic_mech": (
            "手腕屈曲不足，擊球時球頭速度偏低，"
            "也限制了你在最後瞬間改變球路角度的能力。這兩點都傷害你進攻的多樣性。"
        ),
        "wrist_under_generic_drill": (
            "手腕孤立甩動：手臂打直握拍並鎖定手肘，"
            "只用手腕動作讓球頭上下甩動。做3組，每組30次。訓練手腕力量和活動範圍。"
        ),

        # generic, over
        "wrist_over_generic_mech": (
            "手腕過度屈曲超過安全範圍，對腕隧道和屈肌肌腱造成壓力。"
            "如果不加以修正，這個習慣最終會導致肌腱炎，嚴重時甚至是腕隧道症候群。"
        ),
        "wrist_over_generic_drill": (
            "範圍感知訓練：在手背貼一個小標記。"
            "當標記朝向正後方（而非向下）時，就是安全的最大屈曲位置。"
            "每次練習前確認這個位置10次，直到身體熟悉這個界限。"
        ),

        # ================================================================
        # knee_flexion
        # ================================================================

        # serve, under
        "knee_under_serve_mech": (
            "發球時腿打直，髖部和骨盆鎖死，無法做出讓球走出可控弧線的微微前傾。"
            "結果是發球比預期飛得更高，在中場浮起，讓對手可以用平直截擊進攻。"
        ),
        "knee_under_serve_drill": (
            "發球站姿確認：練習賽中每次發球前，"
            "刻意下沉成微微屈膝的站姿（約20到30度）並保持一個完整呼吸再出手。"
            "累積50次發球練習後，這個姿勢自然會成為你的出發點。"
        ),

        # serve, over
        "knee_over_serve_mech": (
            "發球時屈膝太多，重心過早前移，"
            "讓揮拍在球的下方就提前到達最低點——球被拍面從下方切到，"
            "發球高度和長度都變得不穩定。"
        ),
        "knee_over_serve_drill": (
            "鏡子站姿訓練：在全身鏡前發球，確認膝蓋角度符合目標範圍"
            "（大約與肩同寬，微微彎曲）。調整到鏡子確認正確後，"
            "保持這個站姿打30球。"
        ),

        # smash, under
        "knee_under_smash_mech": (
            "起跳殺球時屈膝負荷不足，跳躍高度下降，"
            "也縮短了在最高點停留的時間——那才是最理想的擊球窗口。"
            "接觸點偏低意味著球路角度偏平，對手更容易預判，反應時間也更充裕。"
        ),
        "knee_under_smash_drill": (
            "蹲跳殺球訓練：從半蹲姿勢向上跳起，在最高點完成完整殺球。"
            "重點在於把屈膝當成起跳台而不是靠手臂發力。做3組，每組8次。"
        ),

        # generic, under
        "knee_under_generic_mech": (
            "屈膝不足，腿部對球路的出力貢獻減少。"
            "腿是身體最大的肌肉群；不動用它們，就只能靠手臂力量，"
            "疲勞更快，整體力量也更小。"
        ),
        "knee_under_generic_drill": (
            "弓步出球訓練：自行餵球，踏入完整弓步的同時出手擊球；"
            "規則是後腳膝蓋觸地之前球路必須完成。強制最大屈膝和腿部出力。做3組，每組10次。"
        ),

        # generic, over
        "knee_over_generic_mech": (
            "反覆在過大的屈膝角度下負重——尤其是跳躍落地時——"
            "會增加髕股關節的壓力。持續的深屈膝落地是跳躍性運動出現髕腱炎的主要原因。"
        ),
        "knee_over_generic_drill": (
            "受控落地訓練：從20公分的踏台跳下，以柔和可控的屈膝落地，"
            "在約90度時定住2秒再站起。做3組，每組10次。"
            "訓練落地本體感覺，直接應用到跳躍殺球的落地。"
        ),

        # ================================================================
        # hip_shoulder_separation
        # ================================================================

        # smash, under
        "hss_under_smash_mech": (
            "髖肩分離角是在揮拍前將彈性能量儲存於軀幹肌肉的關鍵角度。"
            "殺球時分離不足，就沒有彈性反彈可以釋放——整個球路全靠手臂，"
            "白白放掉了羽球運動員在頭頂球中最大的免費動力來源。"
        ),
        "hss_under_smash_drill": (
            "纏繞定住訓練：從殺球預備姿勢，轉動髖部朝向後方圍欄，"
            "同時肩膀繼續面對球網。維持這個拉伸姿勢3秒，然後揮拍。"
            "做4組，每組10次。定住的動作防止肩膀過早轉動，訓練纏繞模式。"
        ),

        # high_clear, under
        "hss_under_clear_mech": (
            "在沒有適當髖肩分離的情況下，要打出有深度的後場高球，"
            "肩膀必須獨力完成所有的提升工作。球路常常出來太平，"
            "落在中場容易被預判和進攻。"
        ),
        "hss_under_clear_drill": (
            "錯步站姿後場高球訓練：用誇張的前後步法站姿（異側腳大幅前跨）打高球。"
            "這個站姿從結構上強制在肩轉之前先轉髖。"
            "每次練習用這個站姿打30球，再回到正常步法。"
        ),

        # generic, under
        "hss_under_generic_mech": (
            "髖肩分離不足，軀幹動力鏈會整體同時啟動，而不是依序觸發。"
            "那個順序才是重點：先髖，再軀幹，再肩，再手臂。"
            "把它壓縮成同一個動作，手臂還沒參與，旋轉速度就已經大半消失了。"
        ),
        "hss_under_generic_drill": (
            "坐姿旋轉訓練：坐在椅子上，腳踏平地。"
            "肩膀盡量往右轉，同時保持髖部不動。"
            "維持2秒。每側做3組，每組10次。孤立訓練軀幹活動度，與下肢動作分開。"
        ),

        # generic, over
        "hss_over_generic_mech": (
            "髖肩分離過大會讓骨盆在揮拍中橫向傾斜，"
            "在揮拍中途改變重心。你的身體還在修正平衡時，球拍已經碰到球了，"
            "球拍面角度就不在你原本瞄準的位置。方向大致對，但精準度下降了。"
        ),
        "hss_over_generic_drill": (
            "對齊檢查訓練：在地板上貼一條膠帶標示基準線髖部方向。"
            "每次出球後確認髖部在一步內回到對齊位置。"
            "由訓練夥伴在旁喊出偏移，做3組，每組15球。"
        ),

        # ================================================================
        # weight_transfer
        # ================================================================

        # high_clear, under
        "wt_under_clear_mech": (
            "沒有重心轉移的後場高球基本上是一個靜止的抬球動作。"
            "球要飛到底線——超過13公尺，還要飛高弧線。"
            "不把身體重量往前帶入球路，你就只能靠手臂對抗場地幾何，"
            "狀態差的時候球一定會落在中場。"
        ),
        "wt_under_clear_drill": (
            "跟步後場高球訓練：打出高球後，前腳必須在球落地之前超越後腳至少30公分。"
            "這強制讓動量持續帶入球路。每次練習打40球並加入這個限制，"
            "直到跟步動作變成自然反應。"
        ),

        # smash, under
        "wt_under_smash_mech": (
            "從靜止或重心偏後的姿勢殺球，出力只能靠手臂和軀幹。"
            "正確的殺球是從地板往上帶力：蹬地、穿過髖部，手臂只是最後的鞭打動作。"
            "少了蹬地，動力鏈就只剩一半。"
        ),
        "wt_under_smash_drill": (
            "跳躍落地殺球訓練：每次殺球前做一個雙腳跳，"
            "確認在下降過程中觸球，把重量向下壓入球。"
            "限制：落地點在擊球位置後方的，那次不計數。做3組，每組10次。"
        ),

        # serve, under
        "wt_under_serve_mech": (
            "沒有向前重心轉移的發球，長度容易不穩定。"
            "缺少軀幹帶球拍前移的動作，手腕角度的微小變化就會被放大，"
            "讓發球在太短和剛好合法之間飄移。"
        ),
        "wt_under_serve_drill": (
            "搖擺發球訓練：以重心在後腳開始，揮拍啟動時往前搖擺，"
            "確認擊球時重心已完全轉移到前腳。"
            "每次練習打50球，由訓練夥伴確認球離拍時重心是否已在前方。"
        ),

        # generic, under
        "wt_under_generic_mech": (
            "沒有重心轉移，每一球都靠孤立的手臂和肩膀動作出力。"
            "這些肌群比較小，也更快疲勞。面對防守強的對手，"
            "差別會在第三局顯現出來：原本打到後場的球開始落在中場。"
        ),
        "wt_under_generic_drill": (
            "陰影弓步訓練：針對每個球路方向，踏入帶有明確重心轉移的弓步；"
            "在恢復之前在末端姿勢停一拍。5個方向，每個方向10次。"
        ),

        # generic, over
        "wt_over_generic_mech": (
            "在同一方向過度壓重心，打完球後你的平衡就已經垮了。"
            "回到中心位需要更長時間，對手有充裕時機利用空檔。"
            "特別是殺球時過度轉移的重心，讓斜線網前回球幾乎追不到。"
        ),
        "wt_over_generic_drill": (
            "恢復確認訓練：每次出球後大聲說出目標中心位，然後用剛好兩步走到。"
            "如果你在踉蹌或需要三步，代表重心轉移過頭了。"
            "30球陰影練習，由訓練夥伴給回饋。"
        ),

        # ================================================================
        # STRENGTH text keys
        # ================================================================

        "strength_elbow_smash": (
            "殺球擊球時手肘完全伸直——槓桿臂完整，球頭速度達到最大。繼續保持。"
        ),
        "strength_trunk_smash": (
            "殺球時軀幹轉體很好。動力鏈啟動正確，髖部帶著肩膀走。"
        ),
        "strength_wrist_smash": (
            "殺球擊球時手腕甩動有力。那個最後的甩動才是真正速度的來源，你用得很好。"
        ),
        "strength_knee_smash": (
            "起跳前屈膝負荷充分。你把腿部當作動力來源，而不只是靠手臂。"
        ),
        "strength_hss_smash": (
            "髖肩分離紮實。纏繞動作到位，而且你依序釋放出來——這是高效頭頂球手的標誌。"
        ),
        "strength_wt_clear": (
            "後場高球時重心往前帶——你用全身動量到達底線，沒有過度依賴手臂。"
        ),
        "strength_wt_smash": (
            "殺球時重心向下轉移良好。你在靠重力和體重出力，而不只是肌肉。"
        ),
        "strength_knee_serve": (
            "發球時膝蓋位置一致。穩定的基礎代表揮拍路徑可以重複，發球準確度就是證明。"
        ),
        "strength_elbow_generic": (
            "手肘伸展在正確範圍內。槓桿臂在為你工作。"
        ),
        "strength_trunk_generic": (
            "軀幹轉體看起來受控。轉動幅度足以貢獻力量，又沒有失去方向。"
        ),
        "strength_wrist_generic": (
            "手腕動作紮實。活動範圍充分，沒有過度屈曲。"
        ),
        "strength_knee_generic": (
            "屈膝在有效範圍內。腿部出力可以使用，而且你也在用。"
        ),
        "strength_hss_generic": (
            "髖肩分離足夠。軀幹有參與球路的出力。"
        ),
        "strength_wt_generic": (
            "重心轉移時機恰當。地面反作用力有帶入球路，沒有浪費掉。"
        ),

        # ================================================================
        # stroke labels
        # ================================================================
        "stroke_smash": "殺球",
        "stroke_high_clear": "後場高球",
        "stroke_drop_shot": "吊球",
        "stroke_serve": "發球",

        # ================================================================
        # metric labels
        # ================================================================
        "metric_elbow_extension": "手肘伸展",
        "metric_trunk_rotation": "軀幹轉體",
        "metric_wrist_flexion": "手腕屈曲",
        "metric_knee_flexion": "膝蓋屈曲",
        "metric_hip_shoulder_separation": "髖肩分離",
        "metric_weight_transfer": "重心轉移",

        # ================================================================
        # impact category labels
        # ================================================================
        "impact_power": "力量",
        "impact_accuracy": "精準度",
        "impact_consistency": "穩定性",
        "impact_injury_risk": "受傷風險",

        # ================================================================
        # verdict texts
        # ================================================================
        "verdict_insufficient": (
            "資料不足，無法評估——尚未記錄任何回合。"
        ),
        "verdict_strong": (
            "表現出色（得分 {score}，穩定性 ±{consistency}）。"
            "繼續在比賽壓力下鞏固這些動作模式。"
        ),
        "verdict_developing": (
            "穩步進步中（得分 {score}，穩定性 ±{consistency}）。"
            "基礎已經建立，專注改善以下優先事項。"
        ),
        "verdict_needs_work": (
            "需要加強（得分 {score}，穩定性 ±{consistency}）。"
            "先處理主要弱點，再進行進階訓練。"
        ),
    },

    # Task 2: Simplified Chinese (Mainland China coaching register)
    "zh-Hans": {
        # -- Generic fallback texts --
        "generic_mech": (
            "这个动作超出了我们预期的范围。"
            "在压力下需要精准控制的球路，这个问题会以失误的形式暴露出来。"
        ),
        "generic_drill": (
            "无球步法训练：每次阴影步伐结束后务必回到中心位，"
            "上半身保持放松稳定，让腿部主导移动。"
        ),
        "generic_strength": (
            "整体动作协调不错，继续巩固这个基础。"
        ),

        # -- Section / label / verdict keys --
        "section_weaknesses": "需要改善的方面",
        "section_strengths":  "做得好的方面",
        "label_impact":       "对球路的影响",
        "label_severity":     "优先程度",
        "label_mechanism":    "为什么会影响你的发挥",
        "label_drill":        "改善训练动作",
        "verdict_high":       "优先纠正",
        "verdict_moderate":   "值得改善",
        "verdict_low":        "小幅调整",

        # ================================================================
        # elbow_extension
        # ================================================================

        # smash, under
        "elbow_under_smash_mech": (
            "你在杀球时手肘没有完全伸直，杠杆臂缩短了。"
            "完全伸直的手肘在击球瞬间能增加约15至20%的球头速度——"
            "少了这个伸展，杀球不过是把球送过网，无法真正威胁地板。"
        ),
        "elbow_under_smash_drill": (
            "墙壁点击训练：距墙一臂站立，用前臂甩动把羽球打在墙上，"
            "在击球瞬间有意识地把手肘锁定打直。做3组，每组20次。"
            "动作熟练后改为全场喂球训练。"
        ),

        # smash, over
        "elbow_over_smash_mech": (
            "手肘过度伸展超过打直位置，不会增加任何力量，"
            "反而对内侧副韧带造成明显压力。长期下来这个习惯会引发慢性肘痛，"
            "让你不得不休息好几个星期。"
        ),
        "elbow_over_smash_drill": (
            "轻阻力弹力带阴影挥拍：把弹力带绕在手腕上，"
            "固定在身后稍低处。带子的拉力能防止挥拍末端过度伸展。"
            "做3组，每组15次，专注在手肘刚好打直时停下，不要继续往后。"
        ),

        # high_clear, under
        "elbow_under_clear_mech": (
            "手肘弯曲的后场高球完全依靠手腕甩动和肩膀转动来飞越整个场地，"
            "这对小肌群来说负担太大——球要么飞不够远，"
            "要么你很快就会疲劳，后面几局准确度大幅下滑。"
        ),
        "elbow_under_clear_drill": (
            "抛球训练：用过臂抛球的方式把羽球扔给对面的训练伙伴，"
            "在放开球的瞬间专注于手臂完全伸直。"
            "把这个抛球的感觉带入你的后场挥拍动作。"
            "连续抛20次，然后立刻打20球高球，保持同样的身体感觉。"
        ),

        # generic, under
        "elbow_under_generic_mech": (
            "手肘伸展不足会缩短有效的触球范围，降低击球瞬间球头的角速度。"
            "结果是力量不够，击球区域受限，可以打的球路选择也跟着变少。"
        ),
        "elbow_under_generic_drill": (
            "完全伸展阴影挥拍：在镜子前对每种球路各做50次阴影挥拍，"
            "确认在假想的击球点、跟随动作开始之前，手肘已完全打直。"
        ),

        # generic, over
        "elbow_over_generic_mech": (
            "持续的过度伸展会累积对肘关节的负荷。"
            "就算现在还没有急性疼痛，这个动作习惯是业余球员出现"
            "肱骨外上髁炎（网球肘/羽毛球肘）的主要原因之一。"
        ),
        "elbow_over_generic_drill": (
            "伙伴检查训练：请训练伙伴在你做慢动作挥拍练习时，"
            "轻轻把手指放在你手肘后方。当你感觉到压力时停下挥拍，"
            "那就是安全的末端范围。每次练习做30次，直到肌肉记忆建立起来。"
        ),

        # ================================================================
        # trunk_rotation
        # ================================================================

        # smash, under
        "trunk_under_smash_mech": (
            "单靠手臂的杀球是力量小的杀球。躯干是头顶球出力最大的肌肉群；"
            "转体不足意味着你白白放弃了30至40%的杀球潜在速度。"
            "对方在网前的球员很容易就能预判并拦截。"
        ),
        "trunk_under_smash_drill": (
            "髋肩缠绕训练：侧身对着目标站立，每次挥拍前先把髋部充分转离目标方向，"
            "然后依次展开：先髋部，再肩膀，最后手臂。"
            "用泡沫球练习，这样可以完全专注在转体顺序上，不用担心球的位置。"
            "做3组，每组12次。"
        ),

        # smash, over
        "trunk_over_smash_mech": (
            "躯干转过击球位置，会使球拍面在击球时横向划过球，"
            "赋予侧旋，让杀球偏向边线。在压力下你会发现球路飘向边线区。"
        ),
        "trunk_over_smash_drill": (
            "目标锥筒训练：在网前中线放一个锥筒。每一球杀球都必须落在锥筒60厘米以内。"
            "准确度的反馈会训练你在正确时机停止转体——头两周先求质量，不求速度。"
        ),

        # generic, under
        "trunk_under_generic_mech": (
            "躯干转动不足，出力完全集中在手臂和肩膀。"
            "这些肌群在长时间比赛中容易疲劳，"
            "打出来的球也缺乏让对手难以应付的速度和深度。"
        ),
        "trunk_under_generic_drill": (
            "药球旋转抛掷：侧身靠近墙壁，用2公斤的药球只靠躯干转动抛向墙壁，"
            "末端不要加手臂推力。每侧各做3组，每组10次。"
            "这个动作直接对应每次头顶球使用的髋肩动力链。"
        ),

        # generic, over
        "trunk_over_generic_mech": (
            "躯干过度转体超过理想平面，会让肩膀偏离击球位置，球拍面也跟着偏移。"
            "稳定性受影响最大——就算力量没问题，球路方向也会变得不可预测。"
        ),
        "trunk_over_generic_drill": (
            "有界转体训练：把轻弹力带绑在两手肘之间，手肘维持90度。"
            "弹力带拉紧时就是转体该停下来的提示。每次练习对每种球路做25次阴影挥拍。"
        ),

        # ================================================================
        # wrist_flexion
        # ================================================================

        # smash, under
        "wrist_under_smash_mech": (
            "手腕甩动是你最后的加速器——在击球前几厘米能增加约25%的球头速度。"
            "杀球时手腕不充分屈曲，就像从静止起步用高速档起步的车："
            "发动机在运转，但你没有用到完整的传动系统。"
        ),
        "wrist_under_smash_drill": (
            "手腕甩动孤立训练：握住球拍中段，由训练伙伴扶住拍头，"
            "单独练习手腕甩动20次。然后换为完整挥拍，"
            "在感觉即将触球的瞬间有意识地启动同样的甩动。做4组，每组15次。"
        ),

        # smash, over
        "wrist_over_smash_mech": (
            "手腕甩动超过自然跟随动作的范围，会拉扯前臂的屈肌肌腱。"
            "在负重下反复这样做——尤其是跳跃杀球——是引发手腕肌腱炎的直接路径。"
        ),
        "wrist_over_smash_drill": (
            "受控收拍练习：慢动作下，只把手腕带到自然跟随动作的位置（约中性位后45度），"
            "然后定住2秒。闭眼重复20次，让本体感觉把安全范围刻进肌肉记忆。"
        ),

        # drop_shot, under
        "wrist_under_drop_mech": (
            "吊球需要在击球时精准地让球头减速，使球紧贴网顶落下。"
            "如果手腕控制不够——特别是收力而非甩动的能力——"
            "球会飞太远停在中场，给对手容易的进攻机会。"
        ),
        "wrist_under_drop_drill": (
            "轻触喂球训练：站在网边，轻轻把喂球者手上静止的球刷过网，"
            "目标是让球落在距网带50厘米以内。完全专注在击球时放松手腕。做3组，每组20次。"
        ),

        # generic, under
        "wrist_under_generic_mech": (
            "手腕屈曲不足，击球时球头速度偏低，"
            "也限制了你在最后瞬间改变球路角度的能力。这两点都削弱你的进攻多样性。"
        ),
        "wrist_under_generic_drill": (
            "手腕孤立甩动：手臂打直握拍并锁定手肘，"
            "只用手腕动作让球头上下甩动。做3组，每组30次。训练手腕力量和活动范围。"
        ),

        # generic, over
        "wrist_over_generic_mech": (
            "手腕过度屈曲超过安全范围，对腕管和屈肌肌腱造成压力。"
            "如果不加以纠正，这个习惯最终会导致肌腱炎，严重时甚至引发腕管综合征。"
        ),
        "wrist_over_generic_drill": (
            "范围感知训练：在手背贴一个小标记。"
            "当标记朝向正后方（而非向下）时，就是安全的最大屈曲位置。"
            "每次练习前确认这个位置10次，直到身体熟悉这个界限。"
        ),

        # ================================================================
        # knee_flexion
        # ================================================================

        # serve, under
        "knee_under_serve_mech": (
            "发球时腿打直，髋部和骨盆锁死，无法做出让球走出可控弧线的微微前倾。"
            "结果是发球比预期飞得更高，在中场浮起，让对手可以用平直截击进攻。"
        ),
        "knee_under_serve_drill": (
            "发球站姿确认：练习赛中每次发球前，"
            "刻意下沉成微微屈膝的站姿（约20到30度）并保持一个完整呼吸再出手。"
            "累积50次发球练习后，这个姿势自然会成为你的起点。"
        ),

        # serve, over
        "knee_over_serve_mech": (
            "发球时屈膝太多，重心过早前移，"
            "让挥拍在球的下方就提前到达最低点——球被拍面从下方切到，"
            "发球高度和长度都变得不稳定。"
        ),
        "knee_over_serve_drill": (
            "镜子站姿训练：在全身镜前发球，确认膝盖角度符合目标范围"
            "（大约与肩同宽，微微弯曲）。调整到镜子确认正确后，"
            "保持这个站姿打30球。"
        ),

        # smash, under
        "knee_under_smash_mech": (
            "起跳杀球时屈膝负荷不足，跳跃高度下降，"
            "也缩短了在最高点停留的时间——那才是最理想的击球窗口。"
            "接触点偏低意味着球路角度偏平，对手更容易预判，反应时间也更充裕。"
        ),
        "knee_under_smash_drill": (
            "蹲跳杀球训练：从半蹲姿势向上跳起，在最高点完成完整杀球。"
            "重点在于把屈膝当成起跳台而不是靠手臂发力。做3组，每组8次。"
        ),

        # generic, under
        "knee_under_generic_mech": (
            "屈膝不足，腿部对球路的出力贡献减少。"
            "腿是身体最大的肌肉群；不动用它们，就只能靠手臂力量，"
            "疲劳更快，整体力量也更小。"
        ),
        "knee_under_generic_drill": (
            "弓步出球训练：自行喂球，踏入完整弓步的同时出手击球；"
            "规则是后脚膝盖触地之前球路必须完成。强制最大屈膝和腿部出力。做3组，每组10次。"
        ),

        # generic, over
        "knee_over_generic_mech": (
            "反复在过大的屈膝角度下负重——尤其是跳跃落地时——"
            "会增加髌股关节的压力。持续的深屈膝落地是跳跃性运动出现髌腱炎的主要原因。"
        ),
        "knee_over_generic_drill": (
            "受控落地训练：从20厘米的台阶跳下，以柔和可控的屈膝落地，"
            "在约90度时定住2秒再站起。做3组，每组10次。"
            "训练落地本体感觉，直接应用到跳跃杀球的落地。"
        ),

        # ================================================================
        # hip_shoulder_separation
        # ================================================================

        # smash, under
        "hss_under_smash_mech": (
            "髋肩分离角是储存躯干肌肉弹性能量的关键角度，等待你释放出去。"
            "杀球时分离不足，就没有弹性反弹可以释放——整个球路全靠手臂，"
            "白白放掉了羽毛球运动员在头顶球中最大的免费动力来源。"
        ),
        "hss_under_smash_drill": (
            "缠绕定住训练：从杀球预备姿势，转动髋部朝向后方围栏，"
            "同时肩膀继续面对球网。维持这个拉伸姿势3秒，然后挥拍。"
            "做4组，每组10次。定住的动作防止肩膀过早转动，训练缠绕模式。"
        ),

        # high_clear, under
        "hss_under_clear_mech": (
            "在没有适当髋肩分离的情况下，要打出有深度的后场高球，"
            "肩膀必须独力完成所有的提升工作。球路常常出来太平，"
            "落在中场容易被预判和进攻。"
        ),
        "hss_under_clear_drill": (
            "错步站姿后场高球训练：用夸张的前后步法站姿（异侧脚大幅前跨）打高球。"
            "这个站姿从结构上强制在肩转之前先转髋。"
            "每次练习用这个站姿打30球，再回到正常步法。"
        ),

        # generic, under
        "hss_under_generic_mech": (
            "髋肩分离不足，躯干动力链会整体同时启动，而不是依序触发。"
            "那个顺序才是关键：先髋，再躯干，再肩，再手臂。"
            "把它压缩成同一个动作，手臂还没参与，旋转速度就已经大半消失了。"
        ),
        "hss_under_generic_drill": (
            "坐姿旋转训练：坐在椅子上，脚踏平地。"
            "肩膀尽量往右转，同时保持髋部不动。"
            "维持2秒。每侧做3组，每组10次。孤立训练躯干活动度，与下肢动作分开。"
        ),

        # generic, over
        "hss_over_generic_mech": (
            "髋肩分离过大会让骨盆在挥拍中横向倾斜，"
            "在挥拍中途改变重心。你的身体还在修正平衡时，球拍已经碰到球了，"
            "球拍面角度就不在你原本瞄准的位置。方向大致对，但精准度下降了。"
        ),
        "hss_over_generic_drill": (
            "对齐检查训练：在地板上贴一条胶带标示基准线髋部方向。"
            "每次出球后确认髋部在一步内回到对齐位置。"
            "由训练伙伴在旁喊出偏移，做3组，每组15球。"
        ),

        # ================================================================
        # weight_transfer
        # ================================================================

        # high_clear, under
        "wt_under_clear_mech": (
            "没有重心转移的后场高球基本上是一个静止的抬球动作。"
            "球要飞到底线——超过13米，还要飞高弧线。"
            "不把身体重量往前带入球路，你就只能靠手臂对抗场地几何，"
            "状态差的时候球一定会落在中场。"
        ),
        "wt_under_clear_drill": (
            "跟步后场高球训练：打出高球后，前脚必须在球落地之前超越后脚至少30厘米。"
            "这强制让动量持续带入球路。每次练习打40球并加入这个限制，"
            "直到跟步动作变成自然反应。"
        ),

        # smash, under
        "wt_under_smash_mech": (
            "从静止或重心偏后的姿势杀球，出力只能靠手臂和躯干。"
            "正确的杀球是从地板往上带力：蹬地、穿过髋部，手臂只是最后的鞭打动作。"
            "少了蹬地，动力链就只剩一半。"
        ),
        "wt_under_smash_drill": (
            "跳跃落地杀球训练：每次杀球前做一个双脚跳，"
            "确认在下降过程中触球，把重量向下压入球。"
            "限制：落地点在击球位置后方的，那次不计数。做3组，每组10次。"
        ),

        # serve, under
        "wt_under_serve_mech": (
            "没有向前重心转移的发球，长度容易不稳定。"
            "缺少躯干带球拍前移的动作，手腕角度的微小变化就会被放大，"
            "让发球在太短和刚好合法之间飘移。"
        ),
        "wt_under_serve_drill": (
            "摇摆发球训练：以重心在后脚开始，挥拍启动时往前摇摆，"
            "确认击球时重心已完全转移到前脚。"
            "每次练习打50球，由训练伙伴确认球离拍时重心是否已在前方。"
        ),

        # generic, under
        "wt_under_generic_mech": (
            "没有重心转移，每一球都靠孤立的手臂和肩膀动作出力。"
            "这些肌群比较小，也更快疲劳。面对防守强的对手，"
            "差别会在第三局显现出来：原本打到后场的球开始落在中场。"
        ),
        "wt_under_generic_drill": (
            "阴影弓步训练：针对每个球路方向，踏入带有明确重心转移的弓步；"
            "在恢复之前在末端姿势停一拍。5个方向，每个方向10次。"
        ),

        # generic, over
        "wt_over_generic_mech": (
            "在同一方向过度压重心，打完球后你的平衡就已经垮了。"
            "回到中心位需要更长时间，对手有充裕时机利用空档。"
            "特别是杀球时过度转移的重心，让斜线网前回球几乎追不到。"
        ),
        "wt_over_generic_drill": (
            "恢复确认训练：每次出球后大声说出目标中心位，然后用刚好两步走到。"
            "如果你在踉跄或需要三步，说明重心转移过头了。"
            "30球阴影练习，由训练伙伴给反馈。"
        ),

        # ================================================================
        # STRENGTH text keys
        # ================================================================

        "strength_elbow_smash": (
            "杀球击球时手肘完全伸直——杠杆臂完整，球头速度达到最大。继续保持。"
        ),
        "strength_trunk_smash": (
            "杀球时躯干转体很好。动力链启动正确，髋部带着肩膀走。"
        ),
        "strength_wrist_smash": (
            "杀球击球时手腕甩动有力。那个最后的甩动才是真正速度的来源，你用得很好。"
        ),
        "strength_knee_smash": (
            "起跳前屈膝负荷充分。你把腿部当作动力来源，而不只是靠手臂。"
        ),
        "strength_hss_smash": (
            "髋肩分离扎实。缠绕动作到位，而且你依次释放出来——这是高效头顶球手的标志。"
        ),
        "strength_wt_clear": (
            "后场高球时重心往前带——你用全身动量到达底线，没有过度依赖手臂。"
        ),
        "strength_wt_smash": (
            "杀球时重心向下转移良好。你在靠重力和体重出力，而不只是肌肉。"
        ),
        "strength_knee_serve": (
            "发球时膝盖位置一致。稳定的基础意味着挥拍路径可以重复，发球准确度就是证明。"
        ),
        "strength_elbow_generic": (
            "手肘伸展在正确范围内。杠杆臂在为你工作。"
        ),
        "strength_trunk_generic": (
            "躯干转体看起来受控。转动幅度足以贡献力量，又没有失去方向。"
        ),
        "strength_wrist_generic": (
            "手腕动作扎实。活动范围充分，没有过度屈曲。"
        ),
        "strength_knee_generic": (
            "屈膝在有效范围内。腿部出力可以使用，而且你也在用。"
        ),
        "strength_hss_generic": (
            "髋肩分离足够。躯干有参与球路的出力。"
        ),
        "strength_wt_generic": (
            "重心转移时机恰当。地面反作用力有带入球路，没有浪费掉。"
        ),

        # ================================================================
        # stroke labels
        # ================================================================
        "stroke_smash": "杀球",
        "stroke_high_clear": "后场高球",
        "stroke_drop_shot": "吊球",
        "stroke_serve": "发球",

        # ================================================================
        # metric labels
        # ================================================================
        "metric_elbow_extension": "手肘伸展",
        "metric_trunk_rotation": "躯干转体",
        "metric_wrist_flexion": "手腕屈曲",
        "metric_knee_flexion": "膝盖屈曲",
        "metric_hip_shoulder_separation": "髋肩分离",
        "metric_weight_transfer": "重心转移",

        # ================================================================
        # impact category labels
        # ================================================================
        "impact_power": "力量",
        "impact_accuracy": "精准度",
        "impact_consistency": "稳定性",
        "impact_injury_risk": "受伤风险",

        # ================================================================
        # verdict texts
        # ================================================================
        "verdict_insufficient": (
            "数据不足，无法评估——尚未记录任何回合。"
        ),
        "verdict_strong": (
            "表现出色（得分 {score}，稳定性 ±{consistency}）。"
            "继续在比赛压力下巩固这些动作模式。"
        ),
        "verdict_developing": (
            "稳步进步中（得分 {score}，稳定性 ±{consistency}）。"
            "基础已经建立，专注改善以下优先事项。"
        ),
        "verdict_needs_work": (
            "需要加强（得分 {score}，稳定性 ±{consistency}）。"
            "先处理主要弱点，再进行进阶训练。"
        ),
    },
}

# ---------------------------------------------------------------------------
# Lookup functions
# ---------------------------------------------------------------------------


def lookup_weakness(metric: str, stroke: str, direction: str) -> dict:
    """Return the weakness entry for (metric, stroke, direction).

    Fallback chain:
      1. (metric, stroke, direction)  — stroke-specific
      2. (metric, "*", direction)     — generic per-metric
      3. _GENERIC_FALLBACK            — absolute fallback (never None)
    """
    entry = COACH_KB.get((metric, stroke, direction))
    if entry is not None:
        return entry
    entry = COACH_KB.get((metric, "*", direction))
    if entry is not None:
        return entry
    return _GENERIC_FALLBACK


def lookup_strength(metric: str, stroke: str) -> dict:
    """Return the strength entry for (metric, stroke).

    Fallback chain:
      1. (metric, stroke)  — stroke-specific
      2. (metric, "*")     — generic per-metric
      3. _GENERIC_STRENGTH — absolute fallback (never None)
    """
    entry = STRENGTH_KB.get((metric, stroke))
    if entry is not None:
        return entry
    entry = STRENGTH_KB.get((metric, "*"))
    if entry is not None:
        return entry
    return _GENERIC_STRENGTH


def t(lang: str, key: str, **fmt) -> str:
    """Resolve an i18n string.

    Falls back to English if the key is absent in the requested language,
    then falls back to the key string itself if absent in English too.
    """
    table = I18N.get(lang, {})
    s = table.get(key)
    if s is None:
        s = I18N["en"].get(key)   # fall back to English
    if s is None:
        return key                # last resort: the key itself
    return s.format(**fmt) if fmt else s


def iter_all_entries():
    """Yield every dict in COACH_KB, STRENGTH_KB, plus the generic sentinels."""
    yield from COACH_KB.values()
    yield from STRENGTH_KB.values()
    yield _GENERIC_FALLBACK
    yield _GENERIC_STRENGTH
