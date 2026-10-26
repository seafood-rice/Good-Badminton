"""Rally-outcome insight over ``rally_outcomes.json`` (rally outcome spec 5.5).

``ok`` only when every entry has a winner and the points reconcile with the
recorded result; ``partial`` (``outcomes_incomplete``) otherwise, so a reader
never takes a part-labelled match for the whole story.
"""
from . import envelope

SCHEMA = 1


def _usable(doc):
    return (isinstance(doc, dict) and doc.get("schema") == SCHEMA
            and isinstance(doc.get("rallies"), list) and isinstance(doc.get("summary"), dict))


def rally_outcomes_insight(run):
    doc = run.rally_outcomes
    if not _usable(doc):
        return envelope.unavailable("no_outcomes")
    summary = doc["summary"]
    value = dict(summary, rallies=doc["rallies"])
    coverage = summary.get("coverage")
    reconciliation = summary.get("reconciliation")
    status = reconciliation.get("status") if isinstance(reconciliation, dict) else None
    if coverage != 1 or status != "consistent":
        return envelope.partial(value, "outcomes_incomplete",
                                {"coverage": coverage, "reconciliation": status}, coverage=coverage)
    return envelope.ok(value, coverage=coverage)
