"""Live lab processes on localhost. The diagnoser reads their exported telemetry."""

from __future__ import annotations

import os
import threading
import time

import httpx
import pytest
import uvicorn

os.environ["LAB_INSTANT"] = "1"
os.environ["ORDERS_URL"] = "http://127.0.0.1:8012"
os.environ["PAYMENTS_URL"] = "http://127.0.0.1:8013"
os.environ["CACHE_URL"] = "http://127.0.0.1:8014"
os.environ["LEDGER_URL"] = "http://127.0.0.1:8015"

from chaosmedic.catalog import BASELINE as CONTROL_BASELINE
from chaosmedic.diagnoser import diagnose
from chaosmedic.live import LabClient, exports_to_bundle
from lab.cache.main import app as cache_app
from lab.cache.main import runtime as cache_runtime
from lab.common.baselines import BASELINE as LAB_BASELINE
from lab.common.runtime import set_transport
from lab.gateway.main import app as gateway_app
from lab.gateway.main import runtime as gateway_runtime
from lab.ledger.main import app as ledger_app
from lab.ledger.main import runtime as ledger_runtime
from lab.orders.main import app as orders_app
from lab.orders.main import runtime as orders_runtime
from lab.payments.main import app as payments_app
from lab.payments.main import runtime as payments_runtime

URLS = {
    "gateway": "http://127.0.0.1:8011",
    "orders": "http://127.0.0.1:8012",
    "payments": "http://127.0.0.1:8013",
    "cache": "http://127.0.0.1:8014",
    "ledger": "http://127.0.0.1:8015",
}
APPS = {
    "gateway": gateway_app,
    "orders": orders_app,
    "payments": payments_app,
    "cache": cache_app,
    "ledger": ledger_app,
}
RUNTIMES = [
    gateway_runtime,
    orders_runtime,
    payments_runtime,
    cache_runtime,
    ledger_runtime,
]


def _serve(name: str) -> uvicorn.Server:
    port = int(URLS[name].rsplit(":", 1)[1])
    config = uvicorn.Config(APPS[name], host="127.0.0.1", port=port, log_level="error", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name=f"lab-{name}")
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"{URLS[name]}/health", timeout=0.3)
            return server
        except httpx.HTTPError:
            time.sleep(0.05)
    raise RuntimeError(f"{name} did not start")


@pytest.fixture(scope="module", autouse=True)
def lab_servers() -> None:
    set_transport(None)
    for name in URLS:
        _serve(name)


def _lab() -> LabClient:
    for runtime in RUNTIMES:
        runtime.clear()
    return LabClient(urls=URLS, token="local-lab", client=httpx.Client(timeout=5.0))


def test_lab_baselines_match_the_control_plane() -> None:
    assert LAB_BASELINE == CONTROL_BASELINE


def test_healthy_checkout_then_payments_latency_is_diagnosed() -> None:
    lab = _lab()
    lab.generate_traffic(2)
    healthy = exports_to_bundle(lab.export_all(), "healthy-live")
    assert healthy["health"]["gateway"]["status"] == "up"
    for runtime in RUNTIMES:
        runtime.clear()
    lab.apply_fault("latency_spike", "payments", {"latency_ms": 900})
    lab.generate_traffic(3)
    bundle = exports_to_bundle(lab.export_all(), "latency-live")
    top = diagnose(bundle)["hypotheses"][0]
    assert (top["fault"], top["service"]) == ("latency_spike", "payments")
    lab.execute({"action": "scale_service", "service": "payments", "replicas": 3})
    for runtime in RUNTIMES:
        runtime.spans.clear()
        runtime.logs.clear()
        runtime.requests = 0
        runtime.errors = 0
        runtime.latency_samples.clear()
    lab.generate_traffic(2)
    repaired = exports_to_bundle(lab.export_all(), "latency-repaired")
    assert repaired["health"]["payments"]["status"] == "up"
    assert repaired["metrics"]["payments"]["p99_ms"] < 500


def test_live_faults_match_their_classes() -> None:
    scenarios = [
        ("service_crash", "orders", {}, "service_crash", "orders"),
        ("bad_config", "payments", {"config": {"mode": "reject_all"}}, "bad_config", "payments"),
        ("dependency_outage", "cache", {}, "dependency_outage", "cache"),
        ("dependency_outage", "ledger", {}, "dependency_outage", "ledger"),
        ("memory_leak", "orders", {}, "memory_leak", "orders"),
        ("bad_config", "gateway", {"config": {"timeout_ms": 5}}, "bad_config", "gateway"),
    ]
    for fault, service, detail, expect_fault, expect_service in scenarios:
        lab = _lab()
        lab.apply_fault(fault, service, detail)
        lab.generate_traffic(2)
        bundle = exports_to_bundle(lab.export_all(), f"live-{fault}-{service}")
        top = diagnose(bundle)["hypotheses"][0]
        assert (top["fault"], top["service"]) == (expect_fault, expect_service), (
            bundle["health"],
            top,
        )


def test_admin_token_is_required() -> None:
    lab = _lab()
    denied = lab.client.post(f"{URLS['orders']}/admin/restart")
    assert denied.status_code == 401
