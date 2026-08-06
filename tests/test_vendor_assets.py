"""Regression checks for the self-hosted frontend asset bundles."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "api" / "static" / "vendor"


def test_referenced_vendor_assets_exist():
    references = set()
    for template in (ROOT / "api" / "templates").glob("*.html"):
        text = template.read_text(encoding="utf-8")
        references.update(re.findall(r"/static/vendor/[A-Za-z0-9_./-]+", text))

    missing = [path for path in sorted(references) if not (ROOT / "api" / path.lstrip("/")).is_file()]
    assert not missing, f"Missing referenced vendored assets: {missing}"


def test_current_vendored_bundle_versions():
    bootstrap_css = (VENDOR / "bootstrap/bootstrap.min.css").read_text(encoding="utf-8")
    bootstrap_js = (VENDOR / "bootstrap/bootstrap.bundle.min.js").read_text(encoding="utf-8")
    chart_js = (VENDOR / "chart.min.js").read_text(encoding="utf-8")
    gridstack_js = (VENDOR / "gridstack/gridstack-all.js").read_text(encoding="utf-8")
    fontawesome_css = (VENDOR / "fontawesome/all.min.css").read_text(encoding="utf-8")

    assert "Bootstrap  v5.3.8" in bootstrap_css
    assert "Bootstrap v5.3.8" in bootstrap_js
    assert "Chart.js v4.5.1" in chart_js
    assert 'GDRev="13.1.2"' in gridstack_js
    assert "Font Awesome Free 7.3.1" in fontawesome_css


def test_vendor_source_maps_are_valid_and_present():
    for bundle in (
        VENDOR / "bootstrap/bootstrap.bundle.min.js",
        VENDOR / "chart.min.js",
        VENDOR / "gridstack/gridstack-all.js",
    ):
        text = bundle.read_text(encoding="utf-8")
        match = re.search(r"sourceMappingURL=([^\s]+)", text)
        assert match, f"Missing source map reference: {bundle}"
        path = bundle.parent / match.group(1)
        assert path.is_file(), f"Missing source map: {path}"
        source_map = json.loads(path.read_text(encoding="utf-8"))
        assert source_map["version"] == 3


def test_fontawesome_uses_local_webfonts():
    css = (VENDOR / "fontawesome/all.min.css").read_text(encoding="utf-8")
    assert "../webfonts/fa-solid-900.woff2" in css
    assert not re.search(r"url\(\s*https?://", css)
    for filename in (
        "fa-brands-400.woff2",
        "fa-regular-400.woff2",
        "fa-solid-900.woff2",
        "fa-v4compatibility.woff2",
    ):
        assert (VENDOR / "webfonts" / filename).is_file()


def test_vendor_license_files_are_present():
    for path in (
        VENDOR / "bootstrap/LICENSE.txt",
        VENDOR / "chart-LICENSE.md",
        VENDOR / "fontawesome/LICENSE.txt",
        VENDOR / "gridstack/LICENSE.txt",
        VENDOR / "gridstack/gridstack-all.js.LICENSE.txt",
    ):
        assert path.is_file(), f"Missing vendor license: {path}"
