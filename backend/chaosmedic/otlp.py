"""Minimal OTLP/HTTP JSON readers for traces and logs.

The lab exporter posts this encoding. Protobuf OTLP is not required for the
local pipeline.
"""

from __future__ import annotations

from typing import Any

_STATUS = {0: "unset", 1: "ok", 2: "error"}
_KIND = {0: "internal", 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}


def _attr_value(raw: dict[str, Any]) -> Any:
    if "stringValue" in raw:
        return raw["stringValue"]
    if "intValue" in raw:
        return int(raw["intValue"])
    if "doubleValue" in raw:
        return float(raw["doubleValue"])
    if "boolValue" in raw:
        return bool(raw["boolValue"])
    return None


def _attributes(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items or []:
        parsed[item["key"]] = _attr_value(item.get("value", {}))
    return parsed


def _service_name(resource: dict[str, Any]) -> str:
    attrs = _attributes(resource.get("attributes"))
    return str(attrs.get("service.name") or "unknown")


def _duration_ms(span: dict[str, Any]) -> int:
    start = int(span.get("startTimeUnixNano") or 0)
    end = int(span.get("endTimeUnixNano") or start)
    wall = max(0, end - start) / 1_000_000
    pad = 0
    for item in span.get("attributes") or []:
        if item.get("key") == "lab.duration_pad_ms":
            pad = int(_attr_value(item.get("value", {})) or 0)
    return int(round(wall + pad))


def parse_traces(payload: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for resource_span in payload.get("resourceSpans") or []:
        service = _service_name(resource_span.get("resource") or {})
        for scope in resource_span.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                status_code = int((span.get("status") or {}).get("code") or 0)
                parent = span.get("parentSpanId") or None
                spans.append(
                    {
                        "trace_id": span.get("traceId"),
                        "span_id": span.get("spanId"),
                        "parent_span_id": parent or None,
                        "service": service,
                        "name": span.get("name") or "",
                        "kind": _KIND.get(int(span.get("kind") or 0), "internal"),
                        "duration_ms": _duration_ms(span),
                        "status": "error" if status_code == 2 else "ok",
                        "attributes": _attributes(span.get("attributes")),
                    }
                )
    return spans


def parse_logs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    for resource_log in payload.get("resourceLogs") or []:
        service = _service_name(resource_log.get("resource") or {})
        for scope in resource_log.get("scopeLogs") or []:
            for record in scope.get("logRecords") or []:
                attributes = _attributes(record.get("attributes"))
                body = record.get("body") or {}
                message = body.get("stringValue") or ""
                logs.append(
                    {
                        "id": attributes.get("log.id") or record.get("spanId") or message[:24],
                        "service": attributes.get("service") or service,
                        "level": attributes.get("level") or "info",
                        "event": attributes.get("event") or "log",
                        "message": message,
                        "trace_id": record.get("traceId"),
                        "span_id": record.get("spanId") or None,
                        "attributes": {
                            key: value
                            for key, value in attributes.items()
                            if key not in {"log.id", "service", "level", "event"}
                        },
                    }
                )
    return logs
