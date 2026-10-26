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
        # The same for data/athletes.json: a scope="all" delete forgets the
        # video's match record there.
        monkeypatch.setattr(webapp, "ATHLETES_PATH", tmp_path / "athletes.json")
        # And data/rally_labels/: a scope="all" delete removes the video's labels.
        monkeypatch.setattr(webapp, "RALLY_LABELS_DIR", tmp_path / "rally_labels")


@pytest.fixture(autouse=True)
def _no_strategy_reports_by_default(monkeypatch):
    """A fake match job that finishes would otherwise queue a strategy report job, whose
    Popen call can land in a later test's fake (a tracker thread outlives its test).
    Tests of the report job turn it back on themselves."""
    monkeypatch.setenv("GB_STRATEGY_REPORT", "off")


@pytest.fixture(autouse=True)
def _fresh_job_queue(monkeypatch):
    """Each test gets an empty job table and an uncapped scheduler.

    Many tests post /api/analyze for the same "clip.mp4" with fake processes
    that never finish (or with threading.Thread stubbed out, so a slot is
    never released). Shared across tests, that would 409 the next start or
    leave it queued forever. Tests of the cap itself install their own.
    """
    webapp = sys.modules.get("app")
    if webapp is not None:
        from badminton_analysis.web.job_slots import JobSlots
        monkeypatch.setattr(webapp, "jobs", {})
        monkeypatch.setattr(webapp, "_job_slots",
                            JobSlots(10_000, on_queue_change=webapp._queue_changed))
