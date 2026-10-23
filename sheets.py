"""Sheet region detection and validated crops in oriented source pixels."""

from io import BytesIO
import math

import cv2
import numpy as np
from PIL import Image, ImageOps

from geometry import DesignError, Settings


MAX_CROPS = 24


def detect_regions(image: Image.Image, settings: Settings) -> list[dict]:
    settings.validate()
    gray = np.asarray(image.convert("L"))
    mask = gray > settings.threshold if settings.invert else gray < settings.threshold
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if len(contours) > 10_000:
        raise DesignError("The sheet has too much noise. Clean the background or adjust the threshold.")
    areas = [cv2.contourArea(contour) for contour in contours]
    cutoff = max(20, max(areas, default=0) * 0.005)
    boxes = [cv2.boundingRect(contour) for contour, area in zip(contours, areas) if area >= cutoff]
    if len(boxes) > MAX_CROPS:
        raise DesignError(f"Found more than {MAX_CROPS} regions. Split the sheet into smaller uploads.")
    # Group roughly aligned objects into rows for a predictable reading order.
    boxes.sort(key=lambda box: box[1] + box[3] / 2)
    rows = []
    for box in boxes:
        center = box[1] + box[3] / 2
        if rows and abs(center - rows[-1][0]) < min(box[3], rows[-1][1][0][3]) / 2:
            rows[-1][1].append(box)
        else:
            rows.append((center, [box]))
    regions = []
    for _, row in rows:
        for x, y, width, height in sorted(row):
            regions.append({
                "x": x, "y": y, "width": width, "height": height,
                "name": f"Design {len(regions) + 1}", "size_mm": settings.size_mm,
            })
    return regions


def crop_image(image: Image.Image, crop: object, invert: bool = False) -> bytes:
    if not isinstance(crop, dict):
        raise DesignError("Crop must contain x, y, width and height in source pixels.")
    values = [crop.get(key) for key in ("x", "y", "width", "height")]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise DesignError("Crop coordinates must be finite numbers.")
    x, y, width, height = values
    if x < 0 or y < 0 or width < 3 or height < 3 or x + width > image.width or y + height > image.height:
        raise DesignError("Crop must be inside the image and at least 3 pixels wide and high.")
    cropped = image.crop((math.floor(x), math.floor(y), math.ceil(x + width), math.ceil(y + height)))
    cropped = ImageOps.expand(cropped, border=2, fill="black" if invert else "white")
    output = BytesIO()
    cropped.save(output, format="PNG")
    return output.getvalue()
