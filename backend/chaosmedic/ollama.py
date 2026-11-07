"""Optional Ollama investigator.

The model may call the read-only toolset and must finish with an allowlisted
action. Any malformed reply, shell-looking field, or unreachable server falls
back to the rules diagnoser. The evidence text still comes from the rules
scorer so the board cannot be filled with uncited claims.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

import httpx

from chaosmedic.catalog import FAULTS, SERVICES
from chaosmedic.diagnoser import diagnose
from chaosmedic.runbooks import RunbookError, normalize_action
from chaosmedic.tools import Toolset

ChatFn = Callable[[list[dict[str, str]]], str]

READONLY: dict[str, set[str]] = {
    "list_services": set(),
    "get_health": set(),
    "get_metrics": {"service"},
    "get_logs": {"service", "event"},
    "get_traces": {"service"},
    "get_config": {"service"},
    "get_dependency_probes": set(),
    "get_exclusive_latency": set(),
}

SYSTEM = (
    "You are ChaosMedic's investigator for a local lab. "
    "You may call only these read-only tools: "
    + ", ".join(sorted(READONLY))
    + ". Reply with one JSON object. "
    'To call a tool: {"tool": "get_health", "args": {}}. '
    'To finish: {"fault": "latency_spike", "service": "payments", '
    '"action": {"action": "scale_service", "service": "payments", "replicas": 3}}. '
    "fault must be one of "
    + ", ".join(FAULTS)
    + ". Never propose a shell command, a path, or extra action fields."
)


def _shrink(result: Any) -> Any:
    if isinstance(result, list):
        return result[:12]
    if isinstance(result, dict) and "spans" in result:
        return result
    return result


def ollama_choose(bundle: dict[str, Any], chat: ChatFn, max_steps: int = 6) -> dict[str, Any]:
    tools = Toolset(bundle)
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": "Investigate the open incident. Use tools, then return the final JSON object.",
        },
    ]
    for _ in range(max_steps):
        content = chat(messages)
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("model did not return JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("model JSON must be an object")
        if "tool" in data:
            name = data["tool"]
            if name not in READONLY:
                raise ValueError("tool is not read-only")
            raw_args = data.get("args") or {}
            if not isinstance(raw_args, dict):
                raise ValueError("tool args must be an object")
            args = {key: raw_args[key] for key in raw_args if key in READONLY[name]}
            method = getattr(tools, name)
            result = method(**args) if args else method()
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps({"tool_result": _shrink(result)}, default=str),
                }
            )
            continue
        if "fault" in data and "action" in data and "service" in data:
            if data["fault"] not in FAULTS or data["service"] not in SERVICES:
                raise ValueError("fault or service is not in the catalog")
            try:
                action = normalize_action(data["action"])
            except RunbookError as exc:
                raise ValueError(str(exc)) from exc
            return {"fault": data["fault"], "service": data["service"], "action": action}
        raise ValueError("unrecognized model response")
    raise ValueError("model did not finish")


def default_chat(messages: list[dict[str, str]]) -> str:
    base = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    response = httpx.post(
        f"{base}/api/chat",
        json={"model": model, "messages": messages, "stream": False, "format": "json"},
        timeout=20.0,
    )
    response.raise_for_status()
    body = response.json()
    return body["message"]["content"]


def diagnose_with_mode(
    bundle: dict[str, Any],
    mode: str,
    chat: ChatFn | None = None,
) -> dict[str, Any]:
    if mode != "ollama":
        return diagnose(bundle)
    talk = chat or default_chat
    rules = diagnose(bundle)
    try:
        choice = ollama_choose(bundle, talk)
    except Exception as exc:  # noqa: BLE001 - fallback is the product behavior
        rules["fallback"] = f"ollama:{exc.__class__.__name__}"
        return rules

    matched = None
    for item in rules["hypotheses"]:
        if item["fault"] == choice["fault"] and item["service"] == choice["service"]:
            matched = item
            break
    if matched is None:
        rules["fallback"] = "ollama:hypothesis_not_in_scored_set"
        return rules

    hypotheses = [matched] + [
        item
        for item in rules["hypotheses"]
        if not (item["fault"] == matched["fault"] and item["service"] == matched["service"])
    ]
    for index, item in enumerate(hypotheses, start=1):
        item["rank"] = index
    action = dict(choice["action"])
    action["rationale"] = matched["summary"]
    return {
        "hypotheses": hypotheses[:4],
        "proposed_action": action,
        "tool_calls": rules["tool_calls"],
        "diagnoser": "ollama",
    }
