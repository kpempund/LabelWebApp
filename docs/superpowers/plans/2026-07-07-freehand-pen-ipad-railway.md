# Freehand Pen + iPad/Railway Hosting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a freehand pen tool (usable with an Apple Pencil on iPad) alongside the existing click-polyline workflow, merge both into the exported mask, and deploy the app to Railway behind a shared password.

**Architecture:** Keep the existing `streamlit-image-coordinates` click canvas untouched; add a second `streamlit-drawable-canvas` (Fabric.js `freedraw`) shown when the "Freehand pen" tool is selected. Freehand strokes are stored per-image in original-image pixel coordinates with their own pen width, rasterized at pen thickness, and unioned with the polyline mask at export. A password gate and Railway start command make it deployable.

**Tech Stack:** Python, Streamlit, `streamlit-image-coordinates`, `streamlit-drawable-canvas`, Pillow, NumPy, pytest; Railway (Nixpacks + Procfile).

## Global Constraints

- Coordinates stored/exported in **original-image pixels** (canvas is downscaled to `MAX_DISPLAY_W = 900`).
- Freehand strokes render at **pen thickness = mask thickness** (NOT dilated by the mask-width slider). Click polylines keep using the mask-width slider.
- `core.py` stays **Streamlit-free** and unit-tested.
- Per-image mask PNG name is `{originalname}_mask.png` (unchanged, via `safe_mask_name`).
- `annotations.json` schema changes are **backward-compatible** (old files with no `freehand` key still load).
- Password gate **fails closed**: if `APP_PASSWORD` is unset, the app refuses to serve.
- Freehand stroke object shape (used everywhere): `{"points": [[x, y], ...], "width": <int px, original resolution>}`.

---

### Task 1: Freehand mask rasterization helpers in `core.py`

**Files:**
- Modify: `core.py` (add two functions after `polylines_to_mask`)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `polylines_to_mask` conventions (HxW uint8, values 0/255) from existing `core.py`.
- Produces:
  - `strokes_to_mask(strokes: List[dict], size: Tuple[int, int]) -> np.ndarray` where each stroke is `{"points": List[[float, float]], "width": int}`. Returns HxW uint8 (0/255).
  - `combine_masks(*masks: np.ndarray) -> np.ndarray` — pixelwise union of 0/255 masks; raises `ValueError` if called with no masks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_core.py`:

```python
from core import strokes_to_mask, combine_masks


def test_strokes_to_mask_empty_is_black():
    mask = strokes_to_mask([], size=(100, 60))
    assert mask.shape == (60, 100)
    assert mask.dtype == np.uint8
    assert mask.sum() == 0


def test_stroke_width_is_honored_not_mask_slider():
    stroke = {"points": [[10.0, 30.0], [90.0, 30.0]], "width": 7}
    mask = strokes_to_mask([stroke], size=(100, 60))
    assert set(np.unique(mask)) <= {0, 255}
    assert mask[30, 50] == 255
    thickness = int((mask[:, 50] == 255).sum())
    assert 6 <= thickness <= 9  # ~7px


def test_single_point_stroke_draws_a_dot():
    stroke = {"points": [[50.0, 30.0]], "width": 8}
    mask = strokes_to_mask([stroke], size=(100, 60))
    assert mask[30, 50] == 255
    assert mask[0, 0] == 0


def test_zero_width_stroke_is_skipped():
    mask = strokes_to_mask([{"points": [[10.0, 10.0], [20.0, 20.0]], "width": 0}], size=(50, 50))
    assert mask.sum() == 0


def test_combine_masks_unions_pixels():
    a = strokes_to_mask([{"points": [[10.0, 10.0], [40.0, 10.0]], "width": 3}], size=(50, 50))
    b = strokes_to_mask([{"points": [[10.0, 40.0], [40.0, 40.0]], "width": 3}], size=(50, 50))
    merged = combine_masks(a, b)
    assert merged[10, 25] == 255
    assert merged[40, 25] == 255
    assert set(np.unique(merged)) <= {0, 255}


