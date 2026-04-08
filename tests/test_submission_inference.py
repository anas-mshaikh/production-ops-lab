"""Fast tests for the submission inference wrapper helpers."""

from __future__ import annotations

import pytest

from production_ops_lab.inference import (
    coerce_model_command,
    format_end_line,
    format_start_line,
    format_step_line,
    get_expected_command,
    load_settings,
)


def test_load_settings_requires_hf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="HF_TOKEN is required"):
        load_settings()


def test_submission_log_lines_match_expected_shape() -> None:
    assert (
        format_start_line("app_service_stopped")
        == '[START] task_id="app_service_stopped"'
    )
    assert (
        format_step_line(
            step=1,
            action="svc status app",
            reward=0.5,
            done=False,
            error=None,
        )
        == '[STEP] step=1 action="svc status app" reward=0.50 done=false error=null'
    )
    assert (
        format_end_line(success=True, steps=3, score=1.0, rewards=[0.25, 0.75, 1.0])
        == "[END] success=true steps=3 score=1.00 rewards=[0.25, 0.75, 1.00]"
    )


def test_expected_command_falls_back_to_lab_verify() -> None:
    assert get_expected_command("app_service_stopped", 1) == "svc status app"
    assert get_expected_command("nginx_service_stopped", 2) == "svc restart nginx"
    assert get_expected_command("postgres_service_stopped", 1) == "svc status postgres"
    assert get_expected_command("redis_service_stopped", 2) == "svc restart redis"
    assert get_expected_command("app_service_stopped", 10) == "lab verify"
    assert get_expected_command("unknown-task", 1) == "svc status app"


def test_model_command_coercion_uses_fallback_for_empty_or_fenced_output() -> None:
    assert coerce_model_command("", "svc status app") == "svc status app"
    assert coerce_model_command("```", "svc status app") == "svc status app"
    assert (
        coerce_model_command("command: svc restart app", "svc status app")
        == "svc restart app"
    )
