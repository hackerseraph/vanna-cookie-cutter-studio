"""Local-only web interface for the cookie cutter generator."""

import json
import logging
from pathlib import Path
import re
import threading
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from geometry import DesignError, Settings, build_meshes, preview_svg, trace_image


ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["OUTPUT_DIR"] = ROOT / "output"
generation_lock = threading.Lock()
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
    image = request.files.get("image")
    if image is None or not image.filename:
        raise DesignError("Choose an image before generating.")
    settings = parse_settings()
    if not generation_lock.acquire(blocking=False):
        return jsonify(error="Another design is being generated. Please try again shortly."), 409
    try:
        design = trace_image(image.read(), settings)
        meshes = build_meshes(design)
        design_id = uuid.uuid4().hex
        name = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(image.filename).stem).strip("-")[:60] or "cookie"
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


@app.get("/api/designs/<design_id>/<kind>")
def download(design_id: str, kind: str):
    if not re.fullmatch(r"[0-9a-f]{32}", design_id) or kind not in {"cutter", "stamp", "bundle"}:
        return jsonify(error="Design not found."), 404
    directory = Path(app.config["OUTPUT_DIR"]) / design_id
    manifest = directory / "design.json"
    if not manifest.is_file():
        return jsonify(error="Design not found."), 404
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    filename = "tools.zip" if kind == "bundle" else metadata["tools"][kind]["filename"]
    return send_file(
        directory / filename,
        mimetype="application/zip" if kind == "bundle" else "model/stl",
        as_attachment=True,
        download_name=f'{metadata["name"]}-tools.zip' if kind == "bundle" else filename,
    )


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
    app.logger.exception("Design operation failed")
    return jsonify(error="Generation failed unexpectedly. See the server log for details."), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
