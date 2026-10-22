"""Doubles collection (in-loop) and post-loop orchestration (spec 6.1, 6.6).

Runs BESIDE the singles PlayerTracker, never instead of it. Every public
entry point is never fatal: observe() swallows and logs once; finalize()
returns (and writes, when it can) a players.json payload with status "error".
"""
import glob
import json
import os
from collections import defaultdict

import numpy as np

from ..court.scale import PerspectiveScale
from ..data.writer import clean_value, write_json
from ..stroke.hitter import attribute_hitters
from . import appearance as ap
from .identity import assign_identities
from .multi import Detection, MultiTracker, filter_tracklets

DOUBLES_OUTPUTS = ("tracklets.jsonl", "players.json")
DOUBLES_GLOBS = ("heatmap_P*.png", "heatmap_team*.png")
PLAYERS_VIDEO = "detect_{video_name}_players.mp4"
STROKES_WITHHELD = "doubles: the stroke classifier is singles-only"
SPEED_CAP_MPS = 8.0
JITTER_M = 0.05
NAMED_EVIDENCE = ("shuttle", "swing")


def players_video_name(video_name):
    return PLAYERS_VIDEO.format(video_name=video_name)


def remove_doubles_outputs(save_dir, video_name=None):
    """Remove a previous run's doubles outputs. The identity video is matched by
    exact name (another video's in a shared folder is kept); skipped without one."""
    paths = [os.path.join(save_dir, n) for n in DOUBLES_OUTPUTS]
    for g in DOUBLES_GLOBS:
        paths += glob.glob(os.path.join(save_dir, g))
    if video_name:
        paths.append(os.path.join(save_dir, players_video_name(video_name)))
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Could not remove the previous run's {os.path.basename(p)}: {exc}")


def keypoints_from_pose_data(pose_data, n):
    """Full-frame keypoints aligned with the merged centroid list, or Nones."""
    if not pose_data or pose_data.get("keypoints") is None:
        return [None] * n
    people = np.asarray(pose_data["keypoints"], dtype=float)
    if people.ndim != 3 or people.shape[0] != n:
        return [None] * n
    ox, oy = float(pose_data.get("offset_x", 0)), float(pose_data.get("offset_y", 0))
    out = []
    for kp in people:
        kp = kp[:, :2].copy()
        valid = (kp[:, 0] > 1) & (kp[:, 1] > 1)
        kp[valid, 0] += ox
        kp[valid, 1] += oy
        out.append(kp)
    return out


def make_payload(status, reason, players=(), teams=(), stats=None):
    """players.json payload; stats default to zeros."""
    base = {"tracklets_total": 0, "tracklets_used": 0, "tracklets_dropped": 0,
            "median_confidence": 0.0, "hits_total": 0, "hits_named_share": 0.0,
            "hits_single_candidate": 0}
    base.update(stats or {})
    return {"schema_version": "1.0", "match_format": "doubles", "status": status,
            "reason": reason, "players": list(players), "teams": list(teams), "stats": base,
            "stroke_labels_withheld": STROKES_WITHHELD, "validated": False}


def _is_named(h, pid=None):
    """A hit named from evidence (shuttle or swing); single_candidate guesses are not."""
    return (bool(h.get("hitter_id")) and h.get("hitter_evidence") in NAMED_EVIDENCE
            and (pid is None or h.get("hitter_id") == pid))


def _is_single_candidate(h, pid=None):
    return (bool(h.get("hitter_id")) and h.get("hitter_evidence") == "single_candidate"
            and (pid is None or h.get("hitter_id") == pid))


def _n_valid(kp):
    if kp is None:
        return 0
    kp = np.asarray(kp)
    return int(np.sum((kp[:, 0] > 1) & (kp[:, 1] > 1)))


