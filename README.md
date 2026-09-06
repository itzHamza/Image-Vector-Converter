# Image → Vector (SVG) Converter

A small Flask web app that converts a raster image (ideally a PNG with a
transparent background) into a scalable SVG using [VTracer](https://github.com/visioncortex/vtracer)
(the same Rust-based vectorization engine, exposed via its official Python bindings).

## Features

- Simple drag-and-drop web UI (no frontend framework, just HTML/CSS/JS)
- Adjustable VTracer parameters (color mode, curve style, precision, speckle filtering)
- Live SVG preview + one-click download
- JSON API (`/api/convert`) if you want to call it from another service later
- Ships with a `Dockerfile` ready for Dokploy (or any Docker host)

## Project structure

```
vectorizer/
├── app.py              # Flask app + VTracer conversion logic
├── templates/
│   └── index.html      # Upload UI
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

| Field              | Type   | Default   | Notes                                      |
|--------------------|--------|-----------|---------------------------------------------|
| `image`            | file   | —         | PNG / JPG / WEBP / BMP, max 15MB           |
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

- For **logos / flat icons**, keep `mode=spline`, low `filter_speckle`
  (2–4), and moderate `color_precision` (4–6) for the cleanest result.
- For **photos or complex art**, raise `color_precision` (6–8) and consider
  `mode=polygon` if you want sharper, more geometric shapes instead of smooth
  curves.
- The app converts everything to RGBA PNG internally before tracing, so
  transparency is preserved correctly regardless of the original file type.

## Next steps (optional ideas)

- Swap local disk storage for Cloudflare R2 if you want persistent history.
- Add auth (e.g. Clerk) if this needs to sit behind a login instead of being
  open on its own subdomain.
- Add a batch-upload mode if you'll be converting many assets at once.
