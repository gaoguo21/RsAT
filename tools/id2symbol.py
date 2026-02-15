import io
import os
import tempfile
import time
import uuid
import subprocess

import pandas as pd
from flask import Blueprint, jsonify, request, send_file, current_app
from tools.upload_utils import is_allowed_filename, save_bytes

id2symbol_bp = Blueprint("id2symbol", __name__)

_RESULTS = {}
_TTL_SECONDS = 30 * 60


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _cleanup_results() -> None:
    now = time.time()
    stale = [k for k, v in _RESULTS.items() if now - v["ts"] > _TTL_SECONDS]
    for k in stale:
        _RESULTS.pop(k, None)


def _run_r_id2symbol(input_path: str, organism: str) -> bytes:
    script_path = os.path.join(_project_root(), "r_scripts", "id2symbol.R")
    if not os.path.exists(script_path):
        raise RuntimeError(f"R script not found: {script_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "id2symbol_results.csv")
        cmd = ["Rscript", script_path, input_path, out_path, organism]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            msg = (proc.stderr or "").strip() or (proc.stdout or "").strip() or "Rscript failed."
            raise RuntimeError(msg)
        with open(out_path, "rb") as f:
            return f.read()


def _summarize_mapping(csv_bytes: bytes) -> tuple[int, int, int]:
    df = pd.read_csv(io.BytesIO(csv_bytes))
    df.columns = [str(c).strip().lower() for c in df.columns]
    symbol_col = None
    for col in df.columns:
        if col in {"symbol", "gene_symbol", "genesymbol"}:
            symbol_col = col
            break
    if not symbol_col:
        return (0, 0, 0)

    total = len(df)
    unmapped = int(df[symbol_col].isna().sum())
    mapped = total - unmapped
    return (mapped, total, unmapped)


@id2symbol_bp.post("/run")
def run():
    _cleanup_results()

    if not request.content_type or not request.content_type.startswith("multipart/form-data"):
        return jsonify({"error": "Upload a file using multipart/form-data."}), 400

    file = request.files.get("file")
    organism = (request.form.get("organism") or "").lower().strip()

    if organism not in {"human", "mouse"}:
        return jsonify({"error": "Organism must be Human or Mouse."}), 400
    if not file:
        return jsonify({"error": "No file uploaded."}), 400
    data_exts = {".tsv", ".txt", ".csv"}
    if not is_allowed_filename(file.filename or "", data_exts):
        return jsonify({"error": "Invalid file type. Use .tsv, .txt, or .csv only."}), 400

    raw = file.read()
    if not raw:
        return jsonify({"error": "Uploaded file is empty."}), 400

    input_path = save_bytes(current_app.config["UPLOAD_FOLDER"], file.filename or "", raw, ".txt")

    try:
        csv_bytes = _run_r_id2symbol(input_path, organism)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        try:
            os.remove(input_path)
        except OSError:
            pass

    mapped, total, unmapped = _summarize_mapping(csv_bytes)

    result_id = str(uuid.uuid4())
    _RESULTS[result_id] = {"csv": csv_bytes, "ts": time.time()}

    return jsonify(
        {
            "ok": True,
            "download_url": f"/api/id2symbol/download/{result_id}",
            "mapped": mapped,
            "total": total,
            "unmapped": unmapped,
        }
    )


@id2symbol_bp.get("/download/<result_id>")
def download(result_id: str):
    _cleanup_results()
    if result_id not in _RESULTS:
        return jsonify({"error": "Results expired. Please run again."}), 404

    buf = io.BytesIO(_RESULTS[result_id]["csv"])
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="id2symbol_results.csv",
        mimetype="text/csv",
    )
