"""Unit tests for structured scenario specs and injector registry."""

from __future__ import annotations

import pytest

from production_ops_lab.server.injectors.monolith_injectors import MonolithFailureInjector
from production_ops_lab.server.scenario_generator import ScenarioGenerator


class _NoopBackend:
    pass


def test_active_scenarios_are_complete_and_well_shaped() -> None:
    generator = ScenarioGenerator()

    for task in generator.task_specs:
        assert task.task_id
        assert task.incident_type
        assert task.service
        assert task.correct_fix_description
        assert task.expected_triage_path
        assert task.expected_investigation_commands
        assert task.root_cause_signal_commands
        assert task.expected_fix_path
        assert task.expected_verification_path
        assert task.acceptance_checks
        assert task.recent_event_lines
        assert len(task.red_herrings) <= 1
        assert task.tags


def test_injector_registry_rejects_unknown_incidents() -> None:
    injector = MonolithFailureInjector(_NoopBackend())  # type: ignore[arg-type]

    assert set(injector.incident_types) == {
        "app_service_stopped",
        "bad_env_db_url",
        "queue_backlog_due_to_worker_failure",
        "nginx_service_stopped",
        "postgres_service_stopped",
        "redis_service_stopped",
    }

    with pytest.raises(ValueError, match="Unsupported incident_type"):
        injector.inject("not-a-real-incident", {})
