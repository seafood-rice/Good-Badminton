"""Kestrel front end: the node test suite, and the shell's static assets as served by Flask."""
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

import app as webapp

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_node_suite_passes():
    # Node expands the quoted glob itself (no shell), which also works on Windows.
    proc = subprocess.run(
        [NODE, "--test", "tests/js/*.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, shell=False,
    )
    assert proc.returncode == 0, (proc.stdout[-6000:] + "\n" + proc.stderr[-3000:])


@pytest.fixture
def client():
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def test_kestrel_page_loads_the_module_entry(client):
    r = client.get("/kestrel")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '<script type="module" src="/static/kestrel/main.js"></script>' in html
    assert '<div id="app"></div>' in html
    # the legacy IIFE is no longer loaded as a classic script
    assert "/static/kestrel.js" not in html


def test_legacy_script_moved():
    assert not (STATIC / "kestrel.js").exists()
    src = (STATIC / "kestrel" / "legacy" / "kestrel-legacy.js").read_text(encoding="utf-8")
    assert "window.Kestrel" not in src
    assert "DOMContentLoaded" not in src
    assert "function renderSidebar" not in src
    for name in ("mountLibrary", "mountNew", "mountResults", "onNavigate", "setLang", "setTheme"):
        assert re.search(r"export (?:var|const|let|function) " + name + r"\b", src), name


_STATIC_IMPORT = re.compile(r"""^\s*(?:import|export)\b[^'"`;]*?\bfrom\s*['"]([^'"]+)['"]""", re.M)
_BARE_IMPORT = re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.M)
_DYNAMIC_IMPORT = re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""")


def _url_to_path(url):
    assert url.startswith("/static/"), url
    return STATIC / unquote(url[len("/static/"):])


def _module_graph(entry_url):
    """Every module reachable from `entry_url` by static import, plus dynamic imports that exist on disk.

    Dynamic imports of views owned by other tasks may not exist yet; main.js renders a polite
    "not available yet" panel for those, so only existing ones are required to be served.
    """
    seen, todo = set(), [entry_url]
    while todo:
        url = todo.pop()
        if url in seen:
            continue
        seen.add(url)
        src = _url_to_path(url).read_text(encoding="utf-8")
        base = url.rsplit("/", 1)[0]
        specs = _STATIC_IMPORT.findall(src) + _BARE_IMPORT.findall(src)
        dyn = _DYNAMIC_IMPORT.findall(src)
        for spec, required in [(s, True) for s in specs] + [(s, False) for s in dyn]:
            if not spec.startswith("."):
                continue
            parts = (base + "/" + spec).split("/")
            out = []
            for p in parts:
                if p == "..":
                    out.pop()
                elif p and p != ".":
                    out.append(p)
            target = "/" + "/".join(out)
            if required or _url_to_path(target).exists():
                todo.append(target)
    return seen


def _shell_asset_urls():
    html = (ROOT / "kestrel.html").read_text(encoding="utf-8")
    urls = set(re.findall(r'<link rel="stylesheet" href="([^"]+)"', html))
    urls |= set(re.findall(r'<script[^>]*src="([^"]+)"', html))
    tokens = (STATIC / "kestrel" / "tokens.css").read_text(encoding="utf-8")
    urls |= set(re.findall(r"url\(\s*['\"]?(/static/[^'\")]+)['\"]?\s*\)", tokens))
    urls |= _module_graph("/static/kestrel/main.js")
    ground = (STATIC / "kestrel" / "shell" / "ground.js").read_text(encoding="utf-8")
    urls |= set(re.findall(r"['\"](/static/img/[^'\"]+)['\"]", ground))
    return urls


def test_shell_module_graph_includes_the_shell():
    urls = _shell_asset_urls()
    for must in ("/static/kestrel/main.js", "/static/kestrel/shell/rail.js", "/static/kestrel/shell/topbar.js",
                 "/static/kestrel/shell/palette.js", "/static/kestrel/shell/peek.js",
                 "/static/kestrel/shell/transition.js", "/static/kestrel/shell/ground.js",
                 "/static/kestrel/views/legacy.js", "/static/kestrel/legacy/kestrel-legacy.js",
                 "/static/kestrel/core/router.js", "/static/fonts/SpaceGrotesk-Variable.woff2",
                 "/static/kestrel/tokens.css", "/static/img/ground/court-aerial-night.jpg"):
        assert must in urls, must


def test_every_shell_asset_is_served(client):
    for url in sorted(_shell_asset_urls()):
        r = client.get(url)
        assert r.status_code == 200, url
        if url.endswith(".js"):
            # module scripts are refused by browsers unless served with a JavaScript MIME type
            assert "javascript" in r.headers.get("Content-Type", ""), (url, r.headers.get("Content-Type"))
        r.close()
