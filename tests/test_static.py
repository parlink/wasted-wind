"""The dashboard only calls API routes the server really has, and its assets are served."""
import os
import re
from pathlib import Path

os.environ["WW_DATA_SOURCE"] = "fixtures"

from fastapi.testclient import TestClient  # noqa: E402

from app.server import app  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
client = TestClient(app)


def _route_patterns():
    pats = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if path.startswith("/api/"):
            pats.append(re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"))
    return pats


def test_every_api_path_in_app_js_exists():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r"""["'`](/api/[a-z/_]+)""", js))
    assert used, "app.js should call the API"
    pats = _route_patterns()
    # paths built as `/api/reveal/farm/${id}` appear as the prefix '/api/reveal/farm/'
    missing = [u for u in used
               if not any(p.match(u) or p.match(u + "x") for p in pats)]
    assert not missing, f"app.js calls routes the server doesn't have: {missing}"


def test_assets_served():
    html = client.get("/").text
    for asset in ("app.js", "style.css"):
        assert asset in html
        assert client.get(f"/{asset}").status_code == 200
