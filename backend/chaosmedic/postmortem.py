"""Markdown postmortem export."""

from __future__ import annotations

from typing import Any

from chaosmedic.runbooks import describe_action


def _bullet_evidence(items: list[dict[str, Any]]) -> str:
    if not items:
        return "- None recorded.\n"
    lines = []
    for item in items:
        ref = item["ref"]
        kind = item["kind"]
        trace = f", trace `{item['trace_id']}`" if item.get("trace_id") else ""
        lines.append(f"- `{kind}:{ref}`{trace}: {item['text']}")
    return "\n".join(lines) + "\n"


def render_postmortem(incident: dict[str, Any]) -> str:
    diagnosis = incident["diagnosis"]
    top = diagnosis["hypotheses"][0]
    action = incident.get("approved_action") or diagnosis.get("proposed_action")
    verification = incident.get("verification") or {"passed": False, "checks": []}
    ground = incident["ground_truth"]
    metrics = incident["telemetry"]["metrics"]["gateway"]
    lines: list[str] = []
    lines.append(f"# Postmortem: {incident['title']}")
    lines.append("")
    lines.append(f"- Incident: `{incident['id']}`")
    lines.append(f"- Scenario: `{incident['scenario_id']}`")
    lines.append(f"- Status: {incident['status']}")
    lines.append(f"- Mode: {incident['mode']}")
    lines.append(f"- Diagnoser: {diagnosis.get('diagnoser', 'rules')}")
    if diagnosis.get("fallback"):
        lines.append(f"- Diagnoser fallback: {diagnosis['fallback']}")
    lines.append(f"- Time to diagnosis: {incident['diagnosis_ms']:.2f} ms")
    lines.append(f"- Read-only tool calls: {len(diagnosis.get('tool_calls', []))}")
    if incident.get("approver"):
        lines.append(f"- Approver: {incident['approver']}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(top["summary"])
    lines.append("")
    lines.append("## Impact")
    lines.append("")
    p99 = metrics["p99_ms"]
    p99_text = "n/a" if p99 is None else f"{p99}ms"
    lines.append(
        f"During the degraded window the gateway error rate was "
        f"{metrics['error_rate']:.0%} and gateway p99 was {p99_text}."
    )
    lines.append("")
    lines.append("## Timeline")
    lines.append("")
    lines.append(f"- {incident['created_at']}: outage injected from the lab catalog.")
    lines.append(
        f"- Diagnosis completed in {incident['diagnosis_ms']:.2f} ms "
        f"after {len(diagnosis.get('tool_calls', []))} read-only tool calls."
    )
    if incident.get("approved_at"):
        lines.append(f"- {incident['approved_at']}: repair approved by {incident['approver']}.")
    if verification.get("passed"):
        lines.append("- Recovery checks passed after the repair.")
    elif incident.get("approved_at"):
        lines.append("- Recovery checks did not pass after the repair.")
    lines.append("")
    lines.append("## Hypotheses")
    lines.append("")
    for item in diagnosis["hypotheses"]:
        lines.append(
            f"### {item['rank']}. {item['fault']} on {item['service']} "
            f"(score {item['score']}, confidence {item['confidence']:.2f})"
        )
        lines.append("")
        lines.append(item["summary"])
        lines.append("")
        lines.append("**For**")
        lines.append("")
        lines.append(_bullet_evidence(item["for"]).rstrip())
        lines.append("")
        lines.append("**Against**")
        lines.append("")
        lines.append(_bullet_evidence(item["against"]).rstrip())
        lines.append("")
    lines.append("## Decision")
    lines.append("")
    if action:
        lines.append(f"Approved repair: {describe_action(action)}.")
        lines.append("")
        lines.append("```json")
        # action may contain rationale; show the executable fields only
        shown = {key: action[key] for key in action if key != "rationale"}
        import json
        lines.append(json.dumps(shown, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        rollback = incident.get("rollback_action")
        if rollback:
            lines.append(f"Rollback: {describe_action(rollback)}.")
        elif action.get("action") == "restart_service":
            lines.append(
                "Rollback: restart has no inverse. The cleared process state cannot be restored by an allowlisted repair."
            )
        lines.append("")
    else:
        lines.append("No repair was proposed.")
        lines.append("")
    lines.append("## Verification")
    lines.append("")
    if not verification["checks"]:
        lines.append("Verification did not run.")
    for check in verification["checks"]:
        mark = "x" if check["ok"] else " "
        lines.append(f"- [{mark}] {check['detail']}")
    lines.append("")
    lines.append("## Lab confirmation")
    lines.append("")
    lines.append(
        "The lab seed recorded the injected fault as "
        f"`{ground['fault']}` on `{ground['service']}`. "
        "That label is written here so an operator can compare it with the diagnosis. "
        "It is not passed to the diagnoser."
    )
    lines.append("")
    lines.append("## Follow-ups")
    lines.append("")
    lines.append(f"- {_followup(top['fault'], top['service'])}")
    lines.append("- Keep the repair inside the allowlist. Do not add a shell escape hatch.")
    lines.append("")
    return "\n".join(lines)


def _followup(fault: str, service: str) -> str:
    if fault == "latency_spike":
        return f"Page on {service} exclusive server time, not only on the gateway's end-to-end p99."
    if fault == "service_crash":
        return f"Alert when {service} readiness fails and the process stops producing server spans."
    if fault == "bad_config":
        return f"Require a diff against the {service} baseline manifest before a config change is activated."
    if fault == "dependency_outage":
        return f"Add a short-circuit and a dedicated probe for {service}, separate from caller error rate."
    if fault == "memory_leak":
        return f"Alert when {service} memory slopes upward inside a deploy, and keep restart as the approved mitigation."
    return "Review the signals that decided the top hypothesis."
