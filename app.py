"""
Image → Vector (SVG) converter
Flask web app that lets a user upload a PNG (ideally with a transparent
background) and converts it to an SVG using VTracer.

Two modes are supported:

- "photo"    : passes the image to VTracer more or less as-is. Good for
               photos, gradients, multi-tone artwork.
- "logo"     : a preprocessing pipeline aimed at flat-color logos, text,
               and calligraphy. Anti-aliased transparent edges get
               binarized and despeckled before tracing, which avoids the
               "thousands of tiny paths" problem VTracer's default color
               mode produces on soft/antialiased edges. Supports both
               single-color and multi-color flat artwork.
"""

import os
import uuid
import logging

import numpy as np
import vtracer
from flask import Flask, request, render_template, send_file, jsonify
from werkzeug.utils import secure_filename
from PIL import Image, ImageFilter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(APP_ROOT, "uploads")
OUTPUT_DIR = os.path.join(APP_ROOT, "outputs")
MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # 15 MB
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp"}

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def clean_mask(alpha: np.ndarray, alpha_threshold: int = 128) -> np.ndarray:
    """
    Binarize an alpha channel and remove small speckle/holes via
    morphological opening + closing. Returns a uint8 0/255 mask.
    """
    binary = (alpha >= alpha_threshold).astype(np.uint8) * 255
    mask_img = Image.fromarray(binary, "L")

    # Median filter smooths jagged single-pixel noise from anti-aliasing
    mask_img = mask_img.filter(ImageFilter.MedianFilter(size=5))
    # Closing (max then min): fills small holes inside solid regions
    mask_img = mask_img.filter(ImageFilter.MaxFilter(size=3)).filter(ImageFilter.MinFilter(size=3))
    # Opening (min then max): removes small isolated speckles
    mask_img = mask_img.filter(ImageFilter.MinFilter(size=3)).filter(ImageFilter.MaxFilter(size=3))

    return np.array(mask_img)


def preprocess_logo_single_color(img: Image.Image) -> Image.Image:
    """
    For flat, (near) single-color artwork with a transparent/antialiased
    background (e.g. calligraphy, wordmarks, simple icons):
      1. binarize + despeckle the alpha mask
      2. find the dominant opaque color
      3. flatten to pure black-on-white using the cleaned mask
    VTracer's binary mode traces this cleanly with very few paths, and we
    recolor the result afterwards.
    Returns a black-on-white RGB image plus the dominant color is stashed
    as an attribute for the caller to use when recoloring.
    """
    arr = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3]

    solid_mask = alpha > 200
    if solid_mask.sum() == 0:
        solid_mask = alpha > 10  # fallback for very light/thin artwork

    dominant_color = arr[solid_mask][:, :3].mean(axis=0).astype(int)

    mask = clean_mask(alpha)

    bw = np.where(mask == 255, 0, 255).astype(np.uint8)  # black shape, white bg
    bw_img = Image.fromarray(bw, "L").convert("RGB")
    bw_img.info["dominant_color"] = tuple(int(c) for c in dominant_color)
    return bw_img


def preprocess_logo_multi_color(img: Image.Image, n_colors: int = 8) -> Image.Image:
    """
    For flat multi-color logos: cleans/binarizes the alpha mask (so the
    silhouette is crisp), then quantizes the opaque colors down to a small
    palette to avoid anti-aliasing gradient explosion, and flattens onto
    white so VTracer's color mode has a clean image to trace.

    n_colors is clamped to [2, 256]: PIL's palette-based quantize() can't
    address more than 256 colors (an 8-bit palette), so anything above
    that raises "bad number of colors" — we clamp instead of failing.
    """
    n_colors = max(2, min(256, n_colors))

    arr = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3]
    mask = clean_mask(alpha)

    rgb = arr[:, :, :3].copy()
    quant_img = Image.fromarray(rgb, "RGB").quantize(colors=n_colors, method=Image.MEDIANCUT).convert("RGB")
    quant_arr = np.array(quant_img)

    flat = np.full_like(rgb, 255)
    flat[mask == 255] = quant_arr[mask == 255]
    return Image.fromarray(flat, "RGB")


def recolor_svg(svg_text: str, from_hex: str, to_hex: str) -> str:
    return svg_text.replace(f'fill="{from_hex}"', f'fill="{to_hex}"')


