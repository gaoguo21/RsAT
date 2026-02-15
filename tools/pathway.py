import io
import os
import tempfile
import time
import uuid
import subprocess

import pandas as pd
from flask import Blueprint, jsonify, request, send_file, current_app
from tools.upload_utils import is_allowed_filename, save_bytes

pathway_bp = Blueprint("pathway", __name__)

_RESULTS = {}
_TTL_SECONDS = 30 * 60


def _parse_ranked_from_file(filename: str, raw: bytes) -> list[tuple[str, float]]:
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
        rows = []
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


def _cleanup_results() -> None:
    now = time.time()
    stale = [k for k, v in _RESULTS.items() if now - v["ts"] > _TTL_SECONDS]
    for k in stale:
        _RESULTS.pop(k, None)


def _run_r_enrichment(input_path: str, organism: str, library: str, gmt_path):
    script_path = os.path.join(_project_root(), "r_scripts", "enrichment.R")
    if not os.path.exists(script_path):
        raise RuntimeError(f"R script not found: {script_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "pathway_results.csv")
        cmd = ["Rscript", script_path, input_path, out_path, organism, library, gmt_path or ""]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            msg = (proc.stderr or "").strip() or (proc.stdout or "").strip() or "Rscript failed."
            raise RuntimeError(msg)
        with open(out_path, "rb") as f:
            return f.read()


def _normalize_rows(rows: list[dict]) -> list[dict]:
    for row in rows:
        for key, value in row.items():
            if pd.isna(value):
                row[key] = None
    return rows


def _coerce_results(csv_bytes: bytes) -> list[dict]:
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

    keep = [
        c
        for c in ["pathway", "nes", "pval", "padj", "size", "leadingEdge", "genes"]
        if c in df.columns
    ]
    if keep:
        df = df[keep]

    return _normalize_rows(df.to_dict(orient="records"))


@pathway_bp.post("/run")
def run():
    _cleanup_results()
    ranked = []
    organism = ""
    library = ""
    input_path = ""
    gmt_path = None

    if request.content_type and request.content_type.startswith("multipart/form-data"):
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
        input_path = save_bytes(current_app.config["UPLOAD_FOLDER"], file.filename or "", raw, ".txt")

        if gmt_file:
            if not is_allowed_filename(gmt_file.filename or "", {".gmt"}):
                if input_path:
                    try:
                        os.remove(input_path)
                    except OSError:
                        pass
                return jsonify({"error": "Invalid GMT file type. Use .gmt only."}), 400
            gmt_raw = gmt_file.read()
            if gmt_raw:
                gmt_path = save_bytes(current_app.config["UPLOAD_FOLDER"], gmt_file.filename or "", gmt_raw, ".gmt")
    else:
        return jsonify({"error": "Upload a preranked file using multipart/form-data."}), 400

    if organism not in {"human", "mouse"}:
        return jsonify({"error": "Organism must be Human or Mouse."}), 400
    if library not in {"kegg", "reactome", "hallmark", "go", "biocarta", "custom"}:
        return jsonify({"error": "Library must be KEGG, Reactome, Hallmark, GO, BioCarta, or Custom."}), 400

    if library == "custom" and not gmt_path:
        if input_path:
            try:
                os.remove(input_path)
            except OSError:
                pass
        return jsonify({"error": "Custom dataset selected. Upload a GMT file."}), 400

    if not ranked:
        if input_path:
            try:
                os.remove(input_path)
            except OSError:
                pass
        return jsonify({"error": "No genes provided."}), 400

    fcs = [fc for _, fc in ranked]
    if any(not isinstance(fc, (int, float)) for fc in fcs):
        if input_path:
            try:
                os.remove(input_path)
            except OSError:
                pass
        return jsonify({"error": "Second column must be numeric fold-change values."}), 400

    try:
        csv_bytes = _run_r_enrichment(input_path, organism, library, gmt_path)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        try:
            if input_path:
                os.remove(input_path)
        except OSError:
            pass
        try:
            if gmt_path:
                os.remove(gmt_path)
        except OSError:
            pass

    try:
        rows = _coerce_results(csv_bytes)
    except Exception as exc:
        return jsonify({"error": f"Failed to parse results: {exc}"}), 400

    result_id = str(uuid.uuid4())
    _RESULTS[result_id] = {"csv": csv_bytes, "ts": time.time()}

    return jsonify({"ok": True, "results": rows, "download_url": f"/api/pathway/download/{result_id}"})


@pathway_bp.get("/download/<result_id>")
def download(result_id: str):
    _cleanup_results()
    if result_id not in _RESULTS:
        return jsonify({"error": "Results expired. Please run again."}), 404

    buf = io.BytesIO(_RESULTS[result_id]["csv"])
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="pathway_results.csv",
        mimetype="text/csv",
    )
