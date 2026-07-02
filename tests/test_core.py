from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from figedit_core import (  # noqa: E402
    compile_svg_path,
    formula_text_leaks,
    prepare_render_manifest,
    validate_manifest,
)


def base_manifest(source: Path) -> dict:
    return {
        "project": "test",
        "source_image": str(source),
        "canvas": {"width": 400, "height": 200, "background": "#fff"},
        "classification": {"layout_topology": "linear-flow", "complexity": "medium", "style_type": "academic-color", "reconstruction_mode": "model-led-hybrid"},
        "panels": [{"id": "p", "x": 10, "y": 10, "w": 380, "h": 180}],
        "assets": [],
        "elements": [
            {"id": "a", "type": "rect", "x": 20, "y": 50, "w": 80, "h": 40},
            {"id": "b", "type": "rect", "x": 300, "y": 50, "w": 80, "h": 40},
            {"id": "c", "type": "connector", "from_id": "a", "to_id": "b"},
        ],
    }


class CoreTests(unittest.TestCase):
    def test_manifest_and_connector_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            Image.new("RGB", (400, 200), "white").save(source)
            manifest = base_manifest(source)
            result = validate_manifest(manifest, Path(tmp) / "manifest.json")
            self.assertEqual([], result["errors"])
            manifest["elements"][-1]["to_id"] = "missing"
            self.assertTrue(any("invalid from_id/to_id" in e for e in validate_manifest(manifest)["errors"]))

    def test_full_canvas_asset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            Image.new("RGB", (400, 200), "white").save(source)
            manifest = base_manifest(source)
            manifest["assets"] = [{"id": "whole", "file": "assets/whole.png", "x": 0, "y": 0, "w": 400, "h": 200, "source_region": {"x": 0, "y": 0, "w": 400, "h": 200}}]
            self.assertTrue(any("full-canvas" in e for e in validate_manifest(manifest)["errors"]))

    def test_panel_local_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            Image.new("RGB", (400, 200), "white").save(source)
            manifest = base_manifest(source)
            manifest["elements"][0].update({"coordinate_space": "panel", "panel_id": "p", "x": .8, "y": .1, "w": .3, "h": .2})
            self.assertTrue(any("exceeds panel width" in e for e in validate_manifest(manifest)["errors"]))

    def test_path_compilation(self) -> None:
        segments = compile_svg_path("M 0 0 L 10 0 C 10 1 12 3 15 5 Q 20 10 25 5 Z")
        self.assertEqual(["line", "bezier", "bezier", "line"], [s["type"] for s in segments])

    def test_formula_text_leak(self) -> None:
        leaks = formula_text_leaks({"elements": [{"id": "bad", "type": "text", "text": r"loss=\sum_i x_i"}]})
        self.assertEqual("bad", leaks[0]["id"])

    def test_prepare_assets_formula_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (400, 200), "white").save(source)
            manifest = base_manifest(source)
            manifest["assets"] = [{"id": "icon", "file": "assets/icon.png", "x": 120, "y": 40, "w": 40, "h": 40, "source_region": {"x": 120, "y": 40, "w": 40, "h": 40}, "edge_policy": "allow-border-touch"}]
            manifest["elements"].extend([
                {"id": "icon-image", "type": "image", "asset_id": "icon", "x": 120, "y": 40, "w": 40, "h": 40},
                {"id": "formula", "type": "math", "latex": r"\frac{a}{b}", "x": 160, "y": 120, "w": 80, "h": 30},
                {"id": "curve", "type": "path", "d": "M 20 150 C 60 100 100 100 140 150"},
            ])
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            out = root / "out"
            prepare_render_manifest(path, out)
            prepared = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            image_el = next(e for e in prepared["elements"] if e["id"] == "icon-image")
            math_el = next(e for e in prepared["elements"] if e["id"] == "formula")
            path_el = next(e for e in prepared["elements"] if e["id"] == "curve")
            self.assertTrue(Path(image_el["resolved_file"]).exists())
            self.assertTrue(Path(math_el["formula_file"]).exists())
            self.assertEqual("bezier", path_el["visio_segments"][0]["type"])


if __name__ == "__main__":
    unittest.main()

