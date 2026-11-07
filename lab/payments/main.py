"""Payments service. Appends a ledger entry unless configuration refuses it."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lab.common.runtime import LabRuntime, PeerError, extract_parent, mount_controls

runtime = LabRuntime("payments")
app = FastAPI(title="payments")
mount_controls(app, runtime)
def _ledger_url() -> str:
    return os.environ.get("LEDGER_URL", "http://ledger:8000")


@app.post("/charge")
def charge(request: Request, body: dict) -> JSONResponse:
    blocked = runtime.crashed_response()
    if blocked is not None:
        return blocked
    with runtime.span("POST /charge", "server", incoming=extract_parent(request)) as handle:
        mode = runtime.config.get("mode")
        if mode != "live":
            runtime.log(
                "error",
                "config_error",
                f"payments mode={mode} is not live; refusing charges",
                key="mode",
            )
            handle.fail()
            return JSONResponse({"error": "config"}, status_code=422)
        try:
            runtime.call(
                "POST",
                f"{_ledger_url()}/entries",
                {"amount": body.get("amount", 0)},
                peer="ledger",
                kind="dependency",
                timeout=1.0,
            )
        except PeerError:
            handle.fail()
            return JSONResponse({"error": "ledger"}, status_code=502)
        return JSONResponse({"ok": True}, status_code=200)
