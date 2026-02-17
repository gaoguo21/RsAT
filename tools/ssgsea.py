import os
import subprocess
import tempfile

from flask import Blueprint, jsonify, request, send_file, current_app
from tools.upload_utils import is_allowed_filename, save_bytes
from utils.run_r import run_r

ssgsea_bp = Blueprint("ssgsea", __name__)
_MAX_BYTES = 100 * 1024 * 1024


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_r_ssgsea(expr_path: str, gmt_path: str) -> tuple[bytes, int]:
    script_path = os.path.join(_project_root(), "r_scripts", "ssgsea.R")
    if not os.path.exists(script_path):
        raise RuntimeError(f"R script not found: {script_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "ssgsea_scores.csv")
        summary_path = os.path.join(tmpdir, "ssgsea_summary.txt")
        try:
            run_r(script_path, expr_path, gmt_path, out_path, summary_path)
        except subprocess.CalledProcessError as exc:
            msg = (exc.stderr or "").strip() or (exc.stdout or "").strip() or "Rscript failed."
            raise RuntimeError(msg) from exc
        with open(out_path, "rb") as f:
            csv_bytes = f.read()

        low_overlap_sets = 0
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("low_overlap_sets="):
                        low_overlap_sets = int(line.strip().split("=", 1)[1])
                        break
        except OSError:
            pass
        return csv_bytes, low_overlap_sets


def run_ssgsea_job(job_id, job_dir, expr_path, gmt_path):
    try:
        csv_bytes, low_overlap_sets = _run_r_ssgsea(expr_path, gmt_path)
        result_path = os.path.join(job_dir, "ssgsea_scores.csv")
        with open(result_path, "wb") as f:
            f.write(csv_bytes)
        return {
            "ok": True,
            "download_url": f"/api/ssgsea/download/{job_id}",
            "low_overlap_sets": low_overlap_sets,
        }
    finally:
        for path in (expr_path, gmt_path):
            try:
                os.remove(path)
            except OSError:
                pass


@ssgsea_bp.post("/run")
def run():
    if request.content_length and request.content_length > _MAX_BYTES:
        return jsonify({"error": "File exceeds the 100 MB capacity."}), 400

    if not request.content_type or not request.content_type.startswith("multipart/form-data"):
        return jsonify({"error": "Upload files using multipart/form-data."}), 400

    expr_file = request.files.get("expression")
    gmt_file = request.files.get("gmt")

    if not expr_file:
        return jsonify({"error": "No expression file uploaded."}), 400
    if not gmt_file:
        return jsonify({"error": "No GMT file uploaded."}), 400

    data_exts = {".tsv", ".txt", ".csv"}
    if not is_allowed_filename(expr_file.filename or "", data_exts):
        return jsonify({"error": "Invalid file type. Use .tsv, .txt, or .csv only."}), 400
    if not is_allowed_filename(gmt_file.filename or "", {".gmt"}):
        return jsonify({"error": "Invalid file type. Use .gmt only."}), 400

    expr_raw = expr_file.read()
    if not expr_raw:
        return jsonify({"error": "Expression file is empty."}), 400
    if len(expr_raw) > _MAX_BYTES:
        return jsonify({"error": "File exceeds the 100 MB capacity."}), 400

    gmt_raw = gmt_file.read()
    if not gmt_raw:
        return jsonify({"error": "GMT file is empty."}), 400

    job_queue = current_app.config["JOB_QUEUE"]
    job_id, job_dir = job_queue.create_job("ssgsea")
    expr_path = save_bytes(job_dir, expr_file.filename or "", expr_raw, ".tsv")
    gmt_path = save_bytes(job_dir, gmt_file.filename or "", gmt_raw, ".gmt")

    job_queue.submit(
        job_id,
        "tools.ssgsea:run_ssgsea_job",
        job_id,
        job_dir,
        expr_path,
        gmt_path,
    )
    return jsonify({"job_id": job_id, "status_url": f"/job/{job_id}/status"}), 202


@ssgsea_bp.get("/download/<result_id>")
def download(result_id: str):
    job_queue = current_app.config["JOB_QUEUE"]
    job = job_queue.get_job(result_id)
    if not job or job.get("status") != "finished":
        return jsonify({"error": "Results expired or not ready. Please run again."}), 404

    result_path = os.path.join(job["job_dir"], "ssgsea_scores.csv")
    if not os.path.exists(result_path):
        return jsonify({"error": "Results missing. Please run again."}), 404

    response = send_file(
        result_path,
        as_attachment=True,
        download_name="ssgsea_scores.csv",
        mimetype="text/csv",
    )
    response.call_on_close(lambda: job_queue.finalize_job(result_id))
    return response
