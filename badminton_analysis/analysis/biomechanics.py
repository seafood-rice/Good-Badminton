"""Assemble a TechniqueReport for a single stroke from its window frames."""
import numpy as np

from . import joint_angles as ja
from .scoring import score_stroke

_METRIC_LABEL = {
    "elbow_extension": "Elbow extension",
    "trunk_rotation": "Trunk rotation",
    "wrist_flexion": "Wrist/racket angle",
    "knee_flexion": "Knee flexion",
    "hip_shoulder_separation": "Hip-shoulder separation",
    "weight_transfer": "Forward weight transfer",
}

_ADVICE = {
    ("elbow_extension", "under"): "Extend the arm more fully through contact to transfer power.",
    ("elbow_extension", "over"): "Avoid hyper-extending; keep a slight bend for control.",
    ("trunk_rotation", "under"): "Rotate the trunk more to add rotational power.",
    ("trunk_rotation", "over"): "Reduce trunk over-rotation to stay balanced.",
    ("wrist_flexion", "under"): "Use more wrist snap at contact for racket-head speed.",
    ("wrist_flexion", "over"): "Control the wrist; excessive flexion costs accuracy.",
    ("knee_flexion", "under"): "Bend the knees more to load the legs before the stroke.",
    ("knee_flexion", "over"): "Don't over-squat; keep an athletic, springy stance.",
    ("hip_shoulder_separation", "under"): "Increase hip-shoulder separation to build torque.",
    ("hip_shoulder_separation", "over"): "Tighten the kinetic chain; separation is excessive.",
    ("weight_transfer", "under"): "Drive body weight forward into the shot.",
    ("weight_transfer", "over"): "Stay balanced; you are lunging too far forward.",
}


def _describe(metric, entry):
    label = _METRIC_LABEL.get(metric, metric)
    measured = entry["measured"]
    lo, hi = entry["ideal_range"]
    advice = _ADVICE.get((metric, entry["direction"]), "Work toward the ideal range.")
    return f"{label} {measured:.1f} (ideal {lo}-{hi}). {advice}"


class BiomechanicalAnalyzer:
    def __init__(self, dominant="right"):
        self.dominant = dominant

    def _contact_frame_record(self, stroke_event, window_frames):
        exact = [w for w in window_frames if w["frame"] == stroke_event.contact_frame]
        if exact:
            return exact[0]
        with_kp = [w for w in window_frames if w.get("keypoints") is not None]
        if with_kp:
            return min(with_kp, key=lambda w: abs(w["frame"] - stroke_event.contact_frame))
        return window_frames[len(window_frames) // 2] if window_frames else None

    def _shoulder_width(self, kp, conf):
        if kp is None:
            return None
        if ja.is_valid(kp, ja.L_SHOULDER, conf) and ja.is_valid(kp, ja.R_SHOULDER, conf):
            return float(np.hypot(kp[ja.L_SHOULDER][0] - kp[ja.R_SHOULDER][0],
                                  kp[ja.L_SHOULDER][1] - kp[ja.R_SHOULDER][1]))
        return None

    def analyze(self, stroke_event, window_frames):
        contact = self._contact_frame_record(stroke_event, window_frames)
        kp = contact.get("keypoints") if contact else None
        conf = contact.get("conf") if contact else None
        racket_head = contact.get("racket_head") if contact else None

        if kp is not None:
            metrics = ja.compute_joint_angles(kp, racket_head=racket_head,
                                              dominant=self.dominant, conf=conf)
        else:
            metrics = {k: None for k in
                       ("elbow_extension", "shoulder_abduction", "trunk_rotation",
                        "knee_flexion", "hip_shoulder_separation", "wrist_flexion")}

        # weight transfer: window-start centroid -> contact centroid, normalized by shoulder width
        start_centroid = next((w["centroid"] for w in window_frames if w.get("centroid")), None)
        contact_centroid = contact.get("centroid") if contact else None
        shoulder_w = self._shoulder_width(kp, conf)
        wt = None
        if start_centroid is not None and contact_centroid is not None and shoulder_w:
            wt = ja.weight_transfer_ratio(start_centroid, contact_centroid, shoulder_w)
        metrics["weight_transfer"] = wt

        scored = score_stroke(metrics, stroke_event.stroke_type)

        weaknesses = []
        for metric in scored["weaknesses"]:
            entry = scored["per_metric"][metric]
            weaknesses.append({
                "metric": metric,
                "measured": entry["measured"],
                "ideal_range": entry["ideal_range"],
                "direction": entry["direction"],
                "severity": entry["severity"],
                "description": _describe(metric, entry),
            })

        return {
            "stroke_type": stroke_event.stroke_type,
            "contact_frame": stroke_event.contact_frame,
            "player_side": stroke_event.player_side,
            "confidence": stroke_event.confidence,
            "overall_score": scored["overall"],
            "per_metric": scored["per_metric"],
            "weaknesses": weaknesses,
            "strengths": scored["strengths"],
        }
