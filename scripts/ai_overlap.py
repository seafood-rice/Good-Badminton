"""Warn when other unmerged branches or worktrees touch the paths you are about to edit.

Usage: python scripts/ai_overlap.py <path-prefix> [<path-prefix> ...]

Advisory only: always exits 0. It reports (a) files other branches changed since they forked
from main and that still differ from main, and (b) uncommitted changes in other worktrees, that fall under the given prefixes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROTECTED = 'main'


def _git(*args: str, cwd: str | None = None) -> str:
    out = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, encoding='utf-8')
    return out.stdout if out.returncode == 0 else ''


def _blob(ref: str, path: str) -> str:
    """Blob id of path at ref ('' if absent). Equal blobs mean main already has the branch's
    version of the file (e.g. squash-merged), so it is not a live overlap."""
    return _git('rev-parse', f'{ref}:{path}').strip()


def _matches(path: str, prefixes: list[str]) -> bool:
    return any(path == p or path.startswith(p.rstrip('/') + '/') for p in prefixes)


def _worktrees() -> list[tuple[Path, str]]:
    """(path, branch short name or '') for every worktree."""
    found: list[tuple[Path, str]] = []
    path, branch = None, ''
    for line in _git('worktree', 'list', '--porcelain').splitlines() + ['']:
        if line.startswith('worktree '):
            path = Path(line[9:])
        elif line.startswith('branch '):
            branch = line[7:].removeprefix('refs/heads/')
        elif not line and path is not None:
            found.append((path, branch))
            path, branch = None, ''
    return found


def find_overlaps(prefixes: list[str], here: str | None = None) -> list[str]:
    here = here or _git('rev-parse', '--show-toplevel').strip()
    current = _git('branch', '--show-current').strip()
    report: list[str] = []

    refs = _git('for-each-ref', '--format=%(refname:short)', 'refs/heads', 'refs/remotes/origin').split()
    seen: set[str] = set()
    for ref in refs:
        short = ref.removeprefix('origin/')
        if short in (PROTECTED, current, 'HEAD') or short in seen or short.startswith('archive/'):
            continue
        seen.add(short)
        base = _git('merge-base', PROTECTED, ref).strip()
        if not base:
            continue
        files = [f for f in _git('diff', '--name-only', f'{base}..{ref}').splitlines()
                 if _matches(f, prefixes) and _blob(ref, f) != _blob(PROTECTED, f)]
        if files:
            report.append(f'branch {short}: ' + ', '.join(files[:8]) + (' ...' if len(files) > 8 else ''))

    for path, branch in _worktrees():
        if Path(here).resolve() == path.resolve():
            continue
        dirty = [line[3:].strip('"') for line in _git('status', '--porcelain', cwd=str(path)).splitlines()]
        files = [f for f in dirty if _matches(f.replace('\\', '/'), prefixes)]
        if files:
            report.append(f'worktree {path} ({branch or "detached"}) uncommitted: ' + ', '.join(files[:8]))
    return report


def main(argv: list[str]) -> int:
    prefixes = [a.replace('\\', '/') for a in argv]
    if not prefixes:
        print(__doc__)
        return 0
    report = find_overlaps(prefixes)
    if report:
        print('OVERLAP (advisory): others are touching these paths - coordinate or rebase.')
        for line in report:
            print('  ' + line)
    else:
        print('No overlap found.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
