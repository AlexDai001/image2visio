#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from figedit_core import audit_editability, read_json, write_json


VISIO_NS = {"v": "http://schemas.microsoft.com/office/visio/2012/main"}


def inspect_vsdx(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {"status": "failed", "message": "VSDX is missing or empty", "shape_count": 0, "media": []}
    shape_count = 0
    names: list[str] = []
    media: list[dict] = []
    with zipfile.ZipFile(path) as package:
        for info in package.infolist():
            if info.filename.startswith("visio/media/"):
                media.append({"name": info.filename, "bytes": info.file_size})
            if info.filename.startswith("visio/pages/page") and info.filename.endswith(".xml"):
                root = ET.fromstring(package.read(info.filename))
                shapes = root.findall(".//v:Shape", VISIO_NS)
                shape_count += len(shapes)
                names.extend([s.get("NameU", "") for s in shapes if s.get("NameU")])
    return {"status": "ok", "shape_count": shape_count, "shape_names": names, "media": media}


def render_report_markdown(result: dict) -> str:
    gates = result["gates"]
    lines = [
        "# Visio Reconstruction Quality Report", "",
        f"- Status: `{result['status']}`",
        f"- Expected manifest elements: {result['expected_elements']}",
        f"- Rendered manifest elements: {result['rendered_elements']}",
        f"- Shape metadata coverage: {result['metadata_coverage']}",
        f"- Package shape count: {result['package'].get('shape_count')}",
        f"- Package media entries: {len(result['package'].get('media', []))}",
        f"- Text lift ratio: {result['editability'].get('text_lift_ratio')}",
        "", "## Quality Gates", "",
    ]
    for key, value in gates.items():
        lines.append(f"- {key}: `{value.get('status')}` {value.get('message', '')}".rstrip())
    lines.extend(["", "## Review Items", ""])
    reviews = result.get("reviews", [])
    lines.extend([f"- {item}" for item in reviews] or ["- None."])
    lines.extend(["", "## Notes", "", "- Raster media are allowed only for source-specific cropped assets; a full-canvas reference image is prohibited.", "- PPTX exports remain page renders produced from the saved Visio document."])
    return "\n".join(lines) + "\n"


def text_layout_risks(manifest: dict) -> dict:
    panels = {str(p.get("id")): p for p in manifest.get("panels", [])}
    boxes = []
    overflows = []
    panel_escape = []
    for element in manifest.get("elements", []):
        if element.get("type") != "text" or element.get("allow_overlap"):
            continue
        value = str(element.get("text") or "\n".join(element.get("lines", [])))
        size = float(element.get("font_size", 16) or 16)
        lines = value.splitlines() or [value]
        required_w = max((len(line) for line in lines), default=1) * size * 0.82 + 8
        required_h = max(1, len(lines)) * size * 1.35
        explicit_box = "w" in element and "h" in element
        if explicit_box:
            x, y, w, h = [float(element.get(k, 0)) for k in ("x", "y", "w", "h")]
            check_w, check_h = w, h
            local_panel = panels.get(str(element.get("panel_id"))) if element.get("coordinate_space") == "panel" else None
            if local_panel:
                check_w, check_h = float(local_panel["w"]) * w, float(local_panel["h"]) * h
            if check_w < required_w * 0.85 or check_h < required_h * 0.75:
                overflows.append(str(element.get("id")))
        else:
            x, y, w, h = float(element.get("x", 0)), float(element.get("y", 0)) - required_h * .78, required_w, required_h
            anchor = element.get("text_anchor", "middle")
            if anchor == "middle":
                x -= w / 2
            elif anchor == "end":
                x -= w
        if element.get("coordinate_space") == "panel" and str(element.get("panel_id")) in panels:
            panel = panels[str(element["panel_id"])]
            x, y = float(panel["x"]) + float(panel["w"])*x, float(panel["y"]) + float(panel["h"])*y
            if explicit_box:
                w, h = float(panel["w"])*w, float(panel["h"])*h
        box = {"id": str(element.get("id")), "x": x, "y": y, "w": w, "h": h, "panel": element.get("panel_id")}
        panel = panels.get(str(element.get("panel_id")))
        if panel and (x < float(panel["x"]) or y < float(panel["y"]) or x+w > float(panel["x"])+float(panel["w"]) or y+h > float(panel["y"])+float(panel["h"])):
            panel_escape.append(box["id"])
        boxes.append(box)
    overlaps = []
    for index, left in enumerate(boxes):
        for right in boxes[index+1:]:
            if left["panel"] != right["panel"]:
                continue
            if _bbox_overlap(left, right) >= 0.35:
                overlaps.append([left["id"], right["id"]])
    return {"overflow_ids": overflows[:80], "panel_escape_ids": panel_escape[:80], "overlap_pairs": overlaps[:80], "count": len(overflows) + len(panel_escape) + len(overlaps)}


def _bbox_overlap(a: dict, b: dict) -> float:
    x1, y1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x2, y2 = min(a["x"]+a["w"], b["x"]+b["w"]), min(a["y"]+a["h"], b["y"]+b["h"])
    area = max(0.0, x2-x1) * max(0.0, y2-y1)
    return area / max(1.0, min(a["w"]*a["h"], b["w"]*b["h"]))


def audit(manifest_path: Path, vsdx_path: Path, render_report_path: Path, out_dir: Path) -> dict:
    manifest = read_json(manifest_path)
    report = read_json(render_report_path)
    package = inspect_vsdx(vsdx_path)
    expected_ids = {str(e.get("id")) for e in manifest.get("elements", [])}
    entries = report.get("elements", [])
    rendered_ids = {str(e.get("id")) for e in entries if e.get("status") == "ok"}
    metadata_ok = {str(e.get("id")) for e in entries if e.get("metadata_ok") is True}
    missing = sorted(expected_ids - rendered_ids)
    missing_metadata = sorted(expected_ids - metadata_ok)
    ocr_path = manifest_path.parent / "ocr_results.json"
    ocr = read_json(ocr_path) if ocr_path.exists() else None
    editability = audit_editability(manifest, ocr)
    layout = text_layout_risks(manifest)
    formula_fallbacks = [e for e in entries if e.get("type") in {"math", "formula"} and e.get("render_kind") != "visio-group"]
    crop_issues = [a for a in manifest.get("assets", []) if a.get("crop_status") not in {None, "ok", "verified"}]
    full_canvas = []
    cw, ch = float(manifest["canvas"]["width"]), float(manifest["canvas"]["height"])
    for asset in manifest.get("assets", []):
        if float(asset.get("w", 0))/cw >= 0.9 and float(asset.get("h", 0))/ch >= 0.9:
            full_canvas.append(str(asset.get("id")))
    gates = {
        "vsdx_package": {"status": package["status"]},
        "element_coverage": {"status": "failed" if missing else "ok", "message": f"missing={missing}" if missing else ""},
        "shape_metadata": {"status": "failed" if missing_metadata else "ok", "message": f"missing={missing_metadata}" if missing_metadata else ""},
        "full_canvas_raster": {"status": "failed" if full_canvas else "ok", "message": f"assets={full_canvas}" if full_canvas else ""},
        "formula_groups": {"status": "review" if formula_fallbacks else "ok", "message": f"fallbacks={len(formula_fallbacks)}" if formula_fallbacks else ""},
        "crop_edges": {"status": "review" if crop_issues else "ok", "message": f"issues={len(crop_issues)}" if crop_issues else ""},
        "text_layout": {"status": "review" if layout["count"] else "ok", "message": f"risks={layout['count']}" if layout["count"] else ""},
        "manifest_review": {"status": "review" if (manifest.get("validation") or {}).get("reviews") else "ok", "message": "; ".join((manifest.get("validation") or {}).get("reviews", []))},
        "editability": {"status": editability["status"]},
    }
    failures = [k for k, v in gates.items() if v["status"] == "failed"]
    reviews = [k for k, v in gates.items() if v["status"] == "review"]
    result = {
        "status": "failed" if failures else "review" if reviews else "ok",
        "expected_elements": len(expected_ids), "rendered_elements": len(rendered_ids),
        "metadata_coverage": round(len(metadata_ok)/len(expected_ids), 4) if expected_ids else 1.0,
        "missing_elements": missing, "missing_metadata": missing_metadata,
        "package": package, "editability": editability, "text_layout": layout, "gates": gates,
        "reviews": reviews, "render_report": report,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "quality_report.json", result)
    (out_dir / "quality_report.md").write_text(render_report_markdown(result), encoding="utf-8")
    (out_dir / "editability_report.md").write_text(
        "# Editability Audit\n\n" + "\n".join([
            f"- Status: `{editability['status']}`",
            f"- Readable OCR candidates: {editability['readable_ocr_count']}",
            f"- Lifted OCR text: {editability['lifted_text_count']}",
            f"- Text lift ratio: {editability['text_lift_ratio']}",
            f"- Asset text risks: {editability['asset_text_risk_count']}",
            f"- Formula text leaks: {editability['formula_text_leak_count']}",
        ]) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--vsdx", type=Path, required=True)
    parser.add_argument("--render-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.manifest.resolve(), args.vsdx.resolve(), args.render_report.resolve(), args.out.resolve())
    print(json.dumps({"status": result["status"], "gates": result["gates"]}, ensure_ascii=True, indent=2))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
