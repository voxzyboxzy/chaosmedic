"""Edge service. Checkout enters the lab here."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lab.common.baselines import BASELINE
from lab.common.runtime import LabRuntime, PeerError, extract_parent, mount_controls

runtime = LabRuntime("gateway")
app = FastAPI(title="gateway")
mount_controls(app, runtime)
def _orders_url() -> str:
    return os.environ.get("ORDERS_URL", "http://orders:8000")


@app.post("/checkout")
def checkout(request: Request, body: dict) -> JSONResponse:
    blocked = runtime.crashed_response()
    if blocked is not None:
        return blocked
    with runtime.span("POST /checkout", "server", incoming=extract_parent(request)) as handle:
        timeout_ms = int(runtime.config.get("timeout_ms", BASELINE["gateway"]["timeout_ms"]))
        if timeout_ms < 50:
            runtime.log(
                "error",
                "config_error",
                f"timeout_ms={timeout_ms} is below the baseline; refusing checkout",
                key="timeout_ms",
            )
            handle.fail()
            return JSONResponse({"error": "config"}, status_code=503)
        try:
            response = runtime.call(
                "POST",
                f"{_orders_url()}/orders",
                body,
                peer="orders",
                kind="upstream",
                timeout=timeout_ms / 1000,
            )
        except PeerError as exc:
            handle.fail()
            status = 504 if exc.code == "timeout" else 502
            return JSONResponse({"error": exc.code}, status_code=status)
        if response.status_code >= 400:
            handle.fail()
            return JSONResponse({"error": "upstream", "status": response.status_code}, status_code=502)
        return JSONResponse(response.json(), status_code=200)
