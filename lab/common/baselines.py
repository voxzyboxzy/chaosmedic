"""Baseline manifests for the lab containers.

Kept in sync with chaosmedic.catalog.BASELINE. The lab image does not import
the control plane.
"""

from __future__ import annotations

from typing import Any

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

FLAGS = {
    "cache_bypass": "orders",
    "payments_fallback": "gateway",
}

LEAK_EXTRA_MS = 180
LEAK_MEMORY_MB = 880
BASE_MEMORY_MB = 72
