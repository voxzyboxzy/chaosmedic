"""Rules diagnoser.

Scores competing hypotheses from read-only tools. It never sees the incident
id's ground-truth label and it never proposes a command string.
"""

from __future__ import annotations

from typing import Any

from chaosmedic.catalog import (
    APP_SERVICES,
    INFRA_SERVICES,
    LATENCY_LOW_EXCLUSIVE_MS,
    LATENCY_SPIKE_EXCLUSIVE_MS,
    SERVICES,
)
from chaosmedic.runbooks import normalize_action
from chaosmedic.tools import Toolset


def _evidence(kind: str, ref: str, text: str, trace_id: str | None = None) -> dict[str, Any]:
    item = {"kind": kind, "ref": ref, "text": text}
    if trace_id:
        item["trace_id"] = trace_id
    return item


def _confidence(score: int, second: int) -> float:
    if score <= 0:
        return 0.05
    base = score / (score + 3)
    margin = min(max(score - second, 0), 8) / 8
    return round(min(0.93, base * 0.55 + margin * 0.45), 2)


def _summarize(fault: str, service: str, drift: list[str], score: int) -> str:
    if score <= 0:
        return f"The signals do not support this explanation for {service}."
    if fault == "latency_spike":
        return (
            f"Server time is concentrated in {service}. "
            "Callers look slow because they are waiting on this hop."
        )
    if fault == "service_crash":
        return (
            f"{service} is not serving. Callers record connection failures "
            "and the process produced no successful server spans."
        )
    if fault == "bad_config":
        keys = ", ".join(drift) if drift else "the manifest"
        return (
            f"{service} configuration drifted from the recorded baseline ({keys}) "
            "and the service logged config_error."
        )
    if fault == "dependency_outage":
        return (
            f"{service} is unreachable. The application processes are up and "
            f"record dependency_error against {service}."
        )
    if fault == "memory_leak":
        return (
            f"{service} memory climbed through the window and the process logged "
            "memory pressure. The extra latency is a side effect of that growth."
        )
    return f"{fault} on {service}"


def _action_for(fault: str, service: str) -> dict[str, Any]:
    if fault == "latency_spike":
        raw = {"action": "scale_service", "service": service, "replicas": 3}
    elif fault == "bad_config":
        raw = {"action": "rollback_config", "service": service}
    else:
        # crash, memory leak, and a down cache or ledger are cleared by restart.
        raw = {"action": "restart_service", "service": service}
    return normalize_action(raw)


class _Score:
    def __init__(self, fault: str, service: str) -> None:
        self.fault = fault
        self.service = service
        self.score = 0
        self.for_evidence: list[dict[str, Any]] = []
        self.against_evidence: list[dict[str, Any]] = []
        self.drift: list[str] = []

    def add_for(self, points: int, item: dict[str, Any]) -> None:
        self.score += points
        self.for_evidence.append(item)

    def add_against(self, points: int, item: dict[str, Any]) -> None:
        self.score -= points
        self.against_evidence.append(item)


def diagnose(bundle: dict[str, Any]) -> dict[str, Any]:
    tools = Toolset(bundle)
    health = tools.get_health()
    exclusive = tools.get_exclusive_latency()
    probes = tools.get_dependency_probes()
    logs = tools.get_logs()
    spans = tools.get_traces()
    metrics = {service: tools.get_metrics(service) for service in SERVICES}
    configs = {service: tools.get_config(service) for service in SERVICES}

    scores: list[_Score] = []
    for service in APP_SERVICES:
        scores.append(_score_crash(service, health, logs, spans))
        scores.append(_score_latency(service, health, exclusive, metrics, logs, spans))
        scores.append(_score_config(service, health, configs, logs))
        scores.append(_score_memory(service, health, metrics, logs))
    for service in INFRA_SERVICES:
        scores.append(_score_dependency(service, health, probes, logs, spans))

    ranked = sorted(scores, key=lambda item: (-item.score, item.fault, item.service))
    top = ranked[0]
    second_score = ranked[1].score if len(ranked) > 1 else 0
    hypotheses = []
    for index, item in enumerate(ranked[:4], start=1):
        hypotheses.append(
            {
                "rank": index,
                "fault": item.fault,
                "service": item.service,
                "score": item.score,
                "confidence": _confidence(item.score, second_score if index == 1 else item.score),
                "summary": _summarize(item.fault, item.service, item.drift, item.score),
                "for": item.for_evidence,
                "against": item.against_evidence,
            }
        )
    # Competing rows should show their own margin against the leader, not
    # pretend they are tied with themselves.
    if hypotheses:
        leader = hypotheses[0]["score"]
        for item in hypotheses[1:]:
            item["confidence"] = _confidence(item["score"], leader)

    proposed = None
    if top.score >= 3:
        proposed = _action_for(top.fault, top.service)
        proposed = {
            **proposed,
            "rationale": hypotheses[0]["summary"],
        }

    return {
        "hypotheses": hypotheses,
        "proposed_action": proposed,
        "tool_calls": tools.calls,
        "diagnoser": "rules",
    }