def test_combine_masks_requires_at_least_one():
    with pytest.raises(ValueError):
        combine_masks()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_core.py -k "strokes or combine" -v`
Expected: FAIL with `ImportError: cannot import name 'strokes_to_mask'`.

- [ ] **Step 3: Implement the helpers**

In `core.py`, add after `polylines_to_mask` (before `mask_to_png_bytes`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_core.py -k "strokes or combine" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "feat: add strokes_to_mask and combine_masks for freehand strokes"
```

---

### Task 2: Persist freehand strokes in `annotations.json`

**Files:**
- Modify: `core.py` (`annotations_to_json_bytes`, `parse_annotations_json`)
- Test: `tests/test_core.py` (add new tests; update 3 existing tests to the new return arity)

**Interfaces:**
- Consumes: freehand stroke shape from Task 1.
- Produces:
  - `annotations_to_json_bytes(annotations, mask_width, freehand=None) -> bytes` — `freehand` is `Dict[str, List[stroke]]`; optional for backward compatibility.
  - `parse_annotations_json(raw, known_names) -> (annotations, freehand, mask_width, skipped)` — **now a 4-tuple**; `freehand` is `Dict[str, List[stroke]]`.

- [ ] **Step 1: Update existing tests to the new 4-tuple arity, and add freehand tests**

In `tests/test_core.py`, change the three call sites that unpack `parse_annotations_json`:

```python
# in test_json_roundtrip:
    parsed, fh, width, skipped = parse_annotations_json(raw, known_names={"a.jpg", "b.png"})
    assert width == 5
    assert skipped == []
    assert parsed == annotations
    assert fh == {}

# in test_unknown_image_names_are_skipped:
    parsed, _, _, skipped = parse_annotations_json(raw, known_names={"other.jpg"})

# in test_out_of_range_mask_width_falls_back_to_default:
    parsed, _, width, _ = parse_annotations_json(json.dumps(payload).encode(), known_names={"a.jpg"})
```

Then append new tests:

```python
def test_json_roundtrip_with_freehand():
    annotations = {"a.jpg": [[(1.0, 2.0), (3.0, 4.0)]]}
    freehand = {
        "a.jpg": [{"points": [(5.0, 6.0), (7.0, 8.0)], "width": 12}],
        "b.png": [{"points": [(0.0, 0.0)], "width": 20}],
    }
    raw = annotations_to_json_bytes(annotations, mask_width=4, freehand=freehand)
    parsed, fh, width, skipped = parse_annotations_json(raw, known_names={"a.jpg", "b.png"})
    assert width == 4
    assert skipped == []
    assert parsed["a.jpg"] == [[(1.0, 2.0), (3.0, 4.0)]]
    assert fh["a.jpg"] == [{"points": [(5.0, 6.0), (7.0, 8.0)], "width": 12}]
    assert fh["b.png"] == [{"points": [(0.0, 0.0)], "width": 20}]


def test_old_json_without_freehand_still_loads():
    raw = annotations_to_json_bytes({"a.jpg": [[(0.0, 0.0), (1.0, 1.0)]]}, 4)
    parsed, fh, width, skipped = parse_annotations_json(raw, known_names={"a.jpg"})
    assert parsed == {"a.jpg": [[(0.0, 0.0), (1.0, 1.0)]]}
    assert fh == {}


def test_freehand_only_image_is_exported_and_parsed():
    raw = annotations_to_json_bytes({}, 4, freehand={"c.jpg": [{"points": [(1.0, 1.0), (2.0, 2.0)], "width": 9}]})
    payload = json.loads(raw.decode("utf-8"))
    assert "c.jpg" in payload["images"]
    parsed, fh, _, _ = parse_annotations_json(raw, known_names={"c.jpg"})
    assert parsed["c.jpg"] == []
    assert fh["c.jpg"][0]["width"] == 9


def test_malformed_freehand_raises():
    bad = {"images": {"a.jpg": {"polylines": [], "freehand": [{"points": [[1.0, 2.0]]}]}}}
    with pytest.raises(ValueError):  # missing "width"
        parse_annotations_json(json.dumps(bad).encode(), known_names={"a.jpg"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_core.py -k "freehand or roundtrip or without_freehand" -v`
