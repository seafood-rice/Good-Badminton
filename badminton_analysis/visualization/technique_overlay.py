"""Lightweight in-video technique overlay (live, instantaneous angles)."""
import cv2

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _score_color(score):
    if score >= 75:
        return (0, 200, 0)
    if score >= 50:
        return (0, 200, 200)
    return (0, 0, 220)


def draw_technique_overlay(frame, angles, label=None, score=None, origin=(20, 20)):
    x, y = origin
    line_h = 22

    if label:
        cv2.putText(frame, str(label), (x, y), _FONT, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        y += line_h + 4

    if score is not None:
        color = _score_color(score)
        cv2.rectangle(frame, (x, y - 16), (x + 90, y + 6), color, -1)
        cv2.putText(frame, f"{score:.0f}", (x + 6, y), _FONT, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
        y += line_h + 4

    for name, value in angles.items():
        if value is None:
            continue
        text = f"{name}: {value:.0f}"
        cv2.putText(frame, text, (x, y), _FONT, 0.5, (200, 255, 200), 1, cv2.LINE_AA)
        y += line_h

    return frame
