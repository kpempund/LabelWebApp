# Polyline Wrinkle Annotator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the Streamlit labeling app so wrinkles are annotated as click-vertex centerline polylines, dilated to a fixed pixel width into 0/255 training masks at original image resolution, with JSON export/import.

**Architecture:** Pure logic (mask rasterization, JSON serialization/parsing) lives in a new `core.py` so it is unit-testable without Streamlit. `app.py` becomes UI-only: it renders a downscaled display frame with PIL, captures clicks via `streamlit-image-coordinates`, and keeps all annotation state in `st.session_state` keyed by image name in original-image coordinates.

**Tech Stack:** Python 3.x (venv at `.venv`), Streamlit, `streamlit-image-coordinates`, Pillow, NumPy, pytest.

**Spec:** `docs/superpowers/specs/2026-07-07-polyline-wrinkle-annotator-design.md`

## Global Constraints

- Working directory: `D:\LabelWebApp`. Python is `.venv\Scripts\python.exe` (Windows). Run tools as `.venv\Scripts\python.exe -m pytest ...`, `.venv\Scripts\python.exe -m pip ...`.
- **This project is NOT a git repository.** Skip all commit steps; do not run `git init` (user has not asked for it).
- Coordinates stored in session state / JSON are always **original-image pixels** (floats). Display scaling is view-only.
- Mask width slider range **1–15, default 4**; width means pixels at original resolution.
- Masks are `HxW uint8` with values exclusively `{0, 255}`.
- `streamlit-drawable-canvas` is removed from the app (do not import it anywhere).
- JSON schema (exact):
  ```json
  {"mask_width": 4, "images": {"photo1.jpg": {"polylines": [[[10.0, 20.0], [30.0, 40.0]]]}}}
  ```

---

### Task 1: Dependencies and requirements.txt

**Files:**
- Create: `requirements.txt`
- Create: `tests\__init__.py` (empty file, makes test discovery unambiguous)

**Interfaces:**
- Consumes: nothing
- Produces: installed packages `streamlit-image-coordinates`, `pytest` in `.venv`; `requirements.txt` for reproducibility.

- [ ] **Step 1: Create `requirements.txt`**

```text
streamlit
streamlit-image-coordinates
numpy
Pillow
pytest
```

- [ ] **Step 2: Install dependencies**

Run: `.venv\Scripts\python.exe -m pip install streamlit-image-coordinates pytest`
Expected: `Successfully installed ...` (streamlit/numpy/Pillow are already present; pip will say "Requirement already satisfied" for those).

- [ ] **Step 3: Verify imports**

Run: `.venv\Scripts\python.exe -c "import streamlit_image_coordinates, pytest; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Create empty `tests\__init__.py`**

Create the file with no content.

---

### Task 2: `core.py` — mask rasterization

**Files:**
- Create: `core.py`
- Create: `tests\test_core.py`

**Interfaces:**
- Consumes: nothing
- Produces (used by Task 3 and Task 4):
  - `polylines_to_mask(polylines: list[list[tuple[float, float]]], size: tuple[int, int], width: int) -> np.ndarray` — `size` is `(w, h)`; returns `HxW` uint8, values in `{0, 255}`.
  - `safe_mask_name(original_name: str) -> str` — `"abc.jpg"` → `"abc_mask.png"`.
  - `mask_to_png_bytes(mask: np.ndarray) -> bytes` — PNG-encode an L-mode mask.

- [ ] **Step 1: Write the failing tests**

Create `tests\test_core.py`:

```python
import numpy as np

from core import mask_to_png_bytes, polylines_to_mask, safe_mask_name


def test_empty_polylines_gives_all_black_mask():
    mask = polylines_to_mask([], size=(100, 60), width=4)
    assert mask.shape == (60, 100)  # HxW
    assert mask.dtype == np.uint8
    assert mask.sum() == 0


def test_single_point_polyline_is_ignored():
    mask = polylines_to_mask([[(50.0, 30.0)]], size=(100, 60), width=4)
    assert mask.sum() == 0


def test_horizontal_line_has_requested_thickness():
    width = 5
    mask = polylines_to_mask([[(10.0, 30.0), (90.0, 30.0)]], size=(100, 60), width=width)
    assert set(np.unique(mask)) <= {0, 255}
    # pixel on the centerline is set
    assert mask[30, 50] == 255
    # thickness of the stroke at mid-line is close to the requested width
    col = mask[:, 50]
    thickness = int((col == 255).sum())
    assert width - 1 <= thickness <= width + 2
    # corners stay black
    assert mask[0, 0] == 0 and mask[59, 99] == 0


