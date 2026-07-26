import pytest

import app as webapp
from main_posture import build_parser


def test_lift_flags_defaults():
    args = build_parser().parse_args(["--video-path", "v.mp4", "--stroke-type", "smash"])
    assert args.lift_model is None
    assert args.lift_device == "auto"


def test_lift_flags_parsed():
    args = build_parser().parse_args(
        ["--video-path", "v.mp4", "--stroke-type", "smash",
         "--lift-model", "weights/motionbert.pt", "--lift-device", "cuda"])
    assert args.lift_model == "weights/motionbert.pt"
    assert args.lift_device == "cuda"


# ---- server-side weights resolution (never from the request) --------------


def test_lift_weights_absent(tmp_path):
    assert webapp._lift_weights(base=tmp_path) is None


def test_lift_weights_found(tmp_path):
    p = tmp_path / "motionbert.pt"
    p.write_bytes(b"x")
    assert webapp._lift_weights(base=tmp_path) == str(p)


class _FakeProc:
    """Popen stand-in; a non-zero exit keeps the tracker off the ffmpeg path."""

    returncode = 1

    def __init__(self):
        self.stdout = iter(())

    def wait(self):
        return self.returncode


@pytest.fixture
def analyze_client(tmp_path, monkeypatch):
    """POSTs to /api/posture/analyze capture the command instead of running it."""
    captured = {}

    def _fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr(webapp, "VIDEOS", tmp_path)
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "out")
    monkeypatch.setattr(webapp.subprocess, "Popen", _fake_popen)
    (tmp_path / "clip.mp4").write_bytes(b"x")
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), captured


def _post(client):
    return client.post("/api/posture/analyze",
                       json={"video": "clip.mp4", "stroke_type": "smash",
                             "lift_model": "C:/Windows/System32/evil.pt",
                             "lift_device": "cpu"})


def test_client_supplied_lift_model_path_is_ignored(analyze_client, monkeypatch):
    """A request must not be able to name the file torch.load unpickles."""
    client, captured = analyze_client
    monkeypatch.setattr(webapp, "_lift_weights", lambda base=None: None)
    assert _post(client).status_code == 200
    cmd = captured["cmd"]
    assert "--lift-model" not in cmd
    assert not any("evil.pt" in part for part in cmd)


def test_lift_model_uses_server_resolved_path(analyze_client, monkeypatch):
    client, captured = analyze_client
    monkeypatch.setattr(webapp, "_lift_weights", lambda base=None: "weights/motionbert.pt")
    assert _post(client).status_code == 200
    cmd = captured["cmd"]
    assert cmd[cmd.index("--lift-model") + 1] == "weights/motionbert.pt"
    assert cmd[cmd.index("--lift-device") + 1] == "cpu"
    assert not any("evil.pt" in part for part in cmd)


def test_lift_device_from_request_is_validated(analyze_client, monkeypatch):
    client, captured = analyze_client
    monkeypatch.setattr(webapp, "_lift_weights", lambda base=None: "weights/motionbert.pt")
    r = client.post("/api/posture/analyze",
                    json={"video": "clip.mp4", "stroke_type": "smash",
                          "lift_device": "; rm -rf /"})
    assert r.status_code == 200
    cmd = captured["cmd"]
    assert "--lift-model" in cmd
    assert "--lift-device" not in cmd