Expected: FAIL (roundtrip returns a 3-tuple / `annotations_to_json_bytes` rejects `freehand=` kwarg).

- [ ] **Step 3: Update `annotations_to_json_bytes`**

Replace the existing `annotations_to_json_bytes` in `core.py` with:

```python
def annotations_to_json_bytes(annotations, mask_width, freehand=None):
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
```

- [ ] **Step 4: Update `parse_annotations_json` to also parse freehand and return a 4-tuple**

Replace the body of the `for name, entry in payload["images"].items():` loop and the `return` in `parse_annotations_json`. The full updated function tail (from the `annotations` init onward) is:

```python
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
```

- [ ] **Step 5: Run the full core test suite**

Run: `python -m pytest tests/test_core.py -v`
Expected: PASS (all tests, including the 3 updated and 4 new).

- [ ] **Step 6: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "feat: persist freehand strokes in annotations.json (backward-compatible)"
```

---

### Task 3: Add dependencies and Streamlit config

**Files:**
- Modify: `requirements.txt`
- Create: `.streamlit/config.toml`

**Interfaces:**
- Produces: an installed `streamlit_drawable_canvas.st_canvas` importable by `app.py`.

- [ ] **Step 1: Update `requirements.txt`**

Replace the contents of `requirements.txt` with:

```
streamlit>=1.30,<1.40
streamlit-image-coordinates
streamlit-drawable-canvas==0.9.3
numpy
Pillow
pytest
```

- [ ] **Step 2: Create `.streamlit/config.toml`**

```toml
[server]
headless = true
maxUploadSize = 500

[browser]
gatherUsageStats = false
```

- [ ] **Step 3: Install and verify the canvas imports**

Run:
```bash
python -m pip install -r requirements.txt
python -c "from streamlit_drawable_canvas import st_canvas; print('canvas ok')"
```
Expected: prints `canvas ok` with no import error.

- [ ] **Step 4: Smoke-verify the component renders (manual)**

Run: `streamlit run app.py` (the current app still runs), open the local URL, and confirm the page loads with no red JS/component error in the browser. Stop the server with Ctrl+C.
Expected: page loads cleanly. If the drawable-canvas component later shows a blank/error box in Task 4, pin `streamlit==1.30.0` here and reinstall — that is the known-good floor for `streamlit-drawable-canvas==0.9.3`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .streamlit/config.toml
git commit -m "build: add streamlit-drawable-canvas dependency and streamlit config"
```

---

### Task 4: Wire the freehand tool, password gate, and merged export into `app.py`

**Files:**
- Modify: `app.py` (full replacement — the changes are interwoven across imports, sidebar, canvas, and export)

**Interfaces:**
- Consumes: `strokes_to_mask`, `combine_masks` (Task 1); `annotations_to_json_bytes(..., freehand=...)` and 4-tuple `parse_annotations_json` (Task 2).
- Produces: the running app (verified manually; `app.py` has no unit tests).

- [ ] **Step 1: Replace the entire contents of `app.py` with the following**

