import sys

import pytest

from badminton_analysis.shuttle_track.tracknet import vendored_imports, _VENDOR_DIR


def test_restores_prior_model_entry():
    sentinel = object()
    sys.modules["model"] = sentinel
    try:
        with vendored_imports():
            import model  # tracknet's flat model.py
            assert hasattr(model, "TrackNet")
        assert sys.modules["model"] is sentinel
    finally:
        sys.modules.pop("model", None)


def test_restores_on_exception():
    sentinel = object()
    sys.modules["model"] = sentinel
    try:
        with pytest.raises(RuntimeError):
            with vendored_imports():
                import model  # noqa: F401
                raise RuntimeError("boom")
        assert sys.modules["model"] is sentinel
    finally:
        sys.modules.pop("model", None)


def test_leaves_preexisting_path_entry():
    vendor = str(_VENDOR_DIR)
    added = vendor not in sys.path
    if added:
        sys.path.insert(0, vendor)
    try:
        with vendored_imports():
            pass
        assert vendor in sys.path  # CM must not remove a path it didn't add
    finally:
        if added:
            try:
                sys.path.remove(vendor)
            except ValueError:
                pass
