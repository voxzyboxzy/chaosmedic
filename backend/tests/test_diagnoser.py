"""Rules diagnoser behavior on simulator telemetry."""

from __future__ import annotations

from chaosmedic.diagnoser import diagnose
from chaosmedic.recovery import verify
from chaosmedic.simulator import apply_action, exclusive_ms, healthy_world, inject, observe


def _top(fault: str, service: str, **detail: object) -> dict:
    world = inject(healthy_world(), fault, service, **detail)
    bundle = observe(world, f"case-{fault}-{service}")
    diagnosis = diagnose(bundle)
    return diagnosis


def test_each_fault_class_is_the_leading_hypothesis() -> None:
    cases = [
        ("service_crash", "gateway", {}),
        ("service_crash", "orders", {}),
        ("service_crash", "payments", {}),
        ("latency_spike", "gateway", {"latency_ms": 1100}),
        ("latency_spike", "orders", {"latency_ms": 1700}),
        ("latency_spike", "payments", {"latency_ms": 2600}),
        ("bad_config", "gateway", {"config": {"timeout_ms": 5}}),
        ("bad_config", "orders", {"config": {"upstream_payments": "http://10.0.0.9:9"}}),
        ("bad_config", "payments", {"config": {"mode": "reject_all"}}),
        ("dependency_outage", "cache", {}),
        ("dependency_outage", "ledger", {}),
        ("memory_leak", "orders", {}),
        ("memory_leak", "payments", {}),
    ]
    for fault, service, detail in cases:
        diagnosis = _top(fault, service, **detail)
        top = diagnosis["hypotheses"][0]
        assert (top["fault"], top["service"]) == (fault, service), diagnosis["hypotheses"][:2]
        assert diagnosis["proposed_action"]["service"] == service
        assert len(diagnosis["hypotheses"]) >= 2
        assert diagnosis["tool_calls"]


def test_downstream_latency_is_not_blamed_on_the_gateway() -> None:
    world = inject(healthy_world(), "latency_spike", "payments", latency_ms=2600)
    bundle = observe(world, "propagation")
    diagnosis = diagnose(bundle)
    top = diagnosis["hypotheses"][0]
    assert (top["fault"], top["service"]) == ("latency_spike", "payments")
    assert any("downstream" in item["text"] for item in top["for"])
    assert exclusive_ms(bundle["spans"], "payments") > 2000
    assert exclusive_ms(bundle["spans"], "gateway") < 50
    assert all(
        not (item["fault"] == "latency_spike" and item["service"] == "gateway")
        or item["score"] < top["score"]
        for item in diagnosis["hypotheses"]
    )


def test_forged_exclusive_metric_does_not_override_spans() -> None:
    world = inject(healthy_world(), "latency_spike", "orders", latency_ms=1600)
    bundle = observe(world, "forged")
    bundle["metrics"]["gateway"]["exclusive_ms"] = 5000
    bundle["metrics"]["orders"]["exclusive_ms"] = 1
    diagnosis = diagnose(bundle)
    top = diagnosis["hypotheses"][0]
    assert top["fault"] == "latency_spike"
    assert top["service"] == "orders"


def test_ledger_checkpoint_is_not_an_outage() -> None:
    bundle = observe(healthy_world(), "quiet")
    assert any(log["event"] == "checkpoint" and log["service"] == "ledger" for log in bundle["logs"])
    world = inject(healthy_world(), "latency_spike", "payments", latency_ms=900)
    diagnosis = diagnose(observe(world, "checkpoint-decoy"))
    top = diagnosis["hypotheses"][0]
    assert top["service"] != "ledger"
    assert top["fault"] != "dependency_outage"


def test_restart_does_not_clear_latency_but_scale_does() -> None:
    world = inject(healthy_world(), "latency_spike", "payments", latency_ms=2600)
    restarted = apply_action(world, {"action": "restart_service", "service": "payments"})
    assert verify(observe(restarted, "restarted"))["passed"] is False
    scaled = apply_action(world, {"action": "scale_service", "service": "payments", "replicas": 3})
    assert verify(observe(scaled, "scaled"))["passed"] is True


def test_cache_bypass_is_an_alternate_repair() -> None:
    world = inject(healthy_world(), "dependency_outage", "cache")
    bypassed = apply_action(
        world,
        {"action": "toggle_flag", "service": "orders", "flag": "cache_bypass", "value": True},
    )
    result = verify(observe(bypassed, "bypass"))
    assert result["passed"] is True
    restarted = apply_action(world, {"action": "restart_service", "service": "cache"})
    assert verify(observe(restarted, "cache-up"))["passed"] is True


def test_rollback_config_fixes_bad_mode_and_restart_does_not() -> None:
    world = inject(healthy_world(), "bad_config", "payments", config={"mode": "reject_all"})
    restarted = apply_action(world, {"action": "restart_service", "service": "payments"})
    assert verify(observe(restarted, "restart-config"))["passed"] is False
    rolled = apply_action(world, {"action": "rollback_config", "service": "payments"})
    assert verify(observe(rolled, "rolled-config"))["passed"] is True
