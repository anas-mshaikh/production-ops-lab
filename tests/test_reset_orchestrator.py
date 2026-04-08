"""Unit tests for reset sequencing and diagnostics."""

from __future__ import annotations

import pytest

from production_ops_lab.server.reset_orchestrator import (
    BaselineSnapshot,
    ResetOrchestrator,
)
from production_ops_lab.server.task_models import TaskSpec


class _DummyInjector:
    def __init__(self) -> None:
        self.inject_calls = 0
        self.wait_calls = 0

    def inject(self, incident_type: str, params: dict[str, str | int]) -> None:
        del incident_type, params
        self.inject_calls += 1

    def wait_until_visible(self, task_spec: TaskSpec) -> None:
        del task_spec
        self.wait_calls += 1


class _StubBackend:
    def __init__(
        self,
        transient_failures: dict[str, int] | None = None,
        smoke_results: dict[str, bool] | None = None,
    ) -> None:
        self.transient_failures = dict(transient_failures or {})
        self.smoke_results = smoke_results or {
            "ingress_health": True,
            "read_candidates": True,
            "write_application": True,
            "async_processing": True,
        }
        self.operations: list[str] = []
        self.snapshot = BaselineSnapshot(
            services={
                "nginx": "healthy",
                "app": "healthy",
                "postgres": "healthy",
                "redis": "healthy",
                "worker": "healthy",
                "scheduler": "healthy",
            },
            pending_jobs=0,
            worker_status="healthy",
            scheduler_status="healthy",
            notifications_count=1,
            processed_applications=1,
        )

    def restore_runtime_artifacts(self) -> list[str]:
        self.operations.append("restore_runtime_artifacts")
        return ["runtime/app.env", "runtime/worker.env"]

    def stop_world(self) -> None:
        self.operations.append("stop_world")

    def boot_world(self) -> None:
        self.operations.append("boot_world")

    def reseed_state(self) -> None:
        self.operations.append("reseed_state")

    def wait_for_service_convergence(self) -> None:
        self.operations.append("wait_for_service_convergence")
        self._maybe_fail("service_convergence")

    def run_business_smoke_tests(self) -> dict[str, bool]:
        self.operations.append("run_business_smoke_tests")
        self._maybe_fail("business_smoke")
        return dict(self.smoke_results)

    def capture_baseline_snapshot(self) -> BaselineSnapshot:
        self.operations.append("capture_baseline_snapshot")
        return self.snapshot

    def get_service_snapshot(self) -> dict[str, str]:
        return dict(self.snapshot.services)

    def wait_for(
        self,
        predicate,
        description: str,
        timeout_s: int = 90,
        interval_s: float = 1.0,
        raise_on_timeout: bool = True,
    ) -> bool:
        del description, timeout_s, interval_s, raise_on_timeout
        return bool(predicate())

    def _maybe_fail(self, phase_name: str) -> None:
        remaining = self.transient_failures.get(phase_name, 0)
        if remaining <= 0:
            return
        self.transient_failures[phase_name] = remaining - 1
        raise RuntimeError(f"{phase_name} not ready")


def _task_spec() -> TaskSpec:
    return TaskSpec(
        task_id="app_service_stopped",
        incident_type="app_service_stopped",
        difficulty="easy",
        title="Public app service is stopped",
        component="app_runtime",
        service="app",
        params={},
        alert_message="ALERT",
        initial_visible_symptom="health failing",
        root_cause="app down",
        correct_fix_description="restart app",
        accepted_fix_commands=("svc restart app",),
        required_fix_commands=("svc restart app",),
        expected_triage_path=("svc status app",),
        expected_investigation_commands=("svc status app",),
        root_cause_signal_commands=("svc status app",),
        expected_fix_path=("svc restart app",),
        expected_verification_path=("http check /health",),
        verification_commands=("http check /health",),
        red_herrings=("svc restart nginx",),
        acceptance_checks=("app_health",),
        recent_event_lines=("Synthetic monitor: /health failing.",),
        tags=("service",),
        max_steps=8,
    )


def test_reset_orchestrator_retries_and_reports_success() -> None:
    backend = _StubBackend(transient_failures={"service_convergence": 1})
    injector = _DummyInjector()
    orchestrator = ResetOrchestrator(backend, injector)

    report = orchestrator.reset(_task_spec())

    assert report.failed_phase is None
    assert report.incident_visible is True
    assert report.phase_attempts["service_convergence"] == 2
    assert report.restored_artifacts == ["runtime/app.env", "runtime/worker.env"]
    assert report.smoke_results["async_processing"] is True
    assert report.baseline_snapshot is not None
    assert injector.inject_calls == 1
    assert injector.wait_calls == 1


def test_reset_orchestrator_records_failed_phase_and_aborts_before_injection() -> None:
    backend = _StubBackend(
        smoke_results={
            "ingress_health": True,
            "read_candidates": False,
            "write_application": False,
            "async_processing": False,
        }
    )
    injector = _DummyInjector()
    orchestrator = ResetOrchestrator(backend, injector)

    with pytest.raises(RuntimeError, match="business smoke failed"):
        orchestrator.reset(_task_spec())

    assert orchestrator.last_report is not None
    assert orchestrator.last_report.failed_phase == "business_smoke"
    assert "read_candidates" in str(orchestrator.last_report.failed_check)
    assert injector.inject_calls == 0
    assert injector.wait_calls == 0
