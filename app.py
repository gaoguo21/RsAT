import os
from flask import Flask, render_template, jsonify, redirect, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

from download import download_bp
app.register_blueprint(download_bp)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


app.config["MAX_CONTENT_LENGTH"] = _env_int("MAX_CONTENT_LENGTH", 200 * 1024 * 1024)
app.config["UPLOAD_FOLDER"] = os.path.join(BASE_DIR, ".data", "uploads")
app.config["ALLOWED_UPLOAD_EXTENSIONS"] = {".tsv", ".txt", ".csv", ".gmt"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
app.config["MAX_CONCURRENT_JOBS"] = _env_int("MAX_CONCURRENT_JOBS", 2)
app.config["JOB_TTL_HOURS"] = _env_int("JOB_TTL_HOURS", 24)
app.config["MICROMAMBA_PATH"] = os.environ.get("MICROMAMBA_PATH", "/usr/local/bin/micromamba")
app.config["JOB_BASE_DIR"] = os.path.join(BASE_DIR, ".data", "jobs")
app.config["REDIS_URL"] = os.environ.get("REDIS_URL", "")
os.makedirs(app.config["JOB_BASE_DIR"], exist_ok=True)

from tools.job_queue import JobQueue

job_queue = JobQueue(
    app,
    max_concurrent=app.config["MAX_CONCURRENT_JOBS"],
    job_ttl_hours=app.config["JOB_TTL_HOURS"],
    base_dir=app.config["JOB_BASE_DIR"],
    redis_url=app.config["REDIS_URL"] or None,
)
app.config["JOB_QUEUE"] = job_queue

# ---- IMPORT BLUEPRINTS (ADD HERE) ----
from tools.deg import deg_bp
from tools.pathway import pathway_bp
from tools.id2symbol import id2symbol_bp
from tools.ssgsea import ssgsea_bp
#from tools.extraction import extraction_bp

# ---- REGISTER BLUEPRINTS (ADD HERE) ----
app.register_blueprint(deg_bp, url_prefix="/api/deg")
app.register_blueprint(pathway_bp, url_prefix="/api/pathway")
app.register_blueprint(id2symbol_bp, url_prefix="/api/id2symbol")
app.register_blueprint(ssgsea_bp, url_prefix="/api/ssgsea")
#app.register_blueprint(extraction_bp, url_prefix="/api/extraction")


# ---------------------------
# Pages (UI routes)
# ---------------------------
@app.route("/")
def home():
    return render_template("index.html")  # change to main.html if needed

@app.route("/deg")
def deg_page():
    return redirect(url_for("tool_deg"), code=301)

@app.route("/online-tools/deg")
def tool_deg():
    return render_template("deg.html")

@app.route("/pathway")
def pathway_page():
    return redirect(url_for("tool_pathway"), code=301)

@app.route("/online-tools/pathway")
def tool_pathway():
    return render_template("pathway.html")

@app.route("/id2symbol")
def id2symbol_page():
    return redirect(url_for("tool_id2symbol"), code=301)

@app.route("/online-tools/id2symbol")
def tool_id2symbol():
    return render_template("id2symbol.html")

@app.route("/ssgsea")
def ssgsea_page():
    return redirect(url_for("tool_ssgsea"), code=301)

@app.route("/online-tools/ssgsea")
def tool_ssgsea():
    return render_template("ssgsea.html")

@app.route("/tutorial")
def tutorial_page():
    return redirect(url_for("tutorial_online_tools"), code=301)

@app.route("/online-tools.html")
def online_tools_html_page():
    return redirect(url_for("online_tools_page"), code=301)

@app.route("/online-tools")
def online_tools_page():
    return render_template("online-tools.html")

@app.route("/tutorials.html")
def tutorials_html_page():
    return redirect(url_for("tutorials_page"), code=301)

@app.route("/tutorials")
def tutorials_page():
    return render_template("tutorials.html")

@app.route("/tutorials/online-tools-tutorial")
def tutorial_online_tools():
    return render_template("online-tools-tutorial.html")

@app.route("/tutorials/workflow")
def tutorial_workflow():
    return render_template("workflow.html")

@app.route("/tutorials/genecountcraft-tutorial")
def tutorial_genecountcraft():
    return render_template("genecountcraft-tutorial.html")

@app.route("/guides")
def guides_page():
    return render_template("guides.html")

@app.route("/guides.html")
def guides_html_page():
    return redirect(url_for("guides_page"), code=301)

@app.route("/sources")
def sources_page():
    return render_template("sources.html")

@app.route("/sources.html")
def sources_html_page():
    return redirect(url_for("sources_page"), code=301)

@app.route("/tutorials/online-tools.html")
def tutorial_online_tools_html_page():
    return redirect(url_for("tutorial_online_tools"), code=301)

@app.route("/tutorials/workflow.html")
def tutorial_workflow_html_page():
    return redirect(url_for("tutorial_workflow"), code=301)

@app.route("/online-tools/deg.html")
def online_tools_deg_html_page():
    return redirect(url_for("tool_deg"), code=301)

@app.route("/online-tools/pathway.html")
def online_tools_pathway_html_page():
    return redirect(url_for("tool_pathway"), code=301)

@app.route("/online-tools/id2symbol.html")
def online_tools_id2symbol_html_page():
    return redirect(url_for("tool_id2symbol"), code=301)

@app.route("/online-tools/ssgsea.html")
def online_tools_ssgsea_html_page():
    return redirect(url_for("tool_ssgsea"), code=301)

@app.route("/workflow")
def workflow_page():
    return render_template("workflow.html")

@app.route("/extraction")
def extraction_page():
    return render_template("extraction.html")

@app.route("/legal")
def legal_page():
    return render_template("legal.html")

@app.route("/index/applegal")
def applegal_page():
    return render_template("applegal.html")

@app.route("/job/<job_id>/status")
def job_status(job_id):
    job_queue = app.config["JOB_QUEUE"]
    job = job_queue.get_job(job_id)

    if not job:
        return jsonify({"status": "missing", "error": "Job not found or expired."}), 404

    status = job.get("status") or "unknown"

    # Always include error if present so frontend can show it
    payload = {
        "status": status,
        "result": job.get("result"),
        "error": job.get("error"),
    }

    # If failed, return 200 (frontend already checks status === "failed")
    # but include the message.
    if status == "failed" and not payload["error"]:
        payload["error"] = "Job failed."

    return jsonify(payload), 200



if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=5000, debug=debug)
