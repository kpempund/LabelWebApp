"""Pure annotation logic: mask rasterization and JSON (de)serialization.

Kept free of Streamlit imports so it is unit-testable.
"""

import io
import json
from typing import Dict, List, Set, Tuple

import numpy as np
from PIL import Image, ImageDraw

Point = Tuple[float, float]
Polyline = List[Point]

DEFAULT_MASK_WIDTH = 4
MIN_MASK_WIDTH = 1
MAX_MASK_WIDTH = 15


def polylines_to_mask(polylines: List[Polyline], size: Tuple[int, int], width: int) -> np.ndarray:
    """Rasterize centerline polylines into a binary mask.

    polylines: list of polylines; each polyline is a list of (x, y) points in
        original-image pixel coordinates.
    size: (width, height) of the output mask.
    width: stroke thickness in pixels.

    Returns an HxW uint8 array with values 0 or 255.
    """
    w, h = size
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    radius = width / 2.0
    for line in polylines:
        pts = [(float(x), float(y)) for x, y in line]
        if len(pts) < 2:
            continue
        # joint="curve" rounds interior joints but not the two endpoints,
        # so add cap discs there.
        draw.line(pts, fill=255, width=width, joint="curve")
        for px, py in (pts[0], pts[-1]):
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=255)
    return np.array(img, dtype=np.uint8)


def strokes_to_mask(strokes, size):
    """Rasterize freehand strokes, each at its OWN pen width.

    strokes: list of {"points": [(x, y), ...], "width": int} in original-image
        pixel coordinates. Unlike polylines_to_mask, width is per-stroke and is
        not taken from the global mask-width slider.
    size: (width, height) of the output mask.

    Returns an HxW uint8 array with values 0 or 255.
    """
    w, h = size
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    for stroke in strokes:
        width = int(stroke.get("width", 0))
        if width < 1:
            continue
        pts = [(float(x), float(y)) for x, y in stroke["points"]]
        if not pts:
            continue
        radius = width / 2.0
        if len(pts) == 1:
            px, py = pts[0]
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=255)
            continue
        draw.line(pts, fill=255, width=width, joint="curve")
        for px, py in (pts[0], pts[-1]):
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=255)
    return np.array(img, dtype=np.uint8)


def combine_masks(*masks):
    """Pixelwise union of one or more 0/255 uint8 masks of identical shape."""
    if not masks:
        raise ValueError("combine_masks needs at least one mask")
    out = masks[0].copy()
    for m in masks[1:]:
        out = np.maximum(out, m)
    return out


def mask_to_png_bytes(mask: np.ndarray) -> bytes:
    im = Image.fromarray(mask, mode="L")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def safe_mask_name(original_name: str) -> str:
    base = original_name.rsplit(".", 1)[0]
    return f"{base}_mask.png"


def annotations_to_json_bytes(annotations: Dict[str, List[Polyline]], mask_width: int, freehand=None) -> bytes:
    freehand = freehand or {}
    images = {}
    for name in set(annotations) | set(freehand):
        lines = annotations.get(name, [])
        strokes = freehand.get(name, [])
        if not lines and not strokes:
            continue
        entry = {"polylines": [[[float(x), float(y)] for x, y in line] for line in lines]}
        if strokes:
            entry["freehand"] = [
                {
                    "points": [[float(x), float(y)] for x, y in s["points"]],
                    "width": int(s["width"]),
                }
                for s in strokes
            ]
        images[name] = entry
    payload = {"mask_width": int(mask_width), "images": images}
    return json.dumps(payload, indent=2).encode("utf-8")


def parse_annotations_json(raw: bytes, known_names: Set[str]):
    """Parse an exported annotations.json.

    Returns (annotations, freehand, mask_width, skipped_names).
    Raises ValueError on malformed input; nothing is partially accepted.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Not valid JSON: {e}") from e
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), dict):
        raise ValueError("Expected a top-level object with an 'images' mapping.")

    mask_width = payload.get("mask_width", DEFAULT_MASK_WIDTH)
    if not isinstance(mask_width, int) or not (MIN_MASK_WIDTH <= mask_width <= MAX_MASK_WIDTH):
        mask_width = DEFAULT_MASK_WIDTH

    annotations: Dict[str, List[Polyline]] = {}
    freehand: Dict[str, list] = {}
    skipped: List[str] = []
    for name, entry in payload["images"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("polylines"), list):
            raise ValueError(f"Malformed entry for image '{name}'.")
        polylines: List[Polyline] = []
        for line in entry["polylines"]:
            if not isinstance(line, list) or len(line) < 2:
                raise ValueError(f"Polyline for '{name}' must be a list of >= 2 points.")
            pts: Polyline = []
            for pt in line:
                if (
                    not isinstance(pt, list)
                    or len(pt) != 2
                    or not all(isinstance(v, (int, float)) for v in pt)
                ):
                    raise ValueError(f"Bad point {pt!r} in polylines for '{name}'.")
                pts.append((float(pt[0]), float(pt[1])))
            polylines.append(pts)

        raw_fh = entry.get("freehand", [])
        if not isinstance(raw_fh, list):
            raise ValueError(f"'freehand' for '{name}' must be a list.")
        strokes: list = []
        for s in raw_fh:
            if (
                not isinstance(s, dict)
                or not isinstance(s.get("points"), list)
                or not isinstance(s.get("width"), (int, float))
                or isinstance(s.get("width"), bool)
            ):
                raise ValueError(f"Malformed freehand stroke for '{name}'.")
            spts: Polyline = []
            for pt in s["points"]:
                if (
                    not isinstance(pt, list)
                    or len(pt) != 2
                    or not all(isinstance(v, (int, float)) for v in pt)
                ):
                    raise ValueError(f"Bad freehand point {pt!r} for '{name}'.")
                spts.append((float(pt[0]), float(pt[1])))
            if not spts:
                raise ValueError(f"Freehand stroke for '{name}' needs >= 1 point.")
            strokes.append({"points": spts, "width": int(s["width"])})

        if name not in known_names:
            skipped.append(name)
            continue
        annotations[name] = polylines
        if strokes:
            freehand[name] = strokes
    return annotations, freehand, mask_width, skipped
