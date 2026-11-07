"""Read-only investigation tools.

Tool methods only read a telemetry bundle. They do not mutate the lab and
they do not accept a command string.
"""

from __future__ import annotations

from typing import Any

from chaosmedic.catalog import BASELINE, SERVICES, drift_keys
from chaosmedic.simulator import exclusive_ms


class Toolset:
    """Closed set of reads over one incident bundle."""

    def __init__(self, bundle: dict[str, Any]) -> None:
        self.bundle = bundle
        self.calls: list[dict[str, Any]] = []

    def _record(self, name: str, args: dict[str, Any], result: Any) -> Any:
        self.calls.append({"tool": name, "args": args})
        return result

    def list_services(self) -> list[str]:
        return self._record("list_services", {}, list(SERVICES))

    def get_health(self) -> dict[str, Any]:
        return self._record("get_health", {}, self.bundle["health"])

    def get_metrics(self, service: str) -> dict[str, Any]:
        self._require_service(service)
        metric = dict(self.bundle["metrics"][service])
        # Exclusive time is derived from spans so a forged metric cannot steer
        # the diagnosis. The stored value is for the dashboard only.
        metric["exclusive_ms"] = exclusive_ms(self._spans(), service)
        return self._record("get_metrics", {"service": service}, metric)

    def get_logs(
        self,
        service: str | None = None,
        event: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = []
        for log in self.bundle["logs"]:
            if service is not None and log["service"] != service:
                continue
            if event is not None and log["event"] != event:
                continue
            rows.append(log)
        return self._record("get_logs", {"service": service, "event": event}, rows)

    def get_traces(self, service: str | None = None) -> list[dict[str, Any]]:
        spans = self._spans()
        if service is not None:
            self._require_service(service)
            trace_ids = {span["trace_id"] for span in spans if span["service"] == service}
            spans = [span for span in spans if span["trace_id"] in trace_ids]
        return self._record("get_traces", {"service": service}, spans)

    def get_config(self, service: str) -> dict[str, Any]:
        self._require_service(service)
        current = self.bundle["configs"][service]
        result = {
            "service": service,
            "current": current,
            "baseline": BASELINE[service],
            "drift_keys": drift_keys(service, current),
        }
        return self._record("get_config", {"service": service}, result)

    def get_dependency_probes(self) -> dict[str, str]:
        health = self.bundle["health"]
        result = {
            "cache": health["cache"]["status"],
            "ledger": health["ledger"]["status"],
        }
        return self._record("get_dependency_probes", {}, result)

    def get_exclusive_latency(self) -> dict[str, int | None]:
        spans = self._spans()
        result = {service: exclusive_ms(spans, service) for service in SERVICES}
        return self._record("get_exclusive_latency", {}, result)

    def _spans(self) -> list[dict[str, Any]]:
        return list(self.bundle["spans"])

    def _require_service(self, service: str) -> None:
        if service not in SERVICES:
            raise KeyError(service)
