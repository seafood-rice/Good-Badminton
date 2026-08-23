# scripts/check_posture_regression.py
"""Verify high_clear rep counts are unchanged after the rep-detection changes.

high_clear is the only stroke with frame-verified ground truth, so it is the guard
on "no behaviour change for high_clear". Counts come from the drill_summary.json of
runs made before this work.

Usage:
  python scripts/check_posture_regression.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED = {"highclear1": 16, "IMG_1270": 5, "IMG_9691": 7}


def main():
    failures = []
    checked = 0
    for name, want in sorted(EXPECTED.items()):
        path = os.path.join(ROOT, "outputs", name, "posture", "drill_summary.json")
        if not os.path.exists(path):
            print(f"  {name:<14} SKIP (no run at {path})")
            continue
        checked += 1
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh).get("rep_count")
        ok = got == want
        print(f"  {name:<14} expected {want:>3}  got {str(got):>4}  "
              f"{'OK' if ok else 'CHANGED'}")
        if not ok:
            failures.append((name, want, got))
    if checked == 0:
        print("\nno runs found -- nothing verified.")
        return 1
    if failures:
        print("\nhigh_clear rep counts CHANGED. Investigate before shipping -- none of")
        print("Tasks 1-5 should alter high_clear. Task 3 can legitimately move a")
        print("CONTACT FRAME when several shuttle detections share a frame, but it")
        print("must not change the rep COUNT.")
        return 1
    print("\nhigh_clear rep counts unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
