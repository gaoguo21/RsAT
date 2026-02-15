import io
import json
import os
import tempfile
import time
import uuid
import subprocess

import pandas as pd
from flask import Blueprint, jsonify, render_template, request, send_file, current_app
from tools.upload_utils import is_allowed_filename, save_bytes

deg_bp = Blueprint("deg", __name__)

_UPLOADS = {}
_RESULTS = {}
_TTL_SECONDS = 30 * 60


def _cleanup_uploads() -> None:
    now = time.time()
    stale = [k for k, v in _UPLOADS.items() if now - v["ts"] > _TTL_SECONDS]
    for k in stale:
        entry = _UPLOADS.pop(k, None)
        if entry and entry.get("path"):
            try:
                os.remove(entry["path"])
            except OSError:
                pass


def _cleanup_results() -> None:
    now = time.time()
    stale = [k for k, v in _RESULTS.items() if now - v["ts"] > _TTL_SECONDS]
    for k in stale:
        _RESULTS.pop(k, None)


def _parse_min_count(value) -> int:
    try:
        min_count = int(value)
    except (TypeError, ValueError):
        min_count = 2
    return max(0, min_count)


def _normalize_rows(rows: list[dict]) -> list[dict]:
    for row in rows:
        for key, value in row.items():
            if pd.isna(value):
                row[key] = None
    return rows


def _project_root() -> str:
    # tools/deg.py -> project root is one folder up
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_r_analysis(csv_path: str, group_map: dict, method: str, min_count: int) -> bytes:
    # Update the R script filename here if you renamed it
    # e.g. "deg.R" or "deg_de.R" etc.
    script_path = os.path.join(_project_root(), "r_scripts", "deg.R")

    if not os.path.exists(script_path):
        raise RuntimeError(f"R script not found: {script_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        meta_path = os.path.join(tmpdir, "meta.json")
        out_path = os.path.join(tmpdir, "results.csv")

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"group_map": group_map, "method": method, "min_count": min_count}, f)

        cmd = ["Rscript", script_path, csv_path, meta_path, out_path]
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode != 0:
            # show stderr, fallback to stdout
            msg = (proc.stderr or "").strip() or (proc.stdout or "").strip() or "Rscript failed."
            raise RuntimeError(msg)

        with open(out_path, "rb") as f:
            return f.read()


# ---------------------------
# DEG API endpoints
# These will be mounted under a prefix in app.py, e.g. /degapi/...
# ---------------------------

@deg_bp.post("/columns")
def columns():
    _cleanup_uploads()
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded."}), 400
    data_exts = {".tsv", ".txt", ".csv"}
    if not is_allowed_filename(file.filename or "", data_exts):
        return jsonify({"error": "Invalid file type. Use .tsv, .txt, or .csv only."}), 400

    raw = file.read()
    if not raw:
        return jsonify({"error": "Uploaded file is empty."}), 400

    try:
        df = pd.read_csv(io.BytesIO(raw), sep=None, engine="python")
    except Exception as exc:
        return jsonify({"error": f"Failed to read CSV file: {exc}"}), 400

    if df.shape[1] < 2:
        return jsonify({"error": "CSV must have gene names plus at least one sample column."}), 400

    gene_col = df.columns[0]
    sample_cols = list(df.columns[1:])

    file_id = str(uuid.uuid4())
    upload_path = save_bytes(current_app.config["UPLOAD_FOLDER"], file.filename or "", raw, ".tsv")
    _UPLOADS[file_id] = {"path": upload_path, "ts": time.time()}

    return jsonify({"file_id": file_id, "gene_col": gene_col, "sample_cols": sample_cols})


@deg_bp.post("/export")
def export():
    _cleanup_uploads()
    payload = request.get_json(silent=True) or {}
    file_id = payload.get("file_id")
    group_map = payload.get("group_map") or {}
    method = (payload.get("method") or "").lower().strip()
    min_count = _parse_min_count(payload.get("min_count"))

    if not file_id or file_id not in _UPLOADS:
        return jsonify({"error": "Upload expired or missing. Please upload again."}), 400
    if method not in {"edger", "deseq2"}:
        return jsonify({"error": "Unsupported method."}), 400

    groups = {v for v in group_map.values() if v in {"A", "B"}}
    if groups != {"A", "B"}:
        return jsonify({"error": "Select samples for both Group A and Group B."}), 400

    csv_path = _UPLOADS[file_id]["path"]
    if not csv_path or not os.path.exists(csv_path):
        return jsonify({"error": "Upload missing. Please upload again."}), 400

    try:
        result_bytes = _run_r_analysis(csv_path, group_map, method, min_count)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    buf = io.BytesIO(result_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="de_results.csv",
        mimetype="text/csv",
    )


@deg_bp.post("/analyze")
def analyze():
    _cleanup_uploads()
    _cleanup_results()

    payload = request.get_json(silent=True) or {}
    file_id = payload.get("file_id")
    group_map = payload.get("group_map") or {}
    method = (payload.get("method") or "").lower().strip()
    min_count = _parse_min_count(payload.get("min_count"))

    if not file_id or file_id not in _UPLOADS:
        return jsonify({"error": "Upload expired or missing. Please upload again."}), 400
    if method not in {"edger", "deseq2"}:
        return jsonify({"error": "Unsupported method."}), 400

    groups = {v for v in group_map.values() if v in {"A", "B"}}
    if groups != {"A", "B"}:
        return jsonify({"error": "Select samples for both Group A and Group B."}), 400

    csv_path = _UPLOADS[file_id]["path"]
    if not csv_path or not os.path.exists(csv_path):
        return jsonify({"error": "Upload missing. Please upload again."}), 400

    try:
        result_bytes = _run_r_analysis(csv_path, group_map, method, min_count)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        df = pd.read_csv(io.BytesIO(result_bytes))
    except Exception as exc:
        return jsonify({"error": f"Failed to parse results: {exc}"}), 400

    rows = _normalize_rows(df.to_dict(orient="records"))

    result_id = str(uuid.uuid4())
    _RESULTS[result_id] = {"rows": rows, "csv": result_bytes, "ts": time.time()}

    return jsonify({"result_id": result_id, "total_rows": len(rows)})


@deg_bp.get("/results/<result_id>")
def results_page(result_id: str):
    _cleanup_results()
    if result_id not in _RESULTS:
        return render_template("results.html", result_id=None, error="Results expired. Please run again.")
    return render_template("results.html", result_id=result_id, error=None)


@deg_bp.get("/results/<result_id>/data")
def results_data(result_id: str):
    _cleanup_results()
    if result_id not in _RESULTS:
        return jsonify({"error": "Results expired. Please run again."}), 404

    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 50)

    page = max(1, page)
    page_size = max(1, page_size)
    page_size = min(500, page_size)

    rows = _RESULTS[result_id]["rows"]
    total_rows = len(rows)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    start = (page - 1) * page_size
    end = start + page_size

    return jsonify(
        {
            "rows": rows[start:end],
            "page": page,
            "page_size": page_size,
            "total_rows": total_rows,
            "total_pages": total_pages,
        }
    )


@deg_bp.get("/results/<result_id>/download")
def results_download(result_id: str):
    _cleanup_results()
    if result_id not in _RESULTS:
        return jsonify({"error": "Results expired. Please run again."}), 404

    buf = io.BytesIO(_RESULTS[result_id]["csv"])
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="de_results.csv",
        mimetype="text/csv",
    )
