import app as webapp


def test_tracknet_weights_discovery(tmp_path):
    assert webapp._tracknet_weights(base=tmp_path) is None
    (tmp_path / "tracknet.pt").write_bytes(b"x")
    assert webapp._tracknet_weights(base=tmp_path) == str(tmp_path / "tracknet.pt")


def test_inpaintnet_weights_discovery(tmp_path):
    assert webapp._inpaintnet_weights(base=tmp_path) is None
    (tmp_path / "inpaintnet.pt").write_bytes(b"x")
    assert webapp._inpaintnet_weights(base=tmp_path) == str(tmp_path / "inpaintnet.pt")


def test_api_models_reports_tracknet():
    client = webapp.app.test_client()
    body = client.get("/api/models").get_json()
    assert "tracknet" in body


def test_main_has_tracknet_flags():
    import main
    import argparse
    # Build the parser the same way main.main() does, then parse a sample.
    # Simpler: assert the flags exist by parsing --help-free known args.
    # (Structural: main defines --tracknet-model / --inpaintnet-model.)
    import inspect
    src = inspect.getsource(main.main)
    assert "--tracknet-model" in src and "--inpaintnet-model" in src
    assert "tracknet_weights=args.tracknet_model" in src
    assert "inpaintnet_weights=args.inpaintnet_model" in src
