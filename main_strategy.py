"""Write ``strategy_report.json`` for one analysed run (the CLI the app's report job runs).

    python main_strategy.py <result_dir> [--athletes PATH] [--settings PATH]
                            [--focus upper|lower] [--url U] [--model M] [--langs en,zh]

The match record comes from the athletes store under the directory's name (a missing store means
no record). It prints one line per stage and tracks nothing else: the job follows the process.
Exit codes: 0 for an ``ok`` or ``partial`` report, 2 for ``failed`` (the report, with fallback
text, is still written), 1 for an unexpected error (a fallback document is still attempted).
"""
import argparse
import dataclasses
import sys
from pathlib import Path

from badminton_analysis.library import athletes as athlete_store
from badminton_analysis.strategy import config as strategy_config
from badminton_analysis.strategy import report

ROOT = Path(__file__).resolve().parent


def _parser():
    ap = argparse.ArgumentParser(description="Write the strategy report of an analysed match.")
    ap.add_argument("result_dir", help="outputs/<stem> directory of the analysed match")
    ap.add_argument("--athletes", default=str(ROOT / "data" / "athletes.json"))
    ap.add_argument("--settings", default=str(ROOT / "data" / "settings.json"))
    ap.add_argument("--focus", choices=("upper", "lower"))
    ap.add_argument("--url")
    ap.add_argument("--model")
    ap.add_argument("--langs")
    return ap


def _config(args):
    config = strategy_config.load_config(args.settings)
    changes = {}
    if args.url:
        changes["url"] = args.url.rstrip("/")
    if args.model:
        changes["model"] = args.model
    if args.langs:
        changes["langs"] = strategy_config._langs(args.langs)
    return dataclasses.replace(config, **changes) if changes else config


def _fallback_doc(out_dir, config, code):
    """A failed document with fallback text, for an unexpected error; None if even that fails."""
    try:
        doc = report.generate(out_dir, {}, athlete_store.empty(),
                              dataclasses.replace(config, url="http://127.0.0.1:9/v1"))
        doc["status"], doc["reason"] = "failed", {"code": code, "params": {}}
        doc["endpoint"] = config.url
        return doc
    except Exception:
        return None


def main(argv=None):
    args = _parser().parse_args(argv)
    out_dir = Path(args.result_dir)
    if not out_dir.is_dir():
        print(f"strategy: not a directory: {out_dir}", file=sys.stderr)
        return 1
    config = None
    try:
        config = _config(args)
        data = athlete_store.load(args.athletes)
        record = athlete_store.resolve_record(data, out_dir.name)
        print(f"strategy: building facts for {out_dir.name}", flush=True)
        print(f"strategy: asking {config.model} at {config.url} ({','.join(config.langs)})",
              flush=True)
        doc = report.generate(out_dir, record, data, config, focus=args.focus)
        report.write_atomic(out_dir, doc)
        print(f"strategy: {doc['status']} in {doc['duration_sec']} s -> "
              f"{out_dir / report.REPORT_FILE}", flush=True)
        if doc["status"] == "failed":
            print(f"strategy: failed: {doc['reason']}", file=sys.stderr)
            return 2
        return 0
    except Exception as exc:
        print(f"strategy: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        doc = _fallback_doc(out_dir, config or strategy_config.load_config(), "unexpected")
        if doc is not None:
            try:
                report.write_atomic(out_dir, doc)
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
