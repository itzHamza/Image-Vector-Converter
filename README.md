# Image → Vector (SVG) Converter

A small Flask web app that converts a raster image (ideally a PNG with a
transparent background) into a scalable SVG using [VTracer](https://github.com/visioncortex/vtracer)
(the same Rust-based vectorization engine, exposed via its official Python bindings).

## Features

- Simple drag-and-drop web UI (no frontend framework, just HTML/CSS/JS)
- Three conversion presets tuned for different source material (see below)
- Live SVG preview + one-click download
- JSON API (`/api/convert`) if you want to call it from another service later
- Ships with a `Dockerfile` ready for Dokploy (or any Docker host)

## Why presets instead of raw VTracer settings

VTracer's default color-tracing mode looks at every distinct RGB value in
the image. PNGs with a transparent background almost always have
anti-aliased edges — pixels that blend from full color to fully transparent
— and each of those blended shades counts as a "different color". On
something like calligraphy or a wordmark this can produce **thousands of
tiny, jagged paths** and a multi-megabyte SVG that looks noisy instead of
clean.

To fix that, this app preprocesses the image before handing it to VTracer:

- **Text / logo — single color** (default): binarizes the alpha channel
  (a pixel is either "in" or "out", no blend), removes small
  speckle/holes with morphological opening/closing, traces the clean
  silhouette in VTracer's `binary` mode, then recolors the result back to
  the artwork's original dominant color. This is what turned a real-world
  test case from **9,432 paths / ~2MB** down to **30 paths / ~50KB** with
  a visually near-identical result.
- **Logo — multiple flat colors**: same alpha cleanup, plus color
  quantization down to a small palette (default 8 colors) before tracing
  in color mode. Use this for logos with a handful of distinct flat
  colors.
- **Photo / complex artwork**: skips the cleanup pipeline and passes the
  image to VTracer close to its raw defaults, with the original manual
  parameter controls (color mode, curve style, precision, speckle
  filtering) exposed in the UI. Use this for photos, gradients, or dense
  illustration where per-pixel color detail actually matters.

## Project structure

```
vectorizer/
├── app.py              # Flask app + preprocessing + VTracer conversion logic
├── templates/
│   └── index.html      # Upload UI with preset selector
├── requirements.txt
├── Dockerfile
└── README.md
```

## Running locally

```bash
python3 -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://localhost:8000

## Running with Docker

```bash
docker build -t image-vectorizer .
docker run -p 8000:8000 image-vectorizer
```

Then open http://localhost:8000

## Deploying on Dokploy

1. Push this folder to a Git repo (or a subfolder of an existing repo).
2. In Dokploy, create a new **Application** → source: your repo → build type: **Dockerfile**.
3. Point the build path to this folder (if it's a subfolder, set the "Docker context"/build path accordingly).
4. Set the exposed port to **8000** (matches `EXPOSE 8000` in the Dockerfile).
5. Deploy. Dokploy will build the image and run it with the `gunicorn` command already baked into the `Dockerfile`.
6. (Optional) Attach a domain/subdomain in Dokploy's domain settings, e.g. `vectorizer.tbib.space`.

No environment variables are required to run. If you want to change the
internal port, set `PORT` — but you'd also need to update `EXPOSE`/Dokploy's
port mapping to match.

## API

### `POST /api/convert`

Multipart form-data:

| Field    | Type   | Default        | Notes                                                        |
|----------|--------|----------------|---------------------------------------------------------------|
| `image`  | file   | —              | PNG / JPG / WEBP / BMP, max 15MB                              |
| `preset` | string | `logo-single`  | `logo-single`, `logo-multi`, or `photo`                       |
| `colors` | int    | `8`            | Only used by `logo-multi` — target palette size               |

When `preset=photo`, these additional fields are accepted (same meaning as
raw VTracer parameters):

| Field              | Type   | Default   | Notes                                      |
|--------------------|--------|-----------|---------------------------------------------|
| `colormode`        | string | `color`   | `color` or `binary`                        |
| `mode`             | string | `spline`  | `spline` (smooth), `polygon` (sharp), `none` (pixel) |
| `color_precision`  | int    | `6`       | Higher = more colors preserved             |
| `filter_speckle`   | int    | `4`       | Higher = removes more small noise/specks   |
| `corner_threshold` | int    | `60`      | Lower = sharper corners detected           |
| `path_precision`   | int    | `8`       | Decimal precision of SVG path coordinates  |

Response:

```json
{
  "job_id": "…",
  "svg": "<svg ...>...</svg>",
  "download_url": "/api/download/<job_id>"
}
```

### `GET /api/download/<job_id>`

Downloads the generated SVG file (only available for a short time — the
server does not keep uploads/outputs long-term; wire up S3/R2 storage if you
need persistence).

### `GET /api/health`

Simple health check, returns `{"status": "ok"}`. Useful for Dokploy's health
check config if you want one.

## Notes on quality / tuning

- For **calligraphy, wordmarks, single-color logos**: use the default
  "Text / logo — single color" preset. It's specifically built to handle
  anti-aliased transparent edges cleanly.
- For **multi-color flat logos**: use "Logo — multiple flat colors" and
  adjust the color count if colors are getting merged or split incorrectly.
- For **photos or complex art**: use "Photo / complex artwork" and raise
  `color_precision` (6–8); consider `mode=polygon` for sharper, more
  geometric shapes instead of smooth curves.

## Next steps (optional ideas)

- Swap local disk storage for Cloudflare R2 if you want persistent history.
- Add auth (e.g. Clerk) if this needs to sit behind a login instead of being
  open on its own subdomain.
- Add a batch-upload mode if you'll be converting many assets at once.