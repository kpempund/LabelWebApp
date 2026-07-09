import base64
import io
import os
from dataclasses import dataclass
from typing import List

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageDraw, ImageOps
from streamlit_drawable_canvas import st_canvas
from core import (
    DEFAULT_MASK_WIDTH,
    combine_masks,
    mask_to_png_bytes,
    polylines_to_mask,
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
ss.setdefault("canvas_nonce", 0)


def switch_image(new_idx: int):
    ss["idx"] = new_idx
    ss["current_points"] = []
    ss["last_click"] = None


st.title("Wrinkle Annotator")

with st.sidebar:
    st.header("Controls")
    mask_width = DEFAULT_MASK_WIDTH
    pen_size = st.slider(
        "Pen size (px)",
        MIN_PEN_SIZE,
        MAX_PEN_SIZE,
        ss.get("pen_size_value", DEFAULT_PEN_SIZE),
        key="pen_size_slider",
    )
    ss["pen_size_value"] = pen_size

uploaded = st.file_uploader(
    "Upload image(s) (jpg/png)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True,
)

# Decode each uploaded image only once and reuse it across reruns. With
# update_streamlit on, every pen stroke reruns the whole script; re-decoding and
# re-orienting full-resolution phone photos each time blocks the server long
# enough to drop the websocket on mobile, which shows up as lag and Streamlit's
# "Tried to use SessionInfo before it was initialized" popup.
_prev_cache = ss.get("img_cache", {})
img_cache = {}
items: List[Item] = []
if uploaded:
    for f in uploaded:
        sig = (f.name, f.size)
        img = _prev_cache.get(sig)
        if img is None:
            try:
                decoded = Image.open(f)
                decoded = ImageOps.exif_transpose(decoded)
                img = pil_to_rgb(decoded)
            except Exception as e:
                st.warning(f"Failed to read {f.name}: {e}")
                continue
        img_cache[sig] = img
        items.append(Item(name=f.name, img=img))
ss["img_cache"] = img_cache

if not items:
    st.info("Upload images to start.")
    st.stop()

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
    st.write(f"Freehand strokes: {len(committed_fh)}")
    if committed and st.button("Delete last click wrinkle", use_container_width=True):
        committed.pop()
        st.rerun()
    if committed_fh and st.button("Delete last freehand stroke", use_container_width=True):
        committed_fh.pop()
        st.rerun()

st.subheader("Draw the wrinkle with the pen")
disp_w = max(1, round(orig_w * scale))
disp_h = max(1, round(orig_h * scale))
# Deliver the background as a fabric.js backgroundImage (a data URL inside
# initial_drawing) instead of via st_canvas' background_image argument. That
# argument registers a temporary Streamlit media file whose /media/<id>.png URL
# 404s after any rerun remounts the canvas, and the component's frontend prepends
# the app origin to it (so a bare data URL there becomes a malformed URL). A
# fabric backgroundImage loads the data URL directly: origin-independent, no media
# file, and it survives reruns. It is not a path object, so stroke extraction
# (which only reads objects of type "path") is unaffected.
#
# Memoize it: while drawing (update_streamlit reruns on every stroke) the
# committed work is unchanged, so re-resizing and re-encoding the photo each time
# is pure waste that slows the app and can drop the mobile websocket. The signature
# covers every way the frame can change: image, scale, remount (canvas_nonce), and
# committed click/freehand counts (every mutation also bumps one of these).
bg_sig = (current.name, scale, ss["canvas_nonce"], len(committed), len(committed_fh))
if ss.get("bg_sig") == bg_sig and ss.get("bg_url"):
    bg_data_url = ss["bg_url"]
else:
    bg = render_display_frame(current.img, scale, committed, [], committed_fh)
    _buf = io.BytesIO()
    bg.save(_buf, format="PNG")
    bg_data_url = "data:image/png;base64," + base64.b64encode(_buf.getvalue()).decode()
    ss["bg_sig"] = bg_sig
    ss["bg_url"] = bg_data_url
initial_drawing = {
    "version": "4.4.0",
    "backgroundImage": {
        "type": "image",
        "version": "4.4.0",
        "originX": "left",
        "originY": "top",
        "left": 0,
        "top": 0,
        "width": disp_w,
        "height": disp_h,
        "scaleX": 1,
        "scaleY": 1,
        "crossOrigin": None,
        "src": bg_data_url,
    },
}
canvas_result = st_canvas(
    fill_color="rgba(0,0,0,0)",
    stroke_width=pen_size,
    stroke_color="#00FF00",
    background_image=None,
    update_streamlit=True,
    height=disp_h,
    width=disp_w,
    drawing_mode="freedraw",
    initial_drawing=initial_drawing,
    key=f"canvas_{current.name}_{ss['canvas_nonce']}",
)

# The drawable-canvas toolbar renders inside its own iframe, so page CSS can't
# reach it. Inject a <style> into that iframe to hide the download ("Send to
# Streamlit") and reset ("Reset canvas & history") icons, keeping only undo/redo.
components.html(
    """
    <script>
    (function () {
      const HIDE = ['Send to Streamlit', 'Reset canvas & history'];
      const CSS = HIDE.map(a => 'img[alt="' + a + '"]').join(',') +
        '{display:none !important;}';
      function apply() {
        let frames;
        try { frames = window.parent.document.querySelectorAll('iframe'); }
        catch (e) { return; }
        frames.forEach(function (f) {
          if (!/drawable_canvas/.test(f.src || '')) return;
          let doc;
          try { doc = f.contentDocument; } catch (e) { return; }
          if (!doc || !doc.head || doc.getElementById('hide-canvas-tools')) return;
          const s = doc.createElement('style');
          s.id = 'hide-canvas-tools';
          s.textContent = CSS;
          doc.head.appendChild(s);
        });
      }
      apply();
      setInterval(apply, 500);
    })();
    </script>
    """,
    height=0,
)

b1, b2 = st.columns(2)
with b1:
    if st.button("Finish", use_container_width=True):
        new_strokes = extract_strokes(canvas_result, scale, pen_size)
        if new_strokes:
            committed_fh.extend(new_strokes)
            ss["canvas_nonce"] += 1
            st.rerun()
        else:
            st.warning("Draw at least one stroke first.")
with b2:
    if st.button("Clear", use_container_width=True):
        ss["canvas_nonce"] += 1
        st.rerun()

if committed or committed_fh:
    current_mask = build_mask(committed, committed_fh, (orig_w, orig_h), mask_width)
    st.download_button(
        "Download Mask",
        data=mask_to_png_bytes(current_mask),
        file_name=os.path.splitext(current.name)[0] + ".png",
        mime="image/png",
        use_container_width=True,
    )
