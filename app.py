"""Local-only web interface for the cookie cutter generator."""

import json
import logging
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from geometry import DesignError, Settings, build_meshes, combined_mesh, preview_svg, read_image, trace_image
from bambuddy import BambuddyClient, BambuddyError, load_config
from sheets import MAX_CROPS, crop_image, detect_regions


ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["OUTPUT_DIR"] = ROOT / "output"
app.config["BAMBUDDY_CONFIG"] = ROOT / "bambuddy.local.json"
generation_lock = threading.Lock()
bambuddy_lock = threading.Lock()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/examples")
def examples():
    return jsonify([path.name for path in sorted((ROOT / "Demo-Images").glob("*.png"))])


@app.get("/api/examples/<name>")
def example(name: str):
    if name not in {path.name for path in (ROOT / "Demo-Images").glob("*.png")}:
        return jsonify(error="Example image not found."), 404
    return send_file(ROOT / "Demo-Images" / name, mimetype="image/png")


def parse_settings() -> Settings:
    try:
        settings = Settings(
            size_mm=float(request.form.get("size_mm", "88.9")),
            cutter_depth_mm=float(request.form.get("cutter_depth_mm", "12")),
            threshold=int(request.form.get("threshold", "160")),
            invert=request.form.get("invert", "false") == "true",
            bolden_mm=float(request.form.get("bolden_mm", "0.15")),
        )
    except (ValueError, TypeError) as error:
        raise DesignError("Settings must be valid numbers.") from error
    settings.validate()
    return settings


