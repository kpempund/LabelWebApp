# Polyline Wrinkle Annotator — Design

**Date:** 2026-07-07
**Status:** Approved

## Goal

Replace the freehand-brush labeling in `app.py` with centerline polyline annotation:
each wrinkle is clicked out as a polyline along its centerline, then programmatically
dilated to a fixed pixel width to produce a 0/255 binary training mask at the
original image resolution.

## Decisions (confirmed with user)

- **Draw method:** click vertices on the image; "Finish wrinkle" commits the polyline.
- **Resolution:** annotation happens on a downscaled display image, but coordinates are
  stored and masks rendered at **original image resolution**. The width setting means
  pixels at original resolution.
- **Editing:** list of committed wrinkles with per-item delete; "Undo last point" and
  "Discard in-progress" while drawing. No vertex-level editing of committed wrinkles.
- **Export:** per-image mask PNG download + ZIP containing all masks and an
  `annotations.json` with raw polylines. JSON is re-importable to resume a session.
- **Input capture:** `streamlit-image-coordinates` package (new dependency);
  `streamlit-drawable-canvas` is removed along with brush mode.

## Architecture

Single file, `app.py`, rewritten. Pure helper functions kept separate from UI code so
mask generation and JSON serialization are unit-testable.

### State model (st.session_state)

- `annotations: dict[str, list[list[tuple[float, float]]]]` — image name → list of
  wrinkles; each wrinkle is a list of (x, y) vertices in original-image coordinates.
- `current_points: list[tuple[float, float]]` — in-progress polyline for the currently
  displayed image (cleared when switching images).
- `idx: int` — current image index (existing behavior kept).
- `last_click: tuple | None` — dedupe guard so a Streamlit rerun doesn't re-append the
  same click.

### Display & input

- Display scale `s = min(1.0, MAX_DISPLAY_W / orig_w)` (MAX_DISPLAY_W ≈ 900); never
  upscale.
- Each rerun, render a display frame with PIL:
  - committed wrinkles: red polylines + small vertex dots,
  - in-progress polyline: green line + markers.
- Show the frame via `streamlit_image_coordinates(...)`; a returned click is converted
  to original coords (`x/s, y/s`), clamped to image bounds, and appended to
  `current_points` unless it duplicates the previous vertex.
- Buttons: **Finish wrinkle** (requires ≥ 2 points, else warning), **Undo last point**,
  **Discard in-progress**.

### Wrinkle list panel

Right column lists each committed wrinkle for the current image
("Wrinkle N — k pts") with a delete button that removes it and reruns.

### Mask generation (pure function)

```
def polylines_to_mask(polylines, size, width) -> np.ndarray  # HxW uint8, 0/255
```

- New `L`-mode PIL image at original size, black.
- For each polyline: `ImageDraw.line(points, fill=255, width=width, joint="curve")`,
  plus filled circles of diameter `width` at the first and last vertex (round caps —
  PIL's `joint="curve"` rounds interior joints but not endpoints).
- Width slider: 1–15, default 4.
- Live preview of the current image's mask in the export column.

### Export / import

- Per-image: `⬇️ Download mask PNG` → `<name>_mask.png`.
- ZIP: every image with ≥ 1 wrinkle gets its mask; plus `annotations.json`:

```json
{
  "mask_width": 4,
  "images": {
    "photo1.jpg": {"polylines": [[[x, y], ...], ...]}
  }
}
```

- Import: file uploader accepting a previously exported `annotations.json`; entries are
  matched to uploaded images by name; unknown names are skipped with an info notice;
  malformed JSON shows an error and changes nothing.

## Error handling

- Clicks outside image bounds: clamped.
- Duplicate click on rerun: ignored via `last_click` guard.
- "Finish wrinkle" with < 2 points: warning, nothing committed.
- JSON import: structure validated (dict with "images" of name → polylines of numeric
  pairs); invalid input rejected atomically.

## Testing

- Unit-testable pure functions: `polylines_to_mask` (shape, dtype, values ∈ {0, 255},
  line thickness ≈ width, empty input → all-black) and JSON export/import round-trip.
- UI verified by running the app manually.

## Out of scope (YAGNI)

- Multiple label classes, vertex editing of committed wrinkles, zoom/pan,
  server-side persistence, brush mode.
