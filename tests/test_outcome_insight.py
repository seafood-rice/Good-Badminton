"""Rally-outcome insight reader, sidecar key and the end-of-analysis hook."""
import json
from types import SimpleNamespace

from badminton_analysis import system
from badminton_analysis.insights import sidecar
from badminton_analysis.insights.loaders import load_run
from badminton_analysis.insights.outcomes import rally_outcomes_insight
from badminton_analysis.outcomes import build as ob
from tests.outcome_fixtures import make_run, write


def _doc(coverage, status, labelled=1, entries=1):
    return {"schema": 1, "rallies": [{"id": 1, "winner_side": "upper"}],
            "summary": {"entries": entries, "labelled": labelled, "coverage": coverage,
                        "reconciliation": {"status": status}}}


def _run(doc):
    return SimpleNamespace(rally_outcomes=doc)


def test_unavailable_when_missing_or_wrong_schema():
    for doc in (None, {"schema": 2, "rallies": [], "summary": {}}, {"schema": 1, "rallies": {}, "summary": {}}):
        env = rally_outcomes_insight(_run(doc))
        assert env["status"] == "unavailable" and env["reason"]["code"] == "no_outcomes"


def test_partial_while_incomplete():
    env = rally_outcomes_insight(_run(_doc(0.5, "incomplete", labelled=1, entries=2)))
    assert env["status"] == "partial" and env["reason"]["code"] == "outcomes_incomplete"
    assert env["value"]["rallies"][0]["winner_side"] == "upper" and env["coverage"] == 0.5
    env = rally_outcomes_insight(_run(_doc(1, "short")))
    assert env["status"] == "partial"


def test_ok_when_complete_and_consistent():
    env = rally_outcomes_insight(_run(_doc(1.0, "consistent")))
    assert env["status"] == "ok" and env["value"]["entries"] == 1


def test_loader_and_sidecar_carry_outcomes(tmp_path):
    out = make_run(tmp_path / "vid", [(1, 1.0, 5.0)])
    assert load_run(out).rally_outcomes is None
    assert sidecar.compute_video(out.name, out)["outcomes"]["status"] == "unavailable"
    ob.build(out, tmp_path / "labels", tmp_path / "athletes.json")
    assert load_run(out).rally_outcomes["schema"] == 1
    # rally_outcomes.json is a sidecar input: the cached document is recomputed.
    assert sidecar.compute_video(out.name, out)["outcomes"]["status"] == "partial"


def test_system_hook_builds(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ob, "build", lambda out_dir: seen.append(out_dir))
    system.BadmintonAnalysisSystem._write_rally_outcomes(SimpleNamespace(save_dir=str(tmp_path)))
    assert seen == [str(tmp_path)]


def test_system_hook_never_fatal(monkeypatch, tmp_path, capsys):
    def boom(out_dir):
        raise RuntimeError("x")
    monkeypatch.setattr(ob, "build", boom)
    system.BadmintonAnalysisSystem._write_rally_outcomes(SimpleNamespace(save_dir=str(tmp_path)))
    assert "could not be written" in capsys.readouterr().out