def test_endpoints_get_round_caps():
    # a cap disc at the first vertex must set the pixel at that vertex
    mask = polylines_to_mask([[(10.0, 30.0), (90.0, 30.0)]], size=(100, 60), width=5)
    assert mask[30, 10] == 255
    assert mask[30, 90] == 255


def test_multiple_polylines_all_drawn():
    mask = polylines_to_mask(
        [[(10.0, 10.0), (90.0, 10.0)], [(10.0, 50.0), (90.0, 50.0)]],
        size=(100, 60),
        width=3,
    )
    assert mask[10, 50] == 255
    assert mask[50, 50] == 255


def test_safe_mask_name():
    assert safe_mask_name("abc.jpg") == "abc_mask.png"
    assert safe_mask_name("no_extension") == "no_extension_mask.png"


def test_mask_to_png_bytes_roundtrip():
    from PIL import Image
    import io

    mask = polylines_to_mask([[(10.0, 30.0), (90.0, 30.0)]], size=(100, 60), width=4)
    data = mask_to_png_bytes(mask)
    im = Image.open(io.BytesIO(data))
    assert im.size == (100, 60)
    assert im.mode == "L"
    assert np.array_equal(np.array(im), mask)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_core.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'core'`

- [ ] **Step 3: Write the implementation**

Create `core.py`:

```python
"""Pure annotation logic: mask rasterization and JSON (de)serialization.

Kept free of Streamlit imports so it is unit-testable.
"""

import io
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw

Point = Tuple[float, float]
Polyline = List[Point]


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


def mask_to_png_bytes(mask: np.ndarray) -> bytes:
    im = Image.fromarray(mask, mode="L")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def safe_mask_name(original_name: str) -> str:
    base = original_name.rsplit(".", 1)[0]
    return f"{base}_mask.png"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_core.py -v`
Expected: 7 passed

---

### Task 3: `core.py` — annotations JSON export/import

**Files:**
- Modify: `core.py` (append functions)
- Modify: `tests\test_core.py` (append tests)

**Interfaces:**
- Consumes: `Polyline` type alias from Task 2.
- Produces (used by Task 4):
  - `annotations_to_json_bytes(annotations: dict[str, list[Polyline]], mask_width: int) -> bytes` — images with zero polylines are omitted.
  - `parse_annotations_json(raw: bytes, known_names: set[str]) -> tuple[dict[str, list[Polyline]], int, list[str]]` — returns `(annotations, mask_width, skipped_names)`; raises `ValueError` on any malformed input (atomic reject). Unknown-but-well-formed image names go to `skipped_names`. Out-of-range/missing `mask_width` falls back to 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests\test_core.py`:

```python
import json

import pytest

from core import annotations_to_json_bytes, parse_annotations_json


def test_json_roundtrip():
    annotations = {
        "a.jpg": [[(1.0, 2.0), (3.0, 4.0)], [(5.5, 6.5), (7.0, 8.0), (9.0, 10.0)]],
        "b.png": [[(0.0, 0.0), (10.0, 10.0)]],
    }
    raw = annotations_to_json_bytes(annotations, mask_width=5)
    parsed, width, skipped = parse_annotations_json(raw, known_names={"a.jpg", "b.png"})
    assert width == 5
    assert skipped == []
    assert parsed == annotations


def test_export_omits_images_without_polylines():
    raw = annotations_to_json_bytes({"a.jpg": [], "b.png": [[(0.0, 0.0), (1.0, 1.0)]]}, 4)
    payload = json.loads(raw.decode("utf-8"))
    assert "a.jpg" not in payload["images"]
    assert "b.png" in payload["images"]


def test_unknown_image_names_are_skipped():
    raw = annotations_to_json_bytes({"gone.jpg": [[(0.0, 0.0), (1.0, 1.0)]]}, 4)
    parsed, _, skipped = parse_annotations_json(raw, known_names={"other.jpg"})
    assert parsed == {}
    assert skipped == ["gone.jpg"]


def test_invalid_json_raises_value_error():
    with pytest.raises(ValueError):
        parse_annotations_json(b"not json at all {", known_names=set())


def test_wrong_structure_raises_value_error():
    with pytest.raises(ValueError):
        parse_annotations_json(json.dumps({"images": [1, 2]}).encode(), known_names=set())
    bad_points = {"images": {"a.jpg": {"polylines": [[[1.0], [2.0, 3.0]]]}}}
    with pytest.raises(ValueError):
        parse_annotations_json(json.dumps(bad_points).encode(), known_names={"a.jpg"})


def test_out_of_range_mask_width_falls_back_to_default():
    raw = annotations_to_json_bytes({"a.jpg": [[(0.0, 0.0), (1.0, 1.0)]]}, 4)
    payload = json.loads(raw.decode("utf-8"))
    payload["mask_width"] = 99
    parsed, width, _ = parse_annotations_json(json.dumps(payload).encode(), known_names={"a.jpg"})
    assert width == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_core.py -v`
