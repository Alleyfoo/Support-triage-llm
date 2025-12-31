import http.server
import json
import socketserver
import threading
from pathlib import Path

import pytest

from tools import service_status


@pytest.fixture
def http_ok_server():
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # type: ignore[override]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}/health"
        httpd.shutdown()
        thread.join()


def test_service_status_up(http_ok_server, tmp_path, monkeypatch):
    registry_path = tmp_path / "services_registry.json"
    registry = {
        "api": {
            "check": {"type": "http", "url": http_ok_server, "method": "GET"},
            "expected": {"status_min": 200, "status_max": 299, "body_contains": "ok"},
            "timeout_ms": 500,
            "retries": 0,
            "scope": "internal",
        }
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(service_status, "SERVICES_REGISTRY_PATH", registry_path)
    service_status.CACHE.clear()

    res = service_status.run_service_status({"service_id": "api", "tenant_id": "t1", "region": None})
    meta = res["metadata"]
    assert meta["status"] == "up"
    assert meta["dns_ok"] is True
    assert meta["http_status"] == 200
    assert meta["confidence"] >= 0.5


def test_service_status_unknown_on_dns_fail(tmp_path, monkeypatch):
    registry_path = tmp_path / "services_registry.json"
    registry = {
        "api": {
            "check": {"type": "http", "url": "http://nonexistent.invalid/health", "method": "GET"},
            "expected": {"status_min": 200, "status_max": 299},
            "timeout_ms": 500,
            "retries": 0,
            "scope": "internal",
        }
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(service_status, "SERVICES_REGISTRY_PATH", registry_path)
    service_status.CACHE.clear()
    res = service_status.run_service_status({"service_id": "api", "tenant_id": None, "region": None})
    meta = res["metadata"]
    assert meta["status"] == "unknown"
    assert meta["dns_ok"] is False


def test_service_status_down_on_fail_endpoint(tmp_path, monkeypatch):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # type: ignore[override]
            if self.path.endswith("/fail"):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"fail")
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        registry_path = tmp_path / "services_registry.json"
        registry = {
            "api": {
                "check": {"type": "http", "url": f"http://127.0.0.1:{port}/health/fail", "method": "GET"},
                "expected": {"status_min": 200, "status_max": 299, "body_contains": "ok"},
                "timeout_ms": 500,
                "retries": 0,
                "scope": "internal",
            }
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        monkeypatch.setattr(service_status, "SERVICES_REGISTRY_PATH", registry_path)
        service_status.CACHE.clear()

        res = service_status.run_service_status({"service_id": "api", "tenant_id": "t1", "region": None})
        meta = res["metadata"]
        assert meta["status"] == "down"
        assert meta["http_status"] == 500

        httpd.shutdown()
        thread.join()
