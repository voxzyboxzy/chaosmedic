"""The repair path is a closed allowlist. No shell, no extra fields."""

from __future__ import annotations

from pathlib import Path

from chaosmedic.runbooks import RunbookError, normalize_action

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("subprocess", "os.system", "os.popen", "shell=True", "Popen(")


def test_production_code_does_not_invoke_a_shell() -> None:
    for folder in (ROOT / "backend" / "chaosmedic", ROOT / "lab"):
        for path in folder.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN:
                assert token not in text, f"{path} contains {token}"


def test_shell_action_is_rejected() -> None:
    for raw in (
        {"action": "bash", "service": "orders"},
        {"action": "restart_service", "service": "orders", "command": "rm -rf /"},
        {"action": "restart_service", "service": "orders; rm -rf /"},
        {"action": "scale_service", "service": "payments", "replicas": 9},
        {"action": "toggle_flag", "service": "gateway", "flag": "cache_bypass", "value": True},
        "restart_service",
        {"action": "restart_service", "service": "not-a-service"},
    ):
        try:
            normalize_action(raw)
        except RunbookError:
            continue
        raise AssertionError(f"accepted {raw!r}")


def test_canonical_actions_round_trip() -> None:
    assert normalize_action({"action": "restart_service", "service": "orders"}) == {
        "action": "restart_service",
        "service": "orders",
    }
    assert normalize_action(
        {"action": "scale_service", "service": "payments", "replicas": 3}
    )["replicas"] == 3
