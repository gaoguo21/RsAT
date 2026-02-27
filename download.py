import datetime
import json
import os
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Optional

from botocore.signers import CloudFrontSigner
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from flask import Blueprint, current_app, flash, redirect, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix


download_bp = Blueprint("download", __name__)


@download_bp.record_once
def _configure_app(state):
    app = state.app
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
        x_prefix=1,
    )

    if not app.secret_key:
        app.secret_key = (
            os.environ.get("SECRET_KEY")
            or os.environ.get("FLASK_SECRET_KEY")
            or os.urandom(32)
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


FILES = {
    "mac": "GeneCountCraft_1.0.0.jgp",
    "win": "windowsversion.jpg",
}


@lru_cache(maxsize=4)
def _load_private_key(path: str):
    with open(path, "rb") as handle:
        return load_pem_private_key(handle.read(), password=None)


def _make_signer(private_key_path: str):
    def _signer(message: bytes) -> bytes:
        private_key = _load_private_key(private_key_path)
        return private_key.sign(message, padding.PKCS1v15(), hashes.SHA1())

    return _signer


def _build_signed_url(selected_os: str) -> str:
    domain = (os.environ.get("CF_DOMAIN") or "").strip()
    if not domain:
        raise RuntimeError("CF_DOMAIN is not set")

    key_pair_id = os.environ.get("CF_KEY_PAIR_ID")
    if not key_pair_id:
        raise RuntimeError("CF_KEY_PAIR_ID is not set")

    private_key_path = os.environ.get("CF_PRIVATE_KEY_PATH")
    if not private_key_path:
        raise RuntimeError("CF_PRIVATE_KEY_PATH is not set")

    filename = FILES[selected_os]
    url = f"https://{domain}/GeneCountCraft/{filename}"
    expires_seconds = _env_int("CF_EXPIRES_SECONDS", 300)
    expire_date = datetime.datetime.utcnow() + datetime.timedelta(
        seconds=expires_seconds
    )

    signer = CloudFrontSigner(key_pair_id, _make_signer(private_key_path))
    return signer.generate_presigned_url(url, date_less_than=expire_date)


def _verify_recaptcha(secret: str, token: str, remote_ip: Optional[str]) -> bool:
    url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {
        "secret": secret,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read().decode("utf-8")
        result = json.loads(body)
        return bool(result.get("success"))
    except Exception:
        current_app.logger.exception("reCAPTCHA verification failed")
        return False


@download_bp.route("/download", methods=["GET", "POST"])
def download():
    site_key = os.environ.get("RECAPTCHA_SITE_KEY", "")

    if request.method == "GET":
        return render_template("download.html", site_key=site_key, selected_os=None)

    selected_os = (request.form.get("os") or "").lower()
    token = request.form.get("g-recaptcha-response", "")

    if selected_os not in FILES:
        flash("Please select Mac or Windows.", "error")
        return render_template(
            "download.html", site_key=site_key, selected_os=selected_os
        )

    if not token:
        flash("Please complete the reCAPTCHA.", "error")
        return render_template(
            "download.html", site_key=site_key, selected_os=selected_os
        )

    secret = os.environ.get("RECAPTCHA_SECRET_KEY")
    if not secret:
        flash("Server configuration error. Please try again later.", "error")
        return render_template(
            "download.html", site_key=site_key, selected_os=selected_os
        )

    if not _verify_recaptcha(secret, token, request.remote_addr):
        flash("reCAPTCHA verification failed. Please try again.", "error")
        return render_template(
            "download.html", site_key=site_key, selected_os=selected_os
        )

    try:
        signed_url = _build_signed_url(selected_os)
    except Exception:
        current_app.logger.exception("Failed to generate CloudFront signed URL")
        flash("Unable to generate download link. Please try again later.", "error")
        return render_template(
            "download.html", site_key=site_key, selected_os=selected_os
        )

    return redirect(signed_url)
