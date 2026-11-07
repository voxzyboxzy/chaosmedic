"""SQLite incident store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL,
  mode TEXT NOT NULL,
  ground_truth_json TEXT NOT NULL,
  world_json TEXT NOT NULL,
  telemetry_json TEXT NOT NULL,
  diagnosis_json TEXT,
  diagnosis_ms REAL,
  proposed_action_json TEXT,
  approved_action_json TEXT,
  rollback_action_json TEXT,
  pre_repair_world_json TEXT,
  approver TEXT,
  approved_at TEXT,
  verification_json TEXT,
  postmortem_md TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS otlp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  body_json TEXT NOT NULL,
  received_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def audit(self, incident_id: str | None, ts: str, kind: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO audit (incident_id, ts, kind, payload_json) VALUES (?, ?, ?, ?)",
            (incident_id, ts, kind, json.dumps(payload, sort_keys=True)),
        )
        self.conn.commit()

    def insert_incident(self, row: dict[str, Any]) -> None:
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(columns)
        self.conn.execute(
            f"INSERT INTO incidents ({names}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )
        self.conn.commit()

    def update_incident(self, incident_id: str, **fields: Any) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [incident_id]
        self.conn.execute(
            f"UPDATE incidents SET {assignments} WHERE id = ?",
            values,
        )
        self.conn.commit()

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        cursor = self.conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def add_otlp(self, kind: str, body: dict[str, Any], received_at: str) -> None:
        self.conn.execute(
            "INSERT INTO otlp (kind, body_json, received_at) VALUES (?, ?, ?)",
            (kind, json.dumps(body), received_at),
        )
        self.conn.commit()

    def otlp_counts(self) -> dict[str, int]:
        cursor = self.conn.execute("SELECT kind, COUNT(*) AS n FROM otlp GROUP BY kind")
        return {row["kind"]: row["n"] for row in cursor.fetchall()}

    def latest_incident(self) -> dict[str, Any] | None:
        cursor = self.conn.execute(
            "SELECT * FROM incidents ORDER BY created_at DESC, id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None
