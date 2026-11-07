"""Recorded incidents stay aligned with the simulator, and the harness is deterministic."""

from __future__ import annotations

import json

from chaosmedic.eval.harness import RESULTS_PATH, run_eval, verdicts
from chaosmedic.recording import demo_cases, heldout_cases, load_cases, DEMO_DIR, HELDOUT_DIR
from chaosmedic.simulator import World, observe


def test_recorded_telemetry_matches_the_simulator() -> None:
    for case in demo_cases() + heldout_cases():
        fresh = observe(World.from_dict(case["world"]), case["id"])
        assert fresh == case["telemetry"]


def test_committed_recordings_match_the_builder() -> None:
    for directory, builder in ((DEMO_DIR, demo_cases), (HELDOUT_DIR, heldout_cases)):
        committed = {case["id"]: case for case in load_cases(directory)}
        built = {case["id"]: case for case in builder()}
        assert committed.keys() == built.keys()
        for case_id, case in built.items():
            assert committed[case_id] == case


def test_demo_and_heldout_ids_do_not_overlap() -> None:
    demo_ids = {case["id"] for case in demo_cases()}
    heldout_ids = {case["id"] for case in heldout_cases()}
    assert demo_ids.isdisjoint(heldout_ids)
    assert len(heldout_ids) >= 12


def test_eval_holds_the_committed_verdicts() -> None:
    report = run_eval()
    assert report["incidents"] >= 12
    assert report["root_cause_accuracy"] == 1.0
    assert report["recovery_success_rate"] == 1.0
    assert report["unnecessary_actions"] == 0
    assert report["mean_time_to_diagnosis_ms"] >= 0
    committed = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    assert verdicts(committed) == verdicts(report)
    assert committed["root_cause_accuracy"] == report["root_cause_accuracy"]
    assert committed["unnecessary_actions"] == report["unnecessary_actions"]
    assert committed["recovery_successes"] == report["recovery_successes"]
