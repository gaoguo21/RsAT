import io
import logging
import os
import subprocess
import tempfile
from typing import Optional, List, Tuple, Dict, Any

import pandas as pd
from flask import Blueprint, jsonify, request, send_file, current_app

from tools.upload_utils import is_allowed_filename, save_bytes

pathway_bp = Blueprint("pathway", __name__)
logger = logging.getLogger(__name__)

# Absolute micromamba path for systemd/gunicorn reliability on EC2
MICROMAMBA = "/usr/local/bin/micromamba"
MICROMAMBA_ENV = "rnaenv"


def run_r(script_path: str, *args: str) -> str:
    """
    Run an R script inside micromamba env and return stdout.
    Raises RuntimeError with full stdout/stderr on failure.
    """
    cmd = [MICROMAMBA, "run", "-n", MICROMAMBA_ENV, "Rscript", script_path, *args]
    res = subprocess.run(cmd, capture_output=True, text=True)

    if res.returncode != 0:
        raise RuntimeError(
            "R execution failed.\n"
            f"CMD: {' '.join(cmd)}\n\n"
            f"STDOUT:\n{res.stdout}\n\n"
            f"STDERR:\n{res.stderr}\n"
        )
    return res.stdout


def _parse_ranked_text(text: str) -> List[Tuple[str, float]]:
    """
    Parse a ranked list from text:
      - supports tab/comma/space separated
      - expects at least 2 columns: gene and score (e.g., log2FC)
      - skips header-ish lines if score isn't numeric
    """
    rows: List[Tuple[str, float]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # split by tab first, fallback to whitespace/comma
        if "\t" in line:
            parts = line.split("\t")
        elif "," in line:
            parts = line.split(",")
        else:
            parts = line.split()
        if len(parts) < 2:
            continue
        gene = str(parts[0]).strip()
        try:
            score = float(parts[1])
        except (TypeError, ValueError):
            continue
        if gene:
            rows.append((gene, score))
    return rows


def _parse_ranked_from_file(filename: str, raw: bytes) -> List[Tuple[str, float]]:
    ext = os.path.splitext(filename.lower())[1]
    if ext in {".txt"}:
        return _parse_ranked_text(raw.decode("utf-8", errors="ignore"))

    if ext in {".csv", ".tsv"}:
        try:
            df = pd.read_csv(io.BytesIO(raw), sep=None, engine="python")
        except Exception:
            df = pd.read_csv(io.BytesIO(raw))
        if df.shape[1] < 2:
            return []
        rows: List[Tuple[str, float]] = []
        for _, row in df.iterrows():
            gene = str(row.iloc[0]).strip()
            try:
                fc = float(row.iloc[1])
            except (TypeError, ValueError):
                continue
            if gene:
                rows.append((gene, fc))
        return rows

    return []


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_r_enrichment(input_path: str, organism: str, library: str, gmt_path: Optional[str]) -> bytes:
    """
    Runs r_scripts/enrichment.R inside micromamba env and returns the output CSV bytes.
    """
    script_path = os.path.join(_project_root(), "r_scripts", "enrichment.R")
    if not os.path.exists(script_path):
        raise RuntimeError(f"R script not found: {script_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "pathway_results.csv")

        # Pass "" if no custom GMT provided
        gmt_arg = gmt_path or ""

        # IMPORTANT: pass absolute paths to avoid cwd issues in gunicorn/systemd
        run_r(script_path, input_path, out_path, organism, library, gmt_arg)

        if not os.path.exists(out_path):
            raise RuntimeError("R finished without producing an output file.")

        with open(out_path, "rb") as f:
            return f.read()


def _normalize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for row in rows:
        for key, value in list(row.items()):
            if pd.isna(value):
                row[key] = None
    return rows


def _coerce_results(csv_bytes: bytes) -> List[Dict[str, Any]]:
    df = pd.read_csv(io.BytesIO(csv_bytes))
    df.columns = [str(c).strip() for c in df.columns]

    rename_map = {
        "pathway": "pathway",
        "term": "pathway",
        "genes": "genes",
        "gene_names": "genes",
        "gene_list": "genes",
        "pvalue": "pval",
        "p_value": "pval",
        "p.value": "pval",
        "padj": "padj",
        "fdr": "padj",
        "nes": "nes",
        "size": "size",
        "leadingedge": "leadingEdge",
        "leading_edge": "leadingEdge",
    }

    df = df.rename(columns={c: rename_map.get(c.lower(), c) for c in df.columns})

    keep = [c for c in ["pathway", "nes", "pval", "padj", "size", "leadingEdge", "genes"] if c in df.columns]
    if keep:
        df = df[keep]

    return _normalize_rows(df.to_dict(orient="records"))


def run_pathway_job(job_id: str, job_dir: str, input_path: str, organism: str, library: str, gmt_path: Optional[str]):
    try:
        csv_bytes = _run_r_enrichment(input_path, organism, library, gmt_path)

        result_path = os.path.join(job_dir, "pathway_results.csv")
        with open(result_path, "wb") as f:
            f.write(csv_bytes)

        try:
            rows = _coerce_results(csv_bytes)
        except Exception as exc:
            raise RuntimeError(f"Failed to parse results: {exc}") from exc

        return {
            "ok": True,
            "results": rows,
            "download_url": f"/api/pathway/download/{job_id}",
        }
    finally:
        # Cleanup uploaded inputs (privacy + disk)
        for path in (input_path, gmt_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass


@pathway_bp.post("/run")
def run():
    ranked: List[Tuple[str, float]] = []
    organism = ""
    library = ""
    input_path = ""
    gmt_path: Optional[str] = None

    job_queue = current_app.config["JOB_QUEUE"]
    job_id: Optional[str] = None
    job_dir: Optional[str] = None

    if not (request.content_type and request.content_type.startswith("multipart/form-data")):
        return jsonify({"error": "Upload a preranked file using multipart/form-data."}), 400

    file = request.files.get("file")
    organism = (request.form.get("organism") or "").lower().strip()
    library = (request.form.get("library") or "").lower().strip()
    gmt_file = request.files.get("gmt")

    if not file:
        return jsonify({"error": "No file uploaded."}), 400

    data_exts = {".tsv", ".txt", ".csv"}
    if not is_allowed_filename(file.filename or "", data_exts):
        return jsonify({"error": "Invalid file type. Use .tsv, .txt, or .csv only."}), 400

    raw = file.read()
    if not raw:
        return jsonify({"error": "Uploaded file is empty."}), 400

    ranked = _parse_ranked_from_file(file.filename or "", raw)

    job_id, job_dir = job_queue.create_job("pathway")
    input_path = save_bytes(job_dir, file.filename or "", raw, ".txt")

    if gmt_file:
        if not is_allowed_filename(gmt_file.filename or "", {".gmt"}):
            # Cleanup
            if input_path:
                try:
                    os.remove(input_path)
                except OSError:
                    pass
            if job_id:
                job_queue.finalize_job(job_id)
            return jsonify({"error": "Invalid GMT file type. Use .gmt only."}), 400

        gmt_raw = gmt_file.read()
        if gmt_raw:
            gmt_path = save_bytes(job_dir, gmt_file.filename or "", gmt_raw, ".gmt")

    if organism not in {"human", "mouse"}:
        if job_id:
            job_queue.finalize_job(job_id)
        return jsonify({"error": "Organism must be Human or Mouse."}), 400

    if library not in {"kegg", "reactome", "hallmark", "go", "biocarta", "custom"}:
        if job_id:
            job_queue.finalize_job(job_id)
        return jsonify({"error": "Library must be KEGG, Reactome, Hallmark, GO, BioCarta, or Custom."}), 400

    if library == "custom" and not gmt_path:
        if input_path:
            try:
                os.remove(input_path)
            except OSError:
                pass
        if job_id:
            job_queue.finalize_job(job_id)
        return jsonify({"error": "Custom dataset selected. Upload a GMT file."}), 400

    if not ranked:
        if input_path:
            try:
                os.remove(input_path)
            except OSError:
                pass
        if job_id:
            job_queue.finalize_job(job_id)
        return jsonify({"error": "No genes provided."}), 400

    # Basic numeric validation
    fcs = [fc for _, fc in ranked]
    if any(not isinstance(fc, (int, float)) for fc in fcs):
        if input_path:
            try:
                os.remove(input_path)
            except OSError:
                pass
        if job_id:
            job_queue.finalize_job(job_id)
        return jsonify({"error": "Second column must be numeric fold-change values."}), 400

    job_queue.submit(
        job_id,
        "tools.pathway:run_pathway_job",
        job_id,
        job_dir,
        input_path,
        organism,
        library,
        gmt_path,
    )
    return jsonify({"job_id": job_id, "status_url": f"/job/{job_id}/status"}), 202


@pathway_bp.get("/download/<result_id>")
def download(result_id: str):
    job_queue = current_app.config["JOB_QUEUE"]
    job = job_queue.get_job(result_id)
    if not job or job.get("status") != "finished":
        return jsonify({"error": "Results expired or not ready. Please run again."}), 404

    result_path = os.path.join(job["job_dir"], "pathway_results.csv")
    if not os.path.exists(result_path):
        return jsonify({"error": "Results missing. Please run again."}), 404

    response = send_file(
        result_path,
        as_attachment=True,
        download_name="pathway_results.csv",
        mimetype="text/csv",
    )
    response.call_on_close(lambda: job_queue.finalize_job(result_id))
    return response
