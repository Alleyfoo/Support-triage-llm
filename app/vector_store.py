from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _fallback_embed(text: str, dim: int = 256) -> List[float]:
    """Lightweight hashed bag-of-words embedding as a resilience fallback."""
    tokens = re.findall(r"[A-Za-z0-9]+", text.lower())
    if not tokens:
        return [0.0] * dim
    vec = [0.0] * dim
    for tok in tokens:
        idx = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


_STORE: "TriageVectorStore" | None = None


class TriageVectorStore:
    """Local-first embedding store for triage few-shot retrieval."""

    def __init__(self, dataset_path: Path, cache_path: Path) -> None:
        self.dataset_path = Path(dataset_path)
        self.cache_path = Path(cache_path)
        self.examples: List[Dict[str, Any]] = []
        self.embeddings: List[List[float]] = []
        self.dim: int | None = None

    def refresh(self) -> None:
        """Load dataset, fill embedding cache, and materialize vectors."""
        dataset = self._load_dataset()
        cache = self._load_cache()
        updated = False

        vectors: List[List[float]] = []
        records: List[Dict[str, Any]] = []

        for row in dataset:
            text = str(row.get("input_symptoms") or row.get("input_redacted") or "").strip()
            if not text:
                continue
            key = _hash_text(text)
            vec = cache.get(key)
            if not vec:
                vec = self._embed(text)
                cache[key] = vec
                updated = True
            if self.dim is None:
                self.dim = len(vec)
            if self.dim != len(vec):
                # skip mixed-dimension entries
                continue
            vectors.append(vec)
            records.append({"input": text, "example": row})

        if updated:
            self._save_cache(cache)

        self.examples = records
        self.embeddings = vectors

    def retrieve(self, text: str, k: int = 3, threshold: float = 0.5) -> List[Dict[str, Any]]:
        """Return top-k examples above similarity threshold."""
        if not self.embeddings:
            return []
        vec = self._embed(text)
        if self.dim and len(vec) != self.dim:
            return []
        scored: List[Tuple[float, int]] = []
        for idx, emb in enumerate(self.embeddings):
            score = _cosine(vec, emb)
            if score >= threshold:
                scored.append((score, idx))
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[: max(0, k)]
        return [self.examples[i]["example"] for _, i in top]

    def _load_dataset(self) -> List[Dict[str, Any]]:
        if not self.dataset_path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with self.dataset_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _load_cache(self) -> Dict[str, List[float]]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_cache(self, cache: Dict[str, List[float]]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(cache), encoding="utf-8")

    def _embed(self, text: str) -> List[float]:
        try:
            payload = {"model": config.OLLAMA_EMBED_MODEL, "input": text}
            data = json.dumps(payload).encode("utf-8")
            url = config.OLLAMA_HOST.rstrip("/") + "/api/embeddings"
            req = Request(url, data=data, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=config.OLLAMA_TIMEOUT) as resp:  # nosec - local inference endpoint
                body = resp.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            # Resilience: fall back to hashed BoW embedding locally so validation can run without Ollama embeddings.
            return _fallback_embed(text)
        parsed = json.loads(body)
        embeddings = parsed.get("data") or []
        if not embeddings:
            return _fallback_embed(text)
        vec = embeddings[0].get("embedding")
        if not isinstance(vec, list):
            return _fallback_embed(text)
        return [float(x) for x in vec]


def get_store(force_refresh: bool = False) -> TriageVectorStore:
    """Singleton accessor with optional refresh."""
    global _STORE
    if _STORE is None:
        dataset_path = Path(config.GOLDEN_DATASET_PATH)
        cache_path = Path(dataset_path).parent / "embeddings_cache.json"
        _STORE = TriageVectorStore(dataset_path, cache_path)
        _STORE.refresh()
    elif force_refresh:
        _STORE.refresh()
    return _STORE
