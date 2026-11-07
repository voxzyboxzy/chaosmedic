"""Ollama is optional. Bad model output falls back to the rules diagnoser."""

from __future__ import annotations

import json

from chaosmedic.ollama import diagnose_with_mode
from chaosmedic.simulator import healthy_world, inject, observe


def _bundle() -> dict:
    world = inject(healthy_world(), "latency_spike", "payments", latency_ms=1400)
    return observe(world, "ollama-case")


def test_rules_mode_never_calls_the_model() -> None:
    def chat(messages):  # pragma: no cover - must not run
        raise AssertionError(messages)

    result = diagnose_with_mode(_bundle(), "rules", chat=chat)
    assert result["diagnoser"] == "rules"
    assert result["hypotheses"][0]["service"] == "payments"


def test_shell_action_from_the_model_is_discarded() -> None:
    def chat(messages):
        del messages
        return json.dumps(
            {
                "fault": "latency_spike",
                "service": "payments",
                "action": {"action": "restart_service", "service": "payments; rm -rf /"},
            }
        )

    result = diagnose_with_mode(_bundle(), "ollama", chat=chat)
    assert result["diagnoser"] == "rules"
    assert result["fallback"].startswith("ollama:")
    assert result["proposed_action"]["action"] == "scale_service"


def test_model_can_call_a_read_only_tool_and_select_a_scored_hypothesis() -> None:
    state = {"calls": 0}

    def chat(messages):
        state["calls"] += 1
        if state["calls"] == 1:
            return json.dumps({"tool": "get_exclusive_latency", "args": {}})
        assert "tool_result" in messages[-1]["content"]
        return json.dumps(
            {
                "fault": "latency_spike",
                "service": "payments",
                "action": {"action": "scale_service", "service": "payments", "replicas": 3},
            }
        )

    result = diagnose_with_mode(_bundle(), "ollama", chat=chat)
    assert result["diagnoser"] == "ollama"
    assert result["proposed_action"]["action"] == "scale_service"
    assert result["hypotheses"][0]["for"]
    assert state["calls"] == 2


def test_unknown_tool_falls_back() -> None:
    def chat(messages):
        del messages
        return json.dumps({"tool": "run_shell", "args": {"command": "id"}})

    result = diagnose_with_mode(_bundle(), "ollama", chat=chat)
    assert result["diagnoser"] == "rules"
    assert "fallback" in result