@app.post("/api/generate")
def generate():
    settings = parse_settings()
    image = request.files.get("image")
    if request.form.get("sheet_id"):
        sheet = sheet_directory(request.form["sheet_id"])
        source = read_image((sheet / "image.png").read_bytes())
        try:
            crop = json.loads(request.form.get("crop", "null"))
        except ValueError as error:
            raise DesignError("Crop must be valid JSON.") from error
        data = crop_image(source, crop, settings.invert)
        filename = request.form.get("name", "cookie")
    else:
        if image is None or not image.filename:
            raise DesignError("Choose an image before generating.")
        data = image.read()
        filename = image.filename
    if not generation_lock.acquire(blocking=False):
        return jsonify(error="Another design is being generated. Please try again shortly."), 409
    try:
        design = trace_image(data, settings)
        meshes = build_meshes(design)
        meshes["combined"] = combined_mesh(meshes["cutter"], meshes["stamp"])
        design_id = uuid.uuid4().hex
        name = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-")[:60] or "cookie"
        destination = Path(app.config["OUTPUT_DIR"]) / design_id
        destination.mkdir(parents=True)
        metadata = {
            "id": design_id,
            "name": name,
            "cookie_mm": design.dimensions,
            "cutter_depth_mm": settings.cutter_depth_mm,
            "stamp_impression_mm": settings.cutter_depth_mm / 4,
            "backing_mm": 3.0,
            "source_pixels": list(design.source_size),
            "warnings": design.warnings,
            "preview_svg": preview_svg(design),
            "settings": {
                "size_mm": settings.size_mm,
                "threshold": settings.threshold,
                "invert": settings.invert,
                "bolden_mm": settings.bolden_mm,
            },
            "tools": {},
        }
        for kind, mesh in meshes.items():
            filename = f"{name}-{kind}.stl"
            mesh.export(destination / filename, file_type="stl")
            metadata["tools"][kind] = {
                "url": f"/api/designs/{design_id}/{kind}",
                "filename": filename,
                "dimensions_mm": mesh.extents.tolist(),
                "watertight": bool(mesh.is_watertight),
                "triangles": len(mesh.faces),
            }
        (destination / "design.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        with ZipFile(destination / "tools.zip", "w", ZIP_DEFLATED) as archive:
            for path in destination.iterdir():
                if path.suffix in {".stl", ".json"}:
                    archive.write(path, path.name)
        app.logger.info("Generated design %s (%s), cookie %.2f x %.2f mm", design_id, name, *design.dimensions)
        return jsonify(metadata)
    finally:
        generation_lock.release()


@app.post("/api/sheets/detect")
def detect_sheet():
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        raise DesignError("Choose an image before detecting designs.")
    settings = parse_settings()
    image = read_image(upload.read())
    regions = detect_regions(image, settings)
    sheet_id = uuid.uuid4().hex
    directory = Path(app.config["OUTPUT_DIR"]) / "sheets" / sheet_id
    directory.mkdir(parents=True)
    image.save(directory / "image.png")
    app.logger.info("Detected %s regions in sheet %s", len(regions), sheet_id)
    return jsonify(id=sheet_id, width=image.width, height=image.height, regions=regions,
                   image_url=f"/api/sheets/{sheet_id}/image", max_crops=MAX_CROPS)


def sheet_directory(sheet_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", sheet_id):
        raise DesignError("Invalid sheet ID. Upload the sheet again.")
    directory = Path(app.config["OUTPUT_DIR"]) / "sheets" / sheet_id
    if not (directory / "image.png").is_file():
        raise DesignError("Sheet not found. Upload the sheet again.")
    return directory


@app.get("/api/sheets/<sheet_id>/image")
def sheet_image(sheet_id: str):
    return send_file(sheet_directory(sheet_id) / "image.png", mimetype="image/png")


@app.post("/api/bundles")
def create_bundle():
    payload = request.get_json()
    ids = payload.get("design_ids") if isinstance(payload, dict) else None
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_CROPS
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{32}", item) for item in ids)
            or len(set(ids)) != len(ids)):
        raise DesignError(f"Choose between 1 and {MAX_CROPS} distinct generated designs.")
    directories = [Path(app.config["OUTPUT_DIR"]) / item for item in ids]
    if any(not (directory / "design.json").is_file() for directory in directories):
        raise DesignError("A selected design no longer exists. Generate it again.")
    bundle_id = uuid.uuid4().hex
    directory = Path(app.config["OUTPUT_DIR"]) / "bundles" / bundle_id
    directory.mkdir(parents=True)
    with ZipFile(directory / "bundle.zip", "w", ZIP_DEFLATED) as archive:
        for index, source in enumerate(directories, 1):
            metadata = json.loads((source / "design.json").read_text(encoding="utf-8"))
            folder = f'{index:02d}-{metadata["name"]}'
            for path in source.iterdir():
                if path.suffix in {".stl", ".json"}:
                    archive.write(path, f"{folder}/{path.name}")
    return jsonify(url=f"/api/bundles/{bundle_id}/download")


@app.get("/api/bundles/<bundle_id>/download")
def download_bundle(bundle_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", bundle_id):
        return jsonify(error="Bundle not found."), 404
    path = Path(app.config["OUTPUT_DIR"]) / "bundles" / bundle_id / "bundle.zip"
    if not path.is_file():
        return jsonify(error="Bundle not found."), 404
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name="cookie-sheet-tools.zip")


@app.get("/api/designs/<design_id>/<kind>")
def download(design_id: str, kind: str):
    if not re.fullmatch(r"[0-9a-f]{32}", design_id) or kind not in {"cutter", "stamp", "combined", "bundle"}:
        return jsonify(error="Design not found."), 404
    directory = Path(app.config["OUTPUT_DIR"]) / design_id
    manifest = directory / "design.json"
    if not manifest.is_file():
        return jsonify(error="Design not found."), 404
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if kind != "bundle" and kind not in metadata["tools"]:
        return jsonify(error="This design has no combined STL. Generate it again to add one."), 404
    filename = "tools.zip" if kind == "bundle" else metadata["tools"][kind]["filename"]
    return send_file(
        directory / filename,
        mimetype="application/zip" if kind == "bundle" else "model/stl",
        as_attachment=True,
        download_name=f'{metadata["name"]}-tools.zip' if kind == "bundle" else filename,
    )


@app.before_request
def protect_bambuddy():
    if not request.path.startswith("/api/bambuddy"):
        return None
    if urlsplit(request.host_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        return jsonify(error="Bambuddy integration is available only through localhost."), 403
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            return jsonify(error="Cross-origin Bambuddy requests are not allowed."), 403
        if not request.is_json:
            return jsonify(error="Bambuddy requests must use application/json."), 415
    return None


@app.get("/api/bambuddy")
def bambuddy_status():
    path = Path(app.config["BAMBUDDY_CONFIG"])
    if not path.exists():
        return jsonify(configured=False, message="Link Bambuddy using bambuddy.local.json. See the README setup.")
    config = load_config(path)
    return jsonify(
        configured=True, library_url=config.library_url,
        message="Bambuddy configured. Test the connection before sending models.",
    )


def bambuddy_payload() -> dict:
    payload = request.get_json()
    if not isinstance(payload, dict):
        raise BambuddyError("Bambuddy requests must contain a JSON object.")
    return payload


@app.post("/api/bambuddy/test")
def test_bambuddy():
    bambuddy_payload()
    config = load_config(Path(app.config["BAMBUDDY_CONFIG"]))
    BambuddyClient(config).check_connection()
    return jsonify(message="Connected to Bambuddy's library.", library_url=config.library_url)


@app.post("/api/bambuddy/send")
def send_to_bambuddy():
    payload = bambuddy_payload()
    design_id = payload.get("design_id")
    kind = payload.get("kind")
    if not isinstance(design_id, str) or not re.fullmatch(r"[0-9a-f]{32}", design_id):
        raise BambuddyError("Choose a generated design to send.")
    if kind not in ("cutter", "stamp", "pair", "combined"):
        raise BambuddyError("Choose cutter, stamp, pair or combined STL.")
    config = load_config(Path(app.config["BAMBUDDY_CONFIG"]))
    directory = Path(app.config["OUTPUT_DIR"]) / design_id
    manifest = directory / "design.json"
    if not manifest.is_file():
        return jsonify(error="Design not found."), 404
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    kinds = ("cutter", "stamp") if kind == "pair" else (kind,)
    if any(tool not in metadata["tools"] for tool in kinds):
        raise BambuddyError("Generate this design again to add the combined STL.")
    models = [directory / metadata["tools"][tool]["filename"] for tool in kinds]
    if not bambuddy_lock.acquire(blocking=False):
        return jsonify(error="Another Bambuddy transfer is running. Try again shortly."), 409
    try:
        client = BambuddyClient(config)
        uploaded = []
        for model in models:
            try:
                uploaded.append(client.upload(model))
            except BambuddyError as error:
                completed = ", ".join(item["filename"] for item in uploaded) or "none confirmed"
                raise BambuddyError(
                    f"{error} Files already sent: {completed}. "
                    "Check Bambuddy File Manager before retrying to avoid duplicates."
                ) from error
        app.logger.info("Sent design %s (%s) to Bambuddy library", design_id, kind)
        return jsonify(files=uploaded, library_url=config.library_url, started=False,
                       message="Models sent to Bambuddy. Open File Manager to slice and manage printing. No print was started.")
    finally:
        bambuddy_lock.release()


@app.errorhandler(BambuddyError)
def bambuddy_error(error: BambuddyError):
    app.logger.warning("Bambuddy operation rejected: %s", error)
    return jsonify(error=str(error)), 400


@app.errorhandler(DesignError)
def invalid_design(error: DesignError):
    app.logger.warning("Design rejected: %s", error)
    return jsonify(error=str(error)), 400


@app.errorhandler(RequestEntityTooLarge)
def too_large(error):
    app.logger.warning("Upload exceeded size limit")
    return jsonify(error="Upload is too large. Choose an image below 12 MB."), 413


@app.errorhandler(Exception)
def unexpected_error(error: Exception):
    if isinstance(error, HTTPException):
        return jsonify(error=error.description), error.code
    app.logger.exception("Studio operation failed")
    return jsonify(error="Operation failed unexpectedly. See the server log for details."), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5400, debug=False)
