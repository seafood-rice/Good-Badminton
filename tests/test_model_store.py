import json
import os
import subprocess
import sys

import pytest

from badminton_analysis.models import store
from badminton_analysis.models.store import ModelSpec

SPEC = ModelSpec("rally", ("rally-segmenter.json", "rally-decoder.json"),
                 "rally-model-history.json", "rally")
SEG, DEC = SPEC.files


def _live(w, seg="s0", dec="d0"):
    w.mkdir(parents=True, exist_ok=True)
    if seg is not None:
        (w / SEG).write_text(seg, encoding="utf-8")
    if dec is not None:
        (w / DEC).write_text(dec, encoding="utf-8")


def _cand(w, **files):
    """Candidate files by model file name, e.g. ``_cand(w, **{SEG: "s1"})``."""
    out = {}
    for name, text in files.items():
        p = w / "candidates" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        out[name] = p
    return out


def _read(w, name):
    return (w / name).read_text(encoding="utf-8")


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _marker(w, pid, started=None, what="promote"):
    doc = {"pid": pid, "what": what}
    if started is not None:
        doc["started"] = started
    w.mkdir(parents=True, exist_ok=True)
    (w / SPEC.marker_name).write_text(json.dumps(doc), encoding="utf-8")


def test_marker_name_defaults_per_model_and_can_be_pinned():
    assert SPEC.marker_name == ".swap-in-progress-rally"
    assert ModelSpec("stroke", ("a.pt",), "h.json", None, marker=".swap-in-progress").marker_name \
        == ".swap-in-progress"


