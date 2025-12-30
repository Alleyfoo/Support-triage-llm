#!/usr/bin/env python3
"""Direct Ollama /api/chat benchmark (bypasses pipeline).

Sends the same prompt N times to an Ollama model and records per-iteration
latency and (optionally) the replies. Writes both an Excel workbook and a CSV
log to facilitate monitoring and dashboards.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    # Standard library HTTP client (sufficient for local Ollama)
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
except Exception:  # pragma: no cover
    Request = None  # type: ignore
    urlopen = None  # type: ignore
    URLError = Exception  # type: ignore
    HTTPError = Exception  # type: ignore


def _ns_to_seconds(value: Optional[int]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value) / 1_000_000_000.0
    except Exception:
        return None


def chat_once(
    *,
    host: str,
    model: str,
    prompt: str,
    system: Optional[str],
    num_predict: int,
    temperature: float,
    seed: Optional[int],
    timeout: float,
    stream: bool,
) -> Dict[str, Any]:
    """Send one chat request to Ollama, optionally streaming to capture TTFB."""

    payload: Dict[str, Any] = {
        "model": model,
        "messages": (
            ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}]
        ),
        "stream": bool(stream),
        "options": {
            "num_predict": int(num_predict),
            "temperature": float(temperature),
        },
    }
    if seed is not None:
        payload["options"]["seed"] = int(seed)

    url = host.rstrip("/") + "/api/chat"
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})

    t0 = time.perf_counter()

    if not stream:
        try:
            with urlopen(req, timeout=timeout) as resp:  # nosec - local endpoint
                body = resp.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "elapsed_seconds": round(time.perf_counter() - t0, 6),
            }
        elapsed = time.perf_counter() - t0

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "invalid_json",
                "elapsed_seconds": round(elapsed, 6),
            }

        message = parsed.get("message", {}) or {}
        content = message.get("content") if isinstance(message, dict) else None
        return {
            "ok": True,
            "elapsed_seconds": round(elapsed, 6),
            "ttfb_seconds": None,
            "reply": content if isinstance(content, str) else "",
            "total_duration_seconds": _ns_to_seconds(parsed.get("total_duration")),
            "load_duration_seconds": _ns_to_seconds(parsed.get("load_duration")),
            "eval_duration_seconds": _ns_to_seconds(parsed.get("eval_duration")),
            "prompt_eval_count": parsed.get("prompt_eval_count"),
            "eval_count": parsed.get("eval_count"),
        }

    # Streaming mode: capture first token latency and aggregate response
    first_token_time: Optional[float] = None
    chunks: List[str] = []
    parsed_final: Dict[str, Any] | None = None

    try:
        with urlopen(req, timeout=timeout) as resp:  # nosec - local endpoint
            while True:
                line = resp.readline()
                if not line:
                    break
                try:
                    chunk = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                content = ((chunk.get("message") or {}).get("content") if isinstance(chunk, dict) else None)
                if isinstance(content, str) and content:
                    chunks.append(content)
                    if first_token_time is None:
                        first_token_time = time.perf_counter() - t0
                if chunk.get("done"):
                    parsed_final = chunk
                    break
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "elapsed_seconds": round(time.perf_counter() - t0, 6),
        }

    elapsed = time.perf_counter() - t0
    reply_text = "".join(chunks)
    record: Dict[str, Any] = {
        "ok": True,
        "elapsed_seconds": round(elapsed, 6),
        "ttfb_seconds": round(first_token_time, 6) if first_token_time is not None else None,
        "reply": reply_text,
    }

    if parsed_final:
        record.update(
            {
                "total_duration_seconds": _ns_to_seconds(parsed_final.get("total_duration")),
                "load_duration_seconds": _ns_to_seconds(parsed_final.get("load_duration")),
                "eval_duration_seconds": _ns_to_seconds(parsed_final.get("eval_duration")),
                "prompt_eval_count": parsed_final.get("prompt_eval_count"),
                "eval_count": parsed_final.get("eval_count"),
            }
        )

    return record


def write_report(df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Simple summary similar to other tools
    p95 = float(df["elapsed_seconds"].quantile(0.95)) if not df.empty else math.nan
    ttfb_mean = float(df["ttfb_seconds"].dropna().mean()) if "ttfb_seconds" in df.columns and df["ttfb_seconds"].notna().any() else math.nan
    summary = pd.DataFrame(
        [
            {
                "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "iterations": int(df.shape[0]),
                "avg_latency_seconds": round(float(df["elapsed_seconds"].mean()), 4) if not df.empty else math.nan,
                "p95_latency_seconds": round(p95, 4) if not math.isnan(p95) else math.nan,
                "errors": int((~df["ok"]).sum()) if "ok" in df.columns else 0,
                "avg_ttfb_seconds": round(ttfb_mean, 4) if not math.isnan(ttfb_mean) else None,
            }
        ]
    )
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="results")
        summary.to_excel(writer, index=False, sheet_name="summary")


def main() -> None:
    ap = argparse.ArgumentParser(description="Direct Ollama chat benchmark")
    ap.add_argument("--prompt", default="Send me back a number.", help="Prompt text to send")
    ap.add_argument("--count", type=int, default=100, help="Number of iterations to run")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations before measurement")
    ap.add_argument("--model", default=None, help="Ollama model name (defaults to $OLLAMA_MODEL or llama3.1:8b)")
    ap.add_argument("--host", default=None, help="Ollama host (defaults to $OLLAMA_HOST or http://127.0.0.1:11434)")
    ap.add_argument("--num-predict", type=int, default=200, help="Max tokens to generate (num_predict)")
    ap.add_argument("--temperature", type=float, default=0.7, help="Sampling temperature")
    ap.add_argument("--seed", type=int, help="Deterministic seed (optional)")
    ap.add_argument("--system", default=None, help="Optional system message")
    ap.add_argument("--output", default="data/ollama_direct_benchmark.xlsx", help="Excel output file")
    ap.add_argument("--log-csv", default="data/ollama_direct_benchmark_log.csv", help="CSV log file")
    ap.add_argument("--include-prompts", action="store_true", help="Include prompt and reply columns")
    ap.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout seconds")
    ap.add_argument("--stream", action="store_true", help="Enable streaming to capture time-to-first-byte")
    args = ap.parse_args()

    # Resolve defaults from environment without importing app.config
    import os

    host = args.host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    model = args.model or os.environ.get("OLLAMA_MODEL") or "llama3.1:8b"

    print(f"Backend: ollama model={model} host={host}")
    if args.system:
        print("System message: present")
    print(f"Prompt: {args.prompt}")
    print(f"Options: num_predict={args.num_predict} temperature={args.temperature} seed={args.seed}")

    # Warmup (not logged)
    for _ in range(max(args.warmup, 0)):
        _ = chat_once(
            host=host,
            model=model,
            prompt=args.prompt,
            system=args.system,
            num_predict=args.num_predict,
            temperature=args.temperature,
            seed=args.seed,
            timeout=args.timeout,
            stream=args.stream,
        )

    # Measured iterations
    records = []
    for i in range(1, args.count + 1):
        res = chat_once(
            host=host,
            model=model,
            prompt=args.prompt,
            system=args.system,
            num_predict=args.num_predict,
            temperature=args.temperature,
            seed=args.seed,
            timeout=args.timeout,
            stream=args.stream,
        )
        rec: Dict[str, Any] = {
            "iteration": i,
            **res,
        }
        if args.include_prompts:
            rec["prompt"] = args.prompt
        records.append(rec)

    df = pd.DataFrame.from_records(records)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(df, out_path)

    log_csv = Path(args.log_csv)
    log_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(log_csv, index=False)

    print(f"Processed {len(df)} prompts")
    if not df.empty:
        print(f"Average latency: {df['elapsed_seconds'].mean():.3f} seconds")
        if args.stream and df["ttfb_seconds"].notna().any():
            print(f"Average TTFB: {df['ttfb_seconds'].dropna().mean():.3f} seconds")
    print(f"Results written to: {out_path}")
    print(f"CSV log written to: {log_csv}")


if __name__ == "__main__":
    main()
