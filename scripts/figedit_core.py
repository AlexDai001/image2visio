#!/usr/bin/env python3
"""Standalone FigEdit-inspired evidence, manifest, asset, math, and audit helpers."""

from __future__ import annotations

import html
import json
import math
import multiprocessing
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw


SUPPORTED_TYPES = {
    "rect", "text", "line", "path", "polyline", "polygon", "circle",
    "ellipse", "image", "math", "formula", "connector",
}
LAYER_ORDER = [
    "background", "assets", "panels", "sections", "icons",
    "connectors", "texts", "annotations",
]
FORMULA_CUES = [
    re.compile(r"\\(?:frac|sum|prod|int|sqrt|hat|bar|vec|mathrm|mathbf)\b"),
    re.compile(r"[A-Za-zΑ-Ωα-ω][_^]\{?"),
    re.compile(r"[∑∏∫√≤≥≠≈∞∈∉⊂⊆→↦]"),
    re.compile(r"\b(?:alpha|beta|gamma|delta|epsilon|lambda|mu|sigma|theta)\b", re.I),
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_source(manifest: dict[str, Any], manifest_path: Path) -> Path:
    source = Path(str(manifest.get("source_image", "")))
    if not source.is_absolute():
        source = (manifest_path.parent / source).resolve()
    if not source.exists():
        raise FileNotFoundError(f"source_image not found: {source}")
    return source


def _bbox_from_poly(poly: list[list[float]]) -> dict[str, float]:
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    for name in ("to_dict", "dict"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                return _plain(method())
            except Exception:
                pass
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return {k: _plain(v) for k, v in data.items() if not k.startswith("_")}
    return str(value)


def _normalize_ocr(raw: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    # PaddleOCR v2 shape.
    page = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], list) else raw
    if isinstance(page, list):
        for item in page:
            try:
                poly = [[float(x), float(y)] for x, y in item[0]]
                text, confidence = str(item[1][0]), float(item[1][1])
            except Exception:
                continue
            records.append({
                "id": f"ocr-text-{len(records):04d}", "text": text,
                "confidence": confidence, "polygon": poly, "bbox": _bbox_from_poly(poly),
                "engine": "paddleocr", "review_status": "ok" if confidence >= 0.82 else "low-confidence",
            })
    if records:
        return records
    # PaddleOCR v3 shape.
    for page_obj in raw if isinstance(raw, list) else [raw]:
        data = _plain(page_obj)
        if not isinstance(data, dict):
            continue
        texts = data.get("rec_texts") or data.get("texts") or []
        scores = data.get("rec_scores") or data.get("scores") or []
        polys = data.get("rec_polys") or data.get("dt_polys") or data.get("rec_boxes") or []
        for idx, text in enumerate(texts if isinstance(texts, list) else []):
            if not str(text).strip():
                continue
            score = float(scores[idx]) if idx < len(scores) else 0.0
            raw_poly = polys[idx] if idx < len(polys) else [0, 0, 1, 1]
            if len(raw_poly) == 4 and not isinstance(raw_poly[0], list):
                x1, y1, x2, y2 = [float(v) for v in raw_poly]
                poly = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            else:
                poly = [[float(p[0]), float(p[1])] for p in raw_poly]
            records.append({
                "id": f"ocr-text-{len(records):04d}", "text": str(text),
                "confidence": score, "polygon": poly, "bbox": _bbox_from_poly(poly),
                "engine": "paddleocr", "review_status": "ok" if score >= 0.82 else "low-confidence",
            })
    return records


def run_ocr(image_path: Path, lang: str = "ch", use_gpu: bool = False, profile: str = "v6_medium") -> dict[str, Any]:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception as exc:
        return {"engine": "paddleocr", "status": "disabled", "error": repr(exc), "items": []}
    base: dict[str, Any] = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": True,
    }
    if use_gpu:
        base["device"] = "gpu"
    profiles = {
        "v6_medium": ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"),
        "v6_small": ("PP-OCRv6_small_det", "PP-OCRv6_small_rec"),
        "v6_tiny": ("PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec"),
        "v5_mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
    }
    attempts: list[tuple[str, dict[str, Any]]] = []
    if profile in profiles:
        det, rec = profiles[profile]
        attempts.append((profile, {**base, "text_detection_model_name": det, "text_recognition_model_name": rec}))
    attempts.extend([("default", base), ("legacy", {"lang": lang})])
    errors: list[str] = []
    for selected, kwargs in attempts:
        try:
            engine = PaddleOCR(**kwargs)
            raw = engine.predict(str(image_path)) if hasattr(engine, "predict") else engine.ocr(str(image_path), cls=True)
            items = _normalize_ocr(raw)
            return {
                "engine": "paddleocr", "status": "ok", "lang": lang,
                "requested_profile": profile, "selected_profile": selected,
                "items": items, "count": len(items),
            }
        except Exception as exc:
            errors.append(f"{selected}: {exc!r}")
    return {"engine": "paddleocr", "status": "failed", "error": "; ".join(errors), "items": []}


