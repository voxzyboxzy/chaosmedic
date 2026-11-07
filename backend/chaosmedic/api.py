"""HTTP API for the dashboard and the OTLP receiver."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from chaosmedic.db import Database
from chaosmedic.engine import Engine, IncidentError, now
from chaosmedic.otlp import parse_logs, parse_traces


class InjectRequest(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=80)


class DecisionRequest(BaseModel):
    approver: str = "operator"


def create_app(
    db_path: str | None = None,
    mode: str | None = None,
    diagnoser: str | None = None,
    lab: Any = None,
    chat: Any = None,
) -> FastAPI:
    database = Database(db_path or os.environ.get("CHAOSMEDIC_DB", "data/chaosmedic.db"))
    engine = Engine(
        database,
        mode=mode or os.environ.get("CHAOSMEDIC_MODE", "replay"),
        diagnoser=diagnoser or os.environ.get("CHAOSMEDIC_DIAGNOSER", "rules"),
        lab=lab,
        chat=chat,
    )
    app = FastAPI(title="ChaosMedic", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.engine = engine
    app.state.db = database

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "mode": engine.mode, "diagnoser": engine.diagnoser}

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        payload = engine.overview()
        payload["otlp"] = database.otlp_counts()
        return payload

    @app.post("/api/incidents/inject")
    def inject(body: InjectRequest) -> dict[str, Any]:
        try:
            return engine.inject(body.scenario_id)
        except IncidentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict[str, Any]:
        try:
            return engine._view(engine._require(incident_id))
        except IncidentError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/approve")
    def approve(incident_id: str, body: DecisionRequest) -> dict[str, Any]:
        try:
            return engine.approve(incident_id, body.approver)
        except IncidentError as exc:
            status = 404 if "unknown" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/reject")
    def reject(incident_id: str, body: DecisionRequest) -> dict[str, Any]:
        try:
            return engine.reject(incident_id, body.approver)
        except IncidentError as exc:
            status = 404 if "unknown" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.post("/api/incidents/{incident_id}/rollback")
    def rollback(incident_id: str, body: DecisionRequest) -> dict[str, Any]:
        try:
            return engine.rollback(incident_id, body.approver)
        except IncidentError as exc:
            status = 404 if "unknown" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.get("/api/incidents/{incident_id}/postmortem.md")
    def postmortem(incident_id: str) -> PlainTextResponse:
        try:
            text = engine.postmortem(incident_id)
        except IncidentError as exc:
            status = 404 if "unknown" in str(exc) else 409
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return PlainTextResponse(
            text,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{incident_id}-postmortem.md"'},
        )

    @app.post("/v1/traces")
    def ingest_traces(payload: dict[str, Any]) -> dict[str, int]:
        spans = parse_traces(payload)
        database.add_otlp("traces", {"spans": spans}, now())
        return {"accepted": len(spans)}

    @app.post("/v1/logs")
    def ingest_logs(payload: dict[str, Any]) -> dict[str, int]:
        logs = parse_logs(payload)
        database.add_otlp("logs", {"logs": logs}, now())
        return {"accepted": len(logs)}

    return app
