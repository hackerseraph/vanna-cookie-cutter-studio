# Vanna's Cookie Cutter Studio

A local web app that turns black-and-white raster artwork into two printable,
watertight STL solids: an outer cookie cutter and a matching artwork stamp.
No cloud services, CDN scripts, or external image uploads. Optional Bambuddy
integration sends generated STL models only to your configured local server.
The interface and previews use a warm brown-and-maroon palette on cream.

## Start

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5400. Upload an image or select one of the four included
dinosaur examples, then select **Generate my cookie tools**. Download individual
STLs or a ZIP containing both tools and their measurements. The artwork preview
shows the final cookie; the interactive 3D previews show the printable tools.
Drag to rotate and scroll to zoom. 3D preview requires WebGL.

## Link to Bambuddy on your local network

[Bambuddy](https://github.com/maziggy/bambuddy) manages the printer connection,
slicing and printing. This studio can send the cutter, stamp or both STLs to
Bambuddy's **File Manager** over its REST API; it does not install a slicer,
send printer commands or start prints.

1. Run Bambuddy on this computer or another computer on your LAN. Note its
   address, such as `http://192.168.1.100:8000` (use your actual IP and port).
2. Copy the configuration template:
   ```bash
   cp bambuddy.example.json bambuddy.local.json
   chmod 600 bambuddy.local.json
   ```
3. Edit `bambuddy.local.json`:
   - `url`: Bambuddy's base URL, without `/api/v1` or `/files`. Use a private
     LAN or loopback **IP address**, not a hostname; reverse-proxy base paths
     are supported. HTTP and HTTPS are accepted. HTTPS certificates must be
     trusted by this computer; certificate verification is never disabled.
   - `api_key`: an API key created in Bambuddy with library read and upload
     permissions. Leave empty only if your trusted LAN instance permits
     unauthenticated API access. Keys remain server-side and are never sent
     to this studio's browser UI. The local configuration is ignored by Git.
   - `folder_id`: optional numeric Bambuddy library folder ID, or `null` for
     the root folder. Use a writable folder.
4. Generate a design, then use **Continue in Bambuddy** below the downloads.
   Select **Reload local configuration**, **Test connection**, choose the
   models, and select **Send models to Bambuddy**. No restart is needed after
   changing the configuration.
5. Select **Open Bambuddy File Manager** to inspect the models and choose the
   correct printer, nozzle, filament and slicing settings in Bambuddy. Its
   slicing service must be configured separately for in-Bambuddy slicing;
   otherwise download the STLs and use Bambu Studio, then import the sliced
   plate into Bambuddy. Inspect the toolpaths before starting a print.

The integration uses `GET /api/v1/library/folders` for the connection test and
multipart `POST /api/v1/library/files` for uploads (`X-API-Key` authentication).
It requires a Bambuddy version providing these endpoints. Sending both models
uploads two separate files, not a prearranged plate. Repeated sends may create
duplicates; duplicate warnings from Bambuddy are displayed. If a transfer fails
partway, already-sent filenames are reported. Check File Manager before retrying
an interrupted transfer, since a timeout does not guarantee the upload failed.
Unreachable hosts, authentication errors and incompatible API responses appear
in the UI and server logs without exposing the API key.

Only explicit sends transfer models. Original images are never sent.
Keep both services on a trusted network, especially when using HTTP, which
does not encrypt the API key. No LAN discovery or automatic printing occurs.
The studio still binds only to `127.0.0.1:5400`; it reaches Bambuddy over the LAN
without exposing the studio itself to the network.

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
quarter-depth impressions, invalid inputs, API downloads, persisted bundles,
and the Bambuddy API contract (using a local test server; no printer required).

## Food-contact note

STL validity does not certify food safety. Choose materials and equipment
appropriate for food contact, account for print porosity and cleaning, and
consult the relevant material guidance before using printed tools with food.