```python
import io
import os
import zipfile
from dataclasses import dataclass
from typing import List

import streamlit as st
from PIL import Image, ImageDraw, ImageOps
from streamlit_image_coordinates import streamlit_image_coordinates
from streamlit_drawable_canvas import st_canvas

from core import (
    DEFAULT_MASK_WIDTH,
    MAX_MASK_WIDTH,
    MIN_MASK_WIDTH,
    annotations_to_json_bytes,
    combine_masks,
    mask_to_png_bytes,
    parse_annotations_json,
    polylines_to_mask,
    safe_mask_name,
    strokes_to_mask,
)

MAX_DISPLAY_W = 900
MIN_PEN_SIZE = 1
MAX_PEN_SIZE = 60
DEFAULT_PEN_SIZE = 6

st.set_page_config(page_title="Wrinkle Polyline Annotator", layout="wide")


def _expected_password() -> str:
    pw = os.environ.get("APP_PASSWORD")
    if pw:
        return pw
    try:
        return st.secrets["APP_PASSWORD"]
    except Exception:
        return ""


def check_password() -> None:
    """Gate the whole app behind a shared password. Fails closed if unset."""
    if st.session_state.get("auth_ok"):
        return
    expected = _expected_password()
    if not expected:
        st.error("APP_PASSWORD is not configured on the server. Access is disabled.")
        st.stop()
    pw = st.text_input("🔒 Password", type="password")
    if not pw:
        st.stop()
    if pw == expected:
        st.session_state["auth_ok"] = True
        st.rerun()
    st.error("Incorrect password.")
    st.stop()


check_password()


@dataclass
class Item:
    name: str
    img: Image.Image  # RGB image


def pil_to_rgb(img: Image.Image) -> Image.Image:
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def render_display_frame(img, scale, committed, in_progress, committed_freehand):
    """Downscale the image and draw committed polylines + freehand (red) and
    the in-progress click polyline (green)."""
    disp_w = max(1, round(img.width * scale))
    disp_h = max(1, round(img.height * scale))
    frame = img.resize((disp_w, disp_h)) if scale < 1.0 else img.copy()
    draw = ImageDraw.Draw(frame)

    def draw_path(points, color, dot_r, width=3):
        pts = [(x * scale, y * scale) for x, y in points]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=width, joint="curve")
        for px, py in pts:
            draw.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r], fill=color)

    for line in committed:
        draw_path(line, (255, 0, 0), 3)
    for stroke in committed_freehand:
        w = max(1, round(stroke["width"] * scale))
        draw_path(stroke["points"], (255, 0, 0), max(2, w // 2), width=w)
    draw_path(in_progress, (0, 255, 0), 4)
    return frame


def extract_strokes(canvas_result, scale, pen_size):
    """Convert freedraw path objects from the canvas into original-resolution
    strokes: {"points": [[x, y], ...], "width": int}."""
    data = getattr(canvas_result, "json_data", None)
    if not data:
        return []
    strokes = []
    for obj in data.get("objects", []):
        if obj.get("type") != "path":
            continue
        pts = []
        for cmd in obj.get("path", []):
            if isinstance(cmd, list) and len(cmd) >= 3:
                pts.append([float(cmd[-2]) / scale, float(cmd[-1]) / scale])
        if not pts:
            continue
        sw = float(obj.get("strokeWidth", pen_size))
        strokes.append({"points": pts, "width": max(1, round(sw / scale))})
    return strokes


def build_mask(polylines, strokes, size, mask_width):
    return combine_masks(
        polylines_to_mask(polylines, size=size, width=mask_width),
        strokes_to_mask(strokes, size=size),
    )


ss = st.session_state
ss.setdefault("annotations", {})      # name -> list of polylines (original coords)
ss.setdefault("freehand", {})         # name -> list of {"points", "width"} (original coords)
ss.setdefault("current_points", [])   # in-progress click polyline (original coords)
ss.setdefault("idx", 0)
ss.setdefault("last_click", None)
ss.setdefault("import_applied", None)
ss.setdefault("canvas_nonce", 0)      # bump to reset the freehand canvas


def switch_image(new_idx: int):
    ss["idx"] = new_idx
    ss["current_points"] = []
    ss["last_click"] = None


st.title("🖊️ Wrinkle Annotator → Bitmask PNG Export")

with st.sidebar:
    st.header("Controls 🎛️")
    tool = st.radio("Tool", ["Click points", "Freehand pen"], key="tool")
    mask_width = st.slider(
        "Mask line width (click tool, px)",
        MIN_MASK_WIDTH,
        MAX_MASK_WIDTH,
        ss.get("mask_width_value", DEFAULT_MASK_WIDTH),
        key="mask_width_slider",
    )
    ss["mask_width_value"] = mask_width
    pen_size = DEFAULT_PEN_SIZE
    if tool == "Freehand pen":
        pen_size = st.slider(
            "Pen size (px)",
            MIN_PEN_SIZE,
            MAX_PEN_SIZE,
            ss.get("pen_size_value", DEFAULT_PEN_SIZE),
            key="pen_size_slider",
        )
        ss["pen_size_value"] = pen_size
        st.caption("Draw a wrinkle with the pen; the stroke is painted into the mask at this thickness.")
    else:
        st.caption("Click along a wrinkle's centerline to drop vertices, then press **Finish wrinkle**.")
    st.divider()
    st.subheader("Resume session 📂")
    imported = st.file_uploader("Import annotations.json", type=["json"], key="import_json")

uploaded = st.file_uploader(
    "Upload image(s) (jpg/png)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True,
)

items: List[Item] = []
if uploaded:
    for f in uploaded:
        try:
            img = Image.open(f)
            img = ImageOps.exif_transpose(img)
            items.append(Item(name=f.name, img=pil_to_rgb(img)))
        except Exception as e:
            st.warning(f"Failed to read {f.name}: {e}")

if not items:
    st.info("Upload some images to start ✨")
    st.stop()

# apply a pending JSON import once per uploaded file
if imported is not None:
    import_id = (imported.name, imported.size)
    if ss["import_applied"] != import_id:
        try:
            parsed, parsed_fh, width, skipped = parse_annotations_json(
                imported.getvalue(), known_names={it.name for it in items}
            )
            ss["annotations"].update(parsed)
            ss["freehand"].update(parsed_fh)
            ss["mask_width_value"] = width
            ss["import_applied"] = import_id
            msg = f"Imported annotations for {len(set(parsed) | set(parsed_fh))} image(s)."
            if skipped:
                msg += f" Skipped unknown image(s): {', '.join(skipped)}"
            st.sidebar.success(msg)
            st.rerun()
        except ValueError as e:
            ss["import_applied"] = import_id
            st.sidebar.error(f"Import failed: {e}")

if ss["idx"] >= len(items):
    switch_image(0)

colA, colB, colC = st.columns([1, 2, 1])
with colA:
    if st.button("⬅️ Prev", use_container_width=True):
        switch_image((ss["idx"] - 1) % len(items))
        st.rerun()
with colB:
    st.markdown(
        f"<h4 style='text-align:center;'>🖼️ {ss['idx'] + 1}/{len(items)} — "
        f"<code>{items[ss['idx']].name}</code></h4>",
        unsafe_allow_html=True,
    )
with colC:
    if st.button("Next ➡️", use_container_width=True):
        switch_image((ss["idx"] + 1) % len(items))
        st.rerun()

current = items[ss["idx"]]
orig_w, orig_h = current.img.size
scale = min(1.0, MAX_DISPLAY_W / orig_w)
committed = ss["annotations"].setdefault(current.name, [])
committed_fh = ss["freehand"].setdefault(current.name, [])

left, right = st.columns([2, 1])

with left:
    if tool == "Freehand pen":
        st.subheader("Draw the wrinkle with the pen ✏️")
        disp_w = max(1, round(orig_w * scale))
        disp_h = max(1, round(orig_h * scale))
        bg = render_display_frame(current.img, scale, committed, [], committed_fh)
        canvas_result = st_canvas(
            fill_color="rgba(0,0,0,0)",
            stroke_width=pen_size,
            stroke_color="#00FF00",
            background_image=bg,
            update_streamlit=True,
            height=disp_h,
            width=disp_w,
            drawing_mode="freedraw",
            key=f"canvas_{current.name}_{ss['canvas_nonce']}",
        )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("✅ Finish freehand strokes", use_container_width=True):
                new_strokes = extract_strokes(canvas_result, scale, pen_size)
                if new_strokes:
                    committed_fh.extend(new_strokes)
                    ss["canvas_nonce"] += 1
                    st.rerun()
                else:
                    st.warning("Draw at least one stroke first.")
        with b2:
            if st.button("🧹 Clear pending pen", use_container_width=True):
                ss["canvas_nonce"] += 1
                st.rerun()
    else:
        st.subheader("Click the wrinkle centerline ✍️")
        frame = render_display_frame(current.img, scale, committed, ss["current_points"], committed_fh)
        click = streamlit_image_coordinates(frame, key=f"click_{current.name}")
        if click is not None:
            click_id = (click["x"], click["y"])
            if click_id != ss["last_click"]:
                ss["last_click"] = click_id
                ox = min(max(click["x"] / scale, 0.0), orig_w - 1.0)
                oy = min(max(click["y"] / scale, 0.0), orig_h - 1.0)
                pt = (ox, oy)
                if not ss["current_points"] or ss["current_points"][-1] != pt:
                    ss["current_points"].append(pt)
                    st.rerun()

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("✅ Finish wrinkle", use_container_width=True):
                if len(ss["current_points"]) >= 2:
                    committed.append(ss["current_points"])
                    ss["current_points"] = []
                    st.rerun()
                else:
                    st.warning("Need at least 2 points to finish a wrinkle.")
        with b2:
            if st.button("↩️ Undo last point", use_container_width=True):
                if ss["current_points"]:
                    ss["current_points"].pop()
                    st.rerun()
        with b3:
            if st.button("🗑️ Discard in-progress", use_container_width=True):
                ss["current_points"] = []
                st.rerun()

with right:
    st.subheader(f"Click wrinkles ({len(committed)}) 📋")
    for i, line in enumerate(committed):
        c1, c2 = st.columns([3, 1])
        c1.write(f"Wrinkle {i + 1} — {len(line)} pts")
        if c2.button("🗑️", key=f"del_{current.name}_{i}"):
            committed.pop(i)
            st.rerun()

    st.subheader(f"Freehand strokes ({len(committed_fh)}) ✏️")
    for i, s in enumerate(committed_fh):
        c1, c2 = st.columns([3, 1])
        c1.write(f"Stroke {i + 1} — {len(s['points'])} pts, w={s['width']}")
        if c2.button("🗑️", key=f"delfh_{current.name}_{i}"):
            committed_fh.pop(i)
            st.rerun()

    st.subheader("Export 📦")
    if committed or committed_fh:
        mask = build_mask(committed, committed_fh, (orig_w, orig_h), mask_width)
        st.download_button(
            "⬇️ Download mask PNG (current image)",
            data=mask_to_png_bytes(mask),
            file_name=safe_mask_name(current.name),
            mime="image/png",
            use_container_width=True,
        )
        st.caption("Mask preview (white=255, black=0)")
        st.image(mask, clamp=True, use_container_width=True)
    else:
        st.info("Add at least one wrinkle or pen stroke to export 🙂")

st.divider()
st.subheader("Download ALL masks + annotations as ZIP 🗜️")

sizes = {it.name: it.img.size for it in items}
names_with_work = {n for n, v in ss["annotations"].items() if v} | {n for n, v in ss["freehand"].items() if v}
names_with_work &= set(sizes)  # only images currently uploaded
if not names_with_work:
    st.warning("No wrinkles or pen strokes yet.")
else:
    export_ann = {n: ss["annotations"].get(n, []) for n in names_with_work}
    export_fh = {n: ss["freehand"].get(n, []) for n in names_with_work if ss["freehand"].get(n)}
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in names_with_work:
            m = build_mask(ss["annotations"].get(name, []), ss["freehand"].get(name, []), sizes[name], mask_width)
            zf.writestr(safe_mask_name(name), mask_to_png_bytes(m))
        zf.writestr("annotations.json", annotations_to_json_bytes(export_ann, mask_width, export_fh))
    st.download_button(
        f"⬇️ Download ZIP ({len(names_with_work)} masks + annotations.json)",
        data=zip_buf.getvalue(),
        file_name="masks.zip",
        mime="application/zip",
        use_container_width=True,
    )
```