Expected: previous 7 pass; new tests FAIL/ERROR with `ImportError: cannot import name 'annotations_to_json_bytes'`

- [ ] **Step 3: Write the implementation**

Append to `core.py` (add `import json` and `Dict, Set` to the existing imports at the top):

```python
import json
from typing import Dict, List, Set, Tuple

DEFAULT_MASK_WIDTH = 4
MIN_MASK_WIDTH = 1
MAX_MASK_WIDTH = 15


def annotations_to_json_bytes(annotations: Dict[str, List[Polyline]], mask_width: int) -> bytes:
    payload = {
        "mask_width": int(mask_width),
        "images": {
            name: {"polylines": [[[float(x), float(y)] for x, y in line] for line in lines]}
            for name, lines in annotations.items()
            if lines
        },
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def parse_annotations_json(raw: bytes, known_names: Set[str]):
    """Parse an exported annotations.json.

    Returns (annotations, mask_width, skipped_names).
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
        if name not in known_names:
            skipped.append(name)
            continue
        annotations[name] = polylines
    return annotations, mask_width, skipped
```

Note: consolidate the `typing` import — the final top of `core.py` should read:

```python
import io
import json
from typing import Dict, List, Set, Tuple

import numpy as np
from PIL import Image, ImageDraw
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_core.py -v`
Expected: 13 passed

---

### Task 4: Rewrite `app.py` (UI)

**Files:**
- Rewrite: `app.py` (full replacement of current content)

**Interfaces:**
- Consumes from `core.py`: `polylines_to_mask(polylines, size, width)`, `mask_to_png_bytes(mask)`, `safe_mask_name(name)`, `annotations_to_json_bytes(annotations, mask_width)`, `parse_annotations_json(raw, known_names)`, `DEFAULT_MASK_WIDTH`, `MIN_MASK_WIDTH`, `MAX_MASK_WIDTH`.
- Produces: the user-facing app. No downstream code consumers.

- [ ] **Step 1: Replace `app.py` with the new UI**

