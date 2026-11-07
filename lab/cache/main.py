"""Lab cache. Stand-in for Redis so the dependency can be faulted without a socket."""

from fastapi import FastAPI, Request

from lab.common.runtime import LabRuntime, extract_parent, mount_controls

runtime = LabRuntime("cache")
app = FastAPI(title="cache")
mount_controls(app, runtime)


@app.get("/cache/{key}", response_model=None)
def get_item(key: str, request: Request):
    blocked = runtime.crashed_response()
    if blocked is not None:
        return blocked
    with runtime.span("GET /cache/sku", "server", incoming=extract_parent(request)):
        return {"hit": False, "key": key}