class DoublesCollector:
    def __init__(self, court_mapper, quad, fps, *, appearance_every=5, dedupe_m=0.4):
        self.mapper = court_mapper
        self.fps = float(fps) if fps and fps > 0 else 30.0
        try:
            self.scale = PerspectiveScale.from_quad(quad)
        except Exception:
            self.scale = None
        self.appearance_every = max(1, int(appearance_every))
        self.dedupe_m = dedupe_m
        self.tracker = MultiTracker(self.fps)
        self._n_observed = 0
        self._error_logged = False
        self.tracklets = []
        self.identity_tracks = {}
        self.assignment = {}

    # -- in-loop -----------------------------------------------------------
    def _samples_at(self, n):
        return self.appearance_every == 1 or n % self.appearance_every == 1

    def wants_frame(self):
        """True when the NEXT observe() samples appearance (needs overlay-free pixels).

        The caller copies the frame only then and passes None otherwise: a 4K
        copy on every analysed frame is wasted on the 4 in 5 that are not sampled.
        """
        return self._samples_at(self._n_observed + 1)

    def observe(self, frame_index, frame_bgr, centroids, keypoints_list):
        """Track one analysed frame. Call on EVERY analysed frame: it drives the
        tracker and the sampling cadence. frame_bgr may be None (no signatures)."""
        try:
            self._observe(int(frame_index), frame_bgr, list(centroids or []),
                          list(keypoints_list or []))
        except Exception as exc:
            if not self._error_logged:
                self._error_logged = True
                print(f"Doubles collection failed at frame {frame_index}; skipping such "
                      f"frames, analysis continues: {exc!r}")

    def _observe(self, frame_index, frame_bgr, centroids, keypoints_list):
        self._n_observed += 1
        sample = self._samples_at(self._n_observed)
        cands = []
        for i, cen in enumerate(centroids):
            try:
                img = (float(cen[0]), float(cen[1]))
            except (TypeError, ValueError, IndexError):
                continue
            court = self.mapper.image_to_court(img)
            if court is None or len(court) < 2:
                continue
            kp = keypoints_list[i] if i < len(keypoints_list) else None
            if kp is not None and not isinstance(kp, np.ndarray):
                try:
                    kp = np.asarray(kp, dtype=float)
                except (TypeError, ValueError):
                    kp = None
            if kp is not None and (kp.ndim != 2 or kp.shape[0] < 17):
                kp = None
            cands.append(((float(court[0]), float(court[1])), img, kp))

        kept = []
        for c in sorted(cands, key=lambda c: -_n_valid(c[2])):
            if all(np.hypot(c[0][0] - k[0][0], c[0][1] - k[0][1]) >= self.dedupe_m for k in kept):
                kept.append(c)

        dets = []
        for court, img, kp in kept:
            sig = None
            if sample and frame_bgr is not None and kp is not None:
                sig = ap.signature(frame_bgr, kp, self.scale)
            dets.append(Detection(court=court, image=img, keypoints=kp, appearance=sig))
        self.tracker.step(frame_index, dets)

    # -- post-loop ---------------------------------------------------------
    def finalize(self, save_dir, analysed_frames, *, shuttle=None, constraint_frames=None):
        try:
            return self._finalize(save_dir, int(analysed_frames or 0), shuttle, constraint_frames)
        except Exception as exc:
            print(f"Doubles analysis failed: {exc!r}")
            payload = self._payload("error", f"{type(exc).__name__}: {exc}", [], [], {})
            try:
                write_json(os.path.join(save_dir, "players.json"), payload)
            except Exception as wexc:
                print(f"players.json could not be written: {wexc!r}")
            return payload

    def _finalize(self, save_dir, analysed_frames, shuttle, constraint_frames):
        raw = self.tracker.finish()
        self.tracklets = filter_tracklets(raw, self.fps)
        self._write_tracklets(save_dir, self.tracklets)
        ident = assign_identities(self.tracklets, analysed_frames,
                                  constraint_frames=constraint_frames)
        self.assignment = ident["assignment"]

        # Each observation is a small dict REFERENCING the tracklet's own court /
        # image lists and float32 keypoint array: no per-frame copies.
        tracks = defaultdict(dict)
        for t in self.tracklets:
            pid = ident["assignment"].get(t["id"])
            if pid is None:
                continue
            side, tid = t["side"], t["id"]
            for f, c, im, kp in zip(t["frames"], t["court"], t["image"], t["keypoints"]):
                tracks[pid][f] = {"court": c, "image": im, "keypoints": kp,
                                  "side": side, "tracklet": tid}
        self.identity_tracks = dict(tracks)

        hits = self._augment_hits(save_dir, ident, shuttle)
        names = self._read_names(save_dir)
        players = self._player_rows(ident, hits, names, analysed_frames)
        teams = [{"id": k, "players": sorted(p for p, tk in ident["teams"].items() if tk == k),
                  "coverage_heatmap": f"heatmap_team{k}.png"}
                 for k in (1, 2) if any(tk == k for tk in ident["teams"].values())]
        confs = list(ident["confidence"].values())
        named = sum(1 for h in hits if _is_named(h))
        stats = {"tracklets_total": len(raw), "tracklets_used": len(ident["assignment"]),
                 "tracklets_dropped": len(raw) - len(ident["assignment"]),
                 "median_confidence": round(float(np.median(confs)), 4) if confs else 0.0,
                 "hits_total": len(hits),
                 "hits_named_share": round(named / len(hits), 4) if hits else 0.0,
                 "hits_single_candidate": sum(1 for h in hits if _is_single_candidate(h))}
        payload = self._payload(ident["status"], ident["reason"], players, teams, stats)
        write_json(os.path.join(save_dir, "players.json"), payload)
        print(f"Doubles: status {payload['status']}, {len(players)} players, "
              f"{stats['tracklets_used']}/{stats['tracklets_total']} tracklets, "
              f"{named}/{len(hits)} hits named")
        return payload

    @staticmethod
    def _payload(status, reason, players, teams, stats):
        return make_payload(status, reason, players, teams, stats)

    def _write_tracklets(self, save_dir, tracklets):
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, "tracklets.jsonl"), "w", encoding="utf-8") as fh:
            for t in tracklets:
                # Keypoints to 0.1 px: plenty for pixels, and it keeps the file small.
                kps = [None if k is None else np.round(np.asarray(k, np.float64), 1).tolist()
                       for k in t.get("keypoints") or []]
                row = dict(t, keypoints=kps,
                           appearance=[ap.to_json(s) for s in t.get("appearance") or []])
                fh.write(json.dumps(clean_value(row), separators=(",", ":")) + "\n")

    def _augment_hits(self, save_dir, ident, shuttle):
        path = os.path.join(save_dir, "hits.json")
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            hits = doc.get("hits")
            if not isinstance(hits, list):
                return []
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            print(f"hits.json unreadable, no hitter attribution: {exc!r}")
            return []
        teams = {p: t for p, t in ident["teams"].items()}
        # Isolated: a bad hit entry must not cost the identities and player rows.
        try:
            attributed = attribute_hitters(hits, self.identity_tracks, self.fps,
                                           teams=teams, shuttle=shuttle, scale=self.scale)
        except Exception as exc:
            print(f"Hitter attribution failed, hits.json left un-augmented: {exc!r}")
            return hits
        doc["hits"] = attributed
        doc["hitter_attribution"] = {"validated": False}
        write_json(path, doc)
        return doc["hits"]

    @staticmethod
    def _read_names(save_dir):
        try:
            with open(os.path.join(save_dir, "player_names.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            return {str(k): str(v) for k, v in d.items() if isinstance(v, str) and v.strip()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _player_rows(self, ident, hits, names, analysed_frames):
        rows = []
        for pid in sorted(set(ident["assignment"].values())):
            obs = self.identity_tracks.get(pid, {})
            dist, maxv, secs = self._movement(pid)
            per_tl = [t for t in self.tracklets if ident["assignment"].get(t["id"]) == pid]
            rows.append({
                "id": pid, "team": ident["teams"].get(pid), "name": names.get(pid),
                "frames": len(obs), "coverage": ident["coverage"].get(pid, 0.0),
                "distance_m": round(dist, 2),
                "avg_speed_mps": round(dist / secs, 2) if secs > 0 else 0.0,
                "max_speed_mps": round(maxv, 2),
                "hits": sum(1 for h in hits if h.get("hitter_id") == pid),
                "hits_named": sum(1 for h in hits if _is_named(h, pid)),
                "hits_single_candidate": sum(1 for h in hits if _is_single_candidate(h, pid)),
                "heatmap": f"heatmap_{pid}.png",
                "tracklets": [{"id": t["id"], "start_frame": t["frames"][0],
                               "end_frame": t["frames"][-1],
                               "confidence": ident["confidence"].get(t["id"], 0.0)}
                              for t in per_tl],
            })
        return rows

    def _movement(self, pid):
        """(distance m, max speed m/s, observed seconds), with the PlayerTracker floor and cap."""
        step = max(1, int(round(self.fps / 6)))
        dist = maxv = secs = 0.0
        for t in self.tracklets:
            if self.assignment.get(t["id"]) != pid:
                continue
            fr, co = t["frames"], t["court"]
            secs += (fr[-1] - fr[0] + 1) / self.fps
            idx = list(range(0, len(fr), step))
            if idx[-1] != len(fr) - 1:
                idx.append(len(fr) - 1)
            for a, b in zip(idx, idx[1:]):
                d = float(np.hypot(co[b][0] - co[a][0], co[b][1] - co[a][1]))
                dt = (fr[b] - fr[a]) / self.fps
                if dt <= 0 or d <= JITTER_M:
                    continue
                v = d / dt
                if v > SPEED_CAP_MPS:
                    continue
                dist += d
                maxv = max(maxv, v)
        return dist, maxv, secs
