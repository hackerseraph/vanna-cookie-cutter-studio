from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from app import app, generation_lock


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path):
    app.config.update(TESTING=True, OUTPUT_DIR=tmp_path)
    with app.test_client() as client:
        yield client


def test_home_and_examples(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<h1><a class="brand" href="/">Vanna\'s Cookie Cutter Studio</a></h1>' in html
    assert html.count("<h1>") == 1
    for removed in ("FROM PIXELS TO FRESHLY BAKED", "A little shape.", "A lot of possibility.", 'class="intro"', "Crumb home"):
        assert removed not in html
    names = client.get("/api/examples").json
    assert len(names) == 4
    for name in names:
        response = client.get(f"/api/examples/{name}")
        assert response.status_code == 200
        assert response.mimetype == "image/png"
    assert client.get("/api/examples/missing.png").status_code == 404


def test_generation_downloads_and_persistence(client):
    response = client.post("/api/generate", data={
        "image": (BytesIO((ROOT / "Demo-Images/bat_pteradactyl.png").read_bytes()), "bat.png"),
    })
    assert response.status_code == 200, response.json
    result = response.json
    assert max(result["cookie_mm"]) == pytest.approx(88.9)
    assert result["stamp_impression_mm"] == 3
    assert result["preview_svg"].startswith("<svg")
    directory = Path(app.config["OUTPUT_DIR"]) / result["id"]
    assert json.loads((directory / "design.json").read_text())["id"] == result["id"]
    for kind in ("cutter", "stamp", "combined"):
        download = client.get(result["tools"][kind]["url"])
        assert download.status_code == 200
        assert download.mimetype == "model/stl"
        assert f'{kind}.stl' in download.headers["Content-Disposition"]
        assert len(download.data) > 84
        assert download.data == (directory / result["tools"][kind]["filename"]).read_bytes()
    bundle = client.get(f'/api/designs/{result["id"]}/bundle')
    with ZipFile(BytesIO(bundle.data)) as archive:
        assert sorted(archive.namelist()) == ["bat-combined.stl", "bat-cutter.stl", "bat-stamp.stl", "design.json"]
        assert archive.read("bat-cutter.stl") == (directory / "bat-cutter.stl").read_bytes()
        assert archive.read("bat-combined.stl") == (directory / "bat-combined.stl").read_bytes()


def test_old_design_without_combined_returns_explicit_error(client):
    directory = Path(app.config["OUTPUT_DIR"]) / ("0" * 32)
    directory.mkdir()
    (directory / "design.json").write_text(json.dumps({"tools": {}}))
    response = client.get(f'/api/designs/{"0" * 32}/combined')
    assert response.status_code == 404
    assert "Generate it again" in response.json["error"]


def test_errors_are_json_and_explicit(client):
    assert client.post("/api/generate").status_code == 400
    response = client.post("/api/generate", data={"image": (BytesIO(b"bad"), "image.png")})
    assert response.status_code == 400
    assert "could not be read" in response.json["error"]
    response = client.post("/api/generate", data={
        "image": (BytesIO(b"bad"), "image.png"), "size_mm": "nan",
    })
    assert response.status_code == 400
    assert "Size" in response.json["error"]
    assert client.get("/api/designs/bad/cutter").status_code == 404
    assert client.get(f'/api/designs/{"0"*32}/stamp').status_code == 404
    assert client.get("/not-a-route").is_json


def test_concurrent_generation_returns_busy(client):
    generation_lock.acquire()
    try:
        response = client.post("/api/generate", data={"image": (BytesIO(b"image"), "image.png")})
        assert response.status_code == 409
        assert "Another design" in response.json["error"]
    finally:
        generation_lock.release()


def test_size_limit(client):
    response = client.post("/api/generate", data={
        "image": (BytesIO(b"x" * (12 * 1024 * 1024)), "large.png"),
    })
    assert response.status_code == 413
    assert "12 MB" in response.json["error"]
