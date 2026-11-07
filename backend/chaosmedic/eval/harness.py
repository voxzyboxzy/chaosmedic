"""Measure root-cause accuracy, time to diagnosis, unnecessary actions, and recovery.

The diagnoser sees only the recorded telemetry bundle. Ground truth and the
accepted repairs stay in the harness.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any

from chaosmedic.diagnoser import diagnose
from chaosmedic.recovery import verify
from chaosmedic.recording import HELDOUT_DIR, load_cases
from chaosmedic.runbooks import normalize_action
from chaosmedic.simulator import World, apply_action

ROOT = Path(__file__).resolve().parents[3]
RESULTS_PATH = ROOT / "eval" / "results.json"


def _canonical(action: dict[str, Any] | None) -> dict[str, Any] | None:
    if action is None:
        return None
    cleaned = {key: value for key, value in action.items() if key != "rationale"}
    return normalize_action(cleaned)


def _round(value: float) -> float:
    return round(value, 3)


def run_eval(directory: Path | None = None) -> dict[str, Any]:
    cases = load_cases(directory or HELDOUT_DIR)
    if not cases:
        raise RuntimeError("no held-out incidents are recorded")
    rows: list[dict[str, Any]] = []
    for case in cases:
        bundle = case["telemetry"]
        started = time.perf_counter()
        diagnosis = diagnose(bundle)
        elapsed_ms = (time.perf_counter() - started) * 1000
        top = diagnosis["hypotheses"][0]
        truth = case["ground_truth"]
        correct_root = top["fault"] == truth["fault"] and top["service"] == truth["service"]
        proposed = _canonical(diagnosis.get("proposed_action"))
        accepted = [_canonical(action) for action in case["correct_actions"]]
        unnecessary = 0 if proposed is not None and proposed in accepted else 1
        recovered = False
        verification: dict[str, Any] | None = None
        if proposed is not None:
            world = apply_action(World.from_dict(case["world"]), proposed)
            from chaosmedic.simulator import observe

            repaired = observe(world, case["id"] + "-eval")
            verification = verify(repaired)
            recovered = bool(verification["passed"])
        rows.append(
            {
                "id": case["id"],
                "fault": truth["fault"],
                "service": truth["service"],
                "predicted_fault": top["fault"],
                "predicted_service": top["service"],
                "correct_root_cause": correct_root,
                "diagnosis_ms": round(elapsed_ms, 3),
                "tool_calls": len(diagnosis["tool_calls"]),
                "proposed_action": proposed,
                "unnecessary_actions": unnecessary,
                "recovered": recovered,
            }
        )
    total = len(rows)
    times = [row["diagnosis_ms"] for row in rows]
    correct = sum(1 for row in rows if row["correct_root_cause"])
    unnecessary_total = sum(row["unnecessary_actions"] for row in rows)
    recovered_total = sum(1 for row in rows if row["recovered"])
    return {
        "harness": "chaosmedic.eval",
        "diagnoser": "rules",
        "incidents": total,
        "root_cause_correct": correct,
        "root_cause_accuracy": _round(correct / total),
        "mean_time_to_diagnosis_ms": _round(statistics.fmean(times)),
        "median_time_to_diagnosis_ms": _round(statistics.median(times)),
        "max_time_to_diagnosis_ms": _round(max(times)),
        "unnecessary_actions": unnecessary_total,
        "unnecessary_action_rate": _round(unnecessary_total / total),
        "recovery_successes": recovered_total,
        "recovery_success_rate": _round(recovered_total / total),
        "mean_tool_calls": _round(statistics.fmean(row["tool_calls"] for row in rows)),
        "cases": rows,
    }


def verdicts(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "correct_root_cause": row["correct_root_cause"],
            "unnecessary_actions": row["unnecessary_actions"],
            "recovered": row["recovered"],
            "predicted_fault": row["predicted_fault"],
            "predicted_service": row["predicted_service"],
        }
        for row in report["cases"]
    ]


def write_results(report: dict[str, Any], path: Path | None = None) -> Path:
    destination = path or RESULTS_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def check_report(report: dict[str, Any], min_accuracy: float, expected: Path | None) -> None:
    if report["root_cause_accuracy"] < min_accuracy:
        raise SystemExit(
            f"root-cause accuracy {report['root_cause_accuracy']} is below {min_accuracy}"
        )
    if report["recovery_success_rate"] < min_accuracy:
        raise SystemExit(
            f"recovery success {report['recovery_success_rate']} is below {min_accuracy}"
        )
    if expected is not None and expected.exists():
        committed = json.loads(expected.read_text(encoding="utf-8"))
        if verdicts(committed) != verdicts(report):
            raise SystemExit("held-out verdicts differ from eval/results.json")
