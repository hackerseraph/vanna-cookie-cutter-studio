"""Image tracing and printable, millimeter-based cookie tools."""

from dataclasses import dataclass
from io import BytesIO
import math

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from shapely import affinity, make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
import trimesh


class DesignError(ValueError):
    """An image or setting cannot produce a usable design."""


@dataclass(frozen=True)
class Settings:
    size_mm: float = 88.9
    cutter_depth_mm: float = 12.0
    threshold: int = 160
    invert: bool = False
    bolden_mm: float = 0.15

    def validate(self) -> None:
        for name, value, low, high in (
            ("Size", self.size_mm, 25.4, 152.4),
            ("Cutter depth", self.cutter_depth_mm, 6.0, 25.0),
            ("Threshold", self.threshold, 1, 254),
            ("Line boldening", self.bolden_mm, 0.0, 0.6),
        ):
            if not math.isfinite(value) or not low <= value <= high:
                raise DesignError(f"{name} must be between {low} and {high}.")


@dataclass
class Design:
    outline: Polygon
    artwork: Polygon | MultiPolygon
    settings: Settings
    warnings: list[str]
    source_size: tuple[int, int]

    @property
    def dimensions(self) -> list[float]:
        x0, y0, x1, y1 = self.outline.bounds
        return [x1 - x0, y1 - y0]


def polygons(geometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry] if not geometry.is_empty else []
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        return [part for item in geometry.geoms for part in polygons(item)]
    return []


def _contour_points(contour) -> list[tuple[float, float]]:
    return [(float(point[0][0]), float(point[0][1])) for point in contour]


def read_image(data: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "BMP"}:
                raise DesignError("Use a PNG, JPEG, WebP, or BMP image.")
            if source.width * source.height > 16_000_000:
                raise DesignError("Image is too large. Use an image below 16 megapixels.")
            image = ImageOps.exif_transpose(source).convert("RGBA")
            background = Image.new("RGBA", image.size, "white")
            background.alpha_composite(image)
            return background.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise DesignError("The image could not be read. Upload a valid raster image.") from error


def trace_image(data: bytes, settings: Settings) -> Design:
    settings.validate()
    image = read_image(data)
    source_size = image.size
    image = image.convert("L")
    image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    gray = np.asarray(image)
    mask = (gray < settings.threshold) if not settings.invert else (gray > settings.threshold)
    if not mask.any() or mask.all():
        raise DesignError("No outline found. Use contrasting artwork and background, or adjust the threshold.")
    mask = np.pad(mask.astype(np.uint8) * 255, 2)
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        raise DesignError("No outline found in this image.")
    if len(contours) > 10_000:
        raise DesignError("The image has too much noise to trace. Clean up the background or adjust the threshold.")

    ink_parts = []
    outer_parts = []
    for index, contour in enumerate(contours):
        if hierarchy[0][index][3] != -1 or len(contour) < 3:
            continue
        holes = []
        child = hierarchy[0][index][2]
        while child != -1:
            if len(contours[child]) >= 3:
                holes.append(_contour_points(contours[child]))
            child = hierarchy[0][child][0]
        outer = make_valid(Polygon(_contour_points(contour)))
        outer_parts.extend(polygons(outer))
        ink_parts.extend(polygons(make_valid(Polygon(_contour_points(contour), holes))))
    if not outer_parts:
        raise DesignError("The artwork is too thin or small to trace. Try a higher-resolution image.")

    primary = max(outer_parts, key=lambda part: part.area)
    if primary.area < 20:
        raise DesignError("The artwork is too small to trace reliably.")
    # Closing small raster gaps connects antialiased outline fragments, not separate logos.
    rough_scale = settings.size_mm / max(
        primary.bounds[2] - primary.bounds[0], primary.bounds[3] - primary.bounds[1]
    )
    outline_parts = polygons(
        unary_union(outer_parts).buffer(0.35 / rough_scale).buffer(-0.35 / rough_scale)
    )
    outline = max(outline_parts, key=lambda part: part.area)
    discarded = [part for part in outline_parts if part != outline]
    if any(part.area > outline.area * 0.01 for part in discarded):
        raise DesignError(
            "This image has separate, disconnected shapes. Use Bundle sheet: detect & edit crops "
            "to generate them separately, or crop to one connected silhouette."
        )
    outline = Polygon(outline.exterior).simplify(0.06 / rough_scale, preserve_topology=True)
    x0, y0, x1, y1 = outline.bounds
    scale = settings.size_mm / max(x1 - x0, y1 - y0)

    def normalize(shape):
        centered = affinity.translate(shape, -(x0 + x1) / 2, -(y0 + y1) / 2)
        return affinity.scale(centered, xfact=scale, yfact=-scale, origin=(0, 0))

    outline = normalize(outline)
    ink = normalize(unary_union(ink_parts))
    interior = outline.buffer(-0.45)
    if interior.is_empty:
        raise DesignError("This silhouette is too narrow for a stamp at the selected size.")
    ink = ink.buffer(settings.bolden_mm).intersection(interior)
    relief_parts = [part for part in polygons(make_valid(ink)) if part.area >= 0.02]
    if not relief_parts:
        raise DesignError("No stamp details remain. Increase the size or line boldening.")
    warnings = [
        "Fine details may need line boldening for your nozzle. Inspect the stamp preview before printing.",
        "Remove the cutter before stamping. The backing plate stops the impression at one-quarter cutter depth.",
    ]
    if discarded:
        warnings.append(f"Ignored {len(discarded)} tiny disconnected background speck(s).")
    if min(outline.bounds[2] - outline.bounds[0], outline.bounds[3] - outline.bounds[1]) < 10:
        warnings.append("This design is very narrow; inspect its strength before printing.")
    return Design(outline, unary_union(relief_parts), settings, warnings, source_size)


