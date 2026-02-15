import os
from flask import Flask, render_template

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


app.config["MAX_CONTENT_LENGTH"] = _env_int("MAX_CONTENT_LENGTH", 30 * 1024 * 1024)
app.config["UPLOAD_FOLDER"] = os.path.join(BASE_DIR, ".data", "uploads")
app.config["ALLOWED_UPLOAD_EXTENSIONS"] = {".tsv", ".txt", ".csv", ".gmt"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

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
    return render_template("deg.html")

@app.route("/pathway")
def pathway_page():
    return render_template("pathway.html")

@app.route("/id2symbol")
def id2symbol_page():
    return render_template("id2symbol.html")

@app.route("/ssgsea")
def ssgsea_page():
    return render_template("ssgsea.html")

@app.route("/extraction")
def extraction_page():
    return render_template("extraction.html")


if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=5000, debug=debug)
