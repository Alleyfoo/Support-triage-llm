from __future__ import annotations

import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from app import queue_db

SERVICES_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "services_registry.json"
CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 60
DNS_TIMEOUT_SEC = 1.0
CONNECT_TIMEOUT_SEC = 1.5
READ_TIMEOUT_SEC = 1.5
BODY_READ_LIMIT = 8192
MAX_REDIRECTS = 0  # disable redirects by default
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_SEC = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_registry() -> Dict[str, Any]:
    if not SERVICES_REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(SERVICES_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_host(host: str, port: int | None = None) -> Tuple[bool, List[str], str | None]:
    def _do_resolve() -> List[str]:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        return [info[4][0] for info in infos]

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_do_resolve)
        try:
            addrs = future.result(timeout=DNS_TIMEOUT_SEC)
            return True, list(dict.fromkeys(addrs)), None
        except TimeoutError:
            return False, [], "dns_timeout"
        except Exception as exc:  # pragma: no cover - defensive
            return False, [], str(exc)


def _block_private_ips(addrs: List[str]) -> bool:
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                return True
        except ValueError:
            continue
    return False


def _http_check(url: str, method: str, timeout: Tuple[float, float], body_contains: str | None) -> Tuple[int, float, str]:
    start = time.perf_counter()
    try:
        resp = requests.request(method=method, url=url, timeout=timeout, allow_redirects=False, stream=bool(body_contains))
        latency_ms = (time.perf_counter() - start) * 1000
        snippet = ""
        if body_contains:
            try:
                chunk = resp.raw.read(BODY_READ_LIMIT, decode_content=True)  # type: ignore[attr-defined]
                snippet = chunk.decode(errors="ignore") if hasattr(chunk, "decode") else str(chunk)
            except Exception:
                snippet = ""
        return resp.status_code, latency_ms, snippet
    except Exception as exc:
        return -1, (time.perf_counter() - start) * 1000, str(exc)


