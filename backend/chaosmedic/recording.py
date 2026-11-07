"""Record demo and held-out incidents from the lab simulator.

The files under replay/ are what the dashboard and the eval harness load.
They contain telemetry, not a lookup table for the diagnoser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chaosmedic.simulator import healthy_world, inject, observe

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "replay" / "demo"
HELDOUT_DIR = ROOT / "replay" / "heldout"


def _case(
    *,
    case_id: str,
    title: str,
    summary: str,
    fault: str,
    service: str,
    correct_actions: list[dict[str, Any]],
    order: int,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = detail or {}
    world = inject(healthy_world(), fault, service, **detail)
    telemetry = observe(world, case_id)
    return {
        "id": case_id,
        "title": title,
        "summary": summary,
        "order": order,
        "ground_truth": {"fault": fault, "service": service, "detail": detail},
        "correct_actions": correct_actions,
        "world": world.to_dict(),
        "telemetry": telemetry,
    }


def _restart(service: str) -> dict[str, Any]:
    return {"action": "restart_service", "service": service}


def _scale(service: str) -> dict[str, Any]:
    return {"action": "scale_service", "service": service, "replicas": 3}


def _rollback(service: str) -> dict[str, Any]:
    return {"action": "rollback_config", "service": service}


def demo_cases() -> list[dict[str, Any]]:
    return [
        _case(
            case_id="checkout-timeouts",
            title="Checkout timeouts",
            summary="Shoppers are hitting the gateway deadline during checkout.",
            fault="latency_spike",
            service="payments",
            detail={"latency_ms": 2600},
            correct_actions=[_scale("payments")],
            order=1,
        ),
        _case(
            case_id="orders-unreachable",
            title="Orders go unreachable",
            summary="The gateway can no longer open a connection to orders.",
            fault="service_crash",
            service="orders",
            correct_actions=[_restart("orders")],
            order=2,
        ),
        _case(
            case_id="edge-down",
            title="Edge stops accepting",
            summary="The gateway process is not answering health checks.",
            fault="service_crash",
            service="gateway",
            correct_actions=[_restart("gateway")],
            order=3,
        ),
        _case(
            case_id="charges-declined",
            title="Every charge is declined",
            summary="Payments is up, but every charge is refused immediately.",
            fault="bad_config",
            service="payments",
            detail={"config": {"mode": "reject_all"}},
            correct_actions=[_rollback("payments")],
            order=4,
        ),
        _case(
            case_id="cache-unreachable",
            title="Cache failures stop checkout",
            summary="Orders cannot price a cart because the cache is unreachable.",
            fault="dependency_outage",
            service="cache",
            correct_actions=[
                _restart("cache"),
                {
                    "action": "toggle_flag",
                    "service": "orders",
                    "flag": "cache_bypass",
                    "value": True,
                },
            ],
            order=5,
        ),
        _case(
            case_id="ledger-unreachable",
            title="Ledger stops taking entries",
            summary="Charges fail when payments tries to append to the ledger.",
            fault="dependency_outage",
            service="ledger",
            correct_actions=[_restart("ledger")],
            order=6,
        ),
        _case(
            case_id="orders-memory",
            title="Orders memory climbs",
            summary="Orders is still serving, and its memory graph is a ramp.",
            fault="memory_leak",
            service="orders",
            correct_actions=[_restart("orders")],
            order=7,
        ),
        _case(
            case_id="wrong-payments-host",
            title="Orders calls the wrong payments host",
            summary="Orders is configured with a payments address that is not in the manifest.",
            fault="bad_config",
            service="orders",
            detail={"config": {"upstream_payments": "http://payments.invalid:8000"}},
            correct_actions=[_rollback("orders")],
            order=8,
        ),
    ]


def heldout_cases() -> list[dict[str, Any]]:
    """Parameter combinations that are not the demo scenarios."""
    return [
        _case(
            case_id="ho-payments-crash",
            title="Payments process missing",
            summary="Held-out crash of the payments process.",
            fault="service_crash",
            service="payments",
            correct_actions=[_restart("payments")],
            order=1,
        ),
        _case(
            case_id="ho-gateway-latency",
            title="Gateway think time",
            summary="Held-out latency injected on the gateway itself.",
            fault="latency_spike",
            service="gateway",
            detail={"latency_ms": 1100},
            correct_actions=[_scale("gateway")],
            order=2,
        ),
        _case(
            case_id="ho-orders-latency",
            title="Orders think time",
            summary="Held-out latency injected on orders.",
            fault="latency_spike",
            service="orders",
            detail={"latency_ms": 1700},
            correct_actions=[_scale("orders")],
            order=3,
        ),
        _case(
            case_id="ho-payments-slow-success",
            title="Slow successful charges",
            summary="Payments is slow but still inside the gateway deadline.",
            fault="latency_spike",
            service="payments",
            detail={"latency_ms": 900},
            correct_actions=[_scale("payments")],
            order=4,
        ),
        _case(
            case_id="ho-payments-very-slow",
            title="Payments near the deadline",
            summary="Payments latency that stays just under the gateway timeout.",
            fault="latency_spike",
            service="payments",
            detail={"latency_ms": 2200},
            correct_actions=[_scale("payments")],
            order=5,
        ),
        _case(
            case_id="ho-gateway-timeout-config",
            title="Gateway deadline too small",
            summary="Held-out bad timeout_ms on the gateway.",
            fault="bad_config",
            service="gateway",
            detail={"config": {"timeout_ms": 5}},
            correct_actions=[_rollback("gateway")],
            order=6,
        ),
        _case(
            case_id="ho-payments-sandbox-mode",
            title="Payments left in sandbox",
            summary="Held-out payments mode that refuses live charges.",
            fault="bad_config",
            service="payments",
            detail={"config": {"mode": "sandbox_reject"}},
            correct_actions=[_rollback("payments")],
            order=7,
        ),
        _case(
            case_id="ho-orders-bad-address",
            title="Orders points at an unroutable address",
            summary="Held-out bad upstream address on orders.",
            fault="bad_config",
            service="orders",
            detail={"config": {"upstream_payments": "http://10.0.0.9:9"}},
            correct_actions=[_rollback("orders")],
            order=8,
        ),
        _case(
            case_id="ho-cache-down",
            title="Cache probe failing",
            summary="Held-out cache outage.",
            fault="dependency_outage",
            service="cache",
            correct_actions=[
                _restart("cache"),
                {
                    "action": "toggle_flag",
                    "service": "orders",
                    "flag": "cache_bypass",
                    "value": True,
                },
            ],
            order=9,
        ),
        _case(
            case_id="ho-ledger-down",
            title="Ledger probe failing",
            summary="Held-out ledger outage.",
            fault="dependency_outage",
            service="ledger",
            correct_actions=[_restart("ledger")],
            order=10,
        ),
        _case(
            case_id="ho-payments-memory",
            title="Payments memory ramp",
            summary="Held-out memory leak on payments.",
            fault="memory_leak",
            service="payments",
            correct_actions=[_restart("payments")],
            order=11,
        ),
        _case(
            case_id="ho-orders-memory",
            title="Orders memory ramp",
            summary="Held-out memory leak on orders with a different incident id.",
            fault="memory_leak",
            service="orders",
            correct_actions=[_restart("orders")],
            order=12,
        ),
        _case(
            case_id="ho-gateway-crash",
            title="Gateway process missing",
            summary="Held-out gateway crash.",
            fault="service_crash",
            service="gateway",
            correct_actions=[_restart("gateway")],
            order=13,
        ),
        _case(
            case_id="ho-orders-crash",
            title="Orders process missing",
            summary="Held-out orders crash.",
            fault="service_crash",
            service="orders",
            correct_actions=[_restart("orders")],
            order=14,
        ),
    ]


def write_recordings() -> None:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    HELDOUT_DIR.mkdir(parents=True, exist_ok=True)
    for directory, cases in ((DEMO_DIR, demo_cases()), (HELDOUT_DIR, heldout_cases())):
        for case in cases:
            path = directory / f"{case['id']}.json"
            path.write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_cases(directory: Path) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(directory.glob("*.json")):
        cases.append(json.loads(path.read_text(encoding="utf-8")))
    cases.sort(key=lambda item: (item.get("order", 0), item["id"]))
    return cases


if __name__ == "__main__":
    write_recordings()
    print(f"wrote {len(list(DEMO_DIR.glob('*.json')))} demo and {len(list(HELDOUT_DIR.glob('*.json')))} held-out incidents")
