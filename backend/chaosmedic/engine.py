"""Incident orchestration.

Replay incidents are loaded from recorded telemetry. The diagnoser receives
that bundle and nothing else. Repairs are allowlisted and, in replay mode,
applied to the simulator. Live mode sends the same action to the lab admin API.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from chaosmedic.db import Database
from chaosmedic.ollama import diagnose_with_mode
from chaosmedic.postmortem import render_postmortem
from chaosmedic.recording import DEMO_DIR, load_cases
from chaosmedic.recovery import verify
from chaosmedic.runbooks import RunbookError, describe_action, normalize_action, rollback_for
from chaosmedic.simulator import World, apply_action, healthy_world, observe, topology
from chaosmedic.live import LabClient, LabError


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class IncidentError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _load(raw: str | None) -> Any:
    if raw in (None, ""):
        return None
    return json.loads(raw)


class Engine:
    def __init__(
        self,
        database: Database,
        mode: str = "replay",
        diagnoser: str = "rules",
        lab: LabClient | None = None,
        chat: Any = None,
    ) -> None:
        if mode not in {"replay", "live"}:
            raise ValueError("mode must be replay or live")
        self.db = database
        self.mode = mode
        self.diagnoser = diagnoser
        self.lab = lab
        self.chat = chat
        self._cases = {case["id"]: case for case in load_cases(DEMO_DIR)}

    def scenarios(self) -> list[dict[str, Any]]:
        rows = []
        for case in self._cases.values():
            rows.append(
                {
                    "id": case["id"],
                    "title": case["title"],
                    "summary": case["summary"],
                    "order": case["order"],
                }
            )
        rows.sort(key=lambda item: item["order"])
        return rows

    def healthy_topology(self) -> dict[str, Any]:
        bundle = observe(healthy_world(), "healthy")
        return topology(bundle)

    def overview(self) -> dict[str, Any]:
        latest = self.db.latest_incident()
        incident = self._view(latest) if latest else None
        current_topology = (
            incident["topology"] if incident else self.healthy_topology()
        )
        return {
            "mode": self.mode,
            "diagnoser": self.diagnoser,
            "scenarios": self.scenarios(),
            "topology": current_topology,
            "incident": incident,
        }

    def inject(self, scenario_id: str) -> dict[str, Any]:
        case = self._cases.get(scenario_id)
        if case is None:
            raise IncidentError("unknown scenario")
        incident_id = "inc_" + uuid.uuid4().hex[:12]
        created = now()
        if self.mode == "live":
            if self.lab is None:
                raise IncidentError("live mode has no lab client")
            try:
                self.lab.clear()
                truth = case["ground_truth"]
                self.lab.apply_fault(truth["fault"], truth["service"], truth.get("detail") or {})
                self.lab.generate_traffic()
                bundle = self._live_bundle(incident_id)
            except LabError as exc:
                raise IncidentError(str(exc)) from exc
            world = case["world"]
        else:
            world = case["world"]
            bundle = case["telemetry"]

        started = time.perf_counter()
        diagnosis = diagnose_with_mode(bundle, self.diagnoser, chat=self.chat)
        elapsed_ms = (time.perf_counter() - started) * 1000
        proposed = diagnosis.get("proposed_action")
        status = "pending_approval" if proposed else "undetermined"
        self.db.insert_incident(
            {
                "id": incident_id,
                "scenario_id": scenario_id,
                "title": case["title"],
                "summary": case["summary"],
                "status": status,
                "mode": self.mode,
                "ground_truth_json": _json(case["ground_truth"]),
                "world_json": _json(world),
                "telemetry_json": _json(bundle),
                "diagnosis_json": _json(diagnosis),
                "diagnosis_ms": elapsed_ms,
                "proposed_action_json": _json(proposed) if proposed else None,
                "approved_action_json": None,
                "rollback_action_json": None,
                "pre_repair_world_json": None,
                "approver": None,
                "approved_at": None,
                "verification_json": None,
                "postmortem_md": None,
                "created_at": created,
                "updated_at": created,
            }
        )
        self.db.audit(
            incident_id,
            created,
            "diagnosed",
            {
                "tool_calls": len(diagnosis.get("tool_calls") or []),
                "diagnoser": diagnosis.get("diagnoser"),
                "proposed": proposed["action"] if proposed else None,
            },
        )
        row = self.db.get_incident(incident_id)
        assert row is not None
        return self._view(row)

    def approve(self, incident_id: str, approver: str) -> dict[str, Any]:
        approver = _approver(approver)
        row = self._require(incident_id)
        if row["status"] != "pending_approval":
            raise IncidentError("incident is not waiting for approval")
        proposed = _load(row["proposed_action_json"])
        if not proposed:
            raise IncidentError("no repair was proposed")
        try:
            action = normalize_action(
                {key: value for key, value in proposed.items() if key != "rationale"}
            )
        except RunbookError as exc:
            raise IncidentError(str(exc)) from exc
        world_dict = _load(row["world_json"])
        rollback = rollback_for(action, world_dict)
        if self.mode == "live":
            if self.lab is None:
                raise IncidentError("live mode has no lab client")
            try:
                self.lab.execute(action)
                self.lab.generate_traffic()
                bundle = self._live_bundle(incident_id)
            except LabError as exc:
                raise IncidentError(str(exc)) from exc
        else:
            try:
                updated = apply_action(World.from_dict(world_dict), action)
            except ValueError as exc:
                raise IncidentError(str(exc)) from exc
            world_dict = updated.to_dict()
            bundle = observe(updated, row["scenario_id"] + "-repaired")
        verification = verify(bundle)
        approved_at = now()
        status = "resolved" if verification["passed"] else "repair_failed"
        view_seed = self._record_to_view_input(
            row,
            status=status,
            world_dict=world_dict,
            bundle=bundle,
            action=action,
            rollback=rollback,
            approver=approver,
            approved_at=approved_at,
            verification=verification,
        )
        postmortem = render_postmortem(view_seed)
        self.db.update_incident(
            incident_id,
            status=status,
            world_json=_json(world_dict),
            telemetry_json=_json(bundle),
            approved_action_json=_json(action),
            rollback_action_json=_json(rollback) if rollback else None,
            pre_repair_world_json=row["world_json"],
            approver=approver,
            approved_at=approved_at,
            verification_json=_json(verification),
            postmortem_md=postmortem,
            updated_at=approved_at,
        )
        self.db.audit(
            incident_id,
            approved_at,
            "approved",
            {"action": action["action"], "service": action["service"], "approver": approver},
        )
        updated_row = self.db.get_incident(incident_id)
        assert updated_row is not None
        return self._view(updated_row)

    def reject(self, incident_id: str, approver: str) -> dict[str, Any]:
        approver = _approver(approver)
        row = self._require(incident_id)
        if row["status"] != "pending_approval":
            raise IncidentError("incident is not waiting for approval")
        stamp = now()
        self.db.update_incident(
            incident_id,
            status="rejected",
            approver=approver,
            updated_at=stamp,
        )
        self.db.audit(incident_id, stamp, "rejected", {"approver": approver})
        updated = self.db.get_incident(incident_id)
        assert updated is not None
        return self._view(updated)

    def rollback(self, incident_id: str, approver: str) -> dict[str, Any]:
        approver = _approver(approver)
        row = self._require(incident_id)
        if row["status"] not in {"resolved", "repair_failed"}:
            raise IncidentError("nothing to roll back")
        rollback = _load(row["rollback_action_json"])
        if not rollback:
            raise IncidentError("restart has no inverse and cannot be rolled back")
        pre = _load(row["pre_repair_world_json"])
        if self.mode == "live":
            if self.lab is None:
                raise IncidentError("live mode has no lab client")
            try:
                self._live_rollback(rollback)
                self.lab.generate_traffic()
                bundle = self._live_bundle(incident_id)
            except LabError as exc:
                raise IncidentError(str(exc)) from exc
            world_dict = pre
        else:
            world = World.from_dict(pre)
            bundle = observe(world, row["scenario_id"] + "-rolled-back")
            world_dict = pre
        stamp = now()
        self.db.update_incident(
            incident_id,
            status="rolled_back",
            world_json=_json(world_dict),
            telemetry_json=_json(bundle),
            verification_json=None,
            updated_at=stamp,
            approver=approver,
        )
        self.db.audit(incident_id, stamp, "rolled_back", {"approver": approver})
        updated = self.db.get_incident(incident_id)
        assert updated is not None
        return self._view(updated)

    def postmortem(self, incident_id: str) -> str:
        row = self._require(incident_id)
        if not row["postmortem_md"]:
            raise IncidentError("postmortem is available after a repair decision")
        return row["postmortem_md"]

    def _live_bundle(self, incident_id: str) -> dict[str, Any]:
        assert self.lab is not None
        from chaosmedic.live import exports_to_bundle

        return exports_to_bundle(self.lab.export_all(), incident_id)

    def _live_rollback(self, rollback: dict[str, Any]) -> None:
        assert self.lab is not None
        if rollback["action"] == "restore_config_snapshot":
            self.lab.restore_config(rollback["service"], rollback["config"])
            return
        self.lab.execute(rollback)

    def _require(self, incident_id: str) -> dict[str, Any]:
        row = self.db.get_incident(incident_id)
        if row is None:
            raise IncidentError("unknown incident")
        return row

    def _record_to_view_input(
        self,
        row: dict[str, Any],
        *,
        status: str,
        world_dict: dict[str, Any],
        bundle: dict[str, Any],
        action: dict[str, Any],
        rollback: dict[str, Any] | None,
        approver: str,
        approved_at: str,
        verification: dict[str, Any],
    ) -> dict[str, Any]:
        diagnosis = _load(row["diagnosis_json"])
        return {
            "id": row["id"],
            "scenario_id": row["scenario_id"],
            "title": row["title"],
            "summary": row["summary"],
            "status": status,
            "mode": row["mode"],
            "created_at": row["created_at"],
            "diagnosis_ms": row["diagnosis_ms"],
            "diagnosis": diagnosis,
            "ground_truth": _load(row["ground_truth_json"]),
            "telemetry": bundle,
            "approved_action": action,
            "rollback_action": rollback,
            "approver": approver,
            "approved_at": approved_at,
            "verification": verification,
            "world": world_dict,
        }

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        diagnosis = _load(row["diagnosis_json"]) or {"hypotheses": [], "tool_calls": []}
        bundle = _load(row["telemetry_json"])
        proposed = _load(row["proposed_action_json"])
        approved = _load(row["approved_action_json"])
        stored_rollback = _load(row["rollback_action_json"])
        rollback = stored_rollback
        if rollback is None and proposed:
            try:
                rollback = rollback_for(
                    normalize_action(
                        {key: value for key, value in proposed.items() if key != "rationale"}
                    ),
                    _load(row["world_json"]),
                )
            except RunbookError:
                rollback = None
        verification = _load(row["verification_json"])
        top = diagnosis["hypotheses"][0] if diagnosis.get("hypotheses") else None
        root = top["service"] if top and row["status"] != "undetermined" else None
        # Highlight the leading hypothesis while the operator is still deciding,
        # and clear it once the path is healthy again.
        if row["status"] in {"resolved"}:
            root_for_map = None
        else:
            root_for_map = root
        action = approved or proposed
        return {
            "id": row["id"],
            "scenario_id": row["scenario_id"],
            "title": row["title"],
            "summary": row["summary"],
            "status": row["status"],
            "mode": row["mode"],
            "diagnoser": (diagnosis or {}).get("diagnoser", self.diagnoser),
            "fallback": (diagnosis or {}).get("fallback"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "diagnosis_ms": row["diagnosis_ms"],
            "tool_calls": diagnosis.get("tool_calls") or [],
            "hypotheses": diagnosis.get("hypotheses") or [],
            "proposed_action": None
            if proposed is None
            else {**proposed, "description": describe_action(proposed)},
            "approved_action": None
            if approved is None
            else {**approved, "description": describe_action(approved)},
            "rollback_action": None
            if rollback is None
            else {**rollback, "description": describe_action(rollback)},
            "rollback_available": bool(stored_rollback) and row["status"] in {"resolved", "repair_failed"},
            "approver": row["approver"],
            "approved_at": row["approved_at"],
            "verification": verification,
            "postmortem_ready": bool(row["postmortem_md"]),
            "topology": topology(bundle, root_for_map),
            "telemetry": {
                "logs": bundle["logs"],
                "spans": bundle["spans"],
                "metrics": bundle["metrics"],
                "health": bundle["health"],
                "configs": bundle["configs"],
            },
            "action_description": describe_action(action) if action else None,
            "postmortem": row["postmortem_md"],
        }


def _approver(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned or len(cleaned) > 64:
        raise IncidentError("approver must be 1-64 characters")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-")
    if any(char not in allowed for char in cleaned):
        raise IncidentError("approver contains unsupported characters")
    return cleaned
