# Freehand Pen Tool + iPad/Railway Hosting — Design

**Date:** 2026-07-07
**Status:** Approved (pending spec review)

## Summary

Add a freehand pen drawing tool to the Wrinkle Polyline Annotator, alongside the
existing click-to-drop-vertices workflow, and deploy the app to Railway behind a
shared password so it can be used on an iPad with an Apple Pencil. Freehand
strokes are painted at the pen's own thickness (not dilated by the mask-width
slider) and merged with the click-polyline mask on export. Existing
upload/download flow is preserved; no server-side storage.

## Goals

- A **Freehand pen** tool selectable alongside the existing **Click points** tool.
- Freehand strokes render into the exported mask at **pen thickness = mask
  thickness**, controlled by a dedicated **Pen size** slider.
- App reachable on the public internet (Railway) but gated by a **single shared
  password**.
- Usable on **iPad Safari with Apple Pencil** for both tapping (clicks) and
  drawing (freehand).
- Export unchanged in shape: per-image PNG named `{originalname}_mask.png`, and
  the ZIP of all masks + `annotations.json`.

## Non-Goals (explicitly out of scope)

- Server-side image library or auto-save of in-progress work.
- Per-user accounts / multiple logins.
- Apple Pencil pressure sensitivity or tilt.
- Replacing or rewriting the existing click-polyline workflow.

## Architecture Decision: two canvases, toggled

The existing click workflow uses `streamlit-image-coordinates`, which captures
single taps. Freehand drawing needs stroke capture, provided by
`streamlit-drawable-canvas` (Fabric.js `freedraw` mode, which receives pointer
events from the Apple Pencil).

**Chosen approach:** keep both components and show one at a time based on a
sidebar **Tool** selector.

- `Click points` → renders today's `streamlit-image-coordinates` canvas,
  unchanged.
- `Freehand pen` → renders a `streamlit-drawable-canvas` in `freedraw` mode over
  the same (display-scaled) image.

Rejected alternative: replacing `streamlit-image-coordinates` entirely with
`streamlit-drawable-canvas` (single component). Rejected because it rewrites the
working click/undo/finish logic, increasing regression risk, and contradicts the
"keep the existing tool" requirement.

## Data Model

Coordinates are stored in **original-image pixels** so everything is
resolution-independent (the on-screen canvas is downscaled to `MAX_DISPLAY_W`).

- **Click polylines** — unchanged: `ss["annotations"][name]` = list of polylines;
  each dilated to the global **mask width** slider on rasterization.
- **Freehand strokes** — new: `ss["freehand"][name]` = list of strokes, where each
  stroke is `{"points": [[x, y], ...], "width": <px in original resolution>}`.
  The `width` is the Pen size slider value at draw time, converted from display
  pixels to original pixels (`pen_size / scale`).

### Deriving strokes from the canvas

`streamlit-drawable-canvas` returns `json_data` containing `freedraw` objects.
Each object has an SVG-style `path` (mostly quadratic segments). We extract the
anchor points from the path commands to form the stroke's polyline, then scale
display coords → original coords (`/ scale`). This point-sampling is a faithful
approximation of the stroke for mask purposes; exact Bézier rasterization is not
needed.

## Mask Rasterization & Merge

Final per-image mask = **union** of:

1. `polylines_to_mask(click_polylines, size, width=mask_width)` — existing.
2. `strokes_to_mask(freehand_strokes, size)` — new: each stroke drawn with PIL
   `ImageDraw.line(..., width=stroke["width"], joint="curve")` plus rounded end
   caps, at the stroke's own width.

Union via `numpy` max/`logical_or`, then the existing `mask_to_png_bytes`. Live
mask preview and ZIP export both use this merged mask.

### New `core.py` functions (unit-tested, Streamlit-free)

- `strokes_to_mask(strokes, size) -> np.ndarray` — rasterize freehand strokes at
  per-stroke width.
- `combine_masks(*masks) -> np.ndarray` — union of binary masks.

