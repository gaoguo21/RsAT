import io
import os
import subprocess
import tempfile

import pandas as pd
from flask import Blueprint, jsonify, request, send_file, current_app
from tools.upload_utils import is_allowed_filename, save_bytes
from utils.run_r import run_r

id2symbol_bp = Blueprint("id2symbol", __name__)


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_r_id2symbol(input_path: str, organism: str) -> bytes:
    script_path = os.path.join(_project_root(), "r_scripts", "id2symbol.R")
    if not os.path.exists(script_path):
        raise RuntimeError(f"R script not found: {script_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "id2symbol_results.csv")
        try:
            run_r(script_path, input_path, out_path, organism)
        except subprocess.CalledProcessError as exc:
            msg = (exc.stderr or "").strip() or (exc.stdout or "").strip() or "Rscript failed."
            raise RuntimeError(msg) from exc
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


def run_id2symbol_job(job_id, job_dir, input_path, organism):
    try:
        csv_bytes = _run_r_id2symbol(input_path, organism)
        result_path = os.path.join(job_dir, "id2symbol_results.csv")
        with open(result_path, "wb") as f:
            f.write(csv_bytes)
        mapped, total, unmapped = _summarize_mapping(csv_bytes)
        return {
            "ok": True,
            "download_url": f"/api/id2symbol/download/{job_id}",
            "mapped": mapped,
            "total": total,
            "unmapped": unmapped,
        }
    finally:
        try:
            os.remove(input_path)
        except OSError:
            pass


@id2symbol_bp.post("/run")
def run():
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

    job_queue = current_app.config["JOB_QUEUE"]
    job_id, job_dir = job_queue.create_job("id2symbol")
    input_path = save_bytes(job_dir, file.filename or "", raw, ".txt")

    job_queue.submit(
        job_id,
        "tools.id2symbol:run_id2symbol_job",
        job_id,
        job_dir,
        input_path,
        organism,
    )
    return jsonify({"job_id": job_id, "status_url": f"/job/{job_id}/status"}), 202


@id2symbol_bp.get("/download/<result_id>")
def download(result_id: str):
    job_queue = current_app.config["JOB_QUEUE"]
    job = job_queue.get_job(result_id)
    if not job or job.get("status") != "finished":
        return jsonify({"error": "Results expired or not ready. Please run again."}), 404

    result_path = os.path.join(job["job_dir"], "id2symbol_results.csv")
    if not os.path.exists(result_path):
        return jsonify({"error": "Results missing. Please run again."}), 404

    response = send_file(
        result_path,
        as_attachment=True,
        download_name="id2symbol_results.csv",
        mimetype="text/csv",
    )
    response.call_on_close(lambda: job_queue.finalize_job(result_id))
    return response