- [ ] **Step 2: Run locally with a password set**

Run:
```bash
APP_PASSWORD=test streamlit run app.py
```
(On Windows PowerShell: `$env:APP_PASSWORD='test'; streamlit run app.py`.)
Expected: the app first shows a password box; entering `test` reveals the app.

- [ ] **Step 3: Manual verification checklist (in the browser)**

Confirm each:
- Wrong password is rejected; empty `APP_PASSWORD` (restart without the env var) shows "Access is disabled".
- Upload an image. With **Freehand pen**, draw a stroke → click **Finish freehand strokes** → the stroke appears in the mask preview (right) at roughly the pen thickness, and the canvas clears.
- The freehand stroke shows in the "Freehand strokes" list and its 🗑️ removes it.
- Switch to **Click points**, add a polyline, **Finish wrinkle** → both the polyline and the earlier freehand stroke appear in the merged mask preview.
- **Download ZIP**, confirm it contains `{name}_mask.png` (with both) and `annotations.json` whose entry has both `polylines` and `freehand`.
- Re-upload the same image, **Import annotations.json** → both polylines and freehand strokes are restored.

If the canvas box renders blank or errors, apply the `streamlit==1.30.0` pin noted in Task 3 Step 4 and reinstall, then retry.

- [ ] **Step 4: Verify unit tests still pass**