def test_promote_one_file_leaves_the_other_alone(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    entry = store.promote(SPEC, w, _cand(w, **{SEG: "s1"}), {"note": 1})
    assert _read(w, SEG) == "s1" and _read(w, DEC) == "d0"
    assert entry["kept"] is True and entry["note"] == 1 and entry["replaced"]
    retired = w / "history" / "rally" / entry["replaced"]
    assert (retired / SEG).read_text(encoding="utf-8") == "s0" and not (retired / DEC).exists()
    assert store.history(SPEC, w)[-1]["replaced"] == entry["replaced"]
    assert not (w / SPEC.marker_name).exists()


def test_promote_both_files_and_first_model(tmp_path):
    w = tmp_path / "weights"
    first = store.promote(SPEC, w, _cand(w, **{SEG: "s1", DEC: "d1"}), {})
    assert first["replaced"] and _read(w, SEG) == "s1" and _read(w, DEC) == "d1"   # an "absent" placeholder
    second = store.promote(SPEC, w, _cand(w, **{SEG: "s2", DEC: "d2"}), {})
    retired = w / "history" / "rally" / second["replaced"]
    assert (retired / SEG).read_text(encoding="utf-8") == "s1"
    assert (retired / DEC).read_text(encoding="utf-8") == "d1"
    assert _read(w, SEG) == "s2" and _read(w, DEC) == "d2"


def test_promote_with_a_missing_candidate_changes_nothing(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    with pytest.raises(OSError):
        store.promote(SPEC, w, {SEG: w / "candidates" / "gone.json"}, {})
    assert _read(w, SEG) == "s0" and _read(w, DEC) == "d0"
    assert not (w / SPEC.marker_name).exists()
    assert not list((w / "history" / "rally").glob("*"))


def test_history_keeps_only_the_last_five_replaced_models(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    for n in range(1, 9):
        store.promote(SPEC, w, _cand(w, **{SEG: f"s{n}"}), {})
    dirs = sorted(p for p in (w / "history" / "rally").iterdir() if p.is_dir())
    assert len(dirs) == 5
    assert [(d / SEG).read_text(encoding="utf-8") for d in dirs] == ["s3", "s4", "s5", "s6", "s7"]
    assert len(store.history(SPEC, w)) == 8


def test_rollback_goes_one_model_back_per_call(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    store.promote(SPEC, w, _cand(w, **{SEG: "s1"}), {})
    store.promote(SPEC, w, _cand(w, **{SEG: "s2", DEC: "d2"}), {})
    assert store.can_rollback(SPEC, w)
    assert store.rollback(SPEC, w, train_labels=7) is True
    assert _read(w, SEG) == "s1" and _read(w, DEC) == "d0"
    last = store.history(SPEC, w)[-1]
    assert last["rollback"] is True and last["train_labels"] == 7
    assert store.rollback(SPEC, w) is True
    assert _read(w, SEG) == "s0" and _read(w, DEC) == "d0"
    assert store.can_rollback(SPEC, w) is False
    assert store.rollback(SPEC, w) is False
    assert not list(w.glob(".rollback-*"))


def test_marker_refused_while_its_process_is_alive(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    store.promote(SPEC, w, _cand(w, **{SEG: "s1"}), {})     # leaves something to roll back to
    _marker(w, os.getpid(), started=store.started(os.getpid()))
    cand = _cand(w, **{SEG: "s2"})
    with pytest.raises(store.SwapInProgress):
        store.promote(SPEC, w, cand, {})
    with pytest.raises(store.SwapInProgress):
        store.rollback(SPEC, w)
    assert _read(w, SEG) == "s1" and cand[SEG].exists() and (w / SPEC.marker_name).is_file()


def test_marker_holds_pid_and_start_time_only_while_swapping(tmp_path):
    w = tmp_path / "weights"
    with store.swap_marker(SPEC, w, "promote"):
        doc = json.loads((w / SPEC.marker_name).read_text(encoding="utf-8"))
    assert doc["pid"] == os.getpid() and abs(doc["started"] - store.started(os.getpid())) < 1.0
    assert doc["what"] == "promote"
    assert not (w / SPEC.marker_name).exists()


def test_dead_marker_is_recovered_before_the_next_swap(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    cand = _cand(w, **{SEG: "s1"})
    # what a kill between promote's two moves leaves: live segmenter retired, candidate staged
    retired = store.new_history_dir(SPEC, w)
    os.replace(w / SEG, retired / SEG)
    _marker(w, _dead_pid())
    assert store.recover(SPEC, w) is True
    assert _read(w, SEG) == "s0" and not retired.exists() and not (w / SPEC.marker_name).exists()
    assert store.recover(SPEC, w) is False            # idempotent
    _marker(w, _dead_pid())
    os.replace(w / SEG, store.new_history_dir(SPEC, w) / SEG)
    store.promote(SPEC, w, cand, {})                  # recovers first, then swaps
    assert _read(w, SEG) == "s1" and _read(w, DEC) == "d0"


def test_recover_completes_an_interrupted_rollback(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    store.promote(SPEC, w, _cand(w, **{SEG: "s1"}), {})
    newest = sorted(p for p in (w / "history" / "rally").iterdir())[-1]
    staged = w / f".rollback-{newest.name}-{SEG}"
    os.replace(newest / SEG, staged)
    retired = store.new_history_dir(SPEC, w)
    os.replace(w / SEG, retired / SEG)
    _marker(w, _dead_pid(), what="rollback")
    assert store.recover(SPEC, w) is True
    assert _read(w, SEG) == "s0" and not staged.exists()
    assert (retired / store.RETIRED_MARK).is_file()
    assert store.can_rollback(SPEC, w) is False


def test_recover_leaves_a_live_process_and_a_markerless_dir_alone(tmp_path):
    w = tmp_path / "weights"
    _live(w, seg=None)
    assert store.recover(SPEC, w) is False             # no marker: a missing model stays missing
    _marker(w, os.getpid(), started=store.started(os.getpid()))
    assert store.recover(SPEC, w) is False
    assert (w / SPEC.marker_name).is_file() and not (w / SEG).exists()


def test_reused_pid_with_another_start_time_is_not_alive(tmp_path):
    w = tmp_path / "weights"
    _marker(w, os.getpid(), started=store.started(os.getpid()) - 3600)
    assert store.marker_alive(w / SPEC.marker_name) is False
    _marker(w, os.getpid(), started=store.started(os.getpid()))
    assert store.marker_alive(w / SPEC.marker_name) is True
    _marker(w, _dead_pid())
    assert store.marker_alive(w / SPEC.marker_name) is False


def test_reject_deletes_the_candidate_and_records_it(tmp_path):
    w = tmp_path / "weights"
    _live(w)
    cand = _cand(w, **{SEG: "s1"})
    entry = store.reject(SPEC, w, cand, {"candidate": {"f1": 0.1}})
    assert not cand[SEG].exists() and _read(w, SEG) == "s0"
    assert entry["kept"] is False and entry["replaced"] is None
    assert store.history(SPEC, w)[-1]["candidate"] == {"f1": 0.1}


def test_stroke_style_spec_keeps_its_names(tmp_path):
    spec = ModelSpec("stroke", ("m.pt", "m.json"), "h.json", None, marker=".swap-in-progress")
    w = tmp_path / "weights"
    w.mkdir()
    (w / "m.pt").write_text("a")
    c = w / "c"
    c.mkdir()
    (c / "x.pt").write_text("b")
    (c / "x.json").write_text("{}")
    entry = store.promote(spec, w, {"m.pt": c / "x.pt", "m.json": c / "x.json"}, {})
    assert (w / "history" / entry["replaced"] / "m.pt").is_file()
    assert (w / "m.pt").read_text() == "b" and (w / "m.json").is_file()


def test_rally_rollback_to_no_model(tmp_path):
    """Review 9: the first promote records that nothing was live, so the owner can roll back to no model."""
    w = tmp_path / "weights"
    first = store.promote(SPEC, w, _cand(w, **{SEG: "s1", DEC: "d1"}), {})
    assert store.can_rollback(SPEC, w) is True
    assert (w / "history" / "rally" / first["replaced"] / store.ABSENT_MARK).is_file()
    assert store.rollback(SPEC, w) is True
    assert not (w / SEG).exists() and not (w / DEC).exists()
    assert store.history(SPEC, w)[-1]["rollback"] is True
    assert store.can_rollback(SPEC, w) is False and store.rollback(SPEC, w) is False
    assert not list(w.glob(".rollback-*")) and not (w / SPEC.marker_name).exists()


def test_rollback_to_no_model_only_removes_the_files_that_were_absent(tmp_path):
    w = tmp_path / "weights"
    _live(w, seg="s0", dec=None)                       # a segmenter, never a decoder
    store.promote(SPEC, w, _cand(w, **{SEG: "s1", DEC: "d1"}), {})
    assert store.rollback(SPEC, w) is True
    assert _read(w, SEG) == "s0" and not (w / DEC).exists()


def test_stroke_style_spec_cannot_roll_back_to_no_model(tmp_path):
    spec = ModelSpec("stroke", ("m.pt", "m.json"), "h.json", None, marker=".swap", primary="m.pt",
                     rollback_to_absent=False)
    w = tmp_path / "weights"
    w.mkdir()
    c = w / "c"
    c.mkdir()
    (c / "x.pt").write_text("a")
    (c / "x.json").write_text("{}")
    entry = store.promote(spec, w, {"m.pt": c / "x.pt", "m.json": c / "x.json"}, {})
    assert entry["replaced"] is None and store.can_rollback(spec, w) is False


def test_store_recover_does_not_overwrite_present_live_files(tmp_path):
    """Review 12: a half promote of both files must not clobber a live file that did get installed."""
    w = tmp_path / "weights"
    _live(w, seg="new-s", dec=None)                    # the candidate segmenter landed, the decoder was retired
    retired = store.new_history_dir(SPEC, w)
    (retired / SEG).write_text("old-s", encoding="utf-8")
    (retired / DEC).write_text("old-d", encoding="utf-8")
    _marker(w, _dead_pid())
    assert store.recover(SPEC, w) is True
    assert _read(w, SEG) == "new-s" and _read(w, DEC) == "old-d"
    assert (retired / SEG).read_text(encoding="utf-8") == "old-s"      # its history copy is kept
    assert not (w / SPEC.marker_name).exists()


def test_store_recover_staged_rollback_does_not_overwrite_present_live_files(tmp_path):
    w = tmp_path / "weights"
    _live(w, seg="live-s", dec=None)
    staged_seg, staged_dec = w / f".rollback-20260101T000000000Z-{SEG}", w / f".rollback-20260101T000000000Z-{DEC}"
    staged_seg.write_text("old-s", encoding="utf-8")
    staged_dec.write_text("old-d", encoding="utf-8")
    _marker(w, _dead_pid(), what="rollback")
    store.recover(SPEC, w)
    assert _read(w, SEG) == "live-s" and _read(w, DEC) == "old-d"


def test_a_sidecar_only_history_dir_is_not_a_rollback_target(tmp_path):
    """Review 13: the primary file (the .pt) must be there for a dir to hold a model."""
    spec = ModelSpec("stroke", ("m.pt", "m.json"), "h.json", None, marker=".swap", primary="m.pt",
                     rollback_to_absent=False)
    w = tmp_path / "weights"
    old = store.new_history_dir(spec, w)
    (old / "m.json").write_text("{}", encoding="utf-8")
    assert store.can_rollback(spec, w) is False and store.rollback(spec, w) is False
    (old / "m.pt").write_text("a", encoding="utf-8")
    assert store.can_rollback(spec, w) is True
    # a spec without a primary file counts either file
    other = store.new_history_dir(SPEC, w)
    (other / DEC).write_text("d", encoding="utf-8")
    assert store.can_rollback(SPEC, w) is True


STROKE = ModelSpec("stroke", ("m.pt", "m.json"), "h.json", None, marker=".swap", primary="m.pt",
                   rollback_to_absent=False)


def _group_marker(spec, w, what, **info):
    w.mkdir(parents=True, exist_ok=True)
    doc = {"pid": _dead_pid(), "what": what, **info}
    (w / spec.marker_name).write_text(json.dumps(doc), encoding="utf-8")


def _pair_live(w, pt, js):
    w.mkdir(parents=True, exist_ok=True)
    (w / "m.pt").write_text(pt, encoding="utf-8")
    (w / "m.json").write_text(js, encoding="utf-8")


def test_stroke_recover_half_promote_restores_the_pair(tmp_path):
    """Review 1: a kill between the sidecar install and the .pt install must not leave a mixed pair."""
    w = tmp_path / "weights"
    w.mkdir()
    retired = store.new_history_dir(STROKE, w)          # both old files retired ...
    (retired / "m.pt").write_text("old-pt", encoding="utf-8")
    (retired / "m.json").write_text("old-js", encoding="utf-8")
    (w / "m.json").write_text("new-js", encoding="utf-8")   # ... the new sidecar landed, the .pt did not
    _group_marker(STROKE, w, "promote", names=["m.pt", "m.json"], installs=["m.pt", "m.json"], after="")
    assert store.recover(STROKE, w) is True
    assert _read(w, "m.pt") == "old-pt" and _read(w, "m.json") == "old-js"
    assert not retired.exists() and not (w / STROKE.marker_name).exists()


def test_stroke_recover_half_retire_restores_the_sidecar(tmp_path):
    w = tmp_path / "weights"
    _pair_live(w, "old-pt", "old-js")
    retired = store.new_history_dir(STROKE, w)
    os.replace(w / "m.json", retired / "m.json")        # killed between the two retire moves
    _group_marker(STROKE, w, "promote", names=["m.pt", "m.json"], installs=["m.pt", "m.json"], after="")
    assert store.recover(STROKE, w) is True
    assert _read(w, "m.pt") == "old-pt" and _read(w, "m.json") == "old-js"


def test_recover_leaves_a_finished_promote_alone(tmp_path):
    """A candidate without a sidecar: the old sidecar is retired on purpose and stays retired."""
    w = tmp_path / "weights"
    retired = store.new_history_dir(STROKE, w)
    (retired / "m.pt").write_text("old-pt", encoding="utf-8")
    (retired / "m.json").write_text("old-js", encoding="utf-8")
    (w / "m.pt").write_text("new-pt", encoding="utf-8")
    _group_marker(STROKE, w, "promote", names=["m.pt", "m.json"], installs=["m.pt"], after="")
    assert store.recover(STROKE, w) is False
    assert _read(w, "m.pt") == "new-pt" and not (w / "m.json").exists() and retired.exists()


def test_stroke_recover_staged_rollback_between_the_retire_moves(tmp_path):
    w = tmp_path / "weights"
    _pair_live(w, "v0-pt", "v0-js")
    store.promote(STROKE, w, {"m.pt": _cand(w, **{"m.pt": "v1-pt"})["m.pt"],
                              "m.json": _cand(w, **{"m.json": "v1-js"})["m.json"]}, {})
    home = sorted(p for p in w.joinpath("history").iterdir())[-1]
    staged_js, staged_pt = w / f".rollback-{home.name}.json", w / f".rollback-{home.name}.pt"
    os.replace(home / "m.json", staged_js)
    os.replace(home / "m.pt", staged_pt)
    retired = store.new_history_dir(STROKE, w)
    os.replace(w / "m.json", retired / "m.json")        # killed before the .pt was retired
    _group_marker(STROKE, w, "rollback", names=["m.pt", "m.json"], stamp=home.name, after=home.name)
    assert store.recover(STROKE, w) is True
    assert _read(w, "m.pt") == "v0-pt" and _read(w, "m.json") == "v0-js"
    assert (retired / "m.pt").read_text(encoding="utf-8") == "v1-pt"
    assert (retired / "m.json").read_text(encoding="utf-8") == "v1-js"
    assert (retired / store.RETIRED_MARK).is_file() and not staged_pt.exists() and not staged_js.exists()
    assert not home.exists() and store.can_rollback(STROKE, w) is False


def test_recover_only_touches_the_files_the_swap_moved(tmp_path):
    w = tmp_path / "weights"
    _live(w, seg="other-s", dec=None)                   # the segmenter is outside this promote
    retired = store.new_history_dir(SPEC, w)
    (retired / DEC).write_text("old-d", encoding="utf-8")
    (retired / SEG).write_text("stale-s", encoding="utf-8")
    _group_marker(SPEC, w, "promote", names=[DEC], installs=[DEC], after="")
    assert store.recover(SPEC, w) is True
    assert _read(w, SEG) == "other-s" and _read(w, DEC) == "old-d"
    assert (retired / SEG).read_text(encoding="utf-8") == "stale-s"


def test_real_promote_and_rollback_record_their_group_in_the_marker(tmp_path):
    w = tmp_path / "weights"
    _pair_live(w, "v0-pt", "v0-js")
    seen = []

    def spy(wd, files):
        seen.append(json.loads((w / STROKE.marker_name).read_text(encoding="utf-8")))
        return store.install(STROKE, wd, files)

    store.promote(STROKE, w, {"m.pt": _cand(w, **{"m.pt": "v1"})["m.pt"], "m.json": None}, {}, install_fn=spy)
    assert seen[-1]["names"] == ["m.pt", "m.json"] and seen[-1]["installs"] == ["m.pt"]
    assert store.rollback(STROKE, w, install_fn=spy) is True
    assert seen[-1]["names"] == ["m.pt", "m.json"] and seen[-1]["stamp"] and "after" in seen[-1]
