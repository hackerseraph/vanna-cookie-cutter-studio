from io import BytesIO
import json
from zipfile import ZipFile

import numpy as np
from PIL import Image, ImageDraw, ImageOps
import pytest
import trimesh

from app import app, generation_lock
from geometry import DesignError, Settings, read_image, trace_image
from sheets import MAX_CROPS, crop_image, detect_regions


def png(image):
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def artwork_sheet():
    image = Image.new("RGB", (800, 400), "white")
    draw = ImageDraw.Draw(image)
    for row in range(2):
        for column in range(4):
            x, y = column * 200 + 20, row * 200 + 20
            draw.rounded_rectangle((x, y, x + 130, y + 150), radius=25, outline="black", width=4)
            draw.rounded_rectangle((x + 9, y + 9, x + 121, y + 141), radius=20, outline="black", width=3)
            draw.ellipse((x + 35, y + 45, x + 65, y + 80), fill="black")
    draw.point((790, 390), fill="black")
    return image


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "OUTPUT_DIR", tmp_path)
    with app.test_client() as client:
        yield client


def detect(client, image=None, **settings):
    response = client.post("/api/sheets/detect", data={
        "image": (BytesIO(png(image if image is not None else artwork_sheet())), "sheet.png"), **settings,
    })
    assert response.status_code == 200, response.json
    return response.json


def test_detection_groups_nested_rings_and_ignores_noise():
    image = artwork_sheet()
    regions = detect_regions(image, Settings())
    assert len(regions) == 8
    assert [(region["x"], region["y"]) for region in regions] == [
        (20, 20), (220, 20), (420, 20), (620, 20),
        (20, 220), (220, 220), (420, 220), (620, 220),
    ]
    assert all(region["size_mm"] == 88.9 for region in regions)
    inverted = detect_regions(ImageOps.invert(image), Settings(invert=True))
    assert inverted == regions
    assert detect_regions(Image.new("RGB", (200, 200), "white"), Settings()) == []


def test_detection_limit_is_explicit():
    image = Image.new("L", (600, 600), "white")
    draw = ImageDraw.Draw(image)
    for index in range(MAX_CROPS + 1):
        x, y = (index % 5) * 110, (index // 5) * 110
        draw.rectangle((x + 5, y + 5, x + 70, y + 70), fill="black")
    with pytest.raises(DesignError, match="Split the sheet"):
        detect_regions(image, Settings())


@pytest.mark.parametrize("crop", [
    None, [], {}, {"x": -1, "y": 0, "width": 20, "height": 20},
    {"x": 0, "y": 0, "width": 900, "height": 20},
    {"x": 0, "y": 0, "width": 20, "height": 1},
    {"x": True, "y": 0, "width": 20, "height": 20},
    {"x": float("nan"), "y": 0, "width": 20, "height": 20},
    {"x": 0, "y": float("inf"), "width": 20, "height": 20},
    {"x": "0", "y": 0, "width": 20, "height": 20},
])
def test_invalid_crops_are_rejected(crop):
    with pytest.raises(DesignError):
        crop_image(artwork_sheet(), crop)


def test_crop_preserves_original_and_handles_solid_edge_touching_art():
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 70, 80), fill="black")
    before = np.asarray(image).copy()
    crop = {"x": 10, "y": 10, "width": 61, "height": 71}
    design = trace_image(crop_image(image, crop), Settings())
    assert max(design.dimensions) == pytest.approx(88.9)
    inverted = trace_image(crop_image(ImageOps.invert(image), crop, True), Settings(invert=True))
    assert inverted.dimensions == pytest.approx(design.dimensions)
    np.testing.assert_array_equal(image, before)


def test_normalization_respects_transparency_and_exif_orientation():
    transparent = Image.new("RGBA", (70, 50), (0, 0, 0, 0))
    normalized = read_image(png(transparent))
    assert normalized.getpixel((0, 0)) == (255, 255, 255)
    source = Image.new("RGB", (70, 50), "white")
    exif = Image.Exif()
    exif[274] = 6
    data = BytesIO()
    source.save(data, format="JPEG", exif=exif)
    assert read_image(data.getvalue()).size == (50, 70)


def test_sheet_generation_downloads_and_bundle_persist(client):
    sheet = detect(client)
    assert len(sheet["regions"]) == 8
    assert (sheet["width"], sheet["height"]) == (800, 400)
    image = client.get(sheet["image_url"])
    assert image.status_code == 200
    assert image.mimetype == "image/png"
    assert Image.open(BytesIO(image.data)).size == (800, 400)
    designs = []
    for region, size in zip(sheet["regions"][:2], (50.8, 101.6)):
        response = client.post("/api/generate", data={
            "sheet_id": sheet["id"], "crop": json.dumps(region), "name": "Same Name",
            "size_mm": str(size),
        })
        assert response.status_code == 200, response.json
        design = response.json
        assert design["name"] == "Same-Name"
        assert max(design["cookie_mm"]) == pytest.approx(size, abs=0.001)
        mesh = trimesh.load(BytesIO(client.get(design["tools"]["combined"]["url"]).data), file_type="stl")
        parts = sorted(mesh.split(), key=lambda part: part.bounds[0, 0])
        assert len(parts) == 2
        assert parts[1].bounds[0, 0] - parts[0].bounds[1, 0] == pytest.approx(5, abs=0.001)
        designs.append(design)
    failed = client.post("/api/generate", data={
        "sheet_id": sheet["id"], "crop": json.dumps({"x": 180, "y": 180, "width": 10, "height": 10}),
    })
    assert failed.status_code == 400
    assert not generation_lock.locked()
    response = client.post("/api/bundles", json={"design_ids": [item["id"] for item in designs]})
    assert response.status_code == 200
    downloaded = client.get(response.json["url"])
    assert downloaded.status_code == 200
    with ZipFile(BytesIO(downloaded.data)) as archive:
        assert len(archive.namelist()) == 8
        for index, design in enumerate(designs, 1):
            folder = f"{index:02d}-Same-Name"
            assert archive.read(f"{folder}/Same-Name-combined.stl") == client.get(design["tools"]["combined"]["url"]).data
            assert json.loads(archive.read(f"{folder}/design.json"))["id"] == design["id"]
    assert client.get(response.json["url"]).data == downloaded.data
    assert all(client.get(design["tools"]["cutter"]["url"]).status_code == 200 for design in designs)


def test_sheet_request_errors_and_bounds(client):
    assert client.post("/api/sheets/detect").status_code == 400
    bad = client.post("/api/sheets/detect", data={"image": (BytesIO(b"invalid"), "bad.png")})
    assert bad.status_code == 400
    assert client.get("/api/sheets/bad/image").status_code == 400
    assert client.get(f'/api/sheets/{"0" * 32}/image').status_code == 400
    sheet = detect(client)
    assert client.post("/api/generate", data={"sheet_id": sheet["id"], "crop": "invalid"}).status_code == 400
    assert client.post("/api/generate", data={"sheet_id": sheet["id"], "crop": "{}"}).status_code == 400
    generation_lock.acquire()
    try:
        response = client.post("/api/generate", data={"sheet_id": sheet["id"], "crop": json.dumps(sheet["regions"][0])})
        assert response.status_code == 409
    finally:
        generation_lock.release()
    for ids in ([], ["bad"], ["0" * 32], ["0" * 32] * 2, list(range(25))):
        assert client.post("/api/bundles", json={"design_ids": ids}).status_code == 400
    assert client.get("/api/bundles/bad/download").status_code == 404
    assert client.get(f'/api/bundles/{"0" * 32}/download').status_code == 404
