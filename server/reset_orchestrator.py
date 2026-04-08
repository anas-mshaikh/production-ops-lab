"""Deterministic reset sequencing for the HF-compatible local monolith backend."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .task_models import TaskSpec

if TYPE_CHECKING:
    from .injectors.monolith_injectors import MonolithFailureInjector
    from .real_backend import RealMonolithBackend


logger = logging.getLogger("production_ops_lab.reset")


@dataclass(frozen=True, slots=True)
class BaselineSnapshot:
    """Deterministic internal snapshot of the healthy-world baseline."""

    services: dict[str, str]
    pending_jobs: int
    worker_status: str
    scheduler_status: str
    notifications_count: int
    processed_applications: int


@dataclass(slots=True)
class ResetReport:
    """Internal diagnostics for reset execution."""

    scenario_id: str
    started_at: float
    ended_at: float = 0.0
    duration_s: float = 0.0
    phase_durations_s: dict[str, float] = field(default_factory=dict)
    phase_attempts: dict[str, int] = field(default_factory=dict)
    failed_phase: str | None = None
    failed_check: str | None = None
    service_snapshot: dict[str, str] = field(default_factory=dict)
    smoke_results: dict[str, bool] = field(default_factory=dict)
    restored_artifacts: list[str] = field(default_factory=list)
    baseline_snapshot: BaselineSnapshot | None = None
    incident_visible: bool = False


class ResetOrchestrator:
    """Run deterministic reset phases and collect diagnostics."""

    def __init__(
        self,
        backend: RealMonolithBackend,
        injector: MonolithFailureInjector,
        max_phase_attempts: int = 3,
    ) -> None:
        self._backend = backend
        self._injector = injector
        self._max_phase_attempts = max_phase_attempts
        self.last_report: ResetReport | None = None

    def reset(self, task_spec: TaskSpec) -> ResetReport:
        report = self._build_report(task_spec.task_id)
        try:
            self._run_phase("hard_restore", report, self.hard_restore)
            self._run_phase("boot_world", report, self.boot_world)
            self._run_phase("service_convergence", report, self.wait_for_service_convergence)
            self._run_phase("business_smoke", report, self.run_business_smoke_tests)
            self.snapshot_baseline(report)
            self._run_phase("inject_incident", report, lambda current: self.inject_incident(current, task_spec))
            report.incident_visible = True
            report.service_snapshot = self._backend.get_service_snapshot()
            return self._finish_report(report)
        except Exception as exc:
            if report.failed_phase is None:
                report.failed_phase = "unknown"
            if report.failed_check is None:
                report.failed_check = str(exc)
            report.service_snapshot = self._backend.get_service_snapshot()
            self._finish_report(report, failed=True)
            logger.error(
                "Reset failed scenario=%s phase=%s check=%s",
                report.scenario_id,
                report.failed_phase,
                report.failed_check,
            )
            raise

    def restore_to_baseline(self) -> ResetReport:
        report = self._build_report("healthy_baseline")
        try:
            self._run_phase("hard_restore", report, self.hard_restore)
            self._run_phase("boot_world", report, self.boot_world)
            self._run_phase("service_convergence", report, self.wait_for_service_convergence)
            self._run_phase("business_smoke", report, self.run_business_smoke_tests)
            self.snapshot_baseline(report)
            report.service_snapshot = dict(report.baseline_snapshot.services) if report.baseline_snapshot else {}
            return self._finish_report(report)
        except Exception as exc:
            if report.failed_phase is None:
                report.failed_phase = "unknown"
            if report.failed_check is None:
                report.failed_check = str(exc)
            report.service_snapshot = self._backend.get_service_snapshot()
            self._finish_report(report, failed=True)
            logger.error(
                "Baseline restore failed phase=%s check=%s",
                report.failed_phase,
                report.failed_check,
            )
            raise

    def hard_restore(self, report: ResetReport) -> None:
        report.restored_artifacts.extend(self._backend.restore_runtime_artifacts())
        self._backend.stop_world()

    def boot_world(self, report: ResetReport) -> None:
        del report
        self._backend.boot_world()
        self._backend.reseed_state()

    def wait_for_service_convergence(self, report: ResetReport) -> None:
        del report
        self._backend.wait_for_service_convergence()

    def run_business_smoke_tests(self, report: ResetReport) -> None:
        smoke_results = self._backend.run_business_smoke_tests()
        report.smoke_results = smoke_results
        failed_checks = [name for name, passed in smoke_results.items() if not passed]
        if failed_checks:
            raise RuntimeError(f"business smoke failed: {', '.join(failed_checks)}")

    def snapshot_baseline(self, report: ResetReport) -> None:
        report.baseline_snapshot = self._backend.capture_baseline_snapshot()
        report.service_snapshot = dict(report.baseline_snapshot.services)

    def inject_incident(self, report: ResetReport, task_spec: TaskSpec) -> None:
        del report
        self._injector.inject(task_spec.incident_type, task_spec.params)
        self._injector.wait_until_visible(task_spec)

    def _build_report(self, scenario_id: str) -> ResetReport:
        report = ResetReport(scenario_id=scenario_id, started_at=time.time())
        self.last_report = report
        return report

    def _run_phase(
        self,
        phase_name: str,
        report: ResetReport,
        operation: Callable[[ResetReport], None],
    ) -> None:
        for attempt in range(1, self._max_phase_attempts + 1):
            started_at = time.perf_counter()
            try:
                operation(report)
                report.phase_attempts[phase_name] = attempt
                report.phase_durations_s[phase_name] = (
                    report.phase_durations_s.get(phase_name, 0.0)
                    + (time.perf_counter() - started_at)
                )
                return
            except Exception as exc:
                report.phase_attempts[phase_name] = attempt
                report.phase_durations_s[phase_name] = (
                    report.phase_durations_s.get(phase_name, 0.0)
                    + (time.perf_counter() - started_at)
                )
                if attempt >= self._max_phase_attempts:
                    report.failed_phase = phase_name
                    report.failed_check = str(exc)
                    raise
                if phase_name == "boot_world":
                    self.hard_restore(report)
                elif phase_name == "business_smoke":
                    self.wait_for_service_convergence(report)
                logger.warning(
                    "Reset phase retry scenario=%s phase=%s attempt=%s error=%s",
                    report.scenario_id,
                    phase_name,
                    attempt,
                    exc,
                )

    def _finish_report(self, report: ResetReport, failed: bool = False) -> ResetReport:
        report.ended_at = time.time()
        report.duration_s = report.ended_at - report.started_at
        self.last_report = report
        logger.info(
            "Reset complete scenario=%s failed=%s duration=%.2fs phases=%s",
            report.scenario_id,
            failed,
            report.duration_s,
            report.phase_attempts,
        )
        return report
