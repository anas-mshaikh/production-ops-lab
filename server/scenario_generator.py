"""Deterministic starter task selection for Production Ops Lab v1."""

from __future__ import annotations

from .constants import DIFFICULTY_MAX_STEPS, STARTER_TASK_IDS
from .task_models import TaskSpec


def _build_task_specs() -> tuple[TaskSpec, ...]:
    return (
        TaskSpec(
            task_id="app_service_stopped",
            incident_type="app_service_stopped",
            difficulty="easy",
            title="Public app service is stopped",
            component="app_runtime",
            service="app",
            params={},
            alert_message="ALERT: public requests are failing because the app service is unavailable.",
            initial_visible_symptom="The app service is stopped and the health endpoint is returning 503.",
            root_cause="The app service is stopped and needs to be restarted.",
            correct_fix_description="Restart the app service and verify that the public health check returns healthy.",
            accepted_fix_commands=("svc restart app",),
            required_fix_commands=("svc restart app",),
            expected_triage_path=(
                "svc status app",
                "http check /health",
            ),
            expected_investigation_commands=(
                "svc status app",
                "svc logs app",
                "http check /health",
            ),
            root_cause_signal_commands=(
                "svc status app",
                "http check /health",
            ),
            expected_fix_path=("svc restart app",),
            expected_verification_path=("http check /health", "lab verify"),
            verification_commands=("http check /health", "lab verify"),
            red_herrings=("svc restart nginx",),
            acceptance_checks=("app_health",),
            recent_event_lines=(
                "Synthetic monitor: GET /health is returning 503 through ingress.",
                "Service monitor: app runtime stopped responding to health probes.",
            ),
            tags=("service", "availability", "easy"),
            max_steps=DIFFICULTY_MAX_STEPS["easy"],
        ),
        TaskSpec(
            task_id="bad_env_db_url",
            incident_type="bad_env_db_url",
            difficulty="medium",
            title="Bad application database URL",
            component="app_config",
            service="app",
            params={"database_url": "broken"},
            alert_message="ALERT: application requests are failing after a configuration change.",
            initial_visible_symptom="The app is running but cannot serve requests because database connectivity is broken.",
            root_cause="The app DATABASE_URL value is misconfigured.",
            correct_fix_description="Restore the healthy DATABASE_URL value, restart the app, and verify DB-backed behavior.",
            accepted_fix_commands=(
                "env set app database_url=healthy",
                "svc restart app",
            ),
            required_fix_commands=(
                "env set app database_url=healthy",
                "svc restart app",
            ),
            expected_triage_path=(
                "svc logs app",
                "env show app",
            ),
            expected_investigation_commands=(
                "svc logs app",
                "env show app",
                "http check /health",
            ),
            root_cause_signal_commands=(
                "svc logs app",
                "env show app",
            ),
            expected_fix_path=(
                "env set app database_url=healthy",
                "svc restart app",
            ),
            expected_verification_path=("http check /health", "lab verify"),
            verification_commands=("http check /health", "lab verify"),
            red_herrings=("svc restart postgres",),
            acceptance_checks=("app_health", "candidate_search"),
            recent_event_lines=(
                "Synthetic monitor: application create failures crossed the alert threshold.",
                "Application health check reports degraded dependency state.",
            ),
            tags=("configuration", "database", "medium"),
            max_steps=DIFFICULTY_MAX_STEPS["medium"],
        ),
        TaskSpec(
            task_id="queue_backlog_due_to_worker_failure",
            incident_type="queue_backlog_due_to_worker_failure",
            difficulty="hard",
            title="Queue backlog caused by worker failure",
            component="worker_runtime",
            service="worker",
            params={"expected_backlog_jobs": 3},
            alert_message="ALERT: background processing is delayed and the job backlog is growing.",
            initial_visible_symptom="The worker path is unhealthy and queued jobs are not draining.",
            root_cause="The worker service failed and the queue backlog is rising.",
            correct_fix_description="Restore worker execution so the backlog drains and the worker heartbeat recovers.",
            accepted_fix_commands=("svc restart worker",),
            required_fix_commands=("svc restart worker",),
            expected_triage_path=(
                "queue stats",
                "svc status worker",
            ),
            expected_investigation_commands=(
                "queue stats",
                "svc status worker",
                "svc logs worker",
            ),
            root_cause_signal_commands=(
                "queue stats",
                "svc logs worker",
            ),
            expected_fix_path=("svc restart worker",),
            expected_verification_path=("queue stats", "lab verify"),
            verification_commands=("queue stats", "lab verify"),
            red_herrings=("svc restart scheduler",),
            acceptance_checks=("app_health", "worker_healthy", "queue_drained"),
            recent_event_lines=(
                "Queue monitor: pending background jobs exceeded the warning threshold.",
                "Worker heartbeat is stale while ingress health remains green.",
            ),
            tags=("queue", "worker", "hard"),
            max_steps=DIFFICULTY_MAX_STEPS["hard"],
        ),
        TaskSpec(
            task_id="nginx_service_stopped",
            incident_type="nginx_service_stopped",
            difficulty="easy",
            title="Ingress proxy is stopped",
            component="ingress_runtime",
            service="nginx",
            params={},
            alert_message="ALERT: ingress requests are failing because the nginx service is unavailable.",
            initial_visible_symptom="The ingress proxy is stopped and the public health check is returning 503.",
            root_cause="The nginx service is stopped and needs to be restarted.",
            correct_fix_description="Restart nginx and verify that the public health check returns healthy.",
            accepted_fix_commands=("svc restart nginx",),
            required_fix_commands=("svc restart nginx",),
            expected_triage_path=(
                "svc status nginx",
                "http check /health",
            ),
            expected_investigation_commands=(
                "svc status nginx",
                "svc logs nginx",
                "http check /health",
            ),
            root_cause_signal_commands=(
                "svc status nginx",
                "http check /health",
            ),
            expected_fix_path=("svc restart nginx",),
            expected_verification_path=("http check /health", "lab verify"),
            verification_commands=("http check /health", "lab verify"),
            red_herrings=("svc restart app",),
            acceptance_checks=("app_health",),
            recent_event_lines=(
                "Synthetic monitor: ingress health checks are failing before they reach the app.",
                "Service monitor: nginx stopped responding to the public interface.",
            ),
            tags=("ingress", "availability", "easy"),
            max_steps=DIFFICULTY_MAX_STEPS["easy"],
        ),
        TaskSpec(
            task_id="postgres_service_stopped",
            incident_type="postgres_service_stopped",
            difficulty="medium",
            title="Primary database service is stopped",
            component="database_runtime",
            service="postgres",
            params={},
            alert_message="ALERT: DB-backed requests are failing because the primary database service is unavailable.",
            initial_visible_symptom="The app is degraded, DB-backed behavior is failing, and the database service is stopped.",
            root_cause="The postgres service is stopped and needs to be restarted.",
            correct_fix_description="Restart postgres and verify that app health and DB-backed reads are restored.",
            accepted_fix_commands=("svc restart postgres",),
            required_fix_commands=("svc restart postgres",),
            expected_triage_path=(
                "svc status postgres",
                "http check /health",
            ),
            expected_investigation_commands=(
                "svc status postgres",
                "svc logs app",
                "http check /health",
            ),
            root_cause_signal_commands=(
                "svc status postgres",
                "svc logs app",
            ),
            expected_fix_path=("svc restart postgres",),
            expected_verification_path=("http check /health", "lab verify"),
            verification_commands=("http check /health", "lab verify"),
            red_herrings=("svc restart redis",),
            acceptance_checks=("app_health", "candidate_search"),
            recent_event_lines=(
                "Synthetic monitor: candidate search and application create checks are failing.",
                "Dependency monitor: postgres is not responding to app traffic.",
            ),
            tags=("database", "dependency", "medium"),
            max_steps=DIFFICULTY_MAX_STEPS["medium"],
        ),
        TaskSpec(
            task_id="redis_service_stopped",
            incident_type="redis_service_stopped",
            difficulty="medium",
            title="Redis service is stopped",
            component="cache_queue_runtime",
            service="redis",
            params={},
            alert_message="ALERT: application health is degraded because the redis dependency is unavailable.",
            initial_visible_symptom="The app is degraded, queue operations are unavailable, and the redis service is stopped.",
            root_cause="The redis service is stopped and needs to be restarted.",
            correct_fix_description="Restart redis and verify that app health is restored.",
            accepted_fix_commands=("svc restart redis",),
            required_fix_commands=("svc restart redis",),
            expected_triage_path=(
                "svc status redis",
                "http check /health",
            ),
            expected_investigation_commands=(
                "svc status redis",
                "svc logs app",
                "queue stats",
            ),
            root_cause_signal_commands=(
                "svc status redis",
                "svc logs app",
            ),
            expected_fix_path=("svc restart redis",),
            expected_verification_path=("http check /health", "lab verify"),
            verification_commands=("http check /health", "lab verify"),
            red_herrings=("svc restart worker",),
            acceptance_checks=("app_health",),
            recent_event_lines=(
                "Synthetic monitor: app health degraded after redis became unreachable.",
                "Dependency monitor: redis is no longer accepting queue traffic.",
            ),
            tags=("redis", "dependency", "medium"),
            max_steps=DIFFICULTY_MAX_STEPS["medium"],
        ),
    )


class ScenarioGenerator:
    """Deterministic task selector for starter scenarios."""

    def __init__(self) -> None:
        self._task_specs = _build_task_specs()
        self._task_map = {task.task_id: task for task in self._task_specs}
        self._round_robin_index = 0

    @property
    def task_specs(self) -> tuple[TaskSpec, ...]:
        return self._task_specs

    def select(self, task_id: str | None = None, seed: int | None = None) -> TaskSpec:
        if task_id is not None:
            if task_id not in self._task_map:
                raise ValueError(
                    f"Unknown task_id={task_id!r}. Expected one of {STARTER_TASK_IDS}."
                )
            return self._task_map[task_id]

        if seed is not None:
            return self._task_specs[seed % len(self._task_specs)]

        task = self._task_specs[self._round_robin_index % len(self._task_specs)]
        self._round_robin_index += 1
        return task
