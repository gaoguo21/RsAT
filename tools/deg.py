import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid

import pandas as pd
from flask import Blueprint, jsonify, render_template, request, send_file, current_app

from tools.upload_utils import is_allowed_filename, save_bytes
from utils.run_r import run_r_system  # ✅ DEG should use system R

deg_bp = Blueprint("deg", __name__)


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
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_r_analysis(csv_path: str, group_map: dict, method: str, min_count: int) -> bytes:
    script_path = os.path.join(_project_root(), "r_scripts", "deg.R")
    if not os.path.exists(script_path):
        raise RuntimeError(f"R script not found: {script_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        meta_path = os.path.join(tmpdir, "meta.json")
        out_path = os.path.join(tmpdir, "results.csv")

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"group_map": group_map, "method": method, "min_count": min_count}, f)

        try:
            # ✅ Use system R so DESeq2/edgeR come from your system R libs
            run_r_system(script_path, csv_path, meta_path, out_path)
        except subprocess.CalledProcessError as exc:
            msg = (exc.stderr or "").strip() or (exc.stdout or "").strip() or "Rscript failed."
            raise RuntimeError(msg) from exc

        if not os.path.exists(out_path):
            raise RuntimeError("R finished without producing an output file.")

        with open(out_path, "rb") as f:
            return f.read()


def run_deg_export_job(job_id, job_dir, input_path, group_map, method, min_count):
    try:
        result_bytes = _run_r_analysis(input_path, group_map, method, min_count)
        result_path = os.path.join(job_dir, "de_results.csv")
        with open(result_path, "wb") as f:
            f.write(result_bytes)
        return {"download_url": f"/api/deg/results/{job_id}/download"}
    finally:
        try:
            os.remove(input_path)
        except OSError:
            pass


def run_deg_analyze_job(job_id, job_dir, input_path, group_map, method, min_count):
    try:
        result_bytes = _run_r_analysis(input_path, group_map, method, min_count)
        result_path = os.path.join(job_dir, "de_results.csv")
        with open(result_path, "wb") as f:
            f.write(result_bytes)
        total_rows = None
        try:
            df = pd.read_csv(result_path)
            total_rows = len(df)
        except Exception:
            total_rows = None
        return {"result_id": job_id, "total_rows": total_rows}
    finally:
        try:
            os.remove(input_path)
        except OSError:
            pass


# ---------------------------
# DEG API endpoints
# ---------------------------

@deg_bp.post("/columns")
def columns():
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

    # ✅ create a job folder now and stage the upload there (shared across workers)
    job_queue = current_app.config["JOB_QUEUE"]
    job_id, job_dir = job_queue.create_job("deg_upload")

    staged_path = os.path.join(job_dir, "counts.tsv")
    try:
        with open(staged_path, "wb") as f:
            f.write(raw)
    except OSError as exc:
        job_queue.finalize_job(job_id)
        return jsonify({"error": f"Failed to save upload: {exc}"}), 500

    # ✅ return job_id instead of file_id
    return jsonify({"job_id": job_id, "gene_col": gene_col, "sample_cols": sample_cols})


def _get_staged_counts_path(job_id: str) -> tuple[str, dict]:
    job_queue = current_app.config["JOB_QUEUE"]
    job = job_queue.get_job(job_id)
    if not job:
        raise FileNotFoundError("Upload expired or missing. Please upload again.")
    job_dir = job.get("job_dir")
    if not job_dir:
        raise FileNotFoundError("Upload expired or missing. Please upload again.")
    counts_path = os.path.join(job_dir, "counts.tsv")
    if not os.path.exists(counts_path):
        raise FileNotFoundError("Upload expired or missing. Please upload again.")
    return counts_path, job


