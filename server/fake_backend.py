"""Deterministic fake backend retained for explicit dev/test fallback."""

from __future__ import annotations

from .backend_base import BackendCommandResult, BaseOpsBackend
from .command_gateway import NormalizedCommand
from .constants import STATUS_DEGRADED, STATUS_HEALTHY, STATUS_STALE, STATUS_STOPPED
from .task_models import TaskSpec


class FakeOpsBackend(BaseOpsBackend):
    """Small deterministic world model for the three starter incidents."""

    _KNOWN_SERVICES = ("nginx", "app", "postgres", "redis", "worker", "scheduler")

    def __init__(self) -> None:
        self._task_spec: TaskSpec | None = None
        self._services: dict[str, str] = {}
        self._configured_database_value = "healthy"
        self._applied_database_value = "healthy"
        self._app_restart_required = False
        self._queue_pending_jobs = 0
        self._worker_heartbeat = STATUS_HEALTHY

    def reset(self, task_spec: TaskSpec) -> None:
        """Reset the fake world into the degraded state for a task."""
        self._task_spec = task_spec
        self._services = {
            "nginx": STATUS_HEALTHY,
            "app": STATUS_HEALTHY,
            "postgres": STATUS_HEALTHY,
            "redis": STATUS_HEALTHY,
            "worker": STATUS_HEALTHY,
            "scheduler": STATUS_HEALTHY,
        }
        self._configured_database_value = "healthy"
        self._applied_database_value = "healthy"
        self._app_restart_required = False
        self._queue_pending_jobs = 0
        self._worker_heartbeat = STATUS_HEALTHY

        if task_spec.task_id == "app_service_stopped":
            self._services["app"] = STATUS_STOPPED
        elif task_spec.task_id == "bad_env_db_url":
            self._services["app"] = STATUS_DEGRADED
            self._configured_database_value = "broken"
            self._applied_database_value = "broken"
        elif task_spec.task_id == "queue_backlog_due_to_worker_failure":
            self._services["worker"] = STATUS_STOPPED
            self._worker_heartbeat = STATUS_STALE
            self._queue_pending_jobs = 124

    def execute(self, command: NormalizedCommand) -> BackendCommandResult:
        """Execute a normalized command against the fake backend."""
        if command.category == "invalid":
            return self._error(command.error or "COMMAND ERROR: invalid command.")
        if command.verb == "status":
            return self._service_status(command)
        if command.verb == "logs":
            return self._service_logs(command)
        if command.verb == "restart":
            return self._service_restart(command)
        if command.verb == "show":
            return self._env_show_app(command)
        if command.verb == "set":
            return self._env_set_app(command)
        if command.reward_key == "queue stats":
            return self._queue_stats(command)
        if command.reward_key == "http check /health":
            return self._http_health(command)
        if command.reward_key == "lab verify":
            return self._lab_verify(command)
        return self._error("COMMAND ERROR: unsupported command.")

    def visible_snapshot(self) -> str:
        """Return a concise multi-service snapshot."""
        return (
            f"SERVICES: nginx={self._services['nginx']}, app={self._services['app']}, "
            f"postgres={self._services['postgres']}, redis={self._services['redis']}, "
            f"worker={self._services['worker']}, scheduler={self._services['scheduler']}"
        )

    def visible_health_summary(self) -> str:
        """Return the public visible health summary."""
        if self._task_spec is None:
            return "VISIBLE HEALTH: environment is not initialized."

        if (
            self._task_spec.task_id == "app_service_stopped"
            and self._services["app"] == STATUS_STOPPED
        ):
            return "VISIBLE HEALTH: ingress=healthy, app=stopped, background=healthy, /health=503"
        if (
            self._task_spec.task_id == "bad_env_db_url"
            and not self._is_http_healthy()
        ):
            return "VISIBLE HEALTH: ingress=healthy, app=degraded, database=healthy, /health=503"
        if (
            self._task_spec.task_id == "queue_backlog_due_to_worker_failure"
            and self._services["worker"] != STATUS_HEALTHY
        ):
            return "VISIBLE HEALTH: ingress=healthy, app=healthy, worker=down, queue=backlogged"

        return "VISIBLE HEALTH: ingress=healthy, app=healthy, database=healthy, queue=healthy"

    def visible_incident_snapshot(self, task_spec: TaskSpec) -> str:
        """Return a richer public-safe incident snapshot."""
        smoke = self.run_smoke_tests(public_only=True)
        queue_stats = self._queue_stats_snapshot()
        health = self.check_health_detailed()
        recent_events = "\n".join(f"- {line}" for line in task_spec.recent_event_lines)
        return (
            "HTTP:\n"
            f"- GET /health => {'healthy' if health['http_health'] else 'degraded'}\n"
            "Business checks:\n"
            f"- candidate_search => {'pass' if smoke['candidate_search'] else 'fail'}\n"
            f"- create_application => {'pass' if smoke['write_application'] else 'fail'}\n"
            "Dependencies:\n"
            f"- db_ping => {'ok' if health['database_reachable'] else 'fail'}\n"
            f"- redis_ping => {'ok' if health['redis_reachable'] else 'fail'}\n"
            "Queue:\n"
            f"- pending_jobs => {queue_stats['pending_jobs']}\n"
            f"- worker_heartbeat => {queue_stats['worker_status']}\n"
            "Recent events:\n"
            f"{recent_events}"
        )

    def check_health_detailed(self) -> dict[str, object]:
        """Return an internal health snapshot for deterministic grading."""
        queue_stats = self._queue_stats_snapshot()
        services = {
            service: self._services[service]
            for service in self._KNOWN_SERVICES
        }
        return {
            "services": services,
            "http_health": self._is_http_healthy(),
            "database_reachable": self._services["postgres"] == STATUS_HEALTHY,
            "redis_reachable": self._services["redis"] == STATUS_HEALTHY,
            "pending_jobs": queue_stats["pending_jobs"],
            "worker_status": queue_stats["worker_status"],
            "scheduler_status": queue_stats["scheduler_status"],
            "notifications_count": queue_stats["notifications_count"],
            "processed_applications": queue_stats["processed_applications"],
            "app_restart_required": self._app_restart_required,
            "candidate_search_ok": self._candidate_search_ok(),
            "task_resolved": self.is_task_resolved(),
        }

    def run_smoke_tests(self, public_only: bool = False) -> dict[str, bool]:
        """Return public-safe or internal smoke summaries for the fake world."""
        ingress_health = self._is_http_healthy()
        candidate_search = self._candidate_search_ok()
        write_application = ingress_health and self._configured_database_value == "healthy"
        async_processing = (
            self._services["worker"] == STATUS_HEALTHY
            and self._queue_pending_jobs == 0
        )
        if public_only:
            return {
                "ingress_health": ingress_health,
                "candidate_search": candidate_search,
                "write_application": write_application,
                "async_processing": async_processing,
            }
        return {
            "ingress_health": ingress_health,
            "candidate_search": candidate_search,
            "write_application": write_application,
            "async_processing": async_processing,
        }

    def wait_for_post_fix_convergence(
        self,
        command: NormalizedCommand,
        timeout_s: int,
        interval_s: float = 1.0,
    ) -> dict[str, object]:
        """Fake mode converges immediately once the deterministic state changes."""
        del timeout_s, interval_s
        return {
            "converged": True,
            "duration_s": 0.0,
            "health": self.check_health_detailed(),
            "smoke_results": self.run_smoke_tests(public_only=False),
        }

    def run_verification(self) -> tuple[bool, str]:
        """Run deterministic verification for the fake world."""
        if self._task_spec is None:
            return False, "LAB VERIFY: no active task is loaded."
        if self._task_spec.task_id == "queue_backlog_due_to_worker_failure":
            resolved = (
                self._services["worker"] == STATUS_HEALTHY
                and self._queue_pending_jobs == 0
            )
            message = (
                "LAB VERIFY: worker health is restored and the backlog is drained."
                if resolved
                else "LAB VERIFY: queue processing is still degraded."
            )
            return resolved, message

        resolved = self._is_http_healthy()
        if self._task_spec.task_id == "bad_env_db_url":
            message = (
                "LAB VERIFY: app health and DB-backed behavior are restored."
                if resolved
                else "LAB VERIFY: app health or DB-backed behavior is still failing."
            )
        else:
            message = (
                "LAB VERIFY: app service is running and /health is green."
                if resolved
                else "LAB VERIFY: app service is still unhealthy."
            )
        return resolved, message

    def is_task_resolved(self) -> bool:
        return self.run_verification()[0]

    def close(self) -> None:
        """No-op close for the fake backend."""

    def _error(
        self,
        message: str,
        command_key: str = "invalid",
    ) -> BackendCommandResult:
        return BackendCommandResult(
            command_key=command_key,
            output=message,
            success=False,
            error=message,
        )

    def _service_status(self, command: NormalizedCommand) -> BackendCommandResult:
        service = command.target
        if service not in self._KNOWN_SERVICES:
            return self._error(
                f"COMMAND ERROR: unknown service {service!r}.",
                command_key=command.reward_key,
            )

        if service == "worker":
            output = (
                f"SERVICE STATUS: worker = {self._services['worker']}; "
                f"heartbeat = {self._worker_heartbeat}"
            )
        elif service == "app" and not self._is_http_healthy():
            output = "SERVICE STATUS: app = degraded"
        else:
            output = f"SERVICE STATUS: {service} = {self._services[service]}"
        return BackendCommandResult(command_key=command.reward_key, output=output, success=True)

    def _service_logs(self, command: NormalizedCommand) -> BackendCommandResult:
        service = command.target
        if service not in self._KNOWN_SERVICES:
            return self._error(
                f"COMMAND ERROR: unknown service {service!r}.",
                command_key=command.reward_key,
            )

        if service == "app" and self._services["app"] == STATUS_STOPPED:
            output = "APP LOGS: service is not running."
        elif service == "app" and self._applied_database_value != "healthy":
            output = "APP LOGS: database connection failed: DATABASE_URL is invalid."
        elif service == "worker" and self._services["worker"] != STATUS_HEALTHY:
            output = "WORKER LOGS: worker heartbeat is stale and queued jobs are not draining."
        else:
            output = f"{service.upper()} LOGS: no critical errors in the last 5 minutes."

        return BackendCommandResult(command_key=command.reward_key, output=output, success=True)

    def _service_restart(self, command: NormalizedCommand) -> BackendCommandResult:
        service = command.target
        if service not in self._KNOWN_SERVICES:
            return self._error(
                f"COMMAND ERROR: unknown service {service!r}.",
                command_key=command.reward_key,
            )

        changed_state = False
        verification_passed = False
        fix_applied = False

        if service == "app":
            if self._services["app"] != STATUS_HEALTHY or self._app_restart_required:
                self._applied_database_value = self._configured_database_value
                self._services["app"] = (
                    STATUS_HEALTHY
                    if self._applied_database_value == "healthy"
                    else STATUS_DEGRADED
                )
                self._app_restart_required = False
                changed_state = True
            verification_passed = self._is_http_healthy()
            fix_applied = verification_passed
            output = (
                "SERVICE ACTION: app restarted successfully."
                if verification_passed
                else "SERVICE ACTION: app restarted, but database connectivity is still failing."
            )
        elif service == "worker":
            self._services["worker"] = STATUS_HEALTHY
            self._worker_heartbeat = STATUS_HEALTHY
            self._queue_pending_jobs = 0
            changed_state = True
            verification_passed = True
            fix_applied = True
            output = "SERVICE ACTION: worker restarted and the queue is draining normally."
        else:
            output = f"SERVICE ACTION: {service} restarted successfully."

        return BackendCommandResult(
            command_key=command.reward_key,
            output=output,
            success=True,
            changed_state=changed_state,
            verification_passed=verification_passed,
            fix_applied=fix_applied,
        )

    def _env_show_app(self, command: NormalizedCommand) -> BackendCommandResult:
        suffix = " (restart required)" if self._app_restart_required else ""
        output = f"APP ENV: DATABASE_URL={self._configured_database_value}{suffix}"
        return BackendCommandResult(command_key=command.reward_key, output=output, success=True)

    def _env_set_app(self, command: NormalizedCommand) -> BackendCommandResult:
        if command.reward_key not in {
            "env set app database_url=healthy",
            "env set app database_url=broken",
        }:
            return self._error(
                "COMMAND ERROR: unsupported DATABASE_URL value for v1.",
                command_key=command.reward_key,
            )

        next_value = "healthy" if command.reward_key.endswith("healthy") else "broken"
        changed_state = self._configured_database_value != next_value
        self._configured_database_value = next_value
        self._app_restart_required = True
        return BackendCommandResult(
            command_key=command.reward_key,
            output=f"APP ENV UPDATED: DATABASE_URL set to {next_value}. Restart app to apply changes.",
            success=True,
            changed_state=changed_state,
        )

    def _queue_stats(self, command: NormalizedCommand) -> BackendCommandResult:
        stats = self._queue_stats_snapshot()
        resolved = stats["pending_jobs"] == 0 and stats["worker_status"] == STATUS_HEALTHY
        output = (
            f"QUEUE STATS: pending_jobs={stats['pending_jobs']}, "
            f"worker_heartbeat={stats['worker_status']}"
        )
        return BackendCommandResult(
            command_key=command.reward_key,
            output=output,
            success=True,
            verification_passed=resolved,
            fix_applied=resolved and self._task_spec is not None and self._task_spec.task_id == "queue_backlog_due_to_worker_failure",
        )

    def _http_health(self, command: NormalizedCommand) -> BackendCommandResult:
        verification_passed = self._is_http_healthy()
        output = (
            "HEALTH CHECK OK: /health returned 200."
            if verification_passed
            else "HEALTH CHECK FAILED: /health returned 503."
        )
        return BackendCommandResult(
            command_key=command.reward_key,
            output=output,
            success=True,
            verification_passed=verification_passed,
            fix_applied=verification_passed and self._task_spec is not None and self._task_spec.task_id == "app_service_stopped",
        )

    def _lab_verify(self, command: NormalizedCommand) -> BackendCommandResult:
        verification_passed, message = self.run_verification()
        return BackendCommandResult(
            command_key=command.reward_key,
            output=message,
            success=True,
            verification_passed=verification_passed,
            fix_applied=verification_passed,
        )

    def _is_http_healthy(self) -> bool:
        return (
            self._services["app"] == STATUS_HEALTHY
            and self._applied_database_value == "healthy"
        )

    def _candidate_search_ok(self) -> bool:
        return self._is_http_healthy()

    def _queue_stats_snapshot(self) -> dict[str, int | str]:
        processed_applications = (
            int(self._task_spec.params.get("expected_backlog_jobs", 0))
            if self._task_spec is not None and self._queue_pending_jobs == 0
            else 0
        )
        notifications_count = processed_applications
        return {
            "pending_jobs": self._queue_pending_jobs,
            "worker_status": self._worker_heartbeat,
            "scheduler_status": STATUS_HEALTHY,
            "notifications_count": notifications_count,
            "processed_applications": processed_applications,
        }
