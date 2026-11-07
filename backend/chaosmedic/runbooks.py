"""Allowlisted repairs.

Every proposed mutation is parsed into a closed schema. The executor never
receives a command string, and this module never starts a process.
"""

from __future__ import annotations

from typing import Any

from chaosmedic.catalog import ACTIONS, ACTION_FIELDS, FLAGS, REPLICA_CHOICES, SERVICES

SHELL_CHARACTERS = set(";|&`$<>\n\r")


class RunbookError(ValueError):
    """The proposed repair is outside the allowlist."""


def _reject_shell(value: object) -> None:
    if isinstance(value, str) and any(char in value for char in SHELL_CHARACTERS):
        raise RunbookError("action fields cannot contain shell characters")


def normalize_action(raw: Any) -> dict[str, Any]:
    """Return a canonical action or raise RunbookError.

    The canonical dict is the only object the executor understands.
    """
    if not isinstance(raw, dict):
        raise RunbookError("action must be an object")
    extra = set(raw) - ACTION_FIELDS
    if extra:
        raise RunbookError("action contains fields outside the allowlist")
    for value in raw.values():
        _reject_shell(value)
    name = raw.get("action")
    if name not in ACTIONS:
        raise RunbookError("action is not allowlisted")
    service = raw.get("service")
    if service not in SERVICES:
        raise RunbookError("unknown service")

    if name == "restart_service":
        return {"action": name, "service": service}
    if name == "rollback_config":
        return {"action": name, "service": service}
    if name == "scale_service":
        replicas = raw.get("replicas")
        if replicas not in REPLICA_CHOICES:
            raise RunbookError("replicas must be 1, 2, or 3")
        return {"action": name, "service": service, "replicas": int(replicas)}
    if name == "toggle_flag":
        flag = raw.get("flag")
        if flag not in FLAGS or FLAGS[flag] != service:
            raise RunbookError("flag is not allowlisted for this service")
        value = raw.get("value")
        if not isinstance(value, bool):
            raise RunbookError("flag value must be boolean")
        return {"action": name, "service": service, "flag": flag, "value": value}
    raise RunbookError("action is not allowlisted")


def rollback_for(action: dict[str, Any], world_dict: dict[str, Any]) -> dict[str, Any] | None:
    """Inverse of an allowlisted action, derived from the pre-repair world.

    Restart has no inverse: the process state that was cleared cannot be
    reconstructed as a repair. The caller records that explicitly.
    """
    name = action["action"]
    service = action["service"]
    previous = world_dict["services"][service]
    if name == "scale_service":
        return {
            "action": "scale_service",
            "service": service,
            "replicas": int(previous["replicas"]),
        }
    if name == "toggle_flag":
        flag = action["flag"]
        prior = previous["config"].get(flag)
        if not isinstance(prior, bool):
            prior = False
        return {
            "action": "toggle_flag",
            "service": service,
            "flag": flag,
            "value": prior,
        }
    if name == "rollback_config":
        # Re-apply the config that was active before the rollback.
        return {
            "action": "restore_config_snapshot",
            "service": service,
            "config": previous["config"],
        }
    return None


def describe_action(action: dict[str, Any]) -> str:
    name = action["action"]
    service = action["service"]
    if name == "restart_service":
        return f"Restart {service}"
    if name == "rollback_config":
        return f"Roll {service} config back to the baseline manifest"
    if name == "scale_service":
        noun = "replica" if int(action["replicas"]) == 1 else "replicas"
        return f"Scale {service} to {action['replicas']} {noun}"
    if name == "toggle_flag":
        state = "on" if action["value"] else "off"
        return f"Toggle {service}.{action['flag']} {state}"
    if name == "restore_config_snapshot":
        return f"Restore the previous {service} config snapshot"
    return name
