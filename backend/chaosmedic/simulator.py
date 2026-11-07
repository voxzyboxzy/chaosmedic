"""Deterministic model of the lab.

Replay mode, the eval harness, and recovery checks all observe this world.
Live containers implement the same faults; they are not required to diagnose
a recorded incident.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from chaosmedic.catalog import (
    APP_SERVICES,
    BASE_MEMORY_MB,
    BASELINE,
    FLAGS,
    INFRA_SERVICES,
    LEAK_EXTRA_MS,
    LEAK_MEMORY_MB,
    SERVICES,
    drift_keys,
)


@dataclass
class ServiceState:
    name: str
    crashed: bool = False
    latency_ms: int = 0
    memory_leak: bool = False
    replicas: int = 1
    config: dict[str, Any] = field(default_factory=dict)

    def memory_mb(self) -> int:
        return LEAK_MEMORY_MB if self.memory_leak else BASE_MEMORY_MB

    def own_ms(self) -> int:
        extra = 0 if self.latency_ms and self.replicas >= 3 else self.latency_ms
        if self.memory_leak:
            extra += LEAK_EXTRA_MS
        return 8 + extra


@dataclass
class World:
    services: dict[str, ServiceState]

    def clone(self) -> World:
        return World(
            services={
                name: ServiceState(
                    name=svc.name,
                    crashed=svc.crashed,
                    latency_ms=svc.latency_ms,
                    memory_leak=svc.memory_leak,
                    replicas=svc.replicas,
                    config=deepcopy(svc.config),
                )
                for name, svc in self.services.items()
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "services": {
                name: {
                    "crashed": svc.crashed,
                    "latency_ms": svc.latency_ms,
                    "memory_leak": svc.memory_leak,
                    "replicas": svc.replicas,
                    "config": deepcopy(svc.config),
                }
                for name, svc in self.services.items()
            }
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> World:
        services: dict[str, ServiceState] = {}
        for name in SERVICES:
            raw = payload["services"][name]
            services[name] = ServiceState(
                name=name,
                crashed=bool(raw["crashed"]),
                latency_ms=int(raw["latency_ms"]),
                memory_leak=bool(raw["memory_leak"]),
                replicas=int(raw["replicas"]),
                config=deepcopy(raw["config"]),
            )
        return World(services=services)


def healthy_world() -> World:
    return World(
        services={
            name: ServiceState(name=name, config=deepcopy(BASELINE[name]))
            for name in SERVICES
        }
    )


def inject(world: World, fault: str, service: str, **detail: Any) -> World:
    """Return a new world with one fault applied. Does not mutate `world`."""
    if service not in SERVICES:
        raise ValueError(f"unknown service {service}")
    updated = world.clone()
    target = updated.services[service]
    if fault == "service_crash":
        if service not in APP_SERVICES:
            raise ValueError("service_crash applies to gateway, orders, and payments")
        target.crashed = True
    elif fault == "dependency_outage":
        if service not in INFRA_SERVICES:
            raise ValueError("dependency_outage applies to cache and ledger")
        target.crashed = True
    elif fault == "latency_spike":
        if service not in APP_SERVICES:
            raise ValueError("latency_spike applies to application services")
        target.latency_ms = int(detail["latency_ms"])
    elif fault == "bad_config":
        if service not in APP_SERVICES:
            raise ValueError("bad_config applies to application services")
        target.config.update(detail["config"])
    elif fault == "memory_leak":
        if service not in APP_SERVICES:
            raise ValueError("memory_leak applies to application services")
        target.memory_leak = True
    else:
        raise ValueError(f"unknown fault {fault}")
    return updated


def apply_action(world: World, action: dict[str, Any]) -> World:
    """Apply one allowlisted repair. Unknown actions raise ValueError.

    Scaling to 3 replicas sheds an injected latency spike. Restart clears a
    crash and a memory leak, and leaves config and latency in place. Config
    rollback restores the baseline manifest. Flag toggles only touch flags
    declared in the catalog.
    """
    updated = world.clone()
    name = action["action"]
    service_name = action["service"]
    if service_name not in updated.services:
        raise ValueError("unknown service")
    target = updated.services[service_name]
    if name == "restart_service":
        target.crashed = False
        target.memory_leak = False
    elif name == "scale_service":
        target.replicas = int(action["replicas"])
    elif name == "rollback_config":
        target.config = deepcopy(BASELINE[service_name])
    elif name == "toggle_flag":
        flag = action["flag"]
        if FLAGS.get(flag) != service_name:
            raise ValueError("flag is not allowlisted for this service")
        if not isinstance(action["value"], bool):
            raise ValueError("flag value must be boolean")
        target.config[flag] = action["value"]
    else:
        raise ValueError("action is not allowlisted")
    return updated


def _series(end: int, *, climb: int | None = None, points: int = 12) -> list[int]:
    if climb is None:
        return [int(end)] * points
    if points == 1:
        return [int(end)]
    return [
        int(round(climb + (end - climb) * index / (points - 1)))
        for index in range(points)
    ]


def _health(crashed: bool, error_rate: float, p99_ms: int | None, memory_mb: int) -> str:
    if crashed:
        return "down"
    p99 = 0 if p99_ms is None else p99_ms
    if error_rate >= 0.2 or p99 >= 800 or memory_mb >= 512:
        return "degraded"
    return "up"


class _Builder:
    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []
        self.logs: list[dict[str, Any]] = []

    def span(
        self,
        service: str,
        kind: str,
        name: str,
        duration_ms: int,
        status: str,
        parent: int | None,
        **attributes: Any,
    ) -> int:
        self.spans.append(
            {
                "service": service,
                "kind": kind,
                "name": name,
                "duration_ms": int(duration_ms),
                "status": status,
                "parent": parent,
                "attributes": attributes,
            }
        )
        return len(self.spans) - 1

    def log(
        self,
        service: str,
        level: str,
        event: str,
        message: str,
        span: int | None,
        **attributes: Any,
    ) -> None:
        self.logs.append(
            {
                "service": service,
                "level": level,
                "event": event,
                "message": message,
                "span": span,
                "attributes": attributes,
            }
        )


def _simulate(world: World) -> dict[str, Any]:
    """Simulate one checkout and return span/log templates plus per-service stats."""
    gateway = world.services["gateway"]
    orders = world.services["orders"]
    payments = world.services["payments"]
    cache = world.services["cache"]
    ledger = world.services["ledger"]
    builder = _Builder()
    served = {name: False for name in SERVICES}
    failed = {name: False for name in SERVICES}

    if gateway.crashed:
        failed["gateway"] = True
        return _finish(world, builder, served, failed, ok=False)

    gateway_index = builder.span("gateway", "server", "POST /checkout", 0, "ok", None)
    timeout_ms = int(gateway.config["timeout_ms"])
    if timeout_ms < 50:
        builder.log(
            "gateway",
            "error",
            "config_error",
            f"timeout_ms={timeout_ms} is below the baseline; refusing checkout",
            gateway_index,
            key="timeout_ms",
        )
        builder.spans[gateway_index]["duration_ms"] = 5
        builder.spans[gateway_index]["status"] = "error"
        builder.spans[gateway_index]["attributes"]["http.status_code"] = 503
        served["gateway"] = True
        failed["gateway"] = True
        return _finish(world, builder, served, failed, ok=False)

    orders_result = _simulate_orders(world, builder, orders, payments, cache, ledger, served, failed)
    client_duration = min(orders_result["duration_ms"], timeout_ms)
    client_status = "error" if orders_result["status"] == "error" else "ok"
    timed_out = orders_result["duration_ms"] > timeout_ms
    if timed_out:
        client_status = "error"
    client_index = builder.span(
        "gateway",
        "client",
        "orders POST /orders",
        client_duration,
        client_status,
        gateway_index,
        peer="orders",
    )
    # The client span is a child of the gateway server span. Re-parent by
    # inserting it conceptually: parent links are indexes, so set parent now.
    builder.spans[client_index]["parent"] = gateway_index
    # Orders server spans were created with parent None. Attach them to the client.
    for span in builder.spans:
        if span["service"] == "orders" and span["kind"] == "server" and span["parent"] is None:
            span["parent"] = client_index

    gateway_duration = gateway.own_ms() + client_duration
    builder.spans[gateway_index]["duration_ms"] = gateway_duration
    served["gateway"] = True
    if orders_result["status"] == "error" or timed_out:
        builder.spans[gateway_index]["status"] = "error"
        builder.spans[gateway_index]["attributes"]["http.status_code"] = 504 if timed_out else 502
        failed["gateway"] = True
        if timed_out:
            builder.log(
                "gateway",
                "error",
                "timeout",
                f"upstream orders exceeded timeout_ms={timeout_ms}",
                gateway_index,
                timeout_ms=timeout_ms,
            )
        elif orders.crashed:
            builder.log(
                "gateway",
                "error",
                "upstream_error",
                "connection refused while calling orders",
                client_index,
                upstream="orders",
            )
    else:
        builder.spans[gateway_index]["attributes"]["http.status_code"] = 200
        if gateway_duration > 700:
            builder.log(
                "gateway",
                "warning",
                "budget_exceeded",
                f"checkout server time {gateway_duration}ms exceeded the 700ms budget",
                gateway_index,
                duration_ms=gateway_duration,
            )
        builder.log(
            "gateway",
            "info",
            "request",
            f"checkout completed in {gateway_duration}ms",
            gateway_index,
            duration_ms=gateway_duration,
        )

    return _finish(world, builder, served, failed, ok=not failed["gateway"])


def _simulate_orders(
    world: World,
    builder: _Builder,
    orders: ServiceState,
    payments: ServiceState,
    cache: ServiceState,
    ledger: ServiceState,
    served: dict[str, bool],
    failed: dict[str, bool],
) -> dict[str, Any]:
    if orders.crashed:
        failed["orders"] = True
        return {"duration_ms": 12, "status": "error"}

    orders_index = builder.span("orders", "server", "POST /orders", 0, "ok", None)
    served["orders"] = True
    children = 0
    orders_failed = False

    bypass = bool(orders.config.get("cache_bypass"))
    if not bypass:
        if cache.crashed:
            cache_client = builder.span(
                "orders",
                "client",
                "cache GET /cache/sku",
                12,
                "error",
                orders_index,
                peer="cache",
                error="connection_refused",
            )
            builder.log(
                "orders",
                "error",
                "dependency_error",
                "connection refused while calling cache",
                cache_client,
                dependency="cache",
            )
            children += 12
            orders_failed = True
            failed["cache"] = True
        else:
            cache_duration = cache.own_ms()
            cache_index = builder.span(
                "cache", "server", "GET /cache/sku", cache_duration, "ok", None
            )
            cache_client = builder.span(
                "orders",
                "client",
                "cache GET /cache/sku",
                cache_duration,
                "ok",
                orders_index,
                peer="cache",
            )
            builder.spans[cache_index]["parent"] = cache_client
            builder.spans[cache_index]["attributes"]["http.status_code"] = 200
            served["cache"] = True
            children += cache_duration

    upstream = orders.config.get("upstream_payments")
    baseline_upstream = BASELINE["orders"]["upstream_payments"]
    if not orders_failed and upstream != baseline_upstream:
        client = builder.span(
            "orders",
            "client",
            "payments POST /charge",
            8,
            "error",
            orders_index,
            peer="payments",
            error="bad_upstream",
        )
        builder.log(
            "orders",
            "error",
            "config_error",
            f"upstream_payments={upstream} does not match the baseline host",
            client,
            key="upstream_payments",
        )
        children += 8
        orders_failed = True
        failed["orders"] = True

    if not orders_failed:
        payment = _simulate_payments(builder, payments, ledger, served, failed, orders_index)
        children += payment["client_ms"]
        if payment["status"] == "error":
            orders_failed = True
            failed["orders"] = True

    duration = orders.own_ms() + children
    builder.spans[orders_index]["duration_ms"] = duration
    if orders_failed:
        failed["orders"] = True
        builder.spans[orders_index]["status"] = "error"
        builder.spans[orders_index]["attributes"]["http.status_code"] = 502
        return {"duration_ms": duration, "status": "error"}

    builder.spans[orders_index]["attributes"]["http.status_code"] = 200
    builder.log(
        "orders",
        "info",
        "request",
        f"order priced and charged in {duration}ms",
        orders_index,
        duration_ms=duration,
    )
    if orders.memory_leak:
        builder.log(
            "orders",
            "warning",
            "memory_pressure",
            f"orders RSS is {orders.memory_mb()}MB and still climbing",
            orders_index,
            memory_mb=orders.memory_mb(),
        )
    return {"duration_ms": duration, "status": "ok"}


def _simulate_payments(
    builder: _Builder,
    payments: ServiceState,
    ledger: ServiceState,
    served: dict[str, bool],
    failed: dict[str, bool],
    orders_index: int,
) -> dict[str, Any]:
    if payments.crashed:
        failed["payments"] = True
        client = builder.span(
            "orders",
            "client",
            "payments POST /charge",
            12,
            "error",
            orders_index,
            peer="payments",
            error="connection_refused",
        )
        builder.log(
            "orders",
            "error",
            "upstream_error",
            "connection refused while calling payments",
            client,
            upstream="payments",
        )
        return {"status": "error", "client_ms": 12}

    pay_index = builder.span("payments", "server", "POST /charge", 0, "ok", None)
    # Parent is the orders client span, which does not exist yet. Create the
    # client span first so the server can point at it.
    # We already created the server. Create the client now and re-link.
    served["payments"] = True
    mode = payments.config.get("mode")
    if mode != "live":
        client = builder.span(
            "orders",
            "client",
            "payments POST /charge",
            10,
            "error",
            orders_index,
            peer="payments",
        )
        builder.spans[pay_index]["parent"] = client
        builder.spans[pay_index]["duration_ms"] = 10
        builder.spans[pay_index]["status"] = "error"
        builder.spans[pay_index]["attributes"]["http.status_code"] = 422
        builder.log(
            "payments",
            "error",
            "config_error",
            f"payments mode={mode} is not live; refusing charges",
            pay_index,
            key="mode",
        )
        failed["payments"] = True
        return {"status": "error", "client_ms": 10}

    if ledger.crashed:
        client_ledger = builder.span(
            "payments",
            "client",
            "ledger POST /entries",
            12,
            "error",
            pay_index,
            peer="ledger",
            error="connection_refused",
        )
        builder.log(
            "payments",
            "error",
            "dependency_error",
            "connection refused while calling ledger",
            client_ledger,
            dependency="ledger",
        )
        duration = payments.own_ms() + 12
        order_client = builder.span(
            "orders",
            "client",
            "payments POST /charge",
            duration,
            "error",
            orders_index,
            peer="payments",
        )
        builder.spans[pay_index]["parent"] = order_client
        builder.spans[pay_index]["duration_ms"] = duration
        builder.spans[pay_index]["status"] = "error"
        builder.spans[pay_index]["attributes"]["http.status_code"] = 502
        failed["payments"] = True
        failed["ledger"] = True
        return {"status": "error", "client_ms": duration}

    ledger_duration = ledger.own_ms()
    order_client = builder.span(
        "orders",
        "client",
        "payments POST /charge",
        0,
        "ok",
        orders_index,
        peer="payments",
    )
    ledger_client = builder.span(
        "payments",
        "client",
        "ledger POST /entries",
        ledger_duration,
        "ok",
        pay_index,
        peer="ledger",
    )
    ledger_server = builder.span(
        "ledger", "server", "POST /entries", ledger_duration, "ok", ledger_client
    )
    builder.spans[ledger_server]["attributes"]["http.status_code"] = 200
    served["ledger"] = True
    duration = payments.own_ms() + ledger_duration
    builder.spans[pay_index]["parent"] = order_client
    builder.spans[pay_index]["duration_ms"] = duration
    builder.spans[pay_index]["attributes"]["http.status_code"] = 200
    builder.spans[order_client]["duration_ms"] = duration
    builder.log(
        "payments",
        "info",
        "request",
        f"charge completed in {duration}ms",
        pay_index,
        duration_ms=duration,
    )
    if payments.memory_leak:
        builder.log(
            "payments",
            "warning",
            "memory_pressure",
            f"payments RSS is {payments.memory_mb()}MB and still climbing",
            pay_index,
            memory_mb=payments.memory_mb(),
        )
    return {"status": "ok", "client_ms": duration}


def _finish(
    world: World,
    builder: _Builder,
    served: dict[str, bool],
    failed: dict[str, bool],
    ok: bool,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "spans": builder.spans,
        "logs": builder.logs,
        "served": served,
        "failed": failed,
    }


def exclusive_ms(spans: list[dict[str, Any]], service: str) -> int | None:
    """Mean server time not accounted for by child client spans."""
    values: list[int] = []
    for span in spans:
        if span.get("service") != service or span.get("kind") != "server":
            continue
        span_id = span.get("span_id")
        child_total = sum(
            child["duration_ms"]
            for child in spans
            if child.get("parent_span_id") == span_id and child.get("kind") == "client"
        )
        values.append(max(0, int(span["duration_ms"]) - int(child_total)))
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def _materialize(incident_key: str, template: dict[str, Any], copies: int = 4) -> tuple[list, list]:
    spans: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    log_counter = 0
    for copy in range(copies):
        trace_id = f"{incident_key}-trace-{copy + 1}"
        index_to_id: dict[int, str] = {}
        for index, span in enumerate(template["spans"]):
            span_id = f"{incident_key}-span-{copy + 1}-{index + 1}"
            index_to_id[index] = span_id
        for index, span in enumerate(template["spans"]):
            parent = span["parent"]
            spans.append(
                {
                    "span_id": index_to_id[index],
                    "trace_id": trace_id,
                    "parent_span_id": None if parent is None else index_to_id[parent],
                    "service": span["service"],
                    "kind": span["kind"],
                    "name": span["name"],
                    "duration_ms": span["duration_ms"],
                    "status": span["status"],
                    "attributes": deepcopy(span["attributes"]),
                }
            )
        for span_index, log in enumerate(template["logs"]):
            log_counter += 1
            attached = log["span"]
            logs.append(
                {
                    "id": f"{incident_key}-log-{log_counter:03d}",
                    "service": log["service"],
                    "level": log["level"],
                    "event": log["event"],
                    "message": log["message"],
                    "trace_id": trace_id if attached is not None else f"{incident_key}-bg",
                    "span_id": None if attached is None else index_to_id.get(attached),
                    "attributes": deepcopy(log["attributes"]),
                }
            )
    return spans, logs


def _background_logs(world: World, incident_key: str, existing: int) -> list[dict[str, Any]]:
    """Logs that are not produced by the checkout span tree.

    The ledger checkpoint is recorded on every incident so a noisy dependency
    does not, by itself, become the root cause.
    """
    logs: list[dict[str, Any]] = []
    counter = existing
    counter += 1
    logs.append(
        {
            "id": f"{incident_key}-log-{counter:03d}",
            "service": "ledger",
            "level": "info",
            "event": "checkpoint",
            "message": "checkpoint complete duration_ms=12",
            "trace_id": f"{incident_key}-bg",
            "span_id": None,
            "attributes": {"component": "ledger", "duration_ms": 12},
        }
    )
    for name, svc in world.services.items():
        if not svc.crashed:
            continue
        counter += 1
        logs.append(
            {
                "id": f"{incident_key}-log-{counter:03d}",
                "service": "probe",
                "level": "error",
                "event": "health_failed",
                "message": f"health check failed: {name} connection refused",
                "trace_id": f"{incident_key}-probe-{name}",
                "span_id": None,
                "attributes": {"target": name},
            }
        )
    return logs


def _server_duration(spans: list[dict[str, Any]], service: str) -> int | None:
    durations = [
        span["duration_ms"]
        for span in spans
        if span["service"] == service and span["kind"] == "server"
    ]
    if not durations:
        return None
    return int(round(sum(durations) / len(durations)))


def observe(world: World, incident_key: str) -> dict[str, Any]:
    """Build the telemetry bundle an investigator is allowed to see."""
    template = _simulate(world)
    # One analytical request is repeated so rates are stable. Four traces are
    # kept so the dashboard has a waterfall to open.
    spans, logs = _materialize(incident_key, template, copies=4)
    logs.extend(_background_logs(world, incident_key, len(logs)))
    # Decoy and probe logs were given trace ids inside materialize only when
    # they were part of the template before replication. They are. Good.

    metrics: dict[str, Any] = {}
    health: dict[str, Any] = {}
    sample_spans_one = [span for span in spans if span["trace_id"].endswith("-trace-1")]
    for name in SERVICES:
        svc = world.services[name]
        memory = svc.memory_mb()
        if svc.crashed:
            error_rate = 1.0 if name in template["failed"] else 1.0
            metrics[name] = {
                "requests": 0,
                "errors": 0,
                "error_rate": 0.0,
                "p50_ms": None,
                "p99_ms": None,
                "memory_mb": 0,
                "replicas": svc.replicas,
                "exclusive_ms": None,
                "p99_series": [0] * 12,
                "error_rate_series": [0] * 12,
                "memory_series": [0] * 12,
            }
            health[name] = {"service": name, "status": "down"}
            continue
        duration = _server_duration(sample_spans_one, name)
        requests = 20 if template["served"].get(name) else 0
        errors = 20 if template["failed"].get(name) and requests else 0
        error_rate = round(errors / requests, 4) if requests else 0.0
        exclusive = exclusive_ms(sample_spans_one, name) if requests else None
        p99 = duration
        climb_mem = 120 if svc.memory_leak else None
        end_mem = memory
        climb_p99 = 40 if svc.memory_leak and p99 is not None else None
        metrics[name] = {
            "requests": requests,
            "errors": errors,
            "error_rate": error_rate,
            "p50_ms": p99,
            "p99_ms": p99,
            "memory_mb": memory,
            "replicas": svc.replicas,
            "exclusive_ms": exclusive,
            "p99_series": _series(p99 or 0, climb=climb_p99),
            "error_rate_series": _series(int(error_rate * 100)),
            "memory_series": _series(end_mem, climb=climb_mem),
        }
        health[name] = {
            "service": name,
            "status": _health(False, error_rate, p99, memory),
        }

    configs = {name: deepcopy(world.services[name].config) for name in SERVICES}
    return {
        "incident_key": incident_key,
        "health": health,
        "metrics": metrics,
        "logs": logs,
        "spans": spans,
        "configs": configs,
    }


def topology(bundle: dict[str, Any], root_service: str | None = None) -> dict[str, Any]:
    from chaosmedic.catalog import EDGES

    nodes = []
    for name in SERVICES:
        metric = bundle["metrics"][name]
        nodes.append(
            {
                "id": name,
                "status": bundle["health"][name]["status"],
                "root_cause": root_service == name,
                "error_rate": metric["error_rate"],
                "p99_ms": metric["p99_ms"],
                "memory_mb": metric["memory_mb"],
                "replicas": metric["replicas"],
            }
        )
    edges = []
    health = bundle["health"]
    for source, target in EDGES:
        status = "up"
        if health[source]["status"] != "up" or health[target]["status"] != "up":
            status = "degraded" if "down" not in (
                health[source]["status"],
                health[target]["status"],
            ) else "down"
        edges.append({"source": source, "target": target, "status": status})
    return {"nodes": nodes, "edges": edges}
