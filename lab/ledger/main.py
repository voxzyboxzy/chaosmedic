"""Lab ledger. Stand-in for the system of record database."""

from fastapi import FastAPI, Request

from lab.common.runtime import LabRuntime, extract_parent, mount_controls

runtime = LabRuntime("ledger")
app = FastAPI(title="ledger")
mount_controls(app, runtime)


@app.post("/entries", response_model=None)
def add_entry(request: Request, body: dict):
    blocked = runtime.crashed_response()
    if blocked is not None:
        return blocked
    with runtime.span("POST /entries", "server", incoming=extract_parent(request)):
        runtime.log("info", "checkpoint", "checkpoint complete duration_ms=12", component="ledger", duration_ms=12)
        return {"ok": True, "amount": body.get("amount", 0)}
