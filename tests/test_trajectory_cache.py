# tests/test_trajectory_cache.py
from badminton_analysis.shuttle_track import trajectory as tj


def _video(tmp_path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"fake")
    return str(v)


def test_save_then_load_round_trips(tmp_path):
    v = _video(tmp_path)
    cache = str(tmp_path / "traj.json")
    key = tj.cache_key(v, {"eval_mode": "weight", "inpaint": True})
    traj = {0: (1.5, 2.5), 1: None, 2: (3.0, 4.0)}
    tj.save_cache(cache, key, traj)
    loaded = tj.load_cache(cache, key)
    assert loaded == traj
    assert isinstance(next(iter(loaded)), int)


def test_load_returns_none_on_key_mismatch(tmp_path):
    v = _video(tmp_path)
    cache = str(tmp_path / "traj.json")
    tj.save_cache(cache, tj.cache_key(v, {"a": 1}), {0: (1.0, 1.0)})
    assert tj.load_cache(cache, tj.cache_key(v, {"a": 2})) is None


def test_load_returns_none_when_missing(tmp_path):
    assert tj.load_cache(str(tmp_path / "nope.json"), "k") is None


def test_key_changes_with_params(tmp_path):
    v = _video(tmp_path)
    assert tj.cache_key(v, {"inpaint": True}) != tj.cache_key(v, {"inpaint": False})
