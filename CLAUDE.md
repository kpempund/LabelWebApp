# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page Streamlit app ("Wrinkle Annotator") for hand-labeling wrinkles/crow's-feet
on uploaded photos. Users draw freehand pen strokes over an image; the app rasterizes
those strokes into a binary PNG mask and triggers a client-side download. There is no
server-side storage — all state lives in `st.session_state` for the duration of the
browser session.

## Commands

```bash
# Install
pip install -r requirements.txt

# Run locally (APP_PASSWORD is required — the app refuses to serve without it)
APP_PASSWORD=test streamlit run app.py

# Run tests
pytest

# Run a single test
pytest tests/test_core.py::test_stroke_width_is_honored_not_mask_slider
```

Deployment targets are Streamlit Community Cloud and Railway (via `Procfile`); see `DEPLOY.md`.

## Architecture

The codebase is split in two, deliberately:

- **`core.py`** — pure annotation logic (mask rasterization, JSON serialization). No
  Streamlit imports, fully unit-testable. This is where mask-drawing correctness lives.
- **`app.py`** — all Streamlit UI, session-state wiring, and the canvas/download plumbing.
  Not unit-tested directly; correctness here is behavioral/manual.

When changing rasterization behavior (stroke thickness, endpoint caps, mask merging),
change `core.py` and add a test in `tests/test_core.py`. When changing UI flow (canvas
sync, buttons, image switching), change `app.py`.

### Coordinate system

All stored points (`ss["annotations"]`, `ss["freehand"]`) are in **original-image pixel
coordinates**, not display coordinates. The canvas is shown downscaled by `display_scale()`
to fit `MAX_DISPLAY_W`/`MAX_DISPLAY_H`; conversion to/from display space happens at the
UI boundary (`extract_strokes`, `render_display_frame`). This keeps exported masks
resolution-independent regardless of what size the browser rendered the canvas at.

### Mask model

Final mask per image = union (`combine_masks`) of:
1. `polylines_to_mask` — click-based polylines, all dilated to the single global
   "Pen size" width.
2. `strokes_to_mask` — freehand pen strokes, each rasterized at **its own** width
   (captured at draw time), not the current slider value.

Both draw rounded end caps at line endpoints in addition to `ImageDraw.line(..., joint="curve")`,
since PIL's `joint="curve"` rounds interior joints but not the two endpoints.

### Canvas sync is intentionally lazy

`st_canvas`'s `update_streamlit` flag controls whether every stroke is pushed back to
the server over the websocket. This app keeps it `False` during drawing and only flips
it on (`ss["want_sync"]`) when the user taps "Save & Download Mask". Per-stroke syncing
was found to repeatedly stall/reconnect the websocket on iPad Safari (WebKit), causing
lag and a "SessionInfo before it was initialized" popup. If you touch this flow, preserve
the two-rerun handshake in `app.py` (flip `update_streamlit` on → wait for real strokes
to arrive, since the component first re-emits stale/empty state → commit and remount).

Similarly, the canvas background image is memoized (`bg_sig`) rather than re-encoded on
every rerun, and is delivered as a Fabric.js `backgroundImage` data URL embedded in
`initial_drawing` rather than via `st_canvas`'s `background_image` argument — that
argument registers a temporary `/media/<id>.png` file that 404s after a rerun remounts
the canvas. Don't revert either of these without re-reading the comments in `app.py`
around `bg_sig` and `initial_drawing` — they document specific failure modes that
motivated them.

### Auth

`check_password()` gates the entire app behind a single shared password read from
`APP_PASSWORD` (env var, falling back to `st.secrets`). Fails closed: if unset, the app
refuses to serve rather than allowing unauthenticated access.

## Design docs

`docs/superpowers/specs/` and `docs/superpowers/plans/` contain the design docs and
plans behind past features (e.g. the freehand pen tool and iPad/Railway hosting work).
Consult these for the reasoning behind non-obvious architecture decisions before
changing canvas/sync behavior.
