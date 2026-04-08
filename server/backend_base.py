"""Shared backend result types and backend interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .command_gateway import NormalizedCommand
from .task_models import TaskSpec


@dataclass(frozen=True, slots=True)
class BackendCommandResult:
    """Backend command execution result consumed by the environment."""

    command_key: str
    output: str
    success: bool
    error: str | None = None
    changed_state: bool = False
    verification_passed: bool = False
    fix_applied: bool = False


class BaseOpsBackend(Protocol):
    """Readable internal interface for backend implementations."""

    def reset(self, task_spec: TaskSpec) -> None:
        """Restore the world, validate health, and inject the selected task."""

    def execute(self, command: NormalizedCommand) -> BackendCommandResult:
        """Execute a normalized command against the backend."""

    def visible_snapshot(self) -> str:
        """Return a concise public-safe multi-service snapshot."""

    def visible_health_summary(self) -> str:
        """Return a concise public-safe health summary."""

    def visible_incident_snapshot(self, task_spec: TaskSpec) -> str:
        """Return a richer public-safe incident snapshot for reset and fix views."""

    def check_health_detailed(self) -> dict[str, object]:
        """Return an internal health snapshot for reward and convergence logic."""

    def run_smoke_tests(self, public_only: bool = False) -> dict[str, bool]:
        """Return smoke-test results, optionally limited to public-safe checks."""

    def wait_for_post_fix_convergence(
        self,
        command: NormalizedCommand,
        timeout_s: int,
        interval_s: float = 1.0,
    ) -> dict[str, object]:
        """Poll for the system to settle after a fix-like command."""

    def run_verification(self) -> tuple[bool, str]:
        """Run deterministic task verification."""

    def is_task_resolved(self) -> bool:
        """Return whether the active task is resolved."""

    def close(self) -> None:
        """Clean up backend resources if needed."""
