"""Suppress detections that never move.

The far-court ROI pose pass (added so the opponent is detected at all on
wide multi-court framing) also finds people sitting or crouching courtside
whose feet project inside the court polygon. They are indistinguishable from a
player by position or size -- measured on the 0007 clip, one crouching person
sat at court (2.31, 3.92), between the net and the opponent, and so won the
tracker's "closest to the net" selection in 13.6% of frames.

What separates them is motion: a player in a rally moves, a person watching
their phone does not. This is the same reasoning as the shuttle
static-artifact suppression in the B11 rally design, applied to people.

Deliberately conservative: a candidate is suppressed only after it has been
observed in one small box for most of a multi-second window, so a player who
pauses briefly is unaffected. A player who stands genuinely still for seconds
is suppressed -- acceptable, because that is not play.
"""
from collections import deque


class StaticCandidateFilter:
    """Drops candidates that have held one position for most of a window."""

    def __init__(self, tol_px, window_frames, min_hits):
        self.tol_px = float(tol_px)
        self.window_frames = int(window_frames)
        self.min_hits = int(min_hits)
        self._seen = deque()          # (frame_index, x, y)

    @classmethod
    def from_fps(cls, fps, tol_px=30.0, window_sec=3.0, min_hit_frac=0.9):
        """Build from seconds so behaviour is the same at 30 and 60 fps.

        ``min_hit_frac`` is the share of the window a candidate must occupy
        before it counts as static. High by design: brief stillness is normal
        in a rally, and a false suppression costs a real player's position.
        """
        try:
            rate = float(fps)
        except (TypeError, ValueError):
            rate = 30.0
        if rate <= 0:
            rate = 30.0
        window = max(1, int(round(window_sec * rate)))
        return cls(tol_px=tol_px, window_frames=window,
                   min_hits=max(1, int(round(window * min_hit_frac))))

    def filter(self, frame_index, candidates):
        """Return the candidates that are not static.

        ``candidates`` is any sequence of (x, y)-indexable items; the items
        themselves are returned unchanged, so a caller may carry extra payload
        in a tuple or namedtuple alongside the point.
        """
        cutoff = frame_index - self.window_frames
        while self._seen and self._seen[0][0] <= cutoff:
            self._seen.popleft()

        kept = []
        for cand in candidates:
            x, y = float(cand[0]), float(cand[1])
            hits = 0
            for _f, sx, sy in self._seen:
                if abs(sx - x) <= self.tol_px and abs(sy - y) <= self.tol_px:
                    hits += 1
                    if hits >= self.min_hits:
                        break
            if hits < self.min_hits:
                kept.append(cand)

        for cand in candidates:
            self._seen.append((frame_index, float(cand[0]), float(cand[1])))
        return kept