def _extrude(shape, height: float, z: float = 0) -> list[trimesh.Trimesh]:
    meshes = []
    for part in polygons(shape):
        mesh = trimesh.creation.extrude_polygon(part, height, engine="earcut")
        mesh.apply_translation((0, 0, z))
        meshes.append(mesh)
    return meshes


def _solid(meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    mesh = trimesh.boolean.union(meshes, engine="manifold")
    mesh.remove_unreferenced_vertices()
    if not mesh.is_watertight or not mesh.is_volume or mesh.body_count != 1:
        raise DesignError("The generated geometry is not one watertight solid. Try a cleaner outline.")
    return mesh


def build_meshes(design: Design) -> dict[str, trimesh.Trimesh]:
    # Both tools print backing-down, then turn over to press the original artwork.
    outline = affinity.scale(design.outline, xfact=-1, yfact=1, origin=(0, 0))
    artwork = affinity.scale(design.artwork, xfact=-1, yfact=1, origin=(0, 0))
    depth = design.settings.cutter_depth_mm
    edge_height = 1.5
    rim_height = 2.0
    cutter = _solid(
        _extrude(outline.buffer(3.0).difference(outline), rim_height)
        + _extrude(outline.buffer(1.2).difference(outline), depth - edge_height)
        + _extrude(outline.buffer(0.65).difference(outline), depth)
    )
    stamp = _solid(
        _extrude(outline.buffer(2.0), 3.0)
        + _extrude(artwork, depth / 4, 3.0)
    )
    return {"cutter": cutter, "stamp": stamp}


def combined_mesh(cutter: trimesh.Trimesh, stamp: trimesh.Trimesh) -> trimesh.Trimesh:
    cutter = cutter.copy()
    stamp = stamp.copy()
    cutter.apply_translation((-cutter.bounds[0, 0], -cutter.bounds[:, 1].mean(), -cutter.bounds[0, 2]))
    stamp.apply_translation((
        cutter.bounds[1, 0] + 5.0 - stamp.bounds[0, 0],
        -stamp.bounds[:, 1].mean(),
        -stamp.bounds[0, 2],
    ))
    return trimesh.util.concatenate([cutter, stamp])


def _svg_path(shape) -> str:
    commands = []
    for part in polygons(shape):
        for ring in (part.exterior, *part.interiors):
            coordinates = list(ring.coords)
            commands.append("M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in coordinates) + "Z")
    return " ".join(commands)


def preview_svg(design: Design) -> str:
    x0, y0, x1, y1 = design.outline.bounds
    margin = 5
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0-margin:.3f} '
        f'{y0-margin:.3f} {x1-x0+2*margin:.3f} {y1-y0+2*margin:.3f}" '
        'role="img" aria-label="Cookie outline and stamp artwork">'
        '<g transform="scale(1,-1)">'
        f'<path d="{_svg_path(design.outline)}" fill="#fbf3e7" stroke="#a5714b" stroke-width=".65"/>'
        f'<path d="{_svg_path(design.artwork)}" fill="#773347" fill-rule="evenodd"/>'
        '</g></svg>'
    )
