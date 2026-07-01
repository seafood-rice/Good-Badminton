import json
import pytest
import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_report(tmp_path, video, lang):
    out = tmp_path / video / "posture"
    out.mkdir(parents=True, exist_ok=True)
    (out / ("coach_report_" + lang + ".json")).write_text(
        json.dumps({"lang": lang, "header": {"stroke": "smash"}, "summary": {},
                    "strengths": [], "weaknesses": [], "per_rep": [], "training_plan": {}}),
        encoding="utf-8")
    return out


def test_report_404_when_missing(client):
    assert client.get("/api/posture-report/none?lang=en").status_code == 404


def test_report_returns_requested_language(client, tmp_path):
    _write_report(tmp_path, "drill1", "zh-Hant")
    r = client.get("/api/posture-report/drill1?lang=zh-Hant")
    assert r.status_code == 200
    assert r.get_json()["lang"] == "zh-Hant"


def test_report_defaults_to_english(client, tmp_path):
    _write_report(tmp_path, "drill1", "en")
    r = client.get("/api/posture-report/drill1")
    assert r.status_code == 200 and r.get_json()["lang"] == "en"


def test_report_invalid_lang_400(client, tmp_path):
    _write_report(tmp_path, "drill1", "en")
    assert client.get("/api/posture-report/drill1?lang=fr").status_code == 400


def test_report_500_on_malformed(client, tmp_path):
    out = tmp_path / "bad" / "posture"; out.mkdir(parents=True, exist_ok=True)
    (out / "coach_report_en.json").write_text("{ not json", encoding="utf-8")
    r = client.get("/api/posture-report/bad?lang=en")
    assert r.status_code == 500 and "error" in r.get_json()
