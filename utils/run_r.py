import os
import shutil
import subprocess


def run_r(script_path: str, input_file: str, *extra_args: str):
    use_micromamba = os.getenv("USE_MICROMAMBA", "1") == "1"
    env_name = os.getenv("MAMBA_ENV", "rnaenv")

    if use_micromamba:
        mm = shutil.which("micromamba") or os.getenv("MICROMAMBA_PATH")
        if not mm:
            raise RuntimeError(
                "Micromamba not found. Set MICROMAMBA_PATH to the absolute path "
                "or ensure micromamba is in PATH for systemd."
            )
        if os.getenv("MICROMAMBA_PATH") and not os.path.isabs(mm):
            raise RuntimeError("MICROMAMBA_PATH must be an absolute path.")
        cmd = [mm, "run", "-n", env_name, "Rscript", script_path, input_file, *extra_args]
    else:
        cmd = ["Rscript", script_path, input_file, *extra_args]

    return subprocess.run(cmd, check=True, capture_output=True, text=True)
