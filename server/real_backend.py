"""HF-compatible local monolith backend for Production Ops Lab."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .backend_base import BackendCommandResult, BaseOpsBackend
from .command_gateway import NormalizedCommand
from .constants import STATUS_DEGRADED, STATUS_HEALTHY, STATUS_STALE, STATUS_STOPPED
from .injectors.monolith_injectors import MonolithFailureInjector
from .reset_orchestrator import BaselineSnapshot, ResetOrchestrator, ResetReport
from .task_models import TaskSpec
from .world_spec import WORLD_SPEC, sanitize_database_url


class RealMonolithBackend(BaseOpsBackend):
    """Single-process monolith world that is safe to run inside HF Spaces."""

    _KNOWN_SERVICES = ("nginx", "app", "postgres", "redis", "worker", "scheduler")

    def __init__(self) -> None:
        self._task_spec: TaskSpec | None = None
        self._app_restart_required = False
        self._expected_backlog_jobs = 0
        self._last_reset_report: ResetReport | None = None
        self._last_smoke_results: dict[str, bool] = {}
        self._failure_injector = MonolithFailureInjector(self)
        self._reset_orchestrator = ResetOrchestrator(self, self._failure_injector)
        self._service_process_state: dict[str, str] = {}
        self._configured_database_url = WORLD_SPEC.healthy_database_url
        self._applied_database_url = WORLD_SPEC.healthy_database_url
        self._pending_jobs = 0
        self._notifications_count = 0
        self._processed_applications = 0
        self._candidate_rows = 0
        self._worker_heartbeat = STATUS_STALE
        self._scheduler_heartbeat = STATUS_STALE
        self.stop_world()

    def reset(self, task_spec: TaskSpec) -> None:
        self._task_spec = task_spec
        self._app_restart_required = False
        self._expected_backlog_jobs = int(task_spec.params.get("expected_backlog_jobs", 0))
        self._last_smoke_results = {}
        self._last_reset_report = self._reset_orchestrator.reset(task_spec)

    def execute(self, command: NormalizedCommand) -> BackendCommandResult:
        if command.category == "invalid":
            return BackendCommandResult(
                command_key=command.reward_key,
                output=command.error or "COMMAND ERROR: invalid command.",
                success=False,
                error=command.error or "COMMAND ERROR: invalid command.",
            )
        if command.verb == "status":
            return self._service_status(command)
        if command.verb == "logs":
            return self._service_logs(command)
        if command.verb == "restart":
            return self._restart_service(command)
        if command.verb == "show":
            return self._env_show_app(command)
        if command.verb == "set":
            return self._env_set_app(command)
        if command.reward_key == "queue stats":
            return self._queue_stats(command)
        if command.reward_key == "http check /health":
            return self._http_health(command)
        if command.reward_key == "lab verify":
            resolved, message = self.run_verification()
            return BackendCommandResult(
                command_key=command.reward_key,
                output=message,
                success=True,
                verification_passed=resolved,
                fix_applied=resolved,
            )
        return BackendCommandResult(
            command_key=command.reward_key,
            output="COMMAND ERROR: unsupported command for the backend.",
            success=False,
            error="COMMAND ERROR: unsupported command for the backend.",
        )

    def visible_snapshot(self) -> str:
        services = self.get_service_snapshot()
        statuses = ", ".join(f"{name}={status}" for name, status in services.items())
        return f"SERVICES: {statuses}"

    def visible_health_summary(self) -> str:
        if self._task_spec is None:
            if all(status == STATUS_HEALTHY for status in self.get_service_snapshot().values()):
                return "VISIBLE HEALTH: ingress=healthy, app=healthy, database=healthy, queue=healthy"
            return "VISIBLE HEALTH: environment is ready for task injection."

        app_label = self.service_status_label("app")
        worker_label = self.service_status_label("worker")
        pending_jobs = self._pending_jobs

        if self._task_spec.task_id == "app_service_stopped" and app_label == STATUS_STOPPED:
            return "VISIBLE HEALTH: ingress=healthy, app=stopped, background=healthy, /health=503"
        if (
            self._task_spec.task_id == "nginx_service_stopped"
            and self.service_status_label("nginx") == STATUS_STOPPED
        ):
            return "VISIBLE HEALTH: ingress=stopped, app=healthy, database=healthy, /health=503"
        if self._task_spec.task_id == "bad_env_db_url" and app_label == STATUS_DEGRADED:
            return "VISIBLE HEALTH: ingress=healthy, app=degraded, database=healthy, /health=503"
        if (
            self._task_spec.task_id == "postgres_service_stopped"
            and self.service_status_label("postgres") == STATUS_STOPPED
        ):
            return "VISIBLE HEALTH: ingress=healthy, app=degraded, database=stopped, /health=503"
        if (
            self._task_spec.task_id == "redis_service_stopped"
            and self.service_status_label("redis") == STATUS_STOPPED
        ):
            return "VISIBLE HEALTH: ingress=healthy, app=degraded, redis=stopped, queue=degraded"
        if (
            self._task_spec.task_id == "queue_backlog_due_to_worker_failure"
            and (worker_label != STATUS_HEALTHY or pending_jobs > 0)
        ):
            return "VISIBLE HEALTH: ingress=healthy, app=healthy, worker=down, queue=backlogged"
        return "VISIBLE HEALTH: ingress=healthy, app=healthy, database=healthy, queue=healthy"

    def visible_incident_snapshot(self, task_spec: TaskSpec) -> str:
        smoke = self.run_smoke_tests(public_only=True)
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
            f"- pending_jobs => {health['pending_jobs']}\n"
            f"- worker_heartbeat => {health['worker_status']}\n"
            "Recent events:\n"
            f"{recent_events}"
        )

    def check_health_detailed(self) -> dict[str, object]:
        stats = self.safe_queue_stats()
        return {
            "services": self.get_service_snapshot(),
            "http_health": self.http_health_ok(),
            "database_reachable": self.database_reachable(),
            "redis_reachable": self.redis_reachable(),
            "pending_jobs": int(stats.get("pending_jobs", 0)),
            "worker_status": str(stats.get("worker_status", STATUS_STALE)),
            "scheduler_status": str(stats.get("scheduler_status", STATUS_STALE)),
            "notifications_count": int(stats.get("notifications_count", 0)),
            "processed_applications": int(stats.get("processed_applications", 0)),
            "app_restart_required": self._app_restart_required,
            "candidate_search_ok": self.read_path_ok(),
            "task_resolved": self.is_task_resolved() if self._task_spec is not None else False,
        }

    def run_smoke_tests(self, public_only: bool = False) -> dict[str, bool]:
        smoke_results = {
            "ingress_health": self.http_health_ok(),
            "candidate_search": self.read_path_ok(),
            "write_application": self._can_write_application(),
            "async_processing": self._async_path_ok(),
        }
        if not public_only:
            self._last_smoke_results = dict(smoke_results)
        return smoke_results

    def wait_for_post_fix_convergence(
        self,
        command: NormalizedCommand,
        timeout_s: int,
        interval_s: float = 1.0,
    ) -> dict[str, object]:
        start = time.time()
        converged = True
        if command.target == "app" and command.verb == "restart":
            converged = self.wait_for(
                lambda: not self._app_restart_required,
                "app restart to settle",
                timeout_s=timeout_s,
                interval_s=interval_s,
                raise_on_timeout=False,
            )
        elif command.target == "worker" and command.verb == "restart":
            converged = self.wait_for(
                lambda: self.safe_queue_stats().get("worker_status") == STATUS_HEALTHY,
                "worker restart to settle",
                timeout_s=timeout_s,
                interval_s=interval_s,
                raise_on_timeout=False,
            )
            if converged:
                self.wait_for(
                    lambda: self.safe_queue_stats().get("pending_jobs", 0) == 0,
                    "queue backlog to drain",
                    timeout_s=timeout_s,
                    interval_s=interval_s,
                    raise_on_timeout=False,
                )
        return {
            "converged": converged,
            "duration_s": time.time() - start,
            "health": self.check_health_detailed(),
            "smoke_results": self.run_smoke_tests(public_only=True),
        }

    def run_verification(self) -> tuple[bool, str]:
        if self._task_spec is None:
            return False, "LAB VERIFY: no active task is loaded."

        resolved = all(
            self._acceptance_check_passed(check_name)
            for check_name in self._task_spec.acceptance_checks
        )

        if self._task_spec.task_id == "app_service_stopped":
            message = (
                "LAB VERIFY: app service is running and /health is green."
                if resolved
                else "LAB VERIFY: app service is still unhealthy."
            )
            return resolved, message

        if self._task_spec.task_id == "bad_env_db_url":
            message = (
                "LAB VERIFY: app health and DB-backed read path are restored."
                if resolved
                else "LAB VERIFY: app health or DB-backed behavior is still failing."
            )
            return resolved, message
        if self._task_spec.task_id == "nginx_service_stopped":
            message = (
                "LAB VERIFY: ingress health is restored and public requests are healthy."
                if resolved
                else "LAB VERIFY: ingress is still unhealthy."
            )
            return resolved, message
        if self._task_spec.task_id == "postgres_service_stopped":
            message = (
                "LAB VERIFY: database connectivity and DB-backed app behavior are restored."
                if resolved
                else "LAB VERIFY: database-backed behavior is still degraded."
            )
            return resolved, message
        if self._task_spec.task_id == "redis_service_stopped":
            message = (
                "LAB VERIFY: redis connectivity and app health are restored."
                if resolved
                else "LAB VERIFY: redis-dependent behavior is still degraded."
            )
            return resolved, message

        message = (
            "LAB VERIFY: worker health is restored and the backlog is drained."
            if resolved
            else "LAB VERIFY: queue processing is still degraded."
        )
        return resolved, message

    def is_task_resolved(self) -> bool:
        return self.run_verification()[0]

    def close(self) -> None:
        self.stop_world()

    def restore_healthy_world(self) -> None:
        self._task_spec = None
        self._app_restart_required = False
        self._expected_backlog_jobs = 0
        self._last_smoke_results = {}
        self._last_reset_report = self._reset_orchestrator.restore_to_baseline()

    @property
    def last_reset_report(self) -> ResetReport | None:
        return self._last_reset_report

    @property
    def failure_injector(self) -> MonolithFailureInjector:
        return self._failure_injector

    def restore_runtime_artifacts(self) -> list[str]:
        WORLD_SPEC.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._restore_env_file(
            template_path=WORLD_SPEC.app_env_template,
            runtime_path=WORLD_SPEC.app_env_runtime,
        )
        self._restore_env_file(
            template_path=WORLD_SPEC.worker_env_template,
            runtime_path=WORLD_SPEC.worker_env_runtime,
        )
        return [
            str(WORLD_SPEC.app_env_runtime),
            str(WORLD_SPEC.worker_env_runtime),
        ]

    def stop_world(self) -> None:
        self._service_process_state = {service: STATUS_STOPPED for service in self._KNOWN_SERVICES}
        self._worker_heartbeat = STATUS_STALE
        self._scheduler_heartbeat = STATUS_STALE

    def boot_world(self) -> None:
        self._service_process_state = {service: STATUS_HEALTHY for service in self._KNOWN_SERVICES}
        self._configured_database_url = self._read_env_value(
            WORLD_SPEC.app_env_runtime, "DATABASE_URL", WORLD_SPEC.healthy_database_url
        )
        self._applied_database_url = self._configured_database_url
        self._app_restart_required = False
        self._worker_heartbeat = STATUS_HEALTHY
        self._scheduler_heartbeat = STATUS_HEALTHY

    def reseed_state(self) -> None:
        self._candidate_rows = 3
        self._pending_jobs = 0
        self._notifications_count = 0
        self._processed_applications = 0

    def wait_for_service_convergence(self) -> None:
        self.wait_for(
            lambda: all(
                self._service_process_state.get(service) == STATUS_HEALTHY
                for service in self._KNOWN_SERVICES
            ),
            "all local services to start",
        )
        self.wait_for(self.database_reachable, "database reachability")
        self.wait_for(self.redis_reachable, "redis reachability")
        self.wait_for(lambda: self.service_status_label("app") != STATUS_STOPPED, "app process to start")
        self.wait_for(
            lambda: self.safe_queue_stats().get("worker_status") == STATUS_HEALTHY,
            "worker heartbeat",
        )
        self.wait_for(
            lambda: self.safe_queue_stats().get("scheduler_status") == STATUS_HEALTHY,
            "scheduler heartbeat",
        )

    def run_business_smoke_tests(self) -> dict[str, bool]:
        return self.run_smoke_tests(public_only=False)

    def capture_baseline_snapshot(self) -> BaselineSnapshot:
        if (
            self._task_spec is None
            and self._last_reset_report is not None
            and self._last_reset_report.failed_phase is None
            and self._last_reset_report.baseline_snapshot is not None
        ):
            return self._last_reset_report.baseline_snapshot
        stats = self.safe_queue_stats()
        return BaselineSnapshot(
            services=self.get_service_snapshot(),
            pending_jobs=int(stats.get("pending_jobs", 0)),
            worker_status=str(stats.get("worker_status", STATUS_STALE)),
            scheduler_status=str(stats.get("scheduler_status", STATUS_STALE)),
            notifications_count=int(stats.get("notifications_count", 0)),
            processed_applications=int(stats.get("processed_applications", 0)),
        )

    def get_service_snapshot(self) -> dict[str, str]:
        return {
            service_name: self.service_status_label(service_name)
            for service_name in WORLD_SPEC.service_names
        }

    def set_expected_backlog_jobs(self, value: int) -> None:
        self._expected_backlog_jobs = value

    def set_app_restart_required(self, value: bool) -> None:
        self._app_restart_required = value

    def wait_for(
        self,
        predicate,
        description: str,
        timeout_s: int = 90,
        interval_s: float = 1.0,
        raise_on_timeout: bool = True,
    ) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval_s)
        if raise_on_timeout:
            raise RuntimeError(f"Timed out waiting for {description}.")
        return False

    def service_status_label(self, service: str) -> str:
        state = self._service_process_state.get(service, STATUS_STOPPED)
        if state == STATUS_STOPPED:
            return STATUS_STOPPED
        if service == "app":
            return STATUS_HEALTHY if self._app_runtime_healthy() else STATUS_DEGRADED
        if service == "worker":
            return STATUS_HEALTHY if self._worker_heartbeat == STATUS_HEALTHY else STATUS_STOPPED
        if service == "scheduler":
            return STATUS_HEALTHY if self._scheduler_heartbeat == STATUS_HEALTHY else STATUS_STALE
        return STATUS_HEALTHY

    def safe_queue_stats(self) -> dict[str, int | str]:
        return {
            "pending_jobs": self._pending_jobs,
            "worker_status": self._worker_heartbeat,
            "scheduler_status": self._scheduler_heartbeat,
            "notifications_count": self._notifications_count,
            "processed_applications": self._processed_applications,
        }

    def http_health_ok(self) -> bool:
        return (
            self._service_process_state.get("nginx") == STATUS_HEALTHY
            and self._app_runtime_healthy()
        )

    def read_path_ok(self) -> bool:
        return self.http_health_ok() and self.database_reachable() and self._candidate_rows > 0

    def database_reachable(self) -> bool:
        return self._service_process_state.get("postgres") == STATUS_HEALTHY

    def redis_reachable(self) -> bool:
        return self._service_process_state.get("redis") == STATUS_HEALTHY

    def write_runtime_env_value(self, env_file: Path, key: str, value: str) -> None:
        self._write_env_value(env_file, key, value)
        if key == "DATABASE_URL":
            self._configured_database_url = value

    def restart_service_internal(self, service: str) -> None:
        self._service_process_state[service] = STATUS_HEALTHY
        if service == "app":
            self._applied_database_url = self._configured_database_url
            self._app_restart_required = False
        elif service == "worker":
            self._worker_heartbeat = STATUS_HEALTHY
            self._drain_queue()
        elif service == "scheduler":
            self._scheduler_heartbeat = STATUS_HEALTHY

    def stop_service(self, service: str) -> None:
        self._service_process_state[service] = STATUS_STOPPED
        if service == "worker":
            self._worker_heartbeat = STATUS_STALE

    def seed_queue_backlog(self, jobs: int) -> None:
        self._pending_jobs += jobs

    def _service_status(self, command: NormalizedCommand) -> BackendCommandResult:
        service = command.target
        if service not in self._KNOWN_SERVICES:
            return self._error(f"COMMAND ERROR: unknown service {service!r}.", command.reward_key)
        if service == "worker":
            output = (
                f"SERVICE STATUS: worker = {self.service_status_label('worker')}; "
                f"heartbeat = {self._worker_heartbeat}"
            )
        else:
            output = f"SERVICE STATUS: {service} = {self.service_status_label(service)}"
        return BackendCommandResult(command_key=command.reward_key, output=output, success=True)

    def _service_logs(self, command: NormalizedCommand) -> BackendCommandResult:
        service = command.target
        if service not in self._KNOWN_SERVICES:
            return self._error(f"COMMAND ERROR: unknown service {service!r}.", command.reward_key)

        if service == "app" and self.service_status_label("app") == STATUS_STOPPED:
            output = "APP LOGS: service is not running."
        elif service == "app" and self._configured_database_url != self._applied_database_url:
            output = "APP LOGS: restart pending before new environment values take effect."
        elif service == "app" and self._applied_database_url != WORLD_SPEC.healthy_database_url:
            output = "APP LOGS: database connection failed: DATABASE_URL is invalid."
        elif service == "app" and not self.database_reachable():
            output = "APP LOGS: dependency connection failed: postgres is unavailable."
        elif service == "app" and not self.redis_reachable():
            output = "APP LOGS: dependency connection failed: redis is unavailable."
        elif service == "worker" and self.service_status_label("worker") != STATUS_HEALTHY:
            output = "WORKER LOGS: worker heartbeat is stale and queued jobs are not draining."
        elif service == "nginx" and self.service_status_label("nginx") == STATUS_STOPPED:
            output = "NGINX LOGS: ingress service is not running."
        elif service == "postgres" and self.service_status_label("postgres") == STATUS_STOPPED:
            output = "POSTGRES LOGS: database service is not running."
        elif service == "redis" and self.service_status_label("redis") == STATUS_STOPPED:
            output = "REDIS LOGS: redis service is not running."
        else:
            output = f"{service.upper()} LOGS: no critical errors in the last 5 minutes."
        return BackendCommandResult(command_key=command.reward_key, output=output, success=True)

    def _restart_service(self, command: NormalizedCommand) -> BackendCommandResult:
        service = command.target
        if service not in self._KNOWN_SERVICES:
            return self._error(f"COMMAND ERROR: unknown service {service!r}.", command.reward_key)

        before = self.service_status_label(service)
        self.restart_service_internal(service)
        after = self.service_status_label(service)

        if service == "app":
            healthy = self._app_runtime_healthy()
            output = (
                "SERVICE ACTION: app restarted successfully."
                if healthy
                else "SERVICE ACTION: app restarted, but database connectivity is still failing."
            )
            return BackendCommandResult(
                command_key=command.reward_key,
                output=output,
                success=True,
                changed_state=before != after or healthy,
                verification_passed=healthy,
                fix_applied=healthy,
            )

        if service == "worker":
            queue_drained = self._pending_jobs == 0
            output = (
                "SERVICE ACTION: worker restarted and the queue is draining normally."
                if queue_drained
                else "SERVICE ACTION: worker restarted, but the queue is still draining."
            )
            return BackendCommandResult(
                command_key=command.reward_key,
                output=output,
                success=True,
                changed_state=before != after or queue_drained,
                verification_passed=queue_drained,
                fix_applied=queue_drained,
            )

        return BackendCommandResult(
            command_key=command.reward_key,
            output=f"SERVICE ACTION: {service} restarted successfully.",
            success=True,
            changed_state=before != after,
        )

    def _env_show_app(self, command: NormalizedCommand) -> BackendCommandResult:
        suffix = " (restart required)" if self._app_restart_required else ""
        output = (
            "APP ENV: DATABASE_URL="
            f"{sanitize_database_url(self._configured_database_url)}{suffix}"
        )
        return BackendCommandResult(command_key=command.reward_key, output=output, success=True)

    def _env_set_app(self, command: NormalizedCommand) -> BackendCommandResult:
        requested_value = str(command.args["value"])
        if requested_value not in {WORLD_SPEC.healthy_database_url, WORLD_SPEC.broken_database_url}:
            return self._error(
                "COMMAND ERROR: unsupported DATABASE_URL value for v1.",
                command.reward_key,
            )
        current_value = self._configured_database_url
        changed_state = current_value != requested_value
        self.write_runtime_env_value(WORLD_SPEC.app_env_runtime, "DATABASE_URL", requested_value)
        self._app_restart_required = True
        output = (
            "APP ENV UPDATED: DATABASE_URL set to "
            f"{sanitize_database_url(requested_value)}. Restart app to apply changes."
        )
        return BackendCommandResult(
            command_key=command.reward_key,
            output=output,
            success=True,
            changed_state=changed_state,
        )

    def _queue_stats(self, command: NormalizedCommand) -> BackendCommandResult:
        stats = self.safe_queue_stats()
        resolved = (
            int(stats["pending_jobs"]) == 0
            and str(stats["worker_status"]) == STATUS_HEALTHY
        )
        output = (
            "QUEUE STATS: pending_jobs="
            f"{stats['pending_jobs']}, worker_heartbeat={stats['worker_status']}"
        )
        return BackendCommandResult(
            command_key=command.reward_key,
            output=output,
            success=True,
            verification_passed=resolved,
            fix_applied=resolved
            and self._task_spec is not None
            and self._task_spec.task_id == "queue_backlog_due_to_worker_failure",
        )

    def _http_health(self, command: NormalizedCommand) -> BackendCommandResult:
        healthy = self.http_health_ok()
        output = (
            "HEALTH CHECK OK: /health returned 200."
            if healthy
            else "HEALTH CHECK FAILED: /health returned 503."
        )
        return BackendCommandResult(
            command_key=command.reward_key,
            output=output,
            success=True,
            verification_passed=healthy,
            fix_applied=healthy
            and self._task_spec is not None
            and self._task_spec.task_id in {"app_service_stopped", "bad_env_db_url"},
        )

    def _acceptance_check_passed(self, check_name: str) -> bool:
        if check_name == "app_health":
            return self.http_health_ok() and self.service_status_label("app") == STATUS_HEALTHY
        if check_name == "candidate_search":
            return self.read_path_ok()
        if check_name == "worker_healthy":
            return self.safe_queue_stats().get("worker_status") == STATUS_HEALTHY
        if check_name == "queue_drained":
            stats = self.safe_queue_stats()
            return (
                int(stats.get("pending_jobs", 0)) == 0
                and int(stats.get("processed_applications", 0)) >= self._expected_backlog_jobs
            )
        if check_name == "postgres_healthy":
            return self.database_reachable()
        if check_name == "redis_healthy":
            return self.redis_reachable()
        raise ValueError(f"Unsupported acceptance check {check_name!r}.")

    def _async_path_ok(self) -> bool:
        return (
            self._service_process_state.get("worker") == STATUS_HEALTHY
            and self.redis_reachable()
            and self._worker_heartbeat == STATUS_HEALTHY
            and self._pending_jobs == 0
        )

    def _can_write_application(self) -> bool:
        return (
            self._app_runtime_healthy()
            and self.database_reachable()
            and self.redis_reachable()
        )

    def _app_runtime_healthy(self) -> bool:
        if self._service_process_state.get("app") != STATUS_HEALTHY:
            return False
        if not self.database_reachable() or not self.redis_reachable():
            return False
        if self._app_restart_required:
            return False
        return self._applied_database_url == WORLD_SPEC.healthy_database_url

    def _drain_queue(self) -> None:
        if self._service_process_state.get("worker") != STATUS_HEALTHY:
            return
        drained = self._pending_jobs
        if drained <= 0:
            return
        self._processed_applications += drained
        self._notifications_count += drained
        self._pending_jobs = 0

    def _error(self, message: str, command_key: str) -> BackendCommandResult:
        return BackendCommandResult(
            command_key=command_key,
            output=message,
            success=False,
            error=message,
        )

    def _read_env_value(self, env_file: Path, key: str, default: str) -> str:
        if not env_file.exists():
            return default
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return default

    def _write_env_value(self, env_file: Path, key: str, value: str) -> None:
        lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
        updated: list[str] = []
        found = False
        for line in lines:
            if line.startswith(f"{key}="):
                updated.append(f"{key}={value}")
                found = True
            else:
                updated.append(line)
        if not found:
            updated.append(f"{key}={value}")
        env_file.write_text("\n".join(updated) + "\n", encoding="utf-8")

    def _restore_env_file(self, template_path: Path, runtime_path: Path) -> None:
        if template_path.exists():
            shutil.copyfile(template_path, runtime_path)
            return
        runtime_path.write_text(
            "\n".join(WORLD_SPEC.default_runtime_env) + "\n",
            encoding="utf-8",
        )