def rgb_to_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/convert", methods=["POST"])
def convert():
    """
    Accepts an uploaded image and returns SVG.

    Form fields:
      - preset: "logo-single" | "logo-multi" | "photo"  (default: "logo-single")
      - colors: int, only used for "logo-multi" (default: 8)
      - filter_speckle, color_precision, corner_threshold, path_precision:
        advanced VTracer overrides, used mainly by the "photo" preset.
    """
    if "image" not in request.files:
        return jsonify({"error": "No file part named 'image'"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    preset = request.form.get("preset", "logo-single")
    job_id = uuid.uuid4().hex
    safe_name = secure_filename(file.filename)
    ext = safe_name.rsplit(".", 1)[1].lower()

    input_path = os.path.join(UPLOAD_DIR, f"{job_id}.{ext}")
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}.svg")
    file.save(input_path)

    try:
        with Image.open(input_path) as raw_img:
            raw_img.load()
            img = raw_img.convert("RGBA")
    except Exception as e:
        logger.exception("Failed to open image")
        _cleanup(input_path)
        return jsonify({"error": f"Could not read image: {e}"}), 400

    traced_path = os.path.join(UPLOAD_DIR, f"{job_id}_traced.png")
    dominant_color = None

    try:
        if preset == "logo-single":
            bw_img = preprocess_logo_single_color(img)
            dominant_color = bw_img.info.get("dominant_color")
            bw_img.save(traced_path, "PNG")
            vtracer_params = dict(
                colormode="binary", hierarchical="stacked", mode="spline",
                filter_speckle=10, color_precision=6, layer_difference=16,
                corner_threshold=60, length_threshold=4.0, max_iterations=10,
                splice_threshold=45, path_precision=6,
            )
        elif preset == "logo-multi":
            n_colors = int(request.form.get("colors", 8))
            flat_img = preprocess_logo_multi_color(img, n_colors=n_colors)
            flat_img.save(traced_path, "PNG")
            vtracer_params = dict(
                colormode="color", hierarchical="stacked", mode="spline",
                filter_speckle=8, color_precision=8, layer_difference=8,
                corner_threshold=60, length_threshold=4.0, max_iterations=10,
                splice_threshold=45, path_precision=6,
            )
        else:  # "photo": lighter touch, closer to raw VTracer defaults
            img.save(traced_path, "PNG")
            vtracer_params = dict(
                colormode=request.form.get("colormode", "color"),
                hierarchical="stacked",
                mode=request.form.get("mode", "spline"),
                filter_speckle=int(request.form.get("filter_speckle", 4)),
                color_precision=int(request.form.get("color_precision", 6)),
                layer_difference=16,
                corner_threshold=int(request.form.get("corner_threshold", 60)),
                length_threshold=4.0,
                max_iterations=10,
                splice_threshold=45,
                path_precision=int(request.form.get("path_precision", 8)),
            )

        vtracer.convert_image_to_svg_py(traced_path, output_path, **vtracer_params)

    except Exception as e:
        logger.exception("Conversion failed")
        return jsonify({"error": f"Conversion failed: {e}"}), 500
    finally:
        _cleanup(input_path, traced_path)

    if not os.path.exists(output_path):
        return jsonify({"error": "Conversion produced no output"}), 500

    with open(output_path, "r", encoding="utf-8") as f:
        svg_content = f.read()

    if preset == "logo-single" and dominant_color:
        svg_content = recolor_svg(svg_content, "#000000", rgb_to_hex(dominant_color))
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(svg_content)

    return jsonify({
        "job_id": job_id,
        "svg": svg_content,
        "download_url": f"/api/download/{job_id}",
    })


def _cleanup(*paths):
    for p in paths:
        if p and os.path.exists(p):
            os.remove(p)


@app.route("/api/download/<job_id>")
def download(job_id):
    safe_id = secure_filename(job_id)
    output_path = os.path.join(OUTPUT_DIR, f"{safe_id}.svg")
    if not os.path.exists(output_path):
        return jsonify({"error": "File not found or already cleaned up"}), 404
    return send_file(
        output_path,
        mimetype="image/svg+xml",
        as_attachment=True,
        download_name=f"{safe_id}.svg",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)