def _ocr_worker(queue: Any, image_path: str, lang: str, use_gpu: bool, profile: str) -> None:
    try:
        queue.put(run_ocr(Path(image_path), lang=lang, use_gpu=use_gpu, profile=profile))
    except Exception as exc:
        queue.put({"engine": "paddleocr", "status": "failed", "error": repr(exc), "items": []})


def run_ocr_timed(image_path: Path, lang: str, use_gpu: bool, profile: str, timeout_seconds: int) -> dict[str, Any]:
    if timeout_seconds <= 0:
        return run_ocr(image_path, lang=lang, use_gpu=use_gpu, profile=profile)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_ocr_worker, args=(queue, str(image_path), lang, use_gpu, profile), daemon=True)
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(10)
        return {"engine": "paddleocr", "status": "timed-out", "error": f"OCR exceeded {timeout_seconds} seconds", "items": [], "requested_profile": profile}
    if not queue.empty():
        return queue.get()
    return {"engine": "paddleocr", "status": "failed", "error": f"OCR worker exited with code {process.exitcode}", "items": [], "requested_profile": profile}


def draw_ocr_overlay(image_path: Path, result: dict[str, Any], out_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    for item in result.get("items", []):
        poly = [tuple(p) for p in item.get("polygon", [])]
        if len(poly) >= 3:
            color = (0, 180, 255, 70) if item.get("review_status") == "ok" else (255, 160, 0, 100)
            draw.polygon(poly, outline=(0, 110, 220, 255), fill=color)
        box = item.get("bbox", {})
        draw.text((box.get("x", 0), box.get("y", 0)), str(item.get("text", ""))[:24], fill=(0, 70, 180))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def detect_primitives(image_path: Path, ocr_items: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
        import numpy as np
    except Exception as exc:
        return {"engine": "opencv", "status": "disabled", "error": repr(exc), "lines": [], "rectangles": [], "arrowheads": [], "dashed_groups": []}
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return {"engine": "opencv", "status": "failed", "error": f"cannot read {image_path}", "lines": [], "rectangles": [], "arrowheads": [], "dashed_groups": []}
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    for item in ocr_items:
        box = item.get("bbox", {})
        x, y = max(0, int(box.get("x", 0)) - 2), max(0, int(box.get("y", 0)) - 2)
        w, h = int(box.get("w", 0)) + 4, int(box.get("h", 0)) + 4
        cv2.rectangle(gray, (x, y), (x + w, y + h), 255, -1)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)
    min_len = max(24, int(min(width, height) * 0.018))
    raw_lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 45, minLineLength=min_len, maxLineGap=8)
    lines: list[dict[str, Any]] = []
    if raw_lines is not None:
        for item in raw_lines[:, 0, :]:
            x1, y1, x2, y2 = [int(v) for v in item]
            length = math.hypot(x2 - x1, y2 - y1)
            if length < min_len:
                continue
            orientation = "horizontal" if abs(y2-y1) <= max(3, abs(x2-x1)*0.08) else "vertical" if abs(x2-x1) <= max(3, abs(y2-y1)*0.08) else "diagonal"
            lines.append({"id": f"cv-line-{len(lines):04d}", "type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "length": round(length, 2), "orientation": orientation, "confidence": 0.72, "detector": "opencv-hough", "review_status": "ok"})
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    contours, _ = cv2.findContours(cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rectangles: list[dict[str, Any]] = []
    arrowheads: list[dict[str, Any]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.03 * peri, True)
        if area >= max(500, width * height * 0.00025) and w >= 18 and h >= 12:
            rectangles.append({"id": f"cv-rect-{len(rectangles):04d}", "type": "rect", "x": x, "y": y, "w": w, "h": h, "vertices": len(approx), "confidence": 0.62 if len(approx) == 4 else 0.48, "detector": "opencv-contour", "review_status": "ok" if len(approx) == 4 else "needs-check"})
        if 25 <= cv2.contourArea(contour) <= 3500 and len(approx) == 3:
            arrowheads.append({"id": f"cv-arrowhead-{len(arrowheads):04d}", "type": "arrowhead", "x": x, "y": y, "w": w, "h": h, "confidence": 0.58, "review_status": "needs-check"})
    for line in lines:
        for arrow in arrowheads:
            cx, cy = arrow["x"] + arrow["w"] / 2, arrow["y"] + arrow["h"] / 2
            d0 = math.hypot(line["x1"] - cx, line["y1"] - cy)
            d1 = math.hypot(line["x2"] - cx, line["y2"] - cy)
            if min(d0, d1) <= max(16, max(arrow["w"], arrow["h"]) * 1.8):
                line["arrow_start" if d0 < d1 else "arrow_end"] = True
    return {"engine": "opencv", "status": "ok", "canvas": {"width": width, "height": height}, "lines": lines, "rectangles": rectangles, "arrowheads": arrowheads, "dashed_groups": [], "counts": {"lines": len(lines), "rectangles": len(rectangles), "arrowheads": len(arrowheads), "dashed_groups": 0}}


def draw_structure_overlay(image_path: Path, result: dict[str, Any], out_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    for rect in result.get("rectangles", []):
        x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
        draw.rectangle([x, y, x+w, y+h], outline=(0, 200, 100, 190), width=2)
    for line in result.get("lines", []):
        color = (255, 40, 40, 210) if line.get("arrow_end") or line.get("arrow_start") else (40, 90, 255, 180)
        draw.line([line["x1"], line["y1"], line["x2"], line["y2"]], fill=color, width=2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def sample_styles(image_path: Path, primitives: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    image = Image.open(image_path).convert("RGB")
    arr = np.asarray(image)
    h, w = arr.shape[:2]
    corner = np.vstack([arr[:max(5, h//30), :max(5, w//30)], arr[-max(5, h//30):, -max(5, w//30):]]).reshape(-1, 3)
    to_hex = lambda rgb: "#" + "".join(f"{int(v):02x}" for v in rgb[:3])
    background = to_hex(np.median(corner, axis=0))
    quantized = (np.asarray(image.resize((max(1, w//8), max(1, h//8)))).reshape(-1, 3) // 16) * 16
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    dominant = [{"color": to_hex(colors[i]), "count": int(counts[i])} for i in np.argsort(counts)[-10:][::-1]]
    return {"background": background, "dominant_colors": dominant, "default_stroke": "#111111", "default_text": "#111111", "style_source": "pixel-sampling"}


def draw_style_overlay(image_path: Path, styles: dict[str, Any], out_path: Path) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    y = 12
    for item in styles.get("dominant_colors", [])[:8]:
        draw.rectangle([12, y, 62, y+24], fill=item["color"])
        draw.text((70, y+5), item["color"], fill=(0, 0, 0))
        y += 30
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def prepare_measurements(image_path: Path, out_dir: Path, lang: str = "ch", gpu: bool = False, ocr_profile: str = "v6_medium", ocr_timeout: int = 120) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    diagnostics = out_dir / "diagnostics"
    assets = out_dir / "assets"
    diagnostics.mkdir(exist_ok=True)
    assets.mkdir(exist_ok=True)
    source_copy = assets / f"source{image_path.suffix.lower() or '.png'}"
    shutil.copy2(image_path, source_copy)
    ocr = run_ocr_timed(source_copy, lang=lang, use_gpu=gpu, profile=ocr_profile, timeout_seconds=ocr_timeout)
    write_json(out_dir / "ocr_results.json", ocr)
    draw_ocr_overlay(source_copy, ocr, diagnostics / "ocr_overlay.png")
    primitives = detect_primitives(source_copy, ocr.get("items", []))
    write_json(out_dir / "detected_primitives.json", primitives)
    draw_structure_overlay(source_copy, primitives, diagnostics / "structure_overlay.png")
    styles = sample_styles(source_copy, primitives)
    write_json(out_dir / "style_tokens.json", styles)
    draw_style_overlay(source_copy, styles, diagnostics / "style_overlay.png")
    with Image.open(source_copy) as image:
        width, height = image.size
    draft = {
        "project": out_dir.name,
        "source_image": str(image_path.resolve()),
        "canvas": {"width": width, "height": height, "background": styles["background"]},
        "classification": {"layout_topology": "model-to-classify", "complexity": "model-to-classify", "style_type": "image-derived", "reconstruction_mode": "model-led-hybrid", "reconstruction_intent": "OCR/CV are evidence only; model authors the semantic manifest."},
        "panels": [], "assets": [], "elements": [], "style_tokens": styles,
        "diagnostics": {"measurement_workspace": str(out_dir.resolve()), "ocr_status": ocr.get("status"), "ocr_profile": ocr.get("selected_profile"), "opencv_status": primitives.get("status")},
        "visio": {"page_width_in": 16.0, "page_mode": "replace", "default_font": "Arial"},
    }
    write_json(out_dir / "draft_manifest.json", draft)
    report = ["# Measurement Report", "", f"- Source: {image_path}", f"- Canvas: {width} x {height}", f"- OCR: {ocr.get('status')} ({len(ocr.get('items', []))} candidates)", f"- OpenCV: {primitives.get('status')} ({primitives.get('counts', {})})", "", "Measurement artifacts are evidence only; do not copy detector candidates directly into the final manifest."]
    (out_dir / "measurement_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {"out_dir": str(out_dir), "ocr": ocr.get("status"), "opencv": primitives.get("status")}


def formula_text_leaks(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    leaks = []
    for element in manifest.get("elements", []):
        if element.get("type") != "text" or element.get("formula_policy") == "not-formula":
            continue
        value = str(element.get("text") or " ".join(element.get("lines", [])))
        reasons = [pattern.pattern for pattern in FORMULA_CUES if pattern.search(value)]
        if reasons:
            leaks.append({"id": element.get("id"), "text": value, "reasons": reasons})
    return leaks


def validate_manifest(manifest: dict[str, Any], manifest_path: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    reviews: list[str] = []
    for key in ["project", "source_image", "canvas", "classification", "assets", "elements"]:
        if key not in manifest:
            errors.append(f"missing required key: {key}")
    canvas = manifest.get("canvas", {})
    width, height = float(canvas.get("width", 0) or 0), float(canvas.get("height", 0) or 0)
    if width <= 0 or height <= 0:
        errors.append("canvas width and height must be positive")
    ids: set[str] = set()
    panel_ids = {str(p.get("id")) for p in manifest.get("panels", []) if p.get("id")}
    element_ids: set[str] = set()
    for group_name in ["panels", "assets", "elements"]:
        for item in manifest.get(group_name, []):
            item_id = str(item.get("id") or "")
            if not item_id:
                errors.append(f"{group_name} item missing id")
            elif item_id in ids:
                errors.append(f"duplicate id: {item_id}")
            else:
                ids.add(item_id)
                if group_name == "elements":
                    element_ids.add(item_id)
    source = None
    if manifest_path and manifest.get("source_image"):
        try:
            source = resolve_source(manifest, manifest_path)
        except Exception as exc:
            errors.append(str(exc))
    for panel in manifest.get("panels", []):
        for key in ("x", "y", "w", "h"):
            if key not in panel:
                errors.append(f"panel {panel.get('id')} missing {key}")
        if float(panel.get("w", 0)) <= 0 or float(panel.get("h", 0)) <= 0:
            errors.append(f"panel {panel.get('id')} has non-positive size")
    for asset in manifest.get("assets", []):
        for key in ("file", "x", "y", "w", "h"):
            if key not in asset:
                errors.append(f"asset {asset.get('id')} missing {key}")
        w, h = float(asset.get("w", 0) or 0), float(asset.get("h", 0) or 0)
        if w <= 0 or h <= 0:
            errors.append(f"asset {asset.get('id')} has non-positive size")
        if width and height and w / width >= 0.9 and h / height >= 0.9:
            errors.append(f"asset {asset.get('id')} is a prohibited full-canvas raster")
        elif width and height and (w * h) / (width * height) > 0.4:
            reviews.append(f"asset {asset.get('id')} covers more than 40% of the canvas")
        region = asset.get("source_region") or asset
        if float(region.get("w", 0) or 0) <= 0 or float(region.get("h", 0) or 0) <= 0:
            errors.append(f"asset {asset.get('id')} has invalid source_region")
    for element in manifest.get("elements", []):
        eid, typ = element.get("id"), str(element.get("type", "")).lower()
        if typ not in SUPPORTED_TYPES:
            errors.append(f"element {eid} has unsupported type: {typ}")
        if element.get("coordinate_space", "canvas") == "panel":
            panel_id = str(element.get("panel_id") or "")
            if panel_id not in panel_ids:
                errors.append(f"element {eid} uses panel coordinates but panel_id is invalid")
            keys = ("x", "y", "w", "h") if typ not in {"line", "connector", "path", "polyline", "polygon"} else ()
            if keys and any(float(element.get(k, 0)) < 0 or float(element.get(k, 0)) > 1 for k in keys):
                errors.append(f"element {eid} panel-local coordinates must be within 0..1")
            if keys and float(element.get("x", 0)) + float(element.get("w", 0)) > 1.000001:
                errors.append(f"element {eid} exceeds panel width")
            if keys and float(element.get("y", 0)) + float(element.get("h", 0)) > 1.000001:
                errors.append(f"element {eid} exceeds panel height")
        if typ == "connector":
            if str(element.get("from_id") or "") not in element_ids or str(element.get("to_id") or "") not in element_ids:
                errors.append(f"connector {eid} has invalid from_id/to_id")
        if typ in {"math", "formula"} and not str(element.get("latex") or "").strip():
            errors.append(f"math element {eid} missing latex")
        if typ == "image" and not element.get("asset_id") and not element.get("href"):
            errors.append(f"image element {eid} missing asset_id or href")
        if element.get("panel_id") and str(element.get("panel_id")) not in panel_ids:
            errors.append(f"element {eid} references missing panel_id {element.get('panel_id')}")
    leaks = formula_text_leaks(manifest)
    if leaks:
        reviews.append(f"{len(leaks)} text element(s) contain formula-like content")
    return {"status": "failed" if errors else "review" if reviews else "ok", "errors": errors, "reviews": reviews, "formula_text_leaks": leaks, "source": str(source) if source else None}


def normalize_latex(value: str) -> str:
    text = value.strip()
    for left, right in [("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")]:
        if text.startswith(left) and text.endswith(right):
            return text[len(left):-len(right)].strip()
    return text


def render_formula_svg(latex: str, out_path: Path, font_size: float = 24, fill: str = "#111111") -> dict[str, Any]:
    from matplotlib.font_manager import FontProperties
    from matplotlib.path import Path as MplPath
    from matplotlib.textpath import TextPath
    body = normalize_latex(latex) or r"\ "
    path = TextPath((0, 0), f"${body}$", size=font_size, prop=FontProperties(family="DejaVu Serif"), usetex=False)
    bbox = path.get_extents()
    min_x, min_y, max_x, max_y = bbox.x0, bbox.y0, bbox.x1, bbox.y1
    width, height = max(max_x-min_x, 1.0), max(max_y-min_y, 1.0)
    commands: list[str] = []
    for vertices, code in path.iter_segments(curves=True, simplify=False):
        vals = list(vertices)
        pt = lambda i: (vals[i]-min_x, max_y-vals[i+1])
        if code == MplPath.MOVETO:
            x, y = pt(0); commands.append(f"M {x:.6g} {y:.6g}")
        elif code == MplPath.LINETO:
            x, y = pt(0); commands.append(f"L {x:.6g} {y:.6g}")
        elif code == MplPath.CURVE3:
            x1, y1 = pt(0); x2, y2 = pt(2); commands.append(f"Q {x1:.6g} {y1:.6g} {x2:.6g} {y2:.6g}")
        elif code == MplPath.CURVE4:
            x1, y1 = pt(0); x2, y2 = pt(2); x3, y3 = pt(4); commands.append(f"C {x1:.6g} {y1:.6g} {x2:.6g} {y2:.6g} {x3:.6g} {y3:.6g}")
        elif code == MplPath.CLOSEPOLY:
            commands.append("Z")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.6g}" height="{height:.6g}" viewBox="0 0 {width:.6g} {height:.6g}"><path d="{html.escape(" ".join(commands), quote=True)}" fill="{html.escape(fill, quote=True)}"/></svg>\n'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    return {"width": width, "height": height, "latex": body}


def compile_svg_path(path_data: str) -> list[dict[str, Any]]:
    """Compile common SVG path commands to line/cubic segments for Visio."""
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", path_data or "")
    out: list[dict[str, Any]] = []
    i = 0
    command = ""
    x = y = start_x = start_y = 0.0
    last_cubic: tuple[float, float] | None = None
    last_quad: tuple[float, float] | None = None

    def number() -> float:
        nonlocal i
        if i >= len(tokens) or re.fullmatch(r"[A-Za-z]", tokens[i]):
            raise ValueError(f"SVG path command {command} is missing a coordinate")
        value = float(tokens[i]); i += 1
        return value

    while i < len(tokens):
        if re.fullmatch(r"[A-Za-z]", tokens[i]):
            command = tokens[i]; i += 1
        if not command:
            raise ValueError("SVG path must start with a command")
        relative = command.islower()
        op = command.upper()
        if op == "Z":
            if (x, y) != (start_x, start_y):
                out.append({"type": "line", "points": [x, y, start_x, start_y]})
            x, y = start_x, start_y
            last_cubic = last_quad = None
            command = ""
            continue
        if op == "M":
            nx, ny = number(), number()
            if relative:
                nx, ny = x + nx, y + ny
            x, y = nx, ny
            start_x, start_y = x, y
            command = "l" if relative else "L"
            last_cubic = last_quad = None
        elif op == "L":
            nx, ny = number(), number()
            if relative:
                nx, ny = x + nx, y + ny
            out.append({"type": "line", "points": [x, y, nx, ny]})
            x, y = nx, ny; last_cubic = last_quad = None
        elif op == "H":
            nx = number() + (x if relative else 0)
            out.append({"type": "line", "points": [x, y, nx, y]})
            x = nx; last_cubic = last_quad = None
        elif op == "V":
            ny = number() + (y if relative else 0)
            out.append({"type": "line", "points": [x, y, x, ny]})
            y = ny; last_cubic = last_quad = None
        elif op == "C":
            c1x, c1y, c2x, c2y, nx, ny = [number() for _ in range(6)]
            if relative:
                c1x, c1y, c2x, c2y, nx, ny = x+c1x, y+c1y, x+c2x, y+c2y, x+nx, y+ny
            out.append({"type": "bezier", "points": [x, y, c1x, c1y, c2x, c2y, nx, ny]})
            x, y = nx, ny; last_cubic = (c2x, c2y); last_quad = None
        elif op == "S":
            c2x, c2y, nx, ny = [number() for _ in range(4)]
            c1x, c1y = (2*x-last_cubic[0], 2*y-last_cubic[1]) if last_cubic else (x, y)
            if relative:
                c2x, c2y, nx, ny = x+c2x, y+c2y, x+nx, y+ny
            out.append({"type": "bezier", "points": [x, y, c1x, c1y, c2x, c2y, nx, ny]})
            x, y = nx, ny; last_cubic = (c2x, c2y); last_quad = None
        elif op == "Q":
            qx, qy, nx, ny = [number() for _ in range(4)]
            if relative:
                qx, qy, nx, ny = x+qx, y+qy, x+nx, y+ny
            c1x, c1y = x + 2*(qx-x)/3, y + 2*(qy-y)/3
            c2x, c2y = nx + 2*(qx-nx)/3, ny + 2*(qy-ny)/3
            out.append({"type": "bezier", "points": [x, y, c1x, c1y, c2x, c2y, nx, ny]})
            x, y = nx, ny; last_quad = (qx, qy); last_cubic = None
        elif op == "T":
            nx, ny = number(), number()
            if relative:
                nx, ny = x+nx, y+ny
            qx, qy = (2*x-last_quad[0], 2*y-last_quad[1]) if last_quad else (x, y)
            c1x, c1y = x + 2*(qx-x)/3, y + 2*(qy-y)/3
            c2x, c2y = nx + 2*(qx-nx)/3, ny + 2*(qy-ny)/3
            out.append({"type": "bezier", "points": [x, y, c1x, c1y, c2x, c2y, nx, ny]})
            x, y = nx, ny; last_quad = (qx, qy); last_cubic = None
        else:
            raise ValueError(f"unsupported SVG path command: {command}")
    return out


def _edge_check(crop: Image.Image) -> dict[str, Any]:
    import numpy as np
    arr = np.asarray(crop.convert("L"))
    if arr.size == 0:
        return {"status": "empty", "edge_density": 1.0}
    border = np.concatenate([arr[:2, :].reshape(-1), arr[-2:, :].reshape(-1), arr[:, :2].reshape(-1), arr[:, -2:].reshape(-1)])
    center = arr[arr.shape[0]//4:max(arr.shape[0]//4+1, arr.shape[0]*3//4), arr.shape[1]//4:max(arr.shape[1]//4+1, arr.shape[1]*3//4)]
    bg = float(np.median(center)) if center.size else float(np.median(arr))
    density = float(np.mean(np.abs(border.astype(float)-bg) > 30))
    return {"status": "ok" if density < 0.24 else "needs-padding", "edge_density": round(density, 4)}


def prepare_render_manifest(manifest_path: Path, out_dir: Path) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    validation = validate_manifest(manifest, manifest_path)
    if validation["errors"]:
        raise ValueError("; ".join(validation["errors"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    formulas_dir = out_dir / "formulas"
    diagnostics_dir = out_dir / "diagnostics"
    assets_dir.mkdir(exist_ok=True)
    formulas_dir.mkdir(exist_ok=True)
    diagnostics_dir.mkdir(exist_ok=True)
    measurement_workspace = Path(str((manifest.get("diagnostics") or {}).get("measurement_workspace") or ""))
    if measurement_workspace.exists():
        for name in ["ocr_results.json", "detected_primitives.json", "style_tokens.json", "measurement_report.md"]:
            source_artifact = measurement_workspace / name
            if source_artifact.exists():
                shutil.copy2(source_artifact, out_dir / name)
        source_diagnostics = measurement_workspace / "diagnostics"
        if source_diagnostics.exists():
            for diagnostic in source_diagnostics.glob("*.png"):
                shutil.copy2(diagnostic, diagnostics_dir / diagnostic.name)
    source = resolve_source(manifest, manifest_path)
    source_copy = assets_dir / f"source{source.suffix.lower() or '.png'}"
    shutil.copy2(source, source_copy)
    image = Image.open(source_copy).convert("RGBA")
    width, height = image.size
    thumbs = []
    for asset in manifest.get("assets", []):
        region = asset.get("source_region") or asset
        pad = int(round(float(asset.get("pad", 0) or 0)))
        x, y, w, h = [int(round(float(region.get(k, 0)))) for k in ("x", "y", "w", "h")]
        raw_box = (x-pad, y-pad, x+w+pad, y+h+pad)
        box = (max(0, raw_box[0]), max(0, raw_box[1]), min(width, raw_box[2]), min(height, raw_box[3]))
        crop = image.crop(box)
        check = _edge_check(crop)
        if asset.get("edge_policy") == "allow-border-touch":
            check = {"status": "ok", "edge_density": check.get("edge_density"), "note": "border-touch allowed"}
        asset["edge_check"] = check
        asset["crop_status"] = "verified" if check["status"] == "ok" else "needs-padding"
        rel = Path(str(asset["file"]))
        dest = out_dir / rel if rel.parts and rel.parts[0].lower() == "assets" else assets_dir / rel.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest)
        asset["resolved_file"] = str(dest.resolve())
        thumbs.append((str(asset.get("id")), dest, box, check))
    contact = Image.new("RGB", (800, 120), "white") if not thumbs else Image.new("RGB", (4*260, math.ceil(len(thumbs)/4)*250), "white")
    draw = ImageDraw.Draw(contact)
    for idx, (asset_id, path, box, check) in enumerate(thumbs):
        x0, y0 = (idx % 4)*260+20, (idx//4)*250+20
        thumb = Image.open(path).convert("RGB"); thumb.thumbnail((220, 160)); contact.paste(thumb, (x0, y0))
        draw.text((x0, y0+165), asset_id[:32], fill=(0,0,0)); draw.text((x0, y0+185), f"box={box}", fill=(80,80,80)); draw.text((x0, y0+205), f"edge={check.get('status')}", fill=(0,100,0) if check.get("status") == "ok" else (160,0,0))
    contact.save(out_dir / "contact_sheet.png")
    asset_by_id = {str(a["id"]): a for a in manifest.get("assets", [])}
    for element in manifest.get("elements", []):
        if element.get("type") == "image" and element.get("asset_id") in asset_by_id:
            element["resolved_file"] = asset_by_id[str(element["asset_id"])]["resolved_file"]
        if element.get("type") in {"math", "formula"}:
            dest = formulas_dir / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', str(element['id']))}.svg"
            render_formula_svg(str(element["latex"]), dest, float(element.get("font_size", 24)), str(element.get("fill", "#111111")))
            element["formula_file"] = str(dest.resolve())
        if element.get("type") == "path":
            element["visio_segments"] = compile_svg_path(str(element.get("d") or ""))
    manifest.setdefault("provenance", {})["original_source_image"] = str(source)
    manifest["source_image"] = str(source_copy.resolve())
    manifest["validation"] = validation
    manifest["render_root"] = str(out_dir.resolve())
    processed = out_dir / "manifest.json"
    write_json(processed, manifest)
    overlay = image.convert("RGB")
    overlay_draw = ImageDraw.Draw(overlay, "RGBA")
    for asset in manifest.get("assets", []):
        region = asset.get("source_region") or asset
        x, y, w, h = [float(region.get(k, 0)) for k in ("x", "y", "w", "h")]
        overlay_draw.rectangle([x, y, x+w, y+h], outline=(0,170,80,220), width=3)
        overlay_draw.text((x, y), str(asset.get("id")), fill=(0,120,60))
    overlay.save(diagnostics_dir / "crop_overlay.png")
    return {"manifest": str(processed), "validation": validation, "asset_count": len(manifest.get("assets", [])), "formula_count": len([e for e in manifest.get("elements", []) if e.get("type") in {"math", "formula"}])}


def _norm_text(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _overlap_ratio(a: dict[str, float], b: dict[str, float]) -> float:
    x1, y1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x2, y2 = min(a["x"]+a["w"], b["x"]+b["w"]), min(a["y"]+a["h"], b["y"]+b["h"])
    area = max(0, x2-x1)*max(0, y2-y1)
    return area / max(1.0, min(a["w"]*a["h"], b["w"]*b["h"]))


def audit_editability(manifest: dict[str, Any], ocr: dict[str, Any] | None = None) -> dict[str, Any]:
    ocr_items = (ocr or {}).get("items", [])
    readable = [i for i in ocr_items if len(_norm_text(str(i.get("text", "")))) >= 2 and float(i.get("confidence", 0)) >= 0.55 and float((i.get("bbox") or {}).get("h", 0)) >= 8]
    texts = [e for e in manifest.get("elements", []) if e.get("type") in {"text", "math", "formula"}]
    lifted, missed = [], []
    for item in readable:
        norm = _norm_text(str(item.get("text", "")))
        match = next((e for e in texts if norm and (norm in _norm_text(str(e.get("text") or e.get("latex") or " ".join(e.get("lines", [])))) or _norm_text(str(e.get("text") or e.get("latex") or "")) in norm)), None)
        (lifted if match else missed).append({"ocr": item.get("id"), "text": item.get("text"), "element": match.get("id") if match else None, "bbox": item.get("bbox")})
    risks = []
    for item in readable:
        bbox = item.get("bbox") or {"x":0,"y":0,"w":1,"h":1}
        for asset in manifest.get("assets", []):
            if asset.get("text_policy") in {"preserve-raster", "allow-embedded-text"}:
                continue
            region = asset.get("source_region") or asset
            if _overlap_ratio({k: float(bbox.get(k, 0)) for k in ("x","y","w","h")}, {k: float(region.get(k, 0)) for k in ("x","y","w","h")}) >= 0.72 and not any(x["ocr"] == item.get("id") for x in lifted):
                risks.append({"asset": asset.get("id"), "text": item.get("text"), "bbox": bbox})
                break
    ratio = len(lifted)/len(readable) if readable else None
    leaks = formula_text_leaks(manifest)
    status = "review" if (ratio is not None and ratio < 0.45) or len(risks) > 12 or leaks else "ok"
    return {"status": status, "readable_ocr_count": len(readable), "lifted_text_count": len(lifted), "text_lift_ratio": round(ratio,4) if ratio is not None else None, "asset_text_risk_count": len(risks), "formula_text_leak_count": len(leaks), "missed_text_samples": missed[:40], "asset_text_risk_samples": risks[:40], "formula_text_leak_samples": leaks[:40]}