Both live in `core.py` alongside `polylines_to_mask` and get tests in
`tests/test_core.py` (empty input, single stroke width honored, union
correctness, size handling).

## Resume Session (annotations.json)

Extend the JSON schema so freehand work survives an export→import round trip,
**backward-compatible** with existing files:

```jsonc
{
  "mask_width": 4,
  "images": {
    "photo.jpg": {
      "polylines": [ [[x,y], ...], ... ],
      "freehand":  [ {"points": [[x,y], ...], "width": 22}, ... ]   // NEW, optional
    }
  }
}
```

- `annotations_to_json_bytes` also serializes freehand strokes.
- `parse_annotations_json` reads `freehand` if present; absent → treated as empty
  (old files still load). Validation mirrors the existing polyline validation
  (list of point-pairs, positive width).
- On import, freehand strokes are restored into `ss["freehand"]`; when the
  Freehand tool opens an image, stored strokes are rebuilt into the canvas's
  `initial_drawing` (scaled to display) so they're visible and editable.

## Access Control — shared password

A `check_password()` gate runs first in `app.py`:

- Reads the expected password from env var `APP_PASSWORD` (Railway variable);
  fallback to `st.secrets` for local dev.
- Shows a password `text_input`; on mismatch or empty, `st.stop()`.
- On match, sets a session flag so the prompt doesn't reappear within the session.
- If `APP_PASSWORD` is unset, the app refuses to serve (fails closed) rather than
  running open to the internet.

## Railway Hosting

- **`Procfile`** (currently empty):
  `web: streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
- **`requirements.txt`**: add `streamlit-drawable-canvas`, with `streamlit`
  pinned to a version compatible with the canvas component (verified at
  implementation time; both pinned to known-good versions).
- **`.streamlit/config.toml`** (optional): raise `maxUploadSize` if needed and
  set `server.headless = true` / theme defaults.
- Railway env var `APP_PASSWORD` set in the project dashboard.

## iPad + Apple Pencil

- Wide layout retained. Apple Pencil emits standard pointer events → taps drive
  the click tool, strokes drive the freehand canvas.
- Known polish item: Safari page scroll/zoom can compete with drawing. Mitigate
  with `touch-action: none` CSS on the canvas container; flag for on-device
  tuning if it still fights the Pencil.
- No pressure/tilt handling (out of scope).

## Files Touched

| File | Change |
|------|--------|
| `app.py` | Password gate; Tool selector + Pen size slider; freehand canvas branch; stroke extraction; merged mask preview/export; restore freehand on image switch/import. |
| `core.py` | Add `strokes_to_mask`, `combine_masks`; extend `annotations_to_json_bytes` / `parse_annotations_json` for `freehand`. |
| `tests/test_core.py` | Tests for new helpers and JSON round-trip incl. freehand. |
| `requirements.txt` | Add `streamlit-drawable-canvas`; pin versions. |
| `Procfile` | Streamlit start command for Railway. |
| `.streamlit/config.toml` | (Optional) upload size / headless / theme. |

## Risks & Mitigations

- **Canvas ↔ Streamlit component version drift** → pin both versions; verify
  freedraw + `initial_drawing` work before wiring the rest.
- **Path point-sampling too coarse for tight curves** → sample all path anchor
  points; if a stroke looks jagged in the mask, densify by interpolating along
  segments (fallback, only if observed).
- **Session loss on iPad Safari disconnect** → accepted per scope (no autosave);
  mitigated operationally by exporting the ZIP / annotations.json periodically.

## Success Criteria

- User can switch to Freehand pen and draw a stroke that appears in the mask
  preview at the chosen pen size.
- Click and freehand annotations on the same image both appear in the exported
  `{name}_mask.png` and in the ZIP.
- Export → import restores both polylines and freehand strokes.
- Deployed on Railway, the app prompts for the shared password and, once entered,
  is fully usable on an iPad with an Apple Pencil.
- `core.py` helpers pass unit tests.
