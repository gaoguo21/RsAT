# utils/run_r.py
import os
import shutil
import subprocess
from typing import Optional


def _get_micromamba_path() -> str:
    """
    Resolve micromamba path robustly for systemd/gunicorn.
    Priority:
      1) MICROMAMBA_PATH env (must be absolute)
      2) shutil.which("micromamba")
      3) common default path /usr/local/bin/micromamba
    """
    mm_env = os.getenv("MICROMAMBA_PATH")
    if mm_env:
        if not os.path.isabs(mm_env):
            raise RuntimeError("MICROMAMBA_PATH must be an absolute path.")
        if not os.path.exists(mm_env):
            raise RuntimeError(f"MICROMAMBA_PATH does not exist: {mm_env}")
        return mm_env

    mm = shutil.which("micromamba")
    if mm:
        return mm

    # Common on your EC2 based on your logs:
    fallback = "/usr/local/bin/micromamba"
    if os.path.exists(fallback):
        return fallback

    raise RuntimeError(
        "Micromamba not found. Set MICROMAMBA_PATH to the absolute path "
        "(e.g., /usr/local/bin/micromamba) or ensure micromamba is in PATH for systemd."
    )


def _get_rscript_path() -> str:
    """
    Resolve system Rscript. Priority:
      1) RSCRIPT_PATH env (must be absolute)
      2) shutil.which("Rscript")
      3) common default /usr/bin/Rscript
    """
    r_env = os.getenv("RSCRIPT_PATH")
    if r_env:
        if not os.path.isabs(r_env):
            raise RuntimeError("RSCRIPT_PATH must be an absolute path.")
        if not os.path.exists(r_env):
            raise RuntimeError(f"RSCRIPT_PATH does not exist: {r_env}")
        return r_env

    r = shutil.which("Rscript")
    if r:
        return r

    fallback = "/usr/bin/Rscript"
    if os.path.exists(fallback):
        return fallback

    raise RuntimeError(
        "Rscript not found. Set RSCRIPT_PATH to the absolute path (e.g., /usr/bin/Rscript) "
        "or ensure Rscript is in PATH for systemd."
    )


def run_r_system(script_path: str, *args: str) -> subprocess.CompletedProcess:
    """
    Run Rscript using the SYSTEM R environment (your existing R packages like DESeq2).
    Returns CompletedProcess; raises CalledProcessError on failure (check=True).
    """
    rscript = _get_rscript_path()
    cmd = [rscript, script_path, *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def run_r_mamba(
    script_path: str,
    *args: str,
    env_name: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """
    Run Rscript inside micromamba env (for fgsea).
    Returns CompletedProcess; raises CalledProcessError on failure (check=True).
    """
    mm = _get_micromamba_path()
    env = env_name or os.getenv("MAMBA_ENV", "rnaenv")
    cmd = [mm, "run", "-n", env, "Rscript", script_path, *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


# Backward compatible helper:
# - defaults to system R (safe for DESeq2/edgeR etc.)
# - set use_micromamba=True for fgsea calls
def run_r(script_path: str, input_file: str, *extra_args: str, use_micromamba: bool = False):
    """
    Compatibility wrapper:
      run_r(script_path, input_file, *extra_args, use_micromamba=False)
    """
    if use_micromamba:
        return run_r_mamba(script_path, input_file, *extra_args)
    return run_r_system(script_path, input_file, *extra_args)
