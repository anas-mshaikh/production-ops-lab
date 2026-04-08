"""HF-compatible integration tests for the local monolith backend."""

from __future__ import annotations

import pytest

from production_ops_lab.models import ProductionOpsLabAction
from production_ops_lab.server.production_ops_environment import ProductionOpsEnvironment
from production_ops_lab.server.real_backend import RealMonolithBackend
from production_ops_lab.server.scenario_generator import ScenarioGenerator
from production_ops_lab.server.world_spec import BASELINE_STATE


def test_real_backend_healthy_world_boots() -> None:
    backend = RealMonolithBackend()
    try:
        backend.restore_healthy_world()
        snapshot = backend.capture_baseline_snapshot()
        assert "app=healthy" in backend.visible_health_summary()
        assert "queue=healthy" in backend.visible_health_summary()
        assert "app=healthy" in backend.visible_snapshot()
        assert snapshot.services == BASELINE_STATE
        assert snapshot.pending_jobs == 0
        assert snapshot.worker_status == "healthy"
        assert snapshot.scheduler_status == "healthy"
        assert backend.last_reset_report is not None
        assert all(backend.last_reset_report.smoke_results.values())
    finally:
        backend.close()


def test_real_backend_reset_succeeds_repeatedly() -> None:
    env = ProductionOpsEnvironment()
    try:
        first = env.reset(task_id="app_service_stopped")
        second = env.reset(task_id="bad_env_db_url")
    finally:
        env.close()

    assert first.alert_message.startswith("ALERT:")
    assert second.alert_message.startswith("ALERT:")
    assert second.visible_health_summary.startswith("VISIBLE HEALTH:")
    assert first.system_snapshot.startswith("HTTP:")
    assert second.active_incidents == ["bad_env_db_url"]
    assert second.hint is None


def test_real_backend_three_consecutive_resets_are_stable() -> None:
    env = ProductionOpsEnvironment()
    generator = ScenarioGenerator()
    try:
        for index in range(3):
            task_id = generator.task_specs[index % len(generator.task_specs)].task_id
            observation = env.reset(task_id=task_id)
            backend = env._backend
            assert isinstance(backend, RealMonolithBackend)
            assert observation.visible_health_summary.startswith("VISIBLE HEALTH:")
            assert observation.system_snapshot.startswith("HTTP:")
            assert backend.last_reset_report is not None
            assert backend.last_reset_report.incident_visible is True
            assert backend.last_reset_report.failed_phase is None
            assert all(backend.last_reset_report.smoke_results.values())
    finally:
        env.close()


@pytest.mark.parametrize(
    ("task_id", "commands"),
    [
        (
            "app_service_stopped",
            ["svc status app", "svc restart app", "http check /health"],
        ),
        (
            "nginx_service_stopped",
            ["svc status nginx", "svc restart nginx", "http check /health"],
        ),
        (
            "bad_env_db_url",
            [
                "svc logs app",
                "env set app DATABASE_URL=correct",
                "svc restart app",
                "lab verify",
            ],
        ),
        (
            "postgres_service_stopped",
            ["svc status postgres", "svc restart postgres", "lab verify"],
        ),
        (
            "redis_service_stopped",
            ["svc status redis", "svc restart redis", "lab verify"],
        ),
        (
            "queue_backlog_due_to_worker_failure",
            ["svc status worker", "svc restart worker", "queue stats"],
        ),
    ],
)
def test_real_starter_tasks_resolve_end_to_end(
    task_id: str,
    commands: list[str],
) -> None:
    env = ProductionOpsEnvironment()
    try:
        initial_observation = env.reset(task_id=task_id)
        final_observation = initial_observation
        for command in commands:
            final_observation = env.step(ProductionOpsLabAction(command=command))
    finally:
        env.close()

    assert initial_observation.visible_health_summary.startswith("VISIBLE HEALTH:")
    assert initial_observation.system_snapshot.startswith("HTTP:")
    if task_id == "app_service_stopped":
        assert "app=stopped" in initial_observation.visible_health_summary
    elif task_id == "nginx_service_stopped":
        assert "ingress=stopped" in initial_observation.visible_health_summary
    elif task_id == "bad_env_db_url":
        assert "app=degraded" in initial_observation.visible_health_summary
    elif task_id == "postgres_service_stopped":
        assert "database=stopped" in initial_observation.visible_health_summary
    elif task_id == "redis_service_stopped":
        assert "redis=stopped" in initial_observation.visible_health_summary
    else:
        assert "queue=backlogged" in initial_observation.visible_health_summary
    assert final_observation.done is True
    assert env.state.incident_resolved is True


def test_bad_database_url_requires_explicit_app_restart() -> None:
    env = ProductionOpsEnvironment()
    try:
        env.reset(task_id="bad_env_db_url")
        env.step(ProductionOpsLabAction(command="env set app DATABASE_URL=correct"))
        verify_before_restart = env.step(ProductionOpsLabAction(command="lab verify"))
        env.step(ProductionOpsLabAction(command="svc restart app"))
        verify_after_restart = env.step(ProductionOpsLabAction(command="lab verify"))
    finally:
        env.close()

    assert verify_before_restart.done is False
    assert env.state.incident_resolved is True
    assert verify_after_restart.done is True


@pytest.mark.parametrize("task_id", [
    "app_service_stopped",
    "bad_env_db_url",
    "queue_backlog_due_to_worker_failure",
    "nginx_service_stopped",
    "postgres_service_stopped",
    "redis_service_stopped",
])
def test_injector_mutations_are_visible_and_reset_restores_baseline(task_id: str) -> None:
    backend = RealMonolithBackend()
    generator = ScenarioGenerator()
    scenario = generator.select(task_id=task_id)
    try:
        backend.restore_healthy_world()
        baseline_snapshot = backend.capture_baseline_snapshot()
        backend.failure_injector.inject(scenario.incident_type, scenario.params)
        backend.failure_injector.wait_until_visible(scenario)

        if task_id == "app_service_stopped":
            assert backend.service_status_label("app") == "stopped"
        elif task_id == "nginx_service_stopped":
            assert backend.service_status_label("nginx") == "stopped"
            assert backend.http_health_ok() is False
        elif task_id == "bad_env_db_url":
            assert backend.service_status_label("app") == "degraded"
            assert backend.read_path_ok() is False
        elif task_id == "postgres_service_stopped":
            assert backend.service_status_label("postgres") == "stopped"
            assert backend.read_path_ok() is False
        elif task_id == "redis_service_stopped":
            assert backend.service_status_label("redis") == "stopped"
            assert backend.http_health_ok() is False
        else:
            assert int(backend.safe_queue_stats().get("pending_jobs", 0)) >= int(
                scenario.params["expected_backlog_jobs"]
            )

        backend.restore_healthy_world()
        restored_snapshot = backend.capture_baseline_snapshot()
        assert restored_snapshot == baseline_snapshot
    finally:
        backend.close()
