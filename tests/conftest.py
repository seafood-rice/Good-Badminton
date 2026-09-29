import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def _keep_the_real_tag_store_off_the_suite(tmp_path, monkeypatch):
    """Safety net: no test may write data/library_tags.json.

    A test's own fixture (e.g. tests/test_app_tags.py's `client`) may still
    monkeypatch app.TAGS_PATH itself to a more specific tmp_path; this just
    catches the ones that forget to, such as tests/test_app_delete.py's
    `_tree`, whose scope="all" deletes otherwise fall through to the real
    on-disk store. Only touches sys.modules -- never imports `app` itself --
    so tests that never load the Flask app don't pay for importing it.
    """
    webapp = sys.modules.get("app")
    if webapp is not None:
        monkeypatch.setattr(webapp, "TAGS_PATH", tmp_path / "library_tags.json")
