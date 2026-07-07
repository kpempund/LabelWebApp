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


import json

import pytest

from core import annotations_to_json_bytes, parse_annotations_json, strokes_to_mask, combine_masks


def test_json_roundtrip():
    annotations = {
        "a.jpg": [[(1.0, 2.0), (3.0, 4.0)], [(5.5, 6.5), (7.0, 8.0), (9.0, 10.0)]],
        "b.png": [[(0.0, 0.0), (10.0, 10.0)]],
    }
    raw = annotations_to_json_bytes(annotations, mask_width=5)
    parsed, fh, width, skipped = parse_annotations_json(raw, known_names={"a.jpg", "b.png"})
    assert width == 5
    assert skipped == []
    assert parsed == annotations
    assert fh == {}


def test_export_omits_images_without_polylines():
    raw = annotations_to_json_bytes({"a.jpg": [], "b.png": [[(0.0, 0.0), (1.0, 1.0)]]}, 4)
    payload = json.loads(raw.decode("utf-8"))
    assert "a.jpg" not in payload["images"]
    assert "b.png" in payload["images"]


def test_unknown_image_names_are_skipped():
    raw = annotations_to_json_bytes({"gone.jpg": [[(0.0, 0.0), (1.0, 1.0)]]}, 4)
    parsed, _, _, skipped = parse_annotations_json(raw, known_names={"other.jpg"})
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
    parsed, _, width, _ = parse_annotations_json(json.dumps(payload).encode(), known_names={"a.jpg"})
    assert width == 4


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
