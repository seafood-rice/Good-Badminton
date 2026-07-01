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
            "Your movement pattern is outside the optimal range. "
            "This reduces control and consistency across all shots."
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
            "Without adequate hip-shoulder separation, the trunk kinetic chain fires "
            "as a single block rather than sequentially. Sequential firing — hips "
            "first, then trunk, then shoulder, then arm — is where rotational shots get "
            "their power. Losing the sequence loses the speed."
        ),
        "hss_under_generic_drill": (
            "Seated rotation drill: sit on a bench with your feet flat on the floor. "
            "Rotate the shoulders as far right as possible while keeping the hips "
            "square. Hold for 2 seconds. 3 sets of 10 per side. Isolates trunk mobility "
            "separate from lower-body movement."
        ),

        # generic, over
        "hss_over_generic_mech": (
            "Excessive hip-shoulder separation can cause the pelvis to tilt laterally "
            "during the stroke, which shifts the centre of mass and makes balanced "
            "recovery to base position harder. Accuracy suffers because a moving platform "
            "cannot deliver a controlled racket face."
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
            "Weight transfer transfers ground-reaction force into the stroke. Without it, "
            "the shot relies entirely on isolated limb movement — smaller muscles, less "
            "power, and less stable contact."
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
    },

    # Task 2 fills these:
    "zh-Hant": {},
    "zh-Hans": {},
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
