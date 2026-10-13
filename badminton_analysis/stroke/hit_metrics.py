"""One-to-one hit matching within a time tolerance, plus recall/precision/hitter rates."""
import bisect


def match_events(truth, detected, tol):
    det_sorted = sorted(range(len(detected)), key=lambda j: detected[j])
    det_times = [detected[j] for j in det_sorted]
    cands = []
    for i, t in enumerate(truth):
        lo = bisect.bisect_left(det_times, t - tol)
        hi = bisect.bisect_right(det_times, t + tol)
        for pos in range(lo, hi):
            j = det_sorted[pos]
            cands.append((abs(detected[j] - t), i, j))
    cands.sort()
    used_t, used_d, pairs = set(), set(), []
    for _, i, j in cands:
        if i not in used_t and j not in used_d:
            used_t.add(i)
            used_d.add(j)
            pairs.append((i, j))
    return sorted(pairs)


def summarize(truth, detected, tol, truth_sides=None, det_sides=None):
    pairs = match_events(list(truth), list(detected), tol)
    n_t, n_d, n_m = len(truth), len(detected), len(pairs)
    recall = n_m / n_t if n_t else 0.0
    precision = n_m / n_d if n_d else 0.0
    f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    compared = correct = 0
    if truth_sides is not None and det_sides is not None:
        for i, j in pairs:
            if truth_sides[i] and det_sides[j]:
                compared += 1
                correct += truth_sides[i] == det_sides[j]
    return {"truth": n_t, "detected": n_d, "matched": n_m, "recall": recall,
            "precision": precision, "f1": f1, "hitter_compared": compared,
            "hitter_correct": (correct / compared) if compared else None}
