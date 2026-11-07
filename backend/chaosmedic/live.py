"""Live lab control.

Mutations go to fixed admin paths on the catalog services. The client never
builds a shell command. Telemetry is pulled from each service's debug export
and normalized into the same bundle the rules diagnoser reads.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from chaosmedic.catalog import BASELINE, SERVICES
from chaosmedic.runbooks import RunbookError, normalize_action
from chaosmedic.simulator import exclusive_ms

ADMIN_PATHS = {
    "restart_service": "/admin/restart",
    "rollback_config": "/admin/rollback-config",
    "scale_service": "/admin/scale",
    "toggle_flag": "/admin/flags",
}


def default_urls() -> dict[str, str]:
    return {
        "gateway": os.environ.get("GATEWAY_URL", "http://127.0.0.1:8001"),
        "orders": os.environ.get("ORDERS_URL", "http://127.0.0.1:8002"),
        "payments": os.environ.get("PAYMENTS_URL", "http://127.0.0.1:8003"),
        "cache": os.environ.get("CACHE_URL", "http://127.0.0.1:8004"),
        "ledger": os.environ.get("LEDGER_URL", "http://127.0.0.1:8005"),
    }


class LabError(RuntimeError):
    pass


class LabClient:
    def __init__(
        self,
        urls: dict[str, str] | None = None,
        token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.urls = urls or default_urls()
        self.token = token or os.environ.get("LAB_TOKEN", "local-lab")
        self.client = client or httpx.Client(timeout=5.0)

    def _headers(self) -> dict[str, str]:
        return {"X-Lab-Token": self.token}

    def clear(self) -> None:
        for service in SERVICES:
            self._post(service, "/admin/clear", {})

    def apply_fault(self, fault: str, service: str, detail: dict[str, Any]) -> None:
        if service not in self.urls:
            raise LabError("unknown service")
        if fault == "service_crash":
            body: dict[str, Any] = {"crashed": True}
        elif fault == "dependency_outage":
            body = {"crashed": True}
        elif fault == "latency_spike":
            body = {"latency_ms": int(detail["latency_ms"])}
        elif fault == "bad_config":
            body = {"config": detail["config"]}
        elif fault == "memory_leak":
            body = {"memory_leak": True}
        else:
            raise LabError("unknown fault")
        self._post(service, "/admin/faults", body)

    def execute(self, action: dict[str, Any]) -> None:
        try:
            cleaned = normalize_action(action)
        except RunbookError as exc:
            raise LabError(str(exc)) from exc
        path = ADMIN_PATHS[cleaned["action"]]
        body = {key: value for key, value in cleaned.items() if key not in {"action", "service"}}
        self._post(cleaned["service"], path, body)

    def restore_config(self, service: str, config: dict[str, Any]) -> None:
        if service not in self.urls:
            raise LabError("unknown service")
        allowed = set(BASELINE[service])
        if set(config) - allowed:
            raise LabError("config snapshot contains unknown keys")
        self._post(service, "/admin/config", {"config": config})

    def export_all(self) -> dict[str, dict[str, Any]]:
        exports = {}
        for service in SERVICES:
            try:
                response = self.client.get(
                    self.urls[service] + "/debug/export",
                    headers=self._headers(),
                )
                response.raise_for_status()
                exports[service] = response.json()
            except httpx.HTTPError:
                exports[service] = {"unreachable": True, "service": service}
        return exports

    def generate_traffic(self, attempts: int = 4) -> None:
        url = self.urls["gateway"] + "/checkout"
        for _ in range(attempts):
            try:
                self.client.post(url, json={"sku": "mug", "amount": 1800}, timeout=3.0)
            except httpx.HTTPError:
                continue

    def _post(self, service: str, path: str, body: dict[str, Any]) -> None:
        if service not in self.urls:
            raise LabError("unknown service")
        if not path.startswith("/admin/"):
            raise LabError("refusing a non-admin path")
        response = self.client.post(
            self.urls[service] + path,
            json=body,
            headers=self._headers(),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LabError(f"{service} {path} failed") from exc


def exports_to_bundle(exports: dict[str, dict[str, Any]], incident_key: str) -> dict[str, Any]:
    """Normalize per-service debug exports into one investigation bundle."""
    logs: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    health: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    configs: dict[str, Any] = {}
    log_index = 0
    for service in SERVICES:
        export = exports.get(service) or {"unreachable": True}
        if export.get("unreachable"):
            health[service] = {"service": service, "status": "down"}
            metrics[service] = _empty_metrics()
            configs[service] = dict(BASELINE[service])
            log_index += 1
            logs.append(
                {
                    "id": f"{incident_key}-pull-{log_index:03d}",
                    "service": "probe",
                    "level": "error",
                    "event": "health_failed",
                    "message": f"health check failed: {service} connection refused",
                    "trace_id": f"{incident_key}-probe-{service}",
                    "span_id": None,
                    "attributes": {"target": service},
                }
            )
            continue
        configs[service] = dict(export.get("config") or {})
        for log in export.get("logs") or []:
            logs.append(log)
        for span in export.get("spans") or []:
            spans.append(span)
        counters = export.get("counters") or {}
        requests = int(counters.get("requests") or 0)
        errors = int(counters.get("errors") or 0)
        error_rate = round(errors / requests, 4) if requests else 0.0
        memory = int(export.get("memory_mb") or 0)
        samples = [int(value) for value in (export.get("latency_samples_ms") or [])]
        p99 = max(samples) if samples else None
        crashed = bool(export.get("crashed"))
        if crashed:
            status = "down"
        elif error_rate >= 0.2 or (p99 or 0) >= 800 or memory >= 512:
            status = "degraded"
        else:
            status = "up"
        health[service] = {"service": service, "status": status}
        metrics[service] = {
            "requests": 0 if crashed else requests,
            "errors": 0 if crashed else errors,
            "error_rate": 0.0 if crashed else error_rate,
            "p50_ms": None if crashed else p99,
            "p99_ms": None if crashed else p99,
            "memory_mb": 0 if crashed else memory,
            "replicas": int(export.get("replicas") or 1),
            "exclusive_ms": None if crashed else exclusive_ms(spans, service),
            "p99_series": export.get("p99_series") or ([p99 or 0] * 12),
            "error_rate_series": export.get("error_rate_series") or ([int(error_rate * 100)] * 12),
            "memory_series": export.get("memory_series") or ([memory] * 12),
        }
    # Recompute exclusive time after every span is present.
    for service in SERVICES:
        if health[service]["status"] == "down":
            metrics[service]["exclusive_ms"] = None
        else:
            metrics[service]["exclusive_ms"] = exclusive_ms(spans, service)
    return {
        "incident_key": incident_key,
        "health": health,
        "metrics": metrics,
        "logs": logs,
        "spans": spans,
        "configs": configs,
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "requests": 0,
        "errors": 0,
        "error_rate": 0.0,
        "p50_ms": None,
        "p99_ms": None,
        "memory_mb": 0,
        "replicas": 1,
        "exclusive_ms": None,
        "p99_series": [0] * 12,
        "error_rate_series": [0] * 12,
        "memory_series": [0] * 12,
    }

