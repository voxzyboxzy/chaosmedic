"""Orders service. Reads the cache, then charges payments."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lab.common.baselines import BASELINE
from lab.common.runtime import LabRuntime, PeerError, extract_parent, mount_controls

runtime = LabRuntime("orders")
app = FastAPI(title="orders")
mount_controls(app, runtime)
def _payments_url() -> str:
    return os.environ.get("PAYMENTS_URL", "http://payments:8000")


def _cache_url() -> str:
    return os.environ.get("CACHE_URL", "http://cache:8000")


@app.post("/orders")
def create_order(request: Request, body: dict) -> JSONResponse:
    blocked = runtime.crashed_response()
    if blocked is not None:
        return blocked
    with runtime.span("POST /orders", "server", incoming=extract_parent(request)) as handle:
        if not runtime.config.get("cache_bypass"):
            try:
                runtime.call(
                    "GET",
                    f"{_cache_url()}/cache/{body.get('sku', 'sku')}",
                    {},
                    peer="cache",
                    kind="dependency",
                    timeout=1.0,
                )
            except PeerError:
                handle.fail()
                return JSONResponse({"error": "cache"}, status_code=502)
        upstream = runtime.config.get("upstream_payments")
        if upstream != BASELINE["orders"]["upstream_payments"]:
            runtime.log(
                "error",
                "config_error",
                f"upstream_payments={upstream} does not match the baseline host",
                key="upstream_payments",
            )
            handle.fail()
            return JSONResponse({"error": "config"}, status_code=502)
        try:
            response = runtime.call(
                "POST",
                f"{_payments_url()}/charge",
                {"amount": body.get("amount", 0), "sku": body.get("sku")},
                peer="payments",
                kind="upstream",
                timeout=2.0,
            )
        except PeerError:
            handle.fail()
            return JSONResponse({"error": "payments"}, status_code=502)
        if response.status_code >= 400:
            handle.fail()
            return JSONResponse({"error": "payments", "status": response.status_code}, status_code=502)
        return JSONResponse({"ok": True, "charged": response.json()}, status_code=201)
