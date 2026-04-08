"""Deterministic workflow-shaped reward scoring for Production Ops Lab v1."""

from __future__ import annotations

from dataclasses import dataclass, field

from .backend_base import BackendCommandResult
from .command_gateway import NormalizedCommand
from .task_models import TaskRuntime


def _clamp(value: float, lower: float = -1.0, upper: float = 2.0) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True, slots=True)
class RewardResult:
    """Structured step reward result consumed by the environment loop."""

    total: float
    feedback: str
    phase: str
    breakdown: dict[str, float] = field(default_factory=dict)
    likely_fix_applied: bool = False
    likely_verification: bool = False
    blocked_repeat: bool = False


class RewardEngine:
    """Inspectable reward engine aligned to the starter incident workflow."""

    def classify_phase(self, runtime: TaskRuntime, command: NormalizedCommand) -> str:
        """Classify a command into an RL-facing workflow phase."""
        if command.category == "invalid":
            return "invalid"
        if command.category == "fix":
            return "fix"
        if command.reward_key == "lab verify":
            return "verification"
        if command.reward_key.startswith("svc status "):
            return "triage"
        if command.reward_key in {"svc logs app", "svc logs worker", "env show app"}:
            return "investigation"
        if command.reward_key in {"http check /health", "queue stats"}:
            if runtime.verification_required or runtime.candidate_resolved:
                return "verification"
            return "triage"
        return "invalid"

    def evaluate_step(
        self,
        runtime: TaskRuntime,
        command: NormalizedCommand,
        command_result: BackendCommandResult,
        repeat_count: int,
        resolution_ready: bool,
        resolution_confirmed: bool,
        blocked_repeat: bool = False,
    ) -> RewardResult:
        """Score a single command using deterministic workflow rules."""
        phase = self.classify_phase(runtime, command)
        breakdown = {
            "phase_score": 0.0,
            "evidence_score": 0.0,
            "repeat_penalty": 0.0,
            "unsafe_penalty": 0.0,
            "fix_score": 0.0,
            "verification_score": 0.0,
            "resolution_bonus": 0.0,
            "invalid_penalty": 0.0,
        }
        feedback: list[str] = []
        likely_fix_applied = phase == "fix"
        likely_verification = phase == "verification"

        if blocked_repeat:
            breakdown["repeat_penalty"] = -0.50
            feedback.append("Blocked repeated command without progress.")
        elif command.category == "invalid" or not command_result.success:
            if command.category == "fix":
                breakdown["unsafe_penalty"] = -0.50
                feedback.append("Blocked or unsafe mutation.")
            else:
                breakdown["invalid_penalty"] = -0.20
                feedback.append("Unsupported command.")
        else:
            self._score_phase(runtime, command, command_result, phase, breakdown, feedback)
            if phase == "verification":
                runtime.seen_verification_commands.add(command.reward_key)
                if resolution_ready and command_result.verification_passed:
                    breakdown["verification_score"] = 0.15
                    feedback.append("Verification confirms recovery.")
                    if resolution_confirmed:
                        breakdown["resolution_bonus"] = 1.20
                        runtime.is_verified = True
                        runtime.internal_flags["incident_resolved"] = True
                        feedback.append("Incident resolved.")
                else:
                    breakdown["phase_score"] = min(breakdown["phase_score"], 0.0)
                    breakdown["invalid_penalty"] += -0.10
                    feedback.append("Verification did not confirm recovery.")

            if repeat_count == 2:
                has_positive_signal = any(
                    breakdown[key] > 0.0
                    for key in (
                        "phase_score",
                        "evidence_score",
                        "fix_score",
                        "verification_score",
                        "resolution_bonus",
                    )
                )
                if not has_positive_signal:
                    breakdown = {key: 0.0 for key in breakdown}
                breakdown["repeat_penalty"] += -0.20
                feedback.append("Repeated command produced less value.")

        previous_reward = runtime.cumulative_reward
        raw_total = sum(breakdown.values())
        runtime.cumulative_reward = _clamp(previous_reward + raw_total)
        applied_total = runtime.cumulative_reward - previous_reward
        breakdown["total"] = applied_total

        for key, value in breakdown.items():
            if key != "total" and value != 0.0:
                runtime.add_reward(key, value)

        runtime.internal_flags["last_phase"] = phase
        runtime.internal_flags["last_feedback"] = " ".join(feedback) or "No meaningful progress."
        runtime.internal_flags["last_delta_reward"] = applied_total

        return RewardResult(
            total=applied_total,
            feedback=runtime.internal_flags["last_feedback"],
            phase=phase,
            breakdown=breakdown,
            likely_fix_applied=likely_fix_applied,
            likely_verification=likely_verification,
            blocked_repeat=blocked_repeat,
        )

    def apply_timeout_penalty(self, runtime: TaskRuntime) -> RewardResult:
        """Apply a terminal unresolved penalty that ends clearly negative."""
        target_cumulative = min(runtime.cumulative_reward, -0.25)
        if runtime.cumulative_reward > -0.25:
            target_cumulative = -0.25
        previous_reward = runtime.cumulative_reward
        runtime.cumulative_reward = _clamp(target_cumulative)
        applied_total = runtime.cumulative_reward - previous_reward
        runtime.add_reward("timeout_penalty", applied_total)
        runtime.internal_flags["last_phase"] = "timeout"
        runtime.internal_flags["last_feedback"] = (
            "Incident remained unresolved when the step budget expired."
        )
        runtime.internal_flags["last_delta_reward"] = applied_total
        return RewardResult(
            total=applied_total,
            feedback=runtime.internal_flags["last_feedback"],
            phase="timeout",
            breakdown={"timeout_penalty": applied_total, "total": applied_total},
            likely_fix_applied=False,
            likely_verification=False,
            blocked_repeat=False,
        )

    def _score_phase(
        self,
        runtime: TaskRuntime,
        command: NormalizedCommand,
        command_result: BackendCommandResult,
        phase: str,
        breakdown: dict[str, float],
        feedback: list[str],
    ) -> None:
        task_spec = runtime.task_spec

        if phase == "triage":
            if (
                command.reward_key in task_spec.expected_triage_path
                and command.reward_key not in runtime.seen_triage_commands
                and not runtime.seen_fix_commands
            ):
                runtime.seen_triage_commands.add(command.reward_key)
                breakdown["phase_score"] += 0.10
                feedback.append("Useful early triage.")
            else:
                breakdown["invalid_penalty"] += -0.10
                feedback.append("Low-value triage command.")
            return

        if phase == "investigation":
            if command.reward_key in task_spec.expected_investigation_commands:
                if command.reward_key not in runtime.seen_investigation_commands:
                    runtime.seen_investigation_commands.add(command.reward_key)
                    breakdown["phase_score"] += 0.15
                    feedback.append("Relevant investigation command.")
                if command.reward_key in task_spec.root_cause_signal_commands:
                    if command.reward_key not in runtime.seen_evidence_commands:
                        runtime.seen_evidence_commands.add(command.reward_key)
                        breakdown["evidence_score"] += 0.20
                        feedback.append("Root-cause evidence exposed.")
            else:
                breakdown["invalid_penalty"] += -0.10
                feedback.append("Investigation missed the relevant component.")
            return

        if phase == "fix":
            runtime.verification_required = True
            if (
                command.reward_key in task_spec.required_fix_commands
                and command_result.changed_state
            ):
                if command.reward_key not in runtime.seen_fix_commands:
                    breakdown["fix_score"] += 0.25
                runtime.seen_fix_commands.add(command.reward_key)
                feedback.append("Corrective action changed system state.")
            else:
                breakdown["unsafe_penalty"] += -0.35
                feedback.append("Mutation did not target the incident root cause.")
            return

        if phase == "invalid":
            breakdown["invalid_penalty"] += -0.20
            feedback.append("Invalid command.")
