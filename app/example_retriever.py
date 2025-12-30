from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


@dataclass
class LearningExample:
    input_symptoms: str
    perfect_triage: Dict[str, Any]
    perfect_reply: Dict[str, Any]
    reasoning: str
    case_id: str


def _embed(text: str) -> Dict[str, float]:
    tokens = TOKEN_RE.findall(text.lower())
    if not tokens:
        return {}
    total = float(len(tokens))
    vec: Dict[str, float] = {}
    for token in tokens:
        vec[token] = vec.get(token, 0.0) + 1.0
    return {k: v / total for k, v in vec.items()}


def _similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    shared = set(a.keys()) & set(b.keys())
    dot = sum(a[k] * b[k] for k in shared)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class ExampleRetriever:
    """Lightweight, dependency-free retriever over the curated golden dataset."""

    def __init__(self, dataset_path: Path, *, max_examples: int = 3) -> None:
        self.dataset_path = Path(dataset_path)
        self.max_examples = max(0, max_examples)
        self._examples: List[LearningExample] = []
        self._embeddings: List[Dict[str, float]] = []
        self._load()

    def _load(self) -> None:
        self._examples = []
        self._embeddings = []
        if not self.dataset_path.exists():
            return
        with self.dataset_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                example = LearningExample(
                    input_symptoms=str(obj.get("input_symptoms") or ""),
                    perfect_triage=obj.get("perfect_triage") or {},
                    perfect_reply=obj.get("perfect_reply") or {},
                    reasoning=str(obj.get("reasoning") or ""),
                    case_id=str(obj.get("case_id") or ""),
                )
                self._examples.append(example)
                self._embeddings.append(_embed(example.input_symptoms))

    def query(self, text: str, *, k: int | None = None) -> List[LearningExample]:
        if not self._examples or self.max_examples == 0:
            return []
        target = _embed(text)
        results: List[Tuple[float, int]] = []
        for idx, embedding in enumerate(self._embeddings):
            score = _similarity(target, embedding)
            if score <= 0:
                continue
            results.append((score, idx))
        if not results:
            return []
        results.sort(key=lambda pair: pair[0], reverse=True)
        limit = self.max_examples if k is None else max(0, k)
        top_indices = [idx for _, idx in results[:limit]]
        return [self._examples[i] for i in top_indices]
