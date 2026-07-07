import io
import os
import zipfile
from dataclasses import dataclass
from typing import List

import streamlit as st
from PIL import Image, ImageDraw, ImageOps
from streamlit_drawable_canvas import st_canvas
from streamlit_image_coordinates import streamlit_image_coordinates

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

MAX_DISPLAY_W = 820
MAX_DISPLAY_H = 620
MIN_PEN_SIZE = 1
MAX_PEN_SIZE = 60
DEFAULT_PEN_SIZE = 6

st.set_page_config(
    page_title="Wrinkle Annotator",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1180px;
        padding-top: 1rem;
        padding-bottom: 1rem;
    }
    div[data-testid="stVerticalBlock"] {
        gap: 0.75rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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
    pw = st.text_input("Password", type="password")
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
    img: Image.Image


def pil_to_rgb(img: Image.Image) -> Image.Image:
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def display_scale(img: Image.Image) -> float:
    return min(1.0, MAX_DISPLAY_W / img.width, MAX_DISPLAY_H / img.height)


def render_display_frame(img, scale, committed, in_progress, committed_freehand):
    """Downscale the image and draw committed work plus the active click line."""
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
    """Convert freedraw path objects into original-resolution strokes."""
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
ss.setdefault("annotations", {})
ss.setdefault("freehand", {})
ss.setdefault("current_points", [])
ss.setdefault("idx", 0)
ss.setdefault("last_click", None)
ss.setdefault("import_applied", None)
ss.setdefault("canvas_nonce", 0)


def switch_image(new_idx: int):
    ss["idx"] = new_idx
    ss["current_points"] = []
    ss["last_click"] = None


st.title("Wrinkle Annotator")

with st.sidebar:
    st.header("Controls")
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

    st.divider()
    st.subheader("Resume session")
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
    st.info("Upload images to start.")
    st.stop()

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

col_a, col_b, col_c = st.columns([1, 2, 1])
with col_a:
    if st.button("Prev", use_container_width=True):
        switch_image((ss["idx"] - 1) % len(items))
        st.rerun()
with col_b:
    st.markdown(
        f"<h4 style='text-align:center;'>{ss['idx'] + 1}/{len(items)} - "
        f"<code>{items[ss['idx']].name}</code></h4>",
        unsafe_allow_html=True,
    )
with col_c:
    if st.button("Next", use_container_width=True):
        switch_image((ss["idx"] + 1) % len(items))
        st.rerun()

current = items[ss["idx"]]
orig_w, orig_h = current.img.size
scale = display_scale(current.img)
committed = ss["annotations"].setdefault(current.name, [])
committed_fh = ss["freehand"].setdefault(current.name, [])

with st.sidebar:
    st.divider()
    st.subheader("Current image")
    st.write(f"Click wrinkles: {len(committed)}")
    st.write(f"Freehand strokes: {len(committed_fh)}")
    if committed and st.button("Delete last click wrinkle", use_container_width=True):
        committed.pop()
        st.rerun()
    if committed_fh and st.button("Delete last freehand stroke", use_container_width=True):
        committed_fh.pop()
        st.rerun()
    if committed or committed_fh:
        current_mask = build_mask(committed, committed_fh, (orig_w, orig_h), mask_width)
        st.download_button(
            "Download current mask PNG",
            data=mask_to_png_bytes(current_mask),
            file_name=safe_mask_name(current.name),
            mime="image/png",
            use_container_width=True,
        )

if tool == "Freehand pen":
    st.subheader("Draw the wrinkle with the pen")
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
        if st.button("Finish freehand strokes", use_container_width=True):
            new_strokes = extract_strokes(canvas_result, scale, pen_size)
            if new_strokes:
                committed_fh.extend(new_strokes)
                ss["canvas_nonce"] += 1
                st.rerun()
            else:
                st.warning("Draw at least one stroke first.")
    with b2:
        if st.button("Clear pending pen", use_container_width=True):
            ss["canvas_nonce"] += 1
            st.rerun()
else:
    st.subheader("Click the wrinkle centerline")
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
        if st.button("Finish wrinkle", use_container_width=True):
            if len(ss["current_points"]) >= 2:
                committed.append(ss["current_points"])
                ss["current_points"] = []
                st.rerun()
            else:
                st.warning("Need at least 2 points to finish a wrinkle.")
    with b2:
        if st.button("Undo last point", use_container_width=True):
            if ss["current_points"]:
                ss["current_points"].pop()
                st.rerun()
    with b3:
        if st.button("Discard in-progress", use_container_width=True):
            ss["current_points"] = []
            st.rerun()

sizes = {it.name: it.img.size for it in items}
names_with_work = {n for n, v in ss["annotations"].items() if v} | {
    n for n, v in ss["freehand"].items() if v
}
names_with_work &= set(sizes)
if names_with_work:
    export_ann = {n: ss["annotations"].get(n, []) for n in names_with_work}
    export_fh = {n: ss["freehand"].get(n, []) for n in names_with_work if ss["freehand"].get(n)}
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in names_with_work:
            m = build_mask(ss["annotations"].get(name, []), ss["freehand"].get(name, []), sizes[name], mask_width)
            zf.writestr(safe_mask_name(name), mask_to_png_bytes(m))
        zf.writestr("annotations.json", annotations_to_json_bytes(export_ann, mask_width, export_fh))
    st.download_button(
        f"Download ZIP ({len(names_with_work)} masks + annotations.json)",
        data=zip_buf.getvalue(),
        file_name="masks.zip",
        mime="application/zip",
        use_container_width=True,
    )
