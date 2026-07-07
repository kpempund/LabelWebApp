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
            parsed, _, width, skipped = parse_annotations_json(
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
