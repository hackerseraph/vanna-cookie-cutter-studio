from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import pytest
from shapely import affinity
from shapely.geometry import Polygon
from shapely.ops import unary_union
import trimesh

from geometry import DesignError, Settings, build_meshes, trace_image


ROOT = Path(__file__).resolve().parents[1]


def image_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def rectangle_image(size=(240, 140), box=(20, 20, 220, 120)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle(box, outline="black", width=5)
    draw.line((40, 45, 90, 70), fill="black", width=5)
    return image_bytes(image)


@pytest.mark.parametrize("name", sorted(path.name for path in (ROOT / "Demo-Images").glob("*.png")))
def test_samples_produce_printable_matched_tools(name):
    design = trace_image((ROOT / "Demo-Images" / name).read_bytes(), Settings())
    meshes = build_meshes(design)
    assert max(design.dimensions) == pytest.approx(88.9, abs=0.001)
    for kind, mesh in meshes.items():
        assert mesh.is_watertight
        assert mesh.is_volume
        assert mesh.body_count == 1
        assert mesh.bounds[0, 2] == pytest.approx(0)
        assert np.all(np.isfinite(mesh.vertices))
        assert mesh.volume > 0
        exported = mesh.export(file_type="stl")
        loaded = trimesh.load(BytesIO(exported), file_type="stl")
        assert loaded.is_watertight
        assert loaded.body_count == 1
        extra = 6 if kind == "cutter" else 4
        assert max(mesh.extents[:2]) == pytest.approx(88.9 + extra, abs=0.03)
    assert meshes["cutter"].extents[2] == pytest.approx(12)
    assert meshes["stamp"].extents[2] == pytest.approx(6)
    assert meshes["stamp"].bounds[1, 2] - 3 == pytest.approx(12 / 4)


@pytest.mark.parametrize(
    "size,box,ratio",
    [
        ((240, 140), (20, 20, 220, 120), 2),
        ((140, 240), (20, 20, 120, 220), 0.5),
    ],
)
def test_exact_size_and_aspect_ratio(size, box, ratio):
    design = trace_image(rectangle_image(size, box), Settings())
    width, height = design.dimensions
    assert max(width, height) == pytest.approx(88.9, abs=1e-6)
    assert width / height == pytest.approx(ratio, abs=0.001)
    assert design.source_size == size


def test_relief_mirrored_for_pressing_and_stops_at_requested_depth():
    design = trace_image(rectangle_image(), Settings(cutter_depth_mm=16))
    stamp = build_meshes(design)["stamp"]
    assert stamp.extents[2] == pytest.approx(7)
    top_faces = stamp.triangles[np.all(np.isclose(stamp.triangles[:, :, 2], 7), axis=1)]
    projected = unary_union([Polygon(face[:, :2]) for face in top_faces])
    mirrored = affinity.scale(design.artwork, xfact=-1, yfact=1, origin=(0, 0))
    assert projected.symmetric_difference(mirrored).area < 0.01
    assert projected.symmetric_difference(design.artwork).area > 1
    assert design.artwork.difference(design.outline.buffer(-0.449)).area < 0.001


def test_cutter_aperture_is_target_size_not_outer_grip():
    design = trace_image(rectangle_image(), Settings())
    cutter = build_meshes(design)["cutter"]
    # The cutting-tip inner boundary matches the cookie outline; wall grows outward.
    vertices = cutter.vertices[np.isclose(cutter.vertices[:, 2], 12)]
    width = design.dimensions[0]
    assert np.any(np.isclose(vertices[:, 0], -width / 2, atol=0.001))
    assert np.any(np.isclose(vertices[:, 0], width / 2, atol=0.001))
    assert np.ptp(vertices[:, 0]) == pytest.approx(width + 1.3, abs=0.01)


def test_transparent_background_and_inverse_artwork():
    data = rectangle_image()
    image = Image.open(BytesIO(data)).convert("RGBA")
    values = np.array(image)
    values[values[:, :, 0] > 200, 3] = 0
    transparent = trace_image(image_bytes(Image.fromarray(values)), Settings())
    inverse = Image.fromarray(255 - np.array(Image.open(BytesIO(data))))
    inverted = trace_image(image_bytes(inverse), Settings(invert=True))
    assert transparent.dimensions == pytest.approx(inverted.dimensions)
    assert transparent.artwork.area == pytest.approx(inverted.artwork.area)


@pytest.mark.parametrize("color", ["white", "black"])
def test_blank_image_has_actionable_error(color):
    with pytest.raises(DesignError, match="No outline"):
        trace_image(image_bytes(Image.new("RGB", (100, 100), color)), Settings())


def test_disconnected_large_shapes_are_not_silently_lost():
    image = Image.new("RGB", (240, 140), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 90, 100), fill="black")
    draw.rectangle((150, 20, 220, 100), fill="black")
    with pytest.raises(DesignError, match="disconnected"):
        trace_image(image_bytes(image), Settings())


def test_corrupt_image_rejected():
    with pytest.raises(DesignError, match="could not be read"):
        trace_image(b"not an image", Settings())


@pytest.mark.parametrize(
    "settings",
    [
        Settings(size_mm=float("nan")),
        Settings(size_mm=float("inf")),
        Settings(size_mm=0),
        Settings(cutter_depth_mm=0),
        Settings(threshold=255),
        Settings(bolden_mm=-1),
    ],
)
def test_invalid_settings(settings):
    with pytest.raises(DesignError):
        settings.validate()
