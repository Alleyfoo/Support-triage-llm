from pathlib import Path
import os, shutil
from typing import Optional
from huggingface_hub import hf_hub_download

def ensure_drive_model(repo_id: str, filename: str, drive_subdir: str = "slm_cleanroom/models") -> str:
    drive_root = Path("/content/drive/MyDrive")
    models_dir = drive_root / drive_subdir
    models_dir.mkdir(parents=True, exist_ok=True)
    dest = models_dir / filename
    if dest.exists():
        return str(dest)
    tmp = hf_hub_download(repo_id=repo_id, filename=filename, local_dir="/content")
    shutil.copy2(tmp, dest)
    return str(dest)

def set_model_env(path: str):
    os.environ["MODEL_PATH"] = path
    return path
