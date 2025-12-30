from pathlib import Path
import os, shutil
from huggingface_hub import hf_hub_download


def _validate_filename(fn: str):
    ok = (".gguf", ".ggml", ".bin")
    if not fn.lower().endswith(ok):
        raise ValueError(
            f"HF_FILENAME must be a model artifact (e.g. .gguf). Got: {fn!r}"
        )

DEFAULT_REPO_ID = os.environ.get("HF_REPO_ID", "bartowski/TinyLlama-1.1B-1T-GGUF")
DEFAULT_FILENAME = os.environ.get("HF_FILENAME", "TinyLlama-1.1B-1T-instruct.Q4_K_M.gguf")
DEFAULT_DIR = os.environ.get("MODELS_DIR", "models")

def ensure_model(repo_id: str = DEFAULT_REPO_ID, filename: str = DEFAULT_FILENAME, models_dir: str = DEFAULT_DIR) -> str:
    _validate_filename(filename)
    models = Path(models_dir)
    models.mkdir(parents=True, exist_ok=True)
    dest = models / filename
    if dest.exists():
        return str(dest)
    tmp = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=".")  # ladattu /tmp/.cache → kopio
    shutil.copy2(tmp, dest)
    return str(dest)

if __name__ == "__main__":
    path = ensure_model()
    print("Model ready at:", path)