@deg_bp.post("/export")
def export():
    payload = request.get_json(silent=True) or {}
    job_id = payload.get("job_id")  # ✅ job_id now
    group_map = payload.get("group_map") or {}
    method = (payload.get("method") or "").lower().strip()
    min_count = _parse_min_count(payload.get("min_count"))

    if not job_id:
        return jsonify({"error": "Upload expired or missing. Please upload again."}), 400
    if method not in {"edger", "deseq2"}:
        return jsonify({"error": "Unsupported method."}), 400

    groups = {v for v in group_map.values() if v in {"A", "B"}}
    if groups != {"A", "B"}:
        return jsonify({"error": "Select samples for both Group A and Group B."}), 400

    try:
        counts_path, job = _get_staged_counts_path(job_id)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 400

    job_queue = current_app.config["JOB_QUEUE"]
    new_job_id, new_job_dir = job_queue.create_job("deg_export")

    job_input_path = os.path.join(new_job_dir, "counts.tsv")
    try:
        shutil.copy2(counts_path, job_input_path)  # keep staged upload intact
    except OSError as exc:
        job_queue.finalize_job(new_job_id)
        return jsonify({"error": f"Failed to stage upload: {exc}"}), 400

    job_queue.submit(
        new_job_id,
        "tools.deg:run_deg_export_job",
        new_job_id,
        new_job_dir,
        job_input_path,
        group_map,
        method,
        min_count,
    )
    return jsonify({"job_id": new_job_id, "status_url": f"/job/{new_job_id}/status"}), 202


@deg_bp.post("/analyze")
def analyze():
    payload = request.get_json(silent=True) or {}
    job_id = payload.get("job_id")  # ✅ job_id now
    group_map = payload.get("group_map") or {}
    method = (payload.get("method") or "").lower().strip()
    min_count = _parse_min_count(payload.get("min_count"))

    if not job_id:
        return jsonify({"error": "Upload expired or missing. Please upload again."}), 400
    if method not in {"edger", "deseq2"}:
        return jsonify({"error": "Unsupported method."}), 400

    groups = {v for v in group_map.values() if v in {"A", "B"}}
    if groups != {"A", "B"}:
        return jsonify({"error": "Select samples for both Group A and Group B."}), 400

    try:
        counts_path, job = _get_staged_counts_path(job_id)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 400

    job_queue = current_app.config["JOB_QUEUE"]
    new_job_id, new_job_dir = job_queue.create_job("deg_analyze")

    job_input_path = os.path.join(new_job_dir, "counts.tsv")
    try:
        shutil.copy2(counts_path, job_input_path)
    except OSError as exc:
        job_queue.finalize_job(new_job_id)
        return jsonify({"error": f"Failed to stage upload: {exc}"}), 400

    job_queue.submit(
        new_job_id,
        "tools.deg:run_deg_analyze_job",
        new_job_id,
        new_job_dir,
        job_input_path,
        group_map,
        method,
        min_count,
    )
    return jsonify({"job_id": new_job_id, "status_url": f"/job/{new_job_id}/status", "result_id": new_job_id}), 202


@deg_bp.get("/results/<result_id>")
def results_page(result_id: str):
    job_queue = current_app.config["JOB_QUEUE"]
    job = job_queue.get_job(result_id)
    if not job or job.get("status") != "finished":
        return render_template("results.html", result_id=None, error="Results expired or not ready. Please run again.")
    return render_template("results.html", result_id=result_id, error=None)


@deg_bp.get("/results/<result_id>/data")
def results_data(result_id: str):
    job_queue = current_app.config["JOB_QUEUE"]
    job = job_queue.get_job(result_id)
    if not job or job.get("status") != "finished":
        return jsonify({"error": "Results expired or not ready. Please run again."}), 404

    result_path = os.path.join(job["job_dir"], "de_results.csv")
    if not os.path.exists(result_path):
        return jsonify({"error": "Results missing. Please run again."}), 404

    page = int(request.args.get("page") or 1)
    page_size = int(request.args.get("page_size") or 50)

    page = max(1, page)
    page_size = max(1, page_size)
    page_size = min(500, page_size)

    try:
        df = pd.read_csv(result_path)
    except Exception as exc:
        return jsonify({"error": f"Failed to parse results: {exc}"}), 400

    rows = _normalize_rows(df.to_dict(orient="records"))
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
    job_queue = current_app.config["JOB_QUEUE"]
    job = job_queue.get_job(result_id)
    if not job or job.get("status") != "finished":
        return jsonify({"error": "Results expired or not ready. Please run again."}), 404

    result_path = os.path.join(job["job_dir"], "de_results.csv")
    if not os.path.exists(result_path):
        return jsonify({"error": "Results missing. Please run again."}), 404

    response = send_file(
        result_path,
        as_attachment=True,
        download_name="de_results.csv",
        mimetype="text/csv",
    )
    response.call_on_close(lambda: job_queue.finalize_job(result_id))
    return response