def run_service_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    service_status: Check health for an allowlisted service_id.
    """
    service_id = params["service_id"]
    tenant_id = params.get("tenant_id")
    region = params.get("region") or ""

    registry = _load_registry()
    if service_id not in registry:
        raise ValueError(f"Service not allowlisted: {service_id}")
    entry = registry[service_id]
    check = entry.get("check") or {}
    url = check.get("url")
    method = check.get("method") or "GET"
    expected = entry.get("expected") or {}
    timeout = (CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC)
    retries = int(entry.get("retries", 1))
    status_min = expected.get("status_min", 200)
    status_max = expected.get("status_max", 299)
    body_contains = expected.get("body_contains")

    cache_key = (service_id, region or "")
    cached = CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached.get("ts", 0) <= CACHE_TTL_SECONDS:
        return cached["result"]

    # Circuit breaker check
    breaker = queue_db.get_service_breaker(service_id, entry.get("scope", "external"))
    now_dt = datetime.now(timezone.utc)
    cooldown_ts = breaker.get("cooldown_until") if breaker else None
    if breaker and cooldown_ts:
        try:
            cooldown_dt = datetime.fromisoformat(cooldown_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
            if cooldown_dt > now_dt:
                checked_at = _now_iso()
                metadata = {
                    "service_id": service_id,
                    "tenant_id": tenant_id,
                    "region": region or None,
                    "status": "unknown",
                    "http_status": None,
                    "latency_ms": None,
                    "dns_ok": False,
                    "scope": entry.get("scope", "external"),
                    "confidence": 0.2,
                    "notes": ["circuit_open"],
                }
                result = {
                    "source": "app_events",
                    "evidence_type": "service_status",
                    "time_window": {"start": checked_at, "end": checked_at},
                    "tenant": tenant_id if tenant_id else None,
                    "summary_counts": {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0},
                    "metadata": metadata,
                    "events": [
                        {
                            "ts": checked_at,
                            "type": "service_status",
                            "id": f"svc-{service_id}-{checked_at}",
                            "message_id": None,
                            "detail": f"{service_id} status=unknown circuit_open",
                        }
                    ],
                }
                return result
        except Exception:
            pass

    checked_at = _now_iso()
    status = "unknown"
    http_status = -1
    latency_ms = None
    dns_ok = False
    body_snippet = ""
    notes: List[str] = []

    parsed = requests.utils.urlparse(url or "")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host:
        dns_ok, addrs, dns_err = _resolve_host(host, port)
        if not dns_ok:
            notes.append(dns_err or "dns_failed")
        elif _block_private_ips(addrs) and entry.get("scope", "external") == "external":
            notes.append("blocked_non_public_ip")
            metadata = {
                "service_id": service_id,
                "tenant_id": tenant_id,
                "region": region or None,
                "status": "unknown",
                "http_status": None,
                "latency_ms": None,
                "dns_ok": True,
                "scope": entry.get("scope", "external"),
                "confidence": 0.2,
                "notes": notes,
            }
            detail = f"{service_id} status=unknown http=blocked_non_public_ip"
            result = {
                "source": "app_events",
                "evidence_type": "service_status",
                "time_window": {"start": checked_at, "end": checked_at},
                "tenant": tenant_id if tenant_id else None,
                "summary_counts": {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0},
                "metadata": metadata,
                "events": [
                    {
                        "ts": checked_at,
                        "type": "service_status",
                        "id": f"svc-{service_id}-{checked_at}",
                        "message_id": None,
                        "detail": detail,
                    }
                ],
            }
            CACHE[cache_key] = {"ts": now, "result": result}
            return result
    else:
        notes.append("missing_host")

    attempts = 0
    if dns_ok and url:
        while attempts <= retries:
            attempts += 1
            http_status, latency_ms, body_snippet = _http_check(url, method, timeout, body_contains)
            if http_status != -1:
                break
            time.sleep(0.1 * attempts)
    else:
        notes.append("dns_failed" if not dns_ok else "missing_url")

    conf = 0.2
    if 300 <= http_status < 400:
        notes.append("redirect_blocked")
        status = "unknown"
        http_status = http_status
    elif http_status != -1 and expected:
        body_ok = True
        if body_contains:
            body_ok = body_contains in (body_snippet or "")
        if status_min <= http_status <= status_max and body_ok:
            status = "up"
            conf = 0.8
        else:
            status = "down"
            conf = 0.6
    elif http_status != -1:
        status = "unknown"
        conf = 0.4
    else:
        status = "unknown"
        conf = 0.2

    metadata = {
        "service_id": service_id,
        "tenant_id": tenant_id,
        "region": region or None,
        "status": status,
        "http_status": http_status if http_status != -1 else None,
        "latency_ms": latency_ms,
        "dns_ok": dns_ok,
        "scope": entry.get("scope", "external"),
        "confidence": conf,
        "notes": notes,
    }
    detail_parts = [
        f"{service_id} status={status}",
        f"http={http_status}" if http_status != -1 else "http=unreachable",
        f"latency_ms={int(latency_ms)}" if latency_ms is not None else "",
    ]
    detail = " ".join(p for p in detail_parts if p).strip()

    result = {
        "source": "app_events",
        "evidence_type": "service_status",
        "time_window": {"start": checked_at, "end": checked_at},
        "tenant": tenant_id if tenant_id else None,
        "summary_counts": {"sent": 0, "bounced": 0, "deferred": 0, "delivered": 0},
        "metadata": metadata,
        "events": [
            {
                "ts": checked_at,
                "type": "service_status",
                "id": f"svc-{service_id}-{checked_at}",
                "message_id": None,
                "detail": detail,
            }
        ],
    }

    failure = status in {"down", "unknown"}
    if failure:
        queue_db.bump_service_breaker_failure(
            service_id,
            entry.get("scope", "external"),
            now_dt,
            BREAKER_THRESHOLD,
            BREAKER_COOLDOWN_SEC,
            notes[0] if notes else "failure",
        )
    else:
        queue_db.reset_service_breaker(service_id, entry.get("scope", "external"))

    CACHE[cache_key] = {"ts": now, "result": result}
    return result
