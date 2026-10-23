# Vanna's Cookie Cutter Studio

A local web app that turns black-and-white raster artwork into two printable,
watertight STL solids: an outer cookie cutter and a matching artwork stamp.
No cloud services, CDN scripts, accounts, or external image uploads.
The interface and previews use a warm brown-and-maroon palette on cream.

## Start

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5000. Upload an image or select one of the four included
dinosaur examples, then select **Generate my cookie tools**. Download individual
STLs or a ZIP containing both tools and their measurements. The artwork preview
shows the final cookie; the interactive 3D previews show the printable tools.
Drag to rotate and scroll to zoom. 3D preview requires WebGL.

## Dimensions and operation

- The **cookie silhouette's longest dimension defaults to 3.5 inches (88.9 mm)**.
  The traced silhouette retains its aspect ratio; white image margins do not
  affect sizing. Change the longest dimension between 1 and 6 inches.
- Cutter depth defaults to **12 mm**. Stamp relief is always **one quarter of
  cutter depth**: **3 mm** by default, not one quarter inch.
- The cutter's inner aperture follows the silhouette. Walls grow outward:
  a 0.65 mm cutting edge, 1.2 mm structural wall, and 3 mm grip flange.
  The final 1.5 mm of height has only the thin cutting wall; the grip is 2 mm tall.
  Consequently, the cutter's overall longest dimension is about **94.9 mm** at
  the default cookie size, rather than 88.9 mm.
- The stamp has a **3 mm-thick solid backing plate**, extending 2 mm beyond the
  silhouette, plus the raised black artwork. Its default total height is 6 mm,
  and overall longest dimension about 92.9 mm. All disconnected artwork details
  are joined by the backing, not exported as loose parts.
- Stamp artwork is clipped 0.45 mm inside the cookie outline. Line boldening
  expands black features by 0.15 mm by default and can be adjusted to 0–0.6 mm.
  It is not a guarantee of minimum printable feature width: inspect fine details
  in your slicer for your nozzle and layer settings.
- **Both tools are mirrored in the STL**, with flat grip/backing faces at Z=0.
  Print flat, backing-side down, with no supports normally required. Turn the
  tools over to press so the cookie matches the original artwork.
- Roll dough thicker than the intended impression (3 mm by default). Cut the
  outline, **remove the cutter**, then align and press the stamp until its
  backing meets the dough surface. The backing limits the nominal indentation;
  actual dough compression varies. This is not an in-cutter guided plunger.

All STL coordinates are **millimeters** (STL itself has no unit metadata).
Import at 100% scale in your slicer; do not auto-fit to 3.5 inches, which would
shrink the cookie aperture to account for the grip.

## Artwork tips

Use a single connected outer silhouette with black details on white or
transparent background. PNG works best. JPEG, WebP, and BMP are also supported.
Uploads are limited to 12 MB and 16 megapixels; images are traced at up to 1200
pixels on their longest side. Transparent pixels are composited onto white.
EXIF rotation is respected. Use the invert switch for light-on-dark artwork.

Tiny gaps in the outside contour are closed at a 0.35 mm radius. Tiny detached
background specks are ignored with a warning; significant detached shapes are
rejected rather than silently omitted. Open line drawings cannot reveal an
intended closed cookie boundary: connect the outline first. Use threshold
adjustments to clean up grayscale artwork; regenerate after any settings change.

## Generated files

Each generation persists under `output/<design-id>/`:

- `<image-name>-cutter.stl`
- `<image-name>-stamp.stl`
- `design.json` — dimensions, image settings, warnings, and mesh information
- `tools.zip`

Downloads remain available after a server restart while these files exist.
Generated files are not automatically deleted; remove individual design
directories when no longer needed. Only one design is generated at a time to
keep geometry processing bounded. Failures appear in the UI and timestamped
server logs. The server binds to loopback only and is intended for local use,
not public deployment.

## Check

```bash
.venv/bin/python -m pytest -q
```

Tests cover all four supplied images, STL export/reload, watertight single-body
meshes, actual dimensions, aspect ratio in both orientations, mirrored relief,
quarter-depth impressions, invalid inputs, API downloads, and persisted bundles.

## Food-contact note

STL validity does not certify food safety. Choose materials and equipment
appropriate for food contact, account for print porosity and cleaning, and
consult the relevant material guidance before using printed tools with food.
