"""
Image → Vector (SVG) converter
Simple Flask web app that lets a user upload a PNG (ideally with a
transparent background) and converts it to an SVG using VTracer.
"""

import io
import os
import uuid
import logging

import vtracer
from flask import Flask, request, render_template, send_file, jsonify
from werkzeug.utils import secure_filename
from PIL import Image

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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/convert", methods=["POST"])
def convert():
    """
    Accepts an uploaded image and vtracer parameters, returns SVG.
    Form fields (all optional, sensible defaults applied):
      - colormode: "color" | "binary"
      - hierarchical: "stacked" | "cutout"
      - mode: "spline" | "polygon" | "none"
      - filter_speckle: int
      - color_precision: int
      - corner_threshold: int
      - length_threshold: float
      - splice_threshold: int
      - path_precision: int
    """
    if "image" not in request.files:
        return jsonify({"error": "No file part named 'image'"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    job_id = uuid.uuid4().hex
    safe_name = secure_filename(file.filename)
    ext = safe_name.rsplit(".", 1)[1].lower()

    input_path = os.path.join(UPLOAD_DIR, f"{job_id}.{ext}")
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}.svg")

    file.save(input_path)

    # Normalize to PNG so vtracer + transparency handling is consistent
    normalized_path = os.path.join(UPLOAD_DIR, f"{job_id}_norm.png")
    try:
        with Image.open(input_path) as img:
            img = img.convert("RGBA")
            img.save(normalized_path, "PNG")
    except Exception as e:
        logger.exception("Failed to open/normalize image")
        return jsonify({"error": f"Could not read image: {e}"}), 400

    # Collect vtracer params from form, with defaults tuned for logos/icons
    params = {
        "colormode": request.form.get("colormode", "color"),
        "hierarchical": request.form.get("hierarchical", "stacked"),
        "mode": request.form.get("mode", "spline"),
        "filter_speckle": int(request.form.get("filter_speckle", 4)),
        "color_precision": int(request.form.get("color_precision", 6)),
        "layer_difference": int(request.form.get("layer_difference", 16)),
        "corner_threshold": int(request.form.get("corner_threshold", 60)),
        "length_threshold": float(request.form.get("length_threshold", 4.0)),
        "max_iterations": int(request.form.get("max_iterations", 10)),
        "splice_threshold": int(request.form.get("splice_threshold", 45)),
        "path_precision": int(request.form.get("path_precision", 8)),
    }

    try:
        vtracer.convert_image_to_svg_py(
            normalized_path,
            output_path,
            **params,
        )
    except Exception as e:
        logger.exception("vtracer conversion failed")
        return jsonify({"error": f"Conversion failed: {e}"}), 500
    finally:
        # Clean up input files, keep only the SVG briefly for download
        for p in (input_path, normalized_path):
            if os.path.exists(p):
                os.remove(p)

    if not os.path.exists(output_path):
        return jsonify({"error": "Conversion produced no output"}), 500

    with open(output_path, "r", encoding="utf-8") as f:
        svg_content = f.read()

    return jsonify({
        "job_id": job_id,
        "svg": svg_content,
        "download_url": f"/api/download/{job_id}",
    })


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
