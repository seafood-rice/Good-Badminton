"""Doubles visuals: per-player / per-team court heatmaps and the identity video."""
import functools
import os

import cv2
import numpy as np

PLAYER_COLORS_BGR = {"P1": (255, 140, 0), "P2": (255, 220, 120),
                     "P3": (0, 80, 255), "P4": (120, 180, 255)}
COURT_W, COURT_L = 6.1, 13.4
_REPO_FONT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "simhei.ttf"))
# cv2.putText draws non-ASCII as "?": labels use this font via PIL when it is present.
FONT_PATH = _REPO_FONT if os.path.isfile(_REPO_FONT) else None


def _heatmap_png(path, points, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(3.2, 6.4), dpi=100)
    try:
        if points:
            xs, ys = zip(*points)
            ax.hist2d(xs, ys, bins=(12, 26), range=[[0, COURT_W], [0, COURT_L]], cmap="magma")
        ax.add_patch(plt.Rectangle((0, 0), COURT_W, COURT_L, fill=False, color="white", lw=1.5))
        ax.axhline(COURT_L / 2, color="white", lw=1.0)
        ax.set_xlim(-0.3, COURT_W + 0.3)
        ax.set_ylim(COURT_L + 0.3, -0.3)
        ax.set_facecolor("black")
        # Escape $: matplotlib reads an even count as mathtext, and a player name can break the parse.
        ax.set_title(title.replace("$", r"\$"), fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.tight_layout()
        fig.savefig(path)
    finally:
        plt.close(fig)


def write_heatmaps(save_dir, payload, identity_tracks):
    """Write every heatmap, or none: an abort removes the PNGs this call wrote."""
    written, current = [], None
    try:
        names = {p["id"]: (p.get("name") or p["id"]) for p in payload.get("players") or []}
        for pid, obs in (identity_tracks or {}).items():
            pts = [o["court"] for o in obs.values() if o.get("court") is not None]
            current = f"heatmap_{pid}.png"
            _heatmap_png(os.path.join(save_dir, current), pts, names.get(pid, pid))
            written.append(current)
        for team in payload.get("teams") or []:
            pts = [o["court"] for pid in team.get("players") or []
                   for o in (identity_tracks or {}).get(pid, {}).values()
                   if o.get("court") is not None]
            current = f"heatmap_team{team['id']}.png"
            _heatmap_png(os.path.join(save_dir, current), pts, f"Team {team['id']}")
            written.append(current)
    except Exception as exc:
        print(f"Doubles heatmaps failed: {exc!r}")
        for fname in written + ([current] if current and current not in written else []):
            _remove_file(os.path.join(save_dir, fname))
        return []
    return written


def _remove_file(path):
    """True when `path` is gone afterwards; prints when a file exists and cannot be removed."""
    if not os.path.exists(path):
        return True
    try:
        os.remove(path)
        return True
    except OSError as exc:
        print(f"Could not remove {path}: {exc!r}")
        return False


@functools.lru_cache(maxsize=64)
def _label_sprite(text, color_bgr, size):
    """(alpha HxW float32 in [0, 1], colour HxWx3 uint8) for text in FONT_PATH, or None.

    Rendered once per label and blitted onto each frame: converting a 4K frame
    to PIL and back on every frame would cost far more than the label.
    """
    if FONT_PATH is None:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(FONT_PATH, int(size))
        x0, y0, x1, y1 = font.getbbox(text)
        if x1 <= x0 or y1 <= y0:
            return None
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(mask).text((-x0, -y0), text, font=font, fill=255)
        alpha = np.asarray(mask, dtype=np.float32) / 255.0
        colour = np.empty(alpha.shape + (3,), np.uint8)
        colour[:] = color_bgr
        return alpha, colour
    except Exception as exc:
        print(f"Label font unavailable, falling back to ASCII labels: {exc!r}")
        return None


def _blit(frame, sprite, x, y):
    """Alpha-blend sprite onto frame with its top-left at (x, y), clipped to the frame."""
    alpha, colour = sprite
    h, w = alpha.shape
    fx0, fy0 = max(0, x), max(0, y)
    fx1, fy1 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    a = alpha[fy0 - y:fy1 - y, fx0 - x:fx1 - x, None]
    roi = frame[fy0:fy1, fx0:fx1]
    roi[:] = (colour[fy0 - y:fy1 - y, fx0 - x:fx1 - x] * a + roi * (1.0 - a)).astype(np.uint8)


def _draw_label(frame, text, x, y, color, width):
    """Label up-right of a marker at (x, y): PIL glyphs when the font exists, else cv2."""
    scale = max(0.4, width / 1600)
    sprite = _label_sprite(text, tuple(int(c) for c in color), max(12, int(round(30 * scale))))
    if sprite is None:
        cv2.putText(frame, text, (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2,
                    cv2.LINE_AA)
    else:
        _blit(frame, sprite, x + 6, y - 6 - sprite[0].shape[0])


def render_identity_video(video_path, out_path, identity_tracks, players, fps=None):
    from ..media import video_audio as vap

    cap = None
    writer = None
    temp = os.path.join(os.path.dirname(out_path) or ".", "temp_" + os.path.basename(out_path))
    # A file left by an earlier run must never be reported as this run's output.
    if not _remove_file(out_path):
        return False
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Identity video skipped: cannot open {video_path}")
            return False
        fps = fps or cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        writer = vap.setup_video_writer(frame_width=w, frame_height=h, fps=fps, temp_output_path=temp)
        labels = {p["id"]: (p.get("name") or p["id"]) for p in players or []}
        frame_no = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            for pid, obs in (identity_tracks or {}).items():
                o = obs.get(frame_no)
                if o is None:
                    continue
                x, y = int(o["image"][0]), int(o["image"][1])
                color = PLAYER_COLORS_BGR.get(pid, (255, 255, 255))
                cv2.circle(frame, (x, y), max(4, w // 160), color, -1, cv2.LINE_AA)
                _draw_label(frame, labels.get(pid, pid), x, y, color, w)
            writer.write(frame)
        writer.release()
        writer = None
        # The helper reports its own failures as False rather than raising; the raw
        # encode is still playable, so keep it when the copy step fails.
        if not vap.process_video_without_audio(temp_video_path=temp, output_path=out_path):
            os.replace(temp, out_path)
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
    except Exception as exc:
        print(f"Identity video failed: {exc!r}")
        return False
    finally:
        if writer is not None:
            writer.release()
        if cap is not None:
            cap.release()
        _remove_file(temp)
