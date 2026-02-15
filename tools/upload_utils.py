import os
import uuid
from werkzeug.utils import secure_filename


def normalize_ext(filename: str) -> str:
    safe_name = secure_filename(filename or "")
    return os.path.splitext(safe_name)[1].lower()


def is_allowed_filename(filename: str, allowed_exts: set[str]) -> bool:
    ext = normalize_ext(filename)
    return bool(ext) and ext in allowed_exts


def save_bytes(upload_dir: str, filename: str, data: bytes, default_ext: str) -> str:
    os.makedirs(upload_dir, exist_ok=True)
    ext = normalize_ext(filename) or default_ext
    safe_ext = ext if ext.startswith(".") else f".{ext}"
    path = os.path.join(upload_dir, f"{uuid.uuid4().hex}{safe_ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path
