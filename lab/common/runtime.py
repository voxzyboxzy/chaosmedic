"""Process-local fault state, spans, and admin controls.

Business routes call these helpers. Nothing in this module shells out.
OpenTelemetry spans are exported as OTLP/HTTP JSON when an endpoint is set.
"""

from __future__ import annotations

import contextvars
import os
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Iterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind, Status, StatusCode

from lab.common.baselines import (
    BASE_MEMORY_MB,
    BASELINE,
    FLAGS,
    LEAK_EXTRA_MS,
    LEAK_MEMORY_MB,
)

_stack: contextvars.ContextVar[tuple[dict[str, str], ...]] = contextvars.ContextVar(
    "chaosmedic_span_stack",
    default=(),
)
_transport_override: httpx.BaseTransport | None = None


class PeerError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Handle:
    def __init__(self) -> None:
        self.status = "ok"

    def fail(self) -> None:
        self.status = "error"


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport_override
    _transport_override = transport


class _JsonOTLPExporter(SpanExporter):
    """Best-effort OTLP/HTTP JSON exporter. Drops batches when no endpoint is set."""

    def export(self, spans: Any) -> SpanExportResult:
        endpoint = os.environ.get("CHAOSMEDIC_OTLP_ENDPOINT", "").rstrip("/")
        if not endpoint or not spans:
            return SpanExportResult.SUCCESS
        grouped: dict[str, list[dict[str, Any]]] = {}
        for span in spans:
            service = str(span.attributes.get("lab.service") or "unknown")
            grouped.setdefault(service, []).append(_otel_span_json(span))
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": service}}
                        ]
                    },
                    "scopeSpans": [{"spans": service_spans}],
                }
                for service, service_spans in grouped.items()
            ]
        }
        try:
            httpx.post(f"{endpoint}/v1/traces", json=payload, timeout=1.0)
        except httpx.HTTPError:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _hex_id(value: int, width: int) -> str:
    return f"{value:0{width}x}"


def _otel_span_json(span: Any) -> dict[str, Any]:
    context = span.context
    parent = span.parent
    attributes = []
    for key, value in span.attributes.items():
        if isinstance(value, bool):
            encoded: dict[str, Any] = {"boolValue": value}
        elif isinstance(value, int):
            encoded = {"intValue": value}
        elif isinstance(value, float):
            encoded = {"doubleValue": value}
        else:
            encoded = {"stringValue": str(value)}
        attributes.append({"key": key, "value": encoded})
    kind_map = {
        SpanKind.INTERNAL: 1,
        SpanKind.SERVER: 2,
        SpanKind.CLIENT: 3,
        SpanKind.PRODUCER: 4,
        SpanKind.CONSUMER: 5,
    }
    status = 2 if span.status.status_code == StatusCode.ERROR else 1
    body = {
        "traceId": _hex_id(context.trace_id, 32),
        "spanId": _hex_id(context.span_id, 16),
        "name": span.name,
        "kind": kind_map.get(span.kind, 1),
        "startTimeUnixNano": str(span.start_time),
        "endTimeUnixNano": str(span.end_time),
        "attributes": attributes,
        "status": {"code": status},
    }
    if parent is not None:
        body["parentSpanId"] = _hex_id(parent.span_id, 16)
    return body


def setup_telemetry(service_name: str) -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        return
    resource = Resource.create({"service.name": service_name})
    sdk_provider = TracerProvider(resource=resource)
    sdk_provider.add_span_processor(SimpleSpanProcessor(_JsonOTLPExporter()))
    trace.set_tracer_provider(sdk_provider)