def _first_span(spans: list[dict[str, Any]], service: str, kind: str = "server") -> dict | None:
    for span in spans:
        if span["service"] == service and span["kind"] == kind:
            return span
    return None


def _logs_where(logs: list[dict[str, Any]], **expect: Any) -> list[dict[str, Any]]:
    found = []
    for log in logs:
        matched = True
        for key, value in expect.items():
            if key == "event" and log["event"] != value:
                matched = False
            elif key == "service" and log["service"] != value:
                matched = False
            elif key not in ("event", "service") and log.get("attributes", {}).get(key) != value:
                matched = False
        if matched:
            found.append(log)
    return found


def _score_crash(service, health, logs, spans) -> _Score:
    item = _Score("service_crash", service)
    status = health[service]["status"]
    if status == "down":
        item.add_for(
            5,
            _evidence("metric", f"{service}.health", f"{service} health is down"),
        )
    else:
        item.add_against(
            5,
            _evidence("metric", f"{service}.health", f"{service} health is {status}, so the process is up"),
        )
    server = _first_span(spans, service, "server")
    if status == "down" and server is None:
        item.add_for(
            2,
            _evidence("trace", f"{service}.server_spans", f"no server spans were recorded for {service}"),
        )
    elif server is not None and status == "down":
        item.add_against(
            2,
            _evidence(
                "trace",
                server["span_id"],
                f"{service} still recorded a server span",
                server["trace_id"],
            ),
        )
    refused = [
        log
        for log in logs
        if log["event"] in {"upstream_error", "health_failed"}
        and (
            log.get("attributes", {}).get("upstream") == service
            or log.get("attributes", {}).get("target") == service
        )
    ]
    if status == "down" and refused:
        sample = refused[0]
        item.add_for(
            2,
            _evidence(
                "log",
                sample["id"],
                sample["message"],
                sample.get("trace_id"),
            ),
        )
    return item


def _score_latency(service, health, exclusive, metrics, logs, spans) -> _Score:
    item = _Score("latency_spike", service)
    status = health[service]["status"]
    excl = exclusive.get(service)
    if status == "down":
        item.add_against(
            3,
            _evidence("metric", f"{service}.health", f"{service} is down, so latency is not the primary signal"),
        )
        return item
    if excl is None:
        item.add_against(
            3,
            _evidence("trace", f"{service}.exclusive_ms", f"{service} recorded no server spans"),
        )
        return item
    p99 = metrics[service]["p99_ms"]
    if excl >= LATENCY_SPIKE_EXCLUSIVE_MS:
        span = _first_span(spans, service, "server")
        ref = span["span_id"] if span else f"{service}.exclusive_ms"
        trace_id = span["trace_id"] if span else None
        item.add_for(
            5,
            _evidence(
                "trace",
                ref,
                f"{service} exclusive server time is {excl}ms",
                trace_id,
            ),
        )
        if p99 is not None and p99 >= LATENCY_SPIKE_EXCLUSIVE_MS:
            item.add_for(
                1,
                _evidence("metric", f"{service}.p99_ms", f"{service} p99 is {p99}ms"),
            )
        timeouts = _logs_where(logs, event="timeout")
        if timeouts and service != "gateway":
            item.add_for(
                1,
                _evidence(
                    "log",
                    timeouts[0]["id"],
                    "the gateway timed out waiting on a downstream hop",
                    timeouts[0].get("trace_id"),
                ),
            )
        gateway_exclusive = exclusive.get("gateway")
        if (
            service != "gateway"
            and gateway_exclusive is not None
            and gateway_exclusive < LATENCY_LOW_EXCLUSIVE_MS
        ):
            item.add_for(
                1,
                _evidence(
                    "metric",
                    "gateway.exclusive_ms",
                    f"gateway exclusive server time is only {gateway_exclusive}ms, so the wait is downstream",
                ),
            )
    elif excl < LATENCY_LOW_EXCLUSIVE_MS:
        item.add_against(
            3,
            _evidence(
                "metric",
                f"{service}.exclusive_ms",
                f"{service} exclusive server time is only {excl}ms",
            ),
        )
        if service == "gateway" and _logs_where(logs, event="budget_exceeded"):
            sample = _logs_where(logs, event="budget_exceeded")[0]
            item.add_against(
                1,
                _evidence(
                    "log",
                    sample["id"],
                    "gateway logged a budget warning, but its own server time stayed low, so the wait is downstream",
                    sample.get("trace_id"),
                ),
            )
        if service == "gateway" and _logs_where(logs, event="timeout"):
            sample = _logs_where(logs, event="timeout")[0]
            item.add_against(
                1,
                _evidence(
                    "log",
                    sample["id"],
                    "gateway timed out, but its exclusive time stayed low, so the time was spent downstream",
                    sample.get("trace_id"),
                ),
            )
    else:
        item.add_against(
            1,
            _evidence(
                "metric",
                f"{service}.exclusive_ms",
                f"{service} exclusive time is {excl}ms, elevated but below the spike threshold",
            ),
        )
    return item