```python
import io
import zipfile
from dataclasses import dataclass
from typing import List

import streamlit as st
from PIL import Image, ImageDraw, ImageOps
from streamlit_image_coordinates import streamlit_image_coordinates

from core import (
    DEFAULT_MASK_WIDTH,
    MAX_MASK_WIDTH,
    MIN_MASK_WIDTH,
    annotations_to_json_bytes,
    mask_to_png_bytes,
    parse_annotations_json,
    polylines_to_mask,
    safe_mask_name,
)

MAX_DISPLAY_W = 900

st.set_page_config(page_title="Wrinkle Polyline Annotator", layout="wide")


@dataclass
class Item:
    name: str
    img: Image.Image  # RGB image


def pil_to_rgb(img: Image.Image) -> Image.Image:
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def render_display_frame(img: Image.Image, scale: float, committed, in_progress) -> Image.Image:
    """Downscale the image and draw committed (red) + in-progress (green) polylines."""
    disp_w = max(1, round(img.width * scale))
    disp_h = max(1, round(img.height * scale))
    frame = img.resize((disp_w, disp_h)) if scale < 1.0 else img.copy()
    draw = ImageDraw.Draw(frame)

    def draw_polyline(points, color, dot_r):
        pts = [(x * scale, y * scale) for x, y in points]
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=3, joint="curve")
        for px, py in pts:
            draw.ellipse([px - dot_r, py - dot_r, px + dot_r, py + dot_r], fill=color)

    for line in committed:
        draw_polyline(line, (255, 0, 0), 3)
    draw_polyline(in_progress, (0, 255, 0), 4)
    return frame


ss = st.session_state
ss.setdefault("annotations", {})      # image name -> list of polylines (original coords)
ss.setdefault("current_points", [])   # in-progress polyline (original coords)
ss.setdefault("idx", 0)
ss.setdefault("last_click", None)
ss.setdefault("import_applied", None)


def switch_image(new_idx: int):
    ss["idx"] = new_idx
    ss["current_points"] = []
    ss["last_click"] = None


st.title("🖊️ Wrinkle Polyline Annotator → Bitmask PNG Export")

with st.sidebar:
    st.header("Controls 🎛️")
    mask_width = st.slider(
        "Mask line width (px, original resolution)",
        MIN_MASK_WIDTH,
        MAX_MASK_WIDTH,
        ss.get("mask_width_value", DEFAULT_MASK_WIDTH),
        key="mask_width_slider",
    )
    ss["mask_width_value"] = mask_width
    st.caption(
        "Click along a wrinkle's centerline to drop vertices, then press "
        "**Finish wrinkle**. The centerline is dilated to this width in the exported mask."
    )
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
            parsed, width, skipped = parse_annotations_json(
                imported.getvalue(), known_names={it.name for it in items}
            )
            ss["annotations"].update(parsed)
            ss["mask_width_value"] = width
            ss["import_applied"] = import_id
            msg = f"Imported annotations for {len(parsed)} image(s)."
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

left, right = st.columns([2, 1])

with left:
    st.subheader("Click the wrinkle centerline ✍️")
    frame = render_display_frame(current.img, scale, committed, ss["current_points"])
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
    st.subheader(f"Wrinkles ({len(committed)}) 📋")
    for i, line in enumerate(committed):
        c1, c2 = st.columns([3, 1])
        c1.write(f"Wrinkle {i + 1} — {len(line)} pts")
        if c2.button("🗑️", key=f"del_{current.name}_{i}"):
            committed.pop(i)
            st.rerun()

    st.subheader("Export 📦")
    if committed:
        mask = polylines_to_mask(committed, size=(orig_w, orig_h), width=mask_width)
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
        st.info("Finish at least one wrinkle to export 🙂")

st.divider()
st.subheader("Download ALL masks + annotations as ZIP 🗜️")

annotated = {name: lines for name, lines in ss["annotations"].items() if lines}
if not annotated:
    st.warning("No wrinkles committed yet.")
else:
    sizes = {it.name: it.img.size for it in items}
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, lines in annotated.items():
            if name not in sizes:
                continue  # annotation imported for an image not currently uploaded
            m = polylines_to_mask(lines, size=sizes[name], width=mask_width)
            zf.writestr(safe_mask_name(name), mask_to_png_bytes(m))
        zf.writestr("annotations.json", annotations_to_json_bytes(annotated, mask_width))
    st.download_button(
        f"⬇️ Download ZIP ({len(annotated)} masks + annotations.json)",
        data=zip_buf.getvalue(),
        file_name="masks.zip",
        mime="application/zip",
        use_container_width=True,
    )
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `.venv\Scripts\python.exe -c "import ast; ast.parse(open('app.py', encoding='utf-8').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Run the full test suite**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: 13 passed

- [ ] **Step 4: Manual verification with the running app**

Run: `.venv\Scripts\streamlit.exe run app.py` (background) and check in the browser:

1. Upload 1–2 images (at least one wider than 900 px to exercise scaling).
2. Click 4–5 points along a curve → green line follows clicks.
3. **Undo last point** removes the last vertex; **Finish wrinkle** turns it red and it appears in the list.
4. **Finish wrinkle** with 0–1 points shows the warning.
5. Delete a wrinkle from the list → disappears from image.
6. Mask preview shows white polyline of the chosen width; width slider changes preview thickness live.
7. Download current mask → PNG dimensions equal the ORIGINAL image dimensions.
8. Annotate a second image, download ZIP → contains both `_mask.png` files + `annotations.json`.
9. Restart the app (or press R), re-upload the same images, import `annotations.json` → wrinkles reappear.

Expected: all steps behave as described; no exceptions in the terminal.

---

## Self-Review Notes

- Spec coverage: state model (Task 4 `ss` defaults), display/input incl. clamping + dedupe (Task 4), wrinkle list + delete (Task 4), mask generation with round caps (Task 2), export/import incl. atomic ValueError reject and skipped names (Task 3 + Task 4), width 1–15 default 4 (Global Constraints, Tasks 2–4). No gaps found.
- No placeholders; all code complete.
- Type consistency: `polylines_to_mask(polylines, size=(w, h), width)` used identically in tests and app; `parse_annotations_json` 3-tuple return used consistently.
- Commit steps intentionally omitted: not a git repository (Global Constraints).
