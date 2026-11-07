"""Recovery checks over telemetry the investigator can see."""

from __future__ import annotations

from typing import Any

from chaosmedic.catalog import APP_SERVICES


def verify(bundle: dict[str, Any]) -> dict[str, Any]:
    """Golden signals for the checkout path.

    Infrastructure may stay down when an approved bypass flag is the repair.
    Application processes have to be up, under the memory ceiling, and the
    gateway has to be fast and mostly successful.
    """
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    for service in APP_SERVICES:
        status = bundle["health"][service]["status"]
        add(f"{service}_up", status == "up", f"{service} health is {status}")
        memory = bundle["metrics"][service]["memory_mb"]
        add(
            f"{service}_memory",
            memory < 400,
            f"{service} memory is {memory}MB",
        )

    gateway = bundle["metrics"]["gateway"]
    error_rate = float(gateway["error_rate"])
    p99 = gateway["p99_ms"]
    p99_value = 10_000 if p99 is None else int(p99)
    add(
        "gateway_error_rate",
        error_rate <= 0.05,
        f"gateway error rate is {error_rate:.2%}",
    )
    add(
        "gateway_latency",
        p99_value < 500,
        f"gateway p99 is {p99_value}ms",
    )
    return {"passed": all(check["ok"] for check in checks), "checks": checks}