class LabRuntime:
    def __init__(self, name: str) -> None:
        if name not in BASELINE:
            raise ValueError(name)
        self.name = name
        self.crashed = False
        self.latency_ms = 0
        self.memory_leak = False
        self.replicas = 1
        self.config = deepcopy(BASELINE[name])
        self.logs: list[dict[str, Any]] = []
        self.spans: list[dict[str, Any]] = []
        self.requests = 0
        self.errors = 0
        self.latency_samples: list[int] = []
        self.token = os.environ.get("LAB_TOKEN", "local-lab")
        self._ballast = bytearray()
        setup_telemetry(name)

    def clear(self) -> None:
        self.crashed = False
        self.latency_ms = 0
        self.memory_leak = False
        self.replicas = 1
        self.config = deepcopy(BASELINE[self.name])
        self.logs.clear()
        self.spans.clear()
        self.requests = 0
        self.errors = 0
        self.latency_samples.clear()
        self._ballast = bytearray()

    def memory_mb(self) -> int:
        return LEAK_MEMORY_MB if self.memory_leak else BASE_MEMORY_MB

    def own_extra_ms(self) -> int:
        extra = 0 if self.latency_ms and self.replicas >= 3 else self.latency_ms
        if self.memory_leak:
            extra += LEAK_EXTRA_MS
        return extra

    def crashed_response(self) -> JSONResponse | None:
        if self.crashed:
            return JSONResponse({"error": "crashed", "service": self.name}, status_code=503)
        return None

    def log(self, level: str, event: str, message: str, **attributes: Any) -> None:
        stack = _stack.get()
        current = stack[-1] if stack else None
        self.logs.append(
            {
                "id": f"{self.name}-log-{len(self.logs) + 1:03d}",
                "service": self.name,
                "level": level,
                "event": event,
                "message": message,
                "trace_id": None if current is None else current["trace_id"],
                "span_id": None if current is None else current["span_id"],
                "attributes": attributes,
            }
        )
        self._post_log(self.logs[-1])

    def export(self) -> dict[str, Any]:
        memory = 0 if self.crashed else self.memory_mb()
        samples = self.latency_samples[-20:]
        p99 = max(samples) if samples and not self.crashed else 0
        error_rate = 0.0
        if self.requests and not self.crashed:
            error_rate = self.errors / self.requests
        if self.memory_leak and not self.crashed:
            memory_series = [120 + int((LEAK_MEMORY_MB - 120) * index / 11) for index in range(12)]
        else:
            memory_series = [memory] * 12
        return {
            "service": self.name,
            "crashed": self.crashed,
            "config": self.config,
            "memory_mb": memory,
            "replicas": self.replicas,
            "counters": {
                "requests": 0 if self.crashed else self.requests,
                "errors": 0 if self.crashed else self.errors,
            },
            "latency_samples_ms": [] if self.crashed else samples,
            "logs": [] if self.crashed else list(self.logs[-100:]),
            "spans": [] if self.crashed else list(self.spans[-100:]),
            "p99_series": [p99] * 12,
            "error_rate_series": [int(round(error_rate * 100))] * 12,
            "memory_series": memory_series,
        }

    def metrics_text(self) -> str:
        snapshot = self.export()
        requests = snapshot["counters"]["requests"]
        errors = snapshot["counters"]["errors"]
        lines = [
            f'lab_up{{service="{self.name}"}} {0 if self.crashed else 1}',
            f'lab_requests_total{{service="{self.name}"}} {requests}',
            f'lab_errors_total{{service="{self.name}"}} {errors}',
            f'lab_memory_mb{{service="{self.name}"}} {snapshot["memory_mb"]}',
            f'lab_replicas{{service="{self.name}"}} {self.replicas}',
        ]
        return "\n".join(lines) + "\n"

    @contextmanager
    def span(
        self,
        name: str,
        kind: str,
        incoming: dict[str, str] | None = None,
    ) -> Iterator[Handle]:
        stack = _stack.get()
        if stack:
            trace_id = stack[-1]["trace_id"]
            parent_id = stack[-1]["span_id"]
        elif incoming:
            trace_id = incoming["trace_id"]
            parent_id = incoming["span_id"]
        else:
            trace_id = uuid.uuid4().hex
            parent_id = None
        span_id = uuid.uuid4().hex[:16]
        frame = {"trace_id": trace_id, "span_id": span_id}
        token = _stack.set(stack + (frame,))
        handle = Handle()
        pad = 0
        started = time.perf_counter()
        if kind == "server":
            extra = self.own_extra_ms()
            if extra and os.environ.get("LAB_INSTANT") != "1":
                time.sleep(extra / 1000)
            elif extra:
                pad = extra
        tracer = trace.get_tracer("chaosmedic.lab")
        otel_kind = SpanKind.SERVER if kind == "server" else SpanKind.CLIENT
        try:
            with tracer.start_as_current_span(name, kind=otel_kind) as otel_span:
                otel_span.set_attribute("lab.service", self.name)
                if pad:
                    otel_span.set_attribute("lab.duration_pad_ms", pad)
                try:
                    yield handle
                except Exception:
                    handle.fail()
                    otel_span.set_status(Status(StatusCode.ERROR))
                    raise
                if handle.status == "error":
                    otel_span.set_status(Status(StatusCode.ERROR))
        finally:
            duration = int(round((time.perf_counter() - started) * 1000)) + pad
            self.spans.append(
                {
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "parent_span_id": parent_id,
                    "service": self.name,
                    "name": name,
                    "kind": kind,
                    "duration_ms": duration,
                    "status": handle.status,
                    "attributes": {"lab.duration_pad_ms": pad} if pad else {},
                }
            )
            if kind == "server":
                self.requests += 1
                if handle.status == "error":
                    self.errors += 1
                self.latency_samples.append(duration)
                if self.memory_leak:
                    self._ballast.extend(b"m" * 4096)
                    if len(self._ballast) > 2_000_000:
                        self._ballast = self._ballast[:2_000_000]
            _stack.reset(token)

    def call(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
        peer: str,
        kind: str,
        timeout: float,
    ) -> httpx.Response:
        with self.span(f"{peer} {method} {url.split(':', 1)[-1]}", "client"):
            stack = _stack.get()
            headers = {}
            if stack:
                current = stack[-1]
                headers["traceparent"] = f"00-{current['trace_id']}-{current['span_id']}-01"
            transport = _transport_override
            try:
                with httpx.Client(transport=transport, timeout=timeout) as client:
                    response = client.request(method, url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                self.log(
                    "error",
                    "timeout",
                    f"upstream {peer} exceeded timeout",
                    upstream=peer,
                )
                raise PeerError("timeout") from exc
            except httpx.HTTPError as exc:
                self._peer_down(peer, kind)
                raise PeerError("connection") from exc
            if response.status_code == 503:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                if body.get("error") == "crashed":
                    self._peer_down(peer, kind)
                    raise PeerError("connection")
            return response

    def _peer_down(self, peer: str, kind: str) -> None:
        if kind == "dependency":
            self.log(
                "error",
                "dependency_error",
                f"connection refused while calling {peer}",
                dependency=peer,
            )
        else:
            self.log(
                "error",
                "upstream_error",
                f"connection refused while calling {peer}",
                upstream=peer,
            )

    def _post_log(self, record: dict[str, Any]) -> None:
        endpoint = os.environ.get("CHAOSMEDIC_OTLP_ENDPOINT", "").rstrip("/")
        if not endpoint:
            return
        payload = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.name}}
                        ]
                    },
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "traceId": record.get("trace_id") or "",
                                    "spanId": record.get("span_id") or "",
                                    "body": {"stringValue": record["message"]},
                                    "attributes": [
                                        {"key": "log.id", "value": {"stringValue": record["id"]}},
                                        {"key": "service", "value": {"stringValue": self.name}},
                                        {"key": "level", "value": {"stringValue": record["level"]}},
                                        {"key": "event", "value": {"stringValue": record["event"]}},
                                    ]
                                    + [
                                        {
                                            "key": key,
                                            "value": {"stringValue": str(value)},
                                        }
                                        for key, value in record["attributes"].items()
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        try:
            httpx.post(f"{endpoint}/v1/logs", json=payload, timeout=1.0)
        except httpx.HTTPError:
            return


def extract_parent(request: Request) -> dict[str, str] | None:
    header = request.headers.get("traceparent")
    if not header:
        return None
    parts = header.split("-")
    if len(parts) != 4 or len(parts[1]) != 32 or len(parts[2]) != 16:
        return None
    return {"trace_id": parts[1], "span_id": parts[2]}


def _authorized(runtime: LabRuntime, request: Request) -> None:
    if request.headers.get("x-lab-token") != runtime.token:
        raise HTTPException(status_code=401, detail="lab token required")


def mount_controls(app: FastAPI, runtime: LabRuntime) -> None:
    @app.get("/health")
    def health() -> Any:
        if runtime.crashed:
            return JSONResponse({"status": "down", "service": runtime.name}, status_code=503)
        return {"status": "up", "service": runtime.name}

    @app.get("/metrics")
    def metrics(request: Request) -> PlainTextResponse:
        _authorized(runtime, request)
        return PlainTextResponse(runtime.metrics_text(), media_type="text/plain")

    @app.get("/debug/export")
    def debug_export(request: Request) -> dict[str, Any]:
        _authorized(runtime, request)
        return runtime.export()

    @app.post("/admin/clear")
    def clear(request: Request) -> dict[str, bool]:
        _authorized(runtime, request)
        runtime.clear()
        return {"ok": True}

    @app.post("/admin/faults")
    def faults(request: Request, body: dict[str, Any]) -> dict[str, bool]:
        _authorized(runtime, request)
        if "crashed" in body:
            runtime.crashed = bool(body["crashed"])
        if "latency_ms" in body:
            runtime.latency_ms = int(body["latency_ms"])
        if "memory_leak" in body:
            runtime.memory_leak = bool(body["memory_leak"])
            if runtime.memory_leak:
                runtime.log(
                    "warning",
                    "memory_pressure",
                    f"{runtime.name} RSS is {runtime.memory_mb()}MB and still climbing",
                    memory_mb=runtime.memory_mb(),
                )
        if "config" in body and isinstance(body["config"], dict):
            runtime.config.update(body["config"])
        return {"ok": True}

    @app.post("/admin/restart")
    def restart(request: Request) -> dict[str, bool]:
        _authorized(runtime, request)
        runtime.crashed = False
        runtime.memory_leak = False
        runtime._ballast = bytearray()
        return {"ok": True}

    @app.post("/admin/scale")
    def scale(request: Request, body: dict[str, Any]) -> dict[str, bool]:
        _authorized(runtime, request)
        replicas = body.get("replicas")
        if replicas not in (1, 2, 3):
            raise HTTPException(status_code=400, detail="replicas must be 1, 2, or 3")
        runtime.replicas = int(replicas)
        return {"ok": True}

    @app.post("/admin/rollback-config")
    def rollback_config(request: Request) -> dict[str, bool]:
        _authorized(runtime, request)
        runtime.config = deepcopy(BASELINE[runtime.name])
        return {"ok": True}

    @app.post("/admin/flags")
    def flags(request: Request, body: dict[str, Any]) -> dict[str, bool]:
        _authorized(runtime, request)
        flag = body.get("flag")
        if FLAGS.get(flag) != runtime.name or not isinstance(body.get("value"), bool):
            raise HTTPException(status_code=400, detail="flag is not allowlisted")
        runtime.config[flag] = body["value"]
        return {"ok": True}

    @app.post("/admin/config")
    def replace_config(request: Request, body: dict[str, Any]) -> dict[str, bool]:
        _authorized(runtime, request)
        incoming = body.get("config")
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="config must be an object")
        allowed = set(BASELINE[runtime.name])
        if set(incoming) - allowed:
            raise HTTPException(status_code=400, detail="unknown config keys")
        runtime.config = deepcopy(BASELINE[runtime.name])
        runtime.config.update(incoming)
        return {"ok": True}
