"""Service catalog, baselines, and the repair allowlist.

The allowlist is the only set of mutations the incident commander may propose.
There is no shell action and no free-form command field.
"""

from __future__ import annotations

from typing import Any

SERVICES: tuple[str, ...] = ("gateway", "orders", "payments", "cache", "ledger")
APP_SERVICES: tuple[str, ...] = ("gateway", "orders", "payments")
INFRA_SERVICES: tuple[str, ...] = ("cache", "ledger")

FAULTS: tuple[str, ...] = (
    "service_crash",
    "latency_spike",
    "bad_config",
    "dependency_outage",
    "memory_leak",
)

ACTIONS: tuple[str, ...] = (
    "restart_service",
    "rollback_config",
    "scale_service",
    "toggle_flag",
)

# Fields a caller is allowed to send. Anything else is rejected.
ACTION_FIELDS: frozenset[str] = frozenset(
    {"action", "service", "replicas", "flag", "value"}
)

EDGES: tuple[tuple[str, str], ...] = (
    ("gateway", "orders"),
    ("orders", "cache"),
    ("orders", "payments"),
    ("payments", "ledger"),
)

BASE_MEMORY_MB = 72
LEAK_MEMORY_MB = 880
LEAK_EXTRA_MS = 180
# Exclusive server time at or above this is treated as a latency spike.
LATENCY_SPIKE_EXCLUSIVE_MS = 500
# Exclusive time below this is evidence against a latency spike.
LATENCY_LOW_EXCLUSIVE_MS = 120

BASELINE: dict[str, dict[str, Any]] = {
    "gateway": {
        "upstream_orders": "http://orders:8000",
        "timeout_ms": 2500,
        "payments_fallback": False,
    },
    "orders": {
        "upstream_payments": "http://payments:8000",
        "cache_url": "http://cache:8000",
        "cache_bypass": False,
    },
    "payments": {
        "ledger_url": "http://ledger:8000",
        "mode": "live",
    },
    "cache": {},
    "ledger": {},
}

FLAGS: dict[str, str] = {
    "cache_bypass": "orders",
    "payments_fallback": "gateway",
}

REPLICA_CHOICES: frozenset[int] = frozenset({1, 2, 3})


def drift_keys(service: str, config: dict[str, Any]) -> list[str]:
    base = BASELINE[service]
    keys = sorted(set(base) | set(config))
    return [key for key in keys if config.get(key) != base.get(key)]
