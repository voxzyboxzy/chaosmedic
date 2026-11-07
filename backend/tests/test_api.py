"""Replay API: inject, explain, approve, verify, export, roll back."""

from __future__ import annotations

from fastapi.testclient import TestClient

from chaosmedic.api import create_app


def _client(tmp_path) -> TestClient:
    app = create_app(db_path=str(tmp_path / "chaosmedic.db"), mode="replay", diagnoser="rules")
    return TestClient(app)


def test_health_and_healthy_overview(tmp_path) -> None:
    client = _client(tmp_path)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["mode"] == "replay"
    overview = client.get("/api/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["incident"] is None
    assert {node["id"] for node in body["topology"]["nodes"]} == {
        "gateway",
        "orders",
        "payments",
        "cache",
        "ledger",
    }
    assert all(node["status"] == "up" for node in body["topology"]["nodes"])
    assert any(item["id"] == "checkout-timeouts" for item in body["scenarios"])


def test_inject_approve_and_postmortem_hide_the_label_until_the_writeup(tmp_path) -> None:
    client = _client(tmp_path)
    injected = client.post("/api/incidents/inject", json={"scenario_id": "checkout-timeouts"})
    assert injected.status_code == 200
    incident = injected.json()
    assert "ground_truth" not in incident
    assert incident["status"] == "pending_approval"
    assert incident["hypotheses"][0]["fault"] == "latency_spike"
    assert incident["hypotheses"][0]["service"] == "payments"
    assert incident["proposed_action"]["action"] == "scale_service"
    assert incident["diagnosis_ms"] >= 0
    assert incident["tool_calls"]
    degraded = [node["id"] for node in incident["topology"]["nodes"] if node["status"] != "up"]
    assert "payments" in degraded
    assert "gateway" in degraded
    payments = next(node for node in incident["topology"]["nodes"] if node["id"] == "payments")
    assert payments["root_cause"] is True

    early = client.get(f"/api/incidents/{incident['id']}/postmortem.md")
    assert early.status_code == 409

    approved = client.post(
        f"/api/incidents/{incident['id']}/approve",
        json={"approver": "lab-operator"},
    )
    assert approved.status_code == 200
    resolved = approved.json()
    assert resolved["status"] == "resolved"
    assert resolved["verification"]["passed"] is True
    assert all(node["status"] == "up" for node in resolved["topology"]["nodes"])
    assert "ground_truth" not in resolved
    assert "Lab confirmation" in resolved["postmortem"]
    assert "latency_spike" in resolved["postmortem"]
    assert "trace:" in resolved["postmortem"] or "trace `" in resolved["postmortem"]

    downloaded = client.get(f"/api/incidents/{incident['id']}/postmortem.md")
    assert downloaded.status_code == 200
    assert "text/markdown" in downloaded.headers["content-type"]
    assert "exclusive server time" in downloaded.text

    again = client.post(
        f"/api/incidents/{incident['id']}/approve",
        json={"approver": "lab-operator"},
    )
    assert again.status_code == 409


def test_rollback_restores_the_failure(tmp_path) -> None:
    client = _client(tmp_path)
    incident = client.post("/api/incidents/inject", json={"scenario_id": "checkout-timeouts"}).json()
    client.post(f"/api/incidents/{incident['id']}/approve", json={"approver": "lab-operator"})
    rolled = client.post(
        f"/api/incidents/{incident['id']}/rollback",
        json={"approver": "lab-operator"},
    )
    assert rolled.status_code == 200
    body = rolled.json()
    assert body["status"] == "rolled_back"
    gateway = next(node for node in body["topology"]["nodes"] if node["id"] == "gateway")
    assert gateway["status"] != "up"


def test_restart_has_no_inverse(tmp_path) -> None:
    client = _client(tmp_path)
    incident = client.post("/api/incidents/inject", json={"scenario_id": "orders-unreachable"}).json()
    assert incident["hypotheses"][0] == {
        **incident["hypotheses"][0],
        "fault": "service_crash",
        "service": "orders",
    }
    approved = client.post(
        f"/api/incidents/{incident['id']}/approve",
        json={"approver": "lab-operator"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "resolved"
    rolled = client.post(
        f"/api/incidents/{incident['id']}/rollback",
        json={"approver": "lab-operator"},
    )
    assert rolled.status_code == 409


def test_reject_does_not_repair(tmp_path) -> None:
    client = _client(tmp_path)
    incident = client.post("/api/incidents/inject", json={"scenario_id": "charges-declined"}).json()
    assert incident["hypotheses"][0]["fault"] == "bad_config"
    rejected = client.post(
        f"/api/incidents/{incident['id']}/reject",
        json={"approver": "lab-operator"},
    )
    assert rejected.status_code == 200
    body = rejected.json()
    assert body["status"] == "rejected"
    payments = next(node for node in body["topology"]["nodes"] if node["id"] == "payments")
    assert payments["status"] != "up"


def test_unknown_scenario_and_shell_approver(tmp_path) -> None:
    client = _client(tmp_path)
    missing = client.post("/api/incidents/inject", json={"scenario_id": "not-real"})
    assert missing.status_code == 400
    incident = client.post("/api/incidents/inject", json={"scenario_id": "edge-down"}).json()
    bad = client.post(
        f"/api/incidents/{incident['id']}/approve",
        json={"approver": "ops; rm -rf /"},
    )
    assert bad.status_code == 409


def test_otlp_json_is_accepted(tmp_path) -> None:
    client = _client(tmp_path)
    traces = client.post(
        "/v1/traces",
        json={
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "payments"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "a" * 32,
                                    "spanId": "b" * 16,
                                    "name": "POST /charge",
                                    "kind": 2,
                                    "startTimeUnixNano": "0",
                                    "endTimeUnixNano": "10000000",
                                    "attributes": [
                                        {
                                            "key": "lab.duration_pad_ms",
                                            "value": {"intValue": 40},
                                        }
                                    ],
                                    "status": {"code": 1},
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    )
    assert traces.status_code == 200
    assert traces.json()["accepted"] == 1
    logs = client.post(
        "/v1/logs",
        json={
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "payments"}}
                        ]
                    },
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "traceId": "a" * 32,
                                    "body": {"stringValue": "charge completed"},
                                    "attributes": [
                                        {"key": "event", "value": {"stringValue": "request"}},
                                        {"key": "level", "value": {"stringValue": "info"}},
                                        {"key": "log.id", "value": {"stringValue": "log-1"}},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    )
    assert logs.status_code == 200
    overview = client.get("/api/overview").json()
    assert overview["otlp"]["traces"] == 1
    assert overview["otlp"]["logs"] == 1