def _score_config(service, health, configs, logs) -> _Score:
    item = _Score("bad_config", service)
    drift = configs[service]["drift_keys"]
    item.drift = drift
    config_logs = _logs_where(logs, service=service, event="config_error")
    # Orders logs config_error when its upstream host drifts, even though the
    # attribute lives on the orders service. Payments and gateway do too.
    if drift and config_logs:
        item.add_for(
            4,
            _evidence(
                "metric",
                f"{service}.config",
                f"{service} drifted from baseline on {', '.join(drift)}",
            ),
        )
        item.add_for(
            3,
            _evidence(
                "log",
                config_logs[0]["id"],
                config_logs[0]["message"],
                config_logs[0].get("trace_id"),
            ),
        )
    elif drift:
        item.add_for(
            3,
            _evidence(
                "metric",
                f"{service}.config",
                f"{service} drifted from baseline on {', '.join(drift)}",
            ),
        )
    else:
        item.add_against(
            4,
            _evidence("metric", f"{service}.config", f"{service} config matches the baseline manifest"),
        )
    if health[service]["status"] == "down":
        item.add_against(
            2,
            _evidence("metric", f"{service}.health", f"{service} is down, which a config drift does not explain here"),
        )
    return item


def _score_memory(service, health, metrics, logs) -> _Score:
    item = _Score("memory_leak", service)
    memory = metrics[service]["memory_mb"]
    pressure = _logs_where(logs, service=service, event="memory_pressure")
    if memory >= 512:
        item.add_for(
            5,
            _evidence("metric", f"{service}.memory_mb", f"{service} memory is {memory}MB"),
        )
    else:
        item.add_against(
            3,
            _evidence("metric", f"{service}.memory_mb", f"{service} memory is {memory}MB"),
        )
    if pressure:
        item.add_for(
            2,
            _evidence(
                "log",
                pressure[0]["id"],
                pressure[0]["message"],
                pressure[0].get("trace_id"),
            ),
        )
    if health[service]["status"] == "down":
        item.add_against(
            2,
            _evidence("metric", f"{service}.health", f"{service} is down"),
        )
    return item


def _score_dependency(service, health, probes, logs, spans) -> _Score:
    item = _Score("dependency_outage", service)
    status = probes[service]
    if status == "down":
        item.add_for(
            5,
            _evidence("metric", f"{service}.health", f"{service} dependency probe is down"),
        )
    else:
        item.add_against(
            5,
            _evidence("metric", f"{service}.health", f"{service} dependency probe is {status}"),
        )
    callers = _logs_where(logs, event="dependency_error", dependency=service)
    if callers:
        item.add_for(
            3,
            _evidence(
                "log",
                callers[0]["id"],
                callers[0]["message"],
                callers[0].get("trace_id"),
            ),
        )
    elif status == "down":
        item.add_against(
            1,
            _evidence("log", f"{service}.dependency_error", f"no caller logged dependency_error for {service}"),
        )
    server = _first_span(spans, service, "server")
    if status == "down" and server is None:
        item.add_for(
            2,
            _evidence("trace", f"{service}.server_spans", f"no server spans were recorded for {service}"),
        )
    checkpoint = _logs_where(logs, service="ledger", event="checkpoint")
    if service == "ledger" and status != "down" and checkpoint:
        item.add_against(
            1,
            _evidence(
                "log",
                checkpoint[0]["id"],
                "ledger checkpoint completed, which is a routine background log and not an outage",
                checkpoint[0].get("trace_id"),
            ),
        )
    return item
