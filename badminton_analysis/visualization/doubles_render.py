"""Doubles visuals: per-player / per-team court heatmaps and the identity video."""
import os

import cv2
import numpy as np

PLAYER_COLORS_BGR = {"P1": (255, 140, 0), "P2": (255, 220, 120),
                     "P3": (0, 80, 255), "P4": (120, 180, 255)}
COURT_W, COURT_L = 6.1, 13.4


def _heatmap_png(path, points, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(3.2, 6.4), dpi=100)
    if points:
        xs, ys = zip(*points)
        ax.hist2d(xs, ys, bins=(12, 26), range=[[0, COURT_W], [0, COURT_L]], cmap="magma")
    ax.add_patch(plt.Rectangle((0, 0), COURT_W, COURT_L, fill=False, color="white", lw=1.5))
    ax.axhline(COURT_L / 2, color="white", lw=1.0)
    ax.set_xlim(-0.3, COURT_W + 0.3)
    ax.set_ylim(COURT_L + 0.3, -0.3)
    ax.set_facecolor("black")
    ax.set_title(title, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_heatmaps(save_dir, payload, identity_tracks):
    written = []
    try:
        names = {p["id"]: (p.get("name") or p["id"]) for p in payload.get("players") or []}
        for pid, obs in (identity_tracks or {}).items():
            pts = [o["court"] for o in obs.values() if o.get("court") is not None]
            fname = f"heatmap_{pid}.png"
            _heatmap_png(os.path.join(save_dir, fname), pts, names.get(pid, pid))
            written.append(fname)
        for team in payload.get("teams") or []:
            pts = [o["court"] for pid in team.get("players") or []
                   for o in (identity_tracks or {}).get(pid, {}).values()
                   if o.get("court") is not None]
            fname = f"heatmap_team{team['id']}.png"
            _heatmap_png(os.path.join(save_dir, fname), pts, f"Team {team['id']}")
            written.append(fname)
    except Exception as exc:
        print(f"Doubles heatmaps failed: {exc!r}")
        return []
    return written


def render_identity_video(video_path, out_path, identity_tracks, players, fps=None):
    from ..media import video_audio as vap

    cap = None
    writer = None
    temp = os.path.join(os.path.dirname(out_path) or ".", "temp_" + os.path.basename(out_path))
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
                cv2.putText(frame, labels.get(pid, pid), (x + 6, y - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, max(0.4, w / 1600), color, 2, cv2.LINE_AA)
            writer.write(frame)
        writer.release()
        writer = None
        try:
            vap.process_video_without_audio(temp_video_path=temp, output_path=out_path)
        except Exception as exc:
            print(f"Identity video transcode failed, keeping the raw encode: {exc!r}")
            os.replace(temp, out_path)
        if os.path.exists(temp):
            os.remove(temp)
        return os.path.exists(out_path)
    except Exception as exc:
        print(f"Identity video failed: {exc!r}")
        return False
    finally:
        if writer is not None:
            writer.release()
        if cap is not None:
            cap.release()
