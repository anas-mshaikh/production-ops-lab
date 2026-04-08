"""Registry-driven incident injectors for the HF-compatible local monolith backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..constants import STATUS_STALE, STATUS_STOPPED
from ..task_models import TaskSpec
from ..world_spec import WORLD_SPEC

if TYPE_CHECKING:
    from ..real_backend import RealMonolithBackend


class MonolithFailureInjector:
    """Apply deterministic incident mutations and wait for visible symptoms."""

    def __init__(self, backend: RealMonolithBackend) -> None:
        self._backend = backend
        self._injectors: dict[str, Callable[[dict[str, str | int]], None]] = {
            "app_service_stopped": self._inject_app_service_stopped,
            "bad_env_db_url": self._inject_bad_env_db_url,
            "queue_backlog_due_to_worker_failure": self._inject_queue_backlog_due_to_worker_failure,
            "nginx_service_stopped": self._inject_nginx_service_stopped,
            "postgres_service_stopped": self._inject_postgres_service_stopped,
            "redis_service_stopped": self._inject_redis_service_stopped,
        }
        self._visibility_waiters: dict[str, Callable[[TaskSpec], None]] = {
            "app_service_stopped": self._wait_for_app_service_stopped,
            "bad_env_db_url": self._wait_for_bad_env_db_url,
            "queue_backlog_due_to_worker_failure": self._wait_for_queue_backlog_due_to_worker_failure,
            "nginx_service_stopped": self._wait_for_nginx_service_stopped,
            "postgres_service_stopped": self._wait_for_postgres_service_stopped,
            "redis_service_stopped": self._wait_for_redis_service_stopped,
        }

    @property
    def incident_types(self) -> tuple[str, ...]:
        return tuple(self._injectors)

    def inject(self, incident_type: str, params: dict[str, str | int]) -> None:
        if incident_type not in self._injectors:
            raise ValueError(f"Unsupported incident_type={incident_type!r}.")
        self._injectors[incident_type](params)

    def wait_until_visible(self, task_spec: TaskSpec) -> None:
        if task_spec.incident_type not in self._visibility_waiters:
            raise ValueError(f"Unsupported incident_type={task_spec.incident_type!r}.")
        self._visibility_waiters[task_spec.incident_type](task_spec)

    def _inject_app_service_stopped(self, params: dict[str, str | int]) -> None:
        del params
        self._backend.stop_service("app")

    def _inject_bad_env_db_url(self, params: dict[str, str | int]) -> None:
        next_value = str(params.get("database_url", "broken")).lower()
        if next_value != "broken":
            raise ValueError("bad_env_db_url injector expects params['database_url'] == 'broken'.")
        self._backend.write_runtime_env_value(
            WORLD_SPEC.app_env_runtime,
            "DATABASE_URL",
            WORLD_SPEC.broken_database_url,
        )
        self._backend.restart_service_internal("app")
        self._backend.set_app_restart_required(False)

    def _inject_queue_backlog_due_to_worker_failure(self, params: dict[str, str | int]) -> None:
        backlog_jobs = int(params.get("expected_backlog_jobs", 3))
        self._backend.stop_service("worker")
        self._backend.set_expected_backlog_jobs(backlog_jobs)
        self._backend.seed_queue_backlog(backlog_jobs)

    def _inject_nginx_service_stopped(self, params: dict[str, str | int]) -> None:
        del params
        self._backend.stop_service("nginx")

    def _inject_postgres_service_stopped(self, params: dict[str, str | int]) -> None:
        del params
        self._backend.stop_service("postgres")

    def _inject_redis_service_stopped(self, params: dict[str, str | int]) -> None:
        del params
        self._backend.stop_service("redis")

    def _wait_for_app_service_stopped(self, task_spec: TaskSpec) -> None:
        del task_spec
        self._backend.wait_for(
            lambda: self._backend.service_status_label("app") == STATUS_STOPPED,
            "app service to stop",
        )
        self._backend.wait_for(
            lambda: not self._backend.http_health_ok(),
            "/health to fail after app stop",
        )

    def _wait_for_bad_env_db_url(self, task_spec: TaskSpec) -> None:
        del task_spec
        self._backend.wait_for(
            lambda: self._backend.service_status_label("app") == "degraded",
            "app to become degraded",
        )
        self._backend.wait_for(
            lambda: not self._backend.http_health_ok(),
            "/health to fail after bad DATABASE_URL",
        )
        self._backend.wait_for(
            lambda: not self._backend.read_path_ok(),
            "DB-backed read path to fail after bad DATABASE_URL",
        )

    def _wait_for_queue_backlog_due_to_worker_failure(self, task_spec: TaskSpec) -> None:
        backlog_jobs = int(task_spec.params.get("expected_backlog_jobs", 3))
        self._backend.wait_for(
            lambda: int(self._backend.safe_queue_stats().get("pending_jobs", 0)) >= backlog_jobs,
            "queue backlog to appear",
        )
        self._backend.wait_for(
            lambda: self._backend.service_status_label("worker") in {STATUS_STOPPED, STATUS_STALE},
            "worker to appear unhealthy",
        )

    def _wait_for_nginx_service_stopped(self, task_spec: TaskSpec) -> None:
        del task_spec
        self._backend.wait_for(
            lambda: self._backend.service_status_label("nginx") == STATUS_STOPPED,
            "nginx service to stop",
        )
        self._backend.wait_for(
            lambda: not self._backend.http_health_ok(),
            "public health to fail after nginx stop",
        )

    def _wait_for_postgres_service_stopped(self, task_spec: TaskSpec) -> None:
        del task_spec
        self._backend.wait_for(
            lambda: self._backend.service_status_label("postgres") == STATUS_STOPPED,
            "postgres service to stop",
        )
        self._backend.wait_for(
            lambda: not self._backend.http_health_ok(),
            "app health to fail after postgres stop",
        )
        self._backend.wait_for(
            lambda: not self._backend.read_path_ok(),
            "DB-backed read path to fail after postgres stop",
        )

    def _wait_for_redis_service_stopped(self, task_spec: TaskSpec) -> None:
        del task_spec
        self._backend.wait_for(
            lambda: self._backend.service_status_label("redis") == STATUS_STOPPED,
            "redis service to stop",
        )
        self._backend.wait_for(
            lambda: not self._backend.http_health_ok(),
            "app health to fail after redis stop",
        )
