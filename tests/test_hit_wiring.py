import inspect

import app as webapp


def test_hit_model_discovery_needs_weights_and_sidecar(tmp_path):
    assert webapp._hit_model_weights(base=tmp_path) is None
    (tmp_path / "hit-detector.pt").write_bytes(b"x")
    assert webapp._hit_model_weights(base=tmp_path) is None
    (tmp_path / "hit-detector.json").write_text("{}")
    assert webapp._hit_model_weights(base=tmp_path) == str(tmp_path / "hit-detector.pt")


def test_api_models_reports_hit_detector():
    body = webapp.app.test_client().get("/api/models").get_json()
    assert "hit_detector" in body


def test_main_has_hit_model_and_tracknet_mode_flags():
    import main
    src = inspect.getsource(main.main)
    assert "--hit-model" in src and "hit_model_path=args.hit_model" in src
    assert "--tracknet-mode" in src and "tracknet_mode=args.tracknet_mode" in src


def test_app_passes_hit_model_when_installed():
    src = inspect.getsource(webapp)
    assert "cmd += ['--hit-model', hit_weights]" in src