Run: `python -m pytest -v`
Expected: PASS (core tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: freehand pen tool, password gate, and merged mask export"
```

---

### Task 5: Railway deployment config

**Files:**
- Modify: `Procfile` (currently empty)
- Create: `DEPLOY.md`

**Interfaces:**
- Produces: a start command Railway (Nixpacks) runs; no code depends on this task.

- [ ] **Step 1: Write the `Procfile`**

Set the contents of `Procfile` to exactly one line:

```
web: streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
```

- [ ] **Step 2: Create `DEPLOY.md`**

```markdown
# Deploying to Railway

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, pick this repo.
   Nixpacks auto-detects Python from `requirements.txt` and runs the `Procfile`.
3. In the service **Variables**, add:
   - `APP_PASSWORD` = your chosen shared password (required; the app refuses to
     serve without it).
4. Railway assigns `$PORT`; the `Procfile` already binds Streamlit to it on
   `0.0.0.0`. Under **Settings → Networking**, generate a public domain.
5. Open the domain on the iPad in Safari, enter the password, and draw with the
   Apple Pencil.

## Notes
- Free-session state is in-browser only; export the ZIP / `annotations.json`
  before closing the tab (no server-side autosave).
- To run locally the same way Railway does:
  `PORT=8501 APP_PASSWORD=test streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
```

- [ ] **Step 3: Verify the exact start command runs locally**

Run:
```bash
PORT=8501 APP_PASSWORD=test streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
```
(PowerShell: `$env:PORT='8501'; $env:APP_PASSWORD='test'; streamlit run app.py --server.port $env:PORT --server.address 0.0.0.0 --server.headless true`.)
Expected: server starts and is reachable at `http://localhost:8501` with the password prompt. Stop with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add Procfile DEPLOY.md
git commit -m "build: add Railway Procfile and deployment guide"
```

---

## Self-Review Notes

- **Spec coverage:** freehand tool alongside clicks (Task 4); pen thickness = mask (Tasks 1, 4); shared password fail-closed (Task 4); Railway hosting (Tasks 3, 5); freehand persisted in `annotations.json` backward-compatible (Task 2); export name unchanged (uses existing `safe_mask_name`); iPad `touch-action` — see below.
- **iPad scroll/draw polish:** `streamlit-drawable-canvas` renders its own `<canvas>` that captures pointer events; the wide layout keeps it in view. The spec's optional `touch-action` CSS is a device-tuning item — validate during Task 4 Step 3 on the actual iPad; only inject CSS if the Pencil fights page scroll. Not a required code task.
- **Type consistency:** stroke shape `{"points": [[x, y], ...], "width": int}` is identical across `strokes_to_mask`, `extract_strokes`, JSON (de)serialization, and the render loop. `parse_annotations_json` is a 4-tuple everywhere it's called (test + `app.py`).
- **No placeholders:** every code step is complete.
```
