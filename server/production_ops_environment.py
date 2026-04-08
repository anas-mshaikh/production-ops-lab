"""Main environment orchestrator for Production Ops Lab v1."""

from __future__ import annotations

import os
from uuid import uuid4

from openenv.core.env_server.interfaces import Environment

try:
    from ..models import (
        ProductionOpsLabAction,
        ProductionOpsLabObservation,
        ProductionOpsState,
    )
except ImportError:
    from models import (
        ProductionOpsLabAction,
        ProductionOpsLabObservation,
        ProductionOpsState,
    )

from .backend_base import BackendCommandResult, BaseOpsBackend
from .command_gateway import CommandGateway, NormalizedCommand
from .constants import (
    ALLOWED_COMMANDS_HINT,
    BACKEND_MODE_ENV_VAR,
    BACKEND_MODE_HF_LOCAL,
    BACKEND_MODE_FAKE,
    BACKEND_MODE_REAL,
)
from .real_backend import RealMonolithBackend
from .reward_engine import RewardEngine, RewardResult
from .scenario_generator import ScenarioGenerator
from .task_models import EpisodeStepRecord, EpisodeTranscript, TaskRuntime, TaskSpec


class ProductionOpsEnvironment(
    Environment[ProductionOpsLabAction, ProductionOpsLabObservation, ProductionOpsState]
):
    """Single-host production incident response environment."""

    SUPPORTS_CONCURRENT_SESSIONS = True

    def __init__(self, backend_mode: str | None = None) -> None:
        super().__init__()
        self._gateway = CommandGateway()
        requested_mode = (
            backend_mode or os.getenv(BACKEND_MODE_ENV_VAR, BACKEND_MODE_HF_LOCAL)
        ).lower()
        if requested_mode not in {
            BACKEND_MODE_HF_LOCAL,
            BACKEND_MODE_REAL,
            BACKEND_MODE_FAKE,
        }:
            raise ValueError(
                f"Unsupported backend mode {requested_mode!r}. Expected one of "
                f"'{BACKEND_MODE_HF_LOCAL}', '{BACKEND_MODE_REAL}', or '{BACKEND_MODE_FAKE}'."
            )
        self._backend_mode = BACKEND_MODE_HF_LOCAL
        self._backend = self._build_backend()
        self._reward_engine = RewardEngine()
        self._scenario_generator = ScenarioGenerator()
        self._runtime: TaskRuntime | None = None
        self._state = ProductionOpsState()
        self._history: list[EpisodeStepRecord] = []
        self._active_transcript: EpisodeTranscript | None = None
        self._completed_transcripts: list[EpisodeTranscript] = []

    def reset(
        self,
        seed: int | None = None,
        episode_id: str | None = None,
        task_id: str | None = None,
        **_: object,
    ) -> ProductionOpsLabObservation:
        """Reset the environment into a starter scenario."""
        if (
            self._active_transcript is not None
            and self._active_transcript.end_reason == "in_progress"
        ):
            self._finalize_episode("abandoned", resolved=self._state.incident_resolved)

        task_spec, reset_duration = self._do_reset(seed=seed, task_id=task_id)
        self._runtime = TaskRuntime(task_spec=task_spec)
        self._history = []
        self._state = ProductionOpsState(
            episode_id=episode_id or str(uuid4()),
            task_id=task_spec.task_id,
            difficulty=task_spec.difficulty,
            step_count=0,
            max_steps=task_spec.max_steps,
            cumulative_reward=0.0,
            incident_resolved=False,
            last_command="",
            command_history=[],
        )

        observation = self._build_initial_observation()
        self._active_transcript = EpisodeTranscript(
            episode_id=self._state.episode_id,
            scenario_id=task_spec.task_id,
            difficulty=task_spec.difficulty,
            alert=task_spec.alert_message,
            root_cause=task_spec.root_cause,
            correct_fix_description=task_spec.correct_fix_description,
            initial_observation=observation.model_dump(),
            reset_duration=reset_duration,
        )
        return observation

    def step(
        self,
        action: ProductionOpsLabAction,
        timeout_s: float | None = None,
        **_: object,
    ) -> ProductionOpsLabObservation:
        """Execute a single constrained command against the configured backend."""
        del timeout_s
        if self._runtime is None:
            raise RuntimeError("Environment must be reset before step().")

        if (
            self._runtime.is_verified
            or self._state.step_count >= self._runtime.task_spec.max_steps
        ):
            return self._build_step_observation(
                command_result=BackendCommandResult(
                    command_key="episode_complete",
                    output="EPISODE COMPLETE: reset the environment to start a new task.",
                    success=False,
                    error="Episode already finished.",
                ),
                command=NormalizedCommand(
                    category="invalid",
                    verb="invalid",
                    target="",
                    reward_key="episode_complete",
                    public_text="",
                    error="Episode already finished.",
                ),
                reward_result=RewardResult(
                    total=0.0,
                    feedback="Episode already complete.",
                    phase="invalid",
                    breakdown={"total": 0.0},
                ),
                done=True,
                include_system_snapshot=True,
            )

        raw_command = action.command
        parsed_command = self._parse_action(raw_command)
        public_command = parsed_command.public_text or "invalid"
        repeat_count = self._runtime.next_repeat_count(public_command)
        self._state.step_count += 1

        pre_health = self._backend.check_health_detailed()
        blocked_repeat = self._should_block_repeat(repeat_count)
        if blocked_repeat:
            command_result = BackendCommandResult(
                command_key=parsed_command.reward_key or "invalid",
                output=(
                    "BLOCKED: You have already run this command multiple times without making progress.\n"
                    "Try a different diagnostic path or inspect a different component."
                ),
                success=False,
                error="Repeated command blocked.",
            )
            post_health = pre_health
        elif parsed_command.is_valid:
            command_result = self._backend.execute(parsed_command)
            post_health = self._backend.check_health_detailed()
        else:
            command_result = BackendCommandResult(
                command_key=parsed_command.reward_key,
                output=parsed_command.error or "COMMAND ERROR: invalid command.",
                success=False,
                error=parsed_command.error or "COMMAND ERROR: invalid command.",
            )
            post_health = pre_health

        self._runtime.record_command(public_command, repeat_count)

        convergence_result: dict[str, object] | None = None
        if (
            not blocked_repeat
            and parsed_command.is_valid
            and self._is_fix_like_command(parsed_command)
        ):
            convergence_result = self._wait_for_post_fix_convergence(parsed_command)
            post_health = dict(convergence_result["health"])
            self._runtime.internal_flags["last_convergence_duration_s"] = float(
                convergence_result["duration_s"]
            )
            if self._active_transcript is not None:
                self._active_transcript.post_fix_convergence_duration += float(
                    convergence_result["duration_s"]
                )
            resolved_candidate, _ = self._verify_resolution()
            self._runtime.candidate_resolved = resolved_candidate
            self._runtime.is_fixed = resolved_candidate
        elif not self._runtime.seen_fix_commands:
            self._runtime.candidate_resolved = False

        resolution_ready = self._runtime.candidate_resolved
        resolution_confirmed = False
        phase = self._reward_engine.classify_phase(self._runtime, parsed_command)
        if phase == "verification":
            resolved, _ = self._verify_resolution()
            resolution_confirmed = (
                resolution_ready and resolved and command_result.verification_passed
            )

        reward_result = self._reward_engine.evaluate_step(
            self._runtime,
            parsed_command,
            command_result,
            repeat_count=repeat_count,
            resolution_ready=resolution_ready,
            resolution_confirmed=resolution_confirmed,
            blocked_repeat=blocked_repeat,
        )

        self._state.last_command = public_command
        self._state.command_history.append(public_command)
        self._state.cumulative_reward = self._normalize_public_score(
            self._runtime.cumulative_reward
        )
        self._state.incident_resolved = self._runtime.is_verified

        done = self._runtime.is_verified
        end_reason = "resolved" if done else "in_progress"

        if self._state.step_count >= self._runtime.task_spec.max_steps and not done:
            timeout_result = self._reward_engine.apply_timeout_penalty(self._runtime)
            reward_result = self._merge_reward_results(reward_result, timeout_result)
            self._state.cumulative_reward = self._normalize_public_score(
                self._runtime.cumulative_reward
            )
            done = True
            end_reason = "max_steps_reached"

        self._append_step_record(
            raw_command=raw_command,
            public_command=public_command,
            command_result=command_result,
            reward_result=reward_result,
        )

        observation = self._build_step_observation(
            command_result=command_result,
            command=parsed_command,
            reward_result=reward_result,
            done=done,
            include_system_snapshot=self._is_fix_like_command(parsed_command) or done,
        )

        if done:
            self._finalize_episode(end_reason, resolved=self._runtime.is_verified)

        return observation

    @property
    def state(self) -> ProductionOpsState:
        """Return the safe public state only."""
        return self._state

    def close(self) -> None:
        self._backend.close()

    def _build_backend(self) -> BaseOpsBackend:
        return RealMonolithBackend()

    def _do_reset(
        self, seed: int | None, task_id: str | None
    ) -> tuple[TaskSpec, float]:
        task_spec = self._scenario_generator.select(task_id=task_id, seed=seed)
        self._backend.reset(task_spec)
        reset_duration = 0.0
        last_reset_report = getattr(self._backend, "last_reset_report", None)
        if last_reset_report is not None:
            reset_duration = float(getattr(last_reset_report, "duration_s", 0.0))
        return task_spec, reset_duration

    def _parse_action(self, raw_command: str) -> NormalizedCommand:
        return self._gateway.parse(raw_command)

    def _should_block_repeat(self, repeat_count: int) -> bool:
        return repeat_count >= 3

    def _is_fix_like_command(self, command: NormalizedCommand) -> bool:
        return command.category == "fix"

    def _wait_for_post_fix_convergence(
        self, command: NormalizedCommand
    ) -> dict[str, object]:
        timeout_s = 20
        if command.target in {"app", "worker"} or command.verb == "set":
            timeout_s = 60
        return self._backend.wait_for_post_fix_convergence(
            command, timeout_s=timeout_s, interval_s=1.0
        )

    def _verify_resolution(self) -> tuple[bool, str]:
        return self._backend.run_verification()

    def _finalize_episode(self, end_reason: str, resolved: bool) -> None:
        if self._active_transcript is None:
            return
        self._active_transcript.total_reward = (
            self._runtime.cumulative_reward
            if self._runtime is not None
            else self._state.cumulative_reward
        )
        self._active_transcript.resolved = resolved
        self._active_transcript.end_reason = end_reason
        self._active_transcript.steps_used = self._state.step_count
        self._completed_transcripts.append(self._active_transcript)
        self._active_transcript = None

    def _append_step_record(
        self,
        raw_command: str,
        public_command: str,
        command_result: BackendCommandResult,
        reward_result: RewardResult,
    ) -> None:
        record = EpisodeStepRecord(
            step_number=self._state.step_count,
            raw_command=raw_command,
            normalized_command=public_command,
            phase=reward_result.phase,
            output_preview=command_result.output,
            reward=reward_result.total,
            cumulative_reward=self._runtime.cumulative_reward
            if self._runtime is not None
            else 0.0,
            feedback=reward_result.feedback,
            breakdown=dict(reward_result.breakdown),
            success=command_result.success,
            blocked_repeat=reward_result.blocked_repeat,
        )
        self._history.append(record)
        if self._active_transcript is not None:
            self._active_transcript.steps.append(record)

    def _build_initial_observation(self) -> ProductionOpsLabObservation:
        assert self._runtime is not None
        task_spec = self._runtime.task_spec
        return ProductionOpsLabObservation(
            alert_message=task_spec.alert_message,
            command_output=self._build_reset_command_output(task_spec),
            visible_health_summary=self._build_visible_health_summary(),
            system_snapshot=self._build_system_snapshot(),
            steps_taken=0,
            max_steps=task_spec.max_steps,
            available_commands_hint=list(ALLOWED_COMMANDS_HINT),
            success=True,
            error=None,
            active_incidents=[task_spec.task_id],
            hint=self._build_hint(),
            reward=0.0,
            done=False,
            metadata={"event": "reset", "backend_mode": self._backend_mode},
        )

    def _build_step_observation(
        self,
        command_result: BackendCommandResult,
        command: NormalizedCommand,
        reward_result: RewardResult,
        done: bool,
        include_system_snapshot: bool,
    ) -> ProductionOpsLabObservation:
        assert self._runtime is not None

        command_output = command_result.output
        if (
            done
            and not self._runtime.is_verified
            and self._state.step_count >= self._runtime.task_spec.max_steps
        ):
            command_output = f"{command_output}\nEPISODE COMPLETE: max steps reached without resolving the incident."

        metadata = {
            "backend_mode": self._backend_mode,
            "phase": reward_result.phase,
            "feedback": reward_result.feedback,
            "reward_breakdown": dict(reward_result.breakdown),
            "raw_step_reward": reward_result.total,
            "raw_cumulative_reward": self._runtime.cumulative_reward,
            "reported_score": self._normalize_public_score(
                self._runtime.cumulative_reward
            ),
        }
        if command.public_text:
            metadata["command"] = command.public_text

        return ProductionOpsLabObservation(
            alert_message=self._runtime.task_spec.alert_message,
            command_output=command_output,
            visible_health_summary=self._build_visible_health_summary(),
            system_snapshot=self._build_system_snapshot()
            if include_system_snapshot
            else "",
            steps_taken=self._state.step_count,
            max_steps=self._runtime.task_spec.max_steps,
            available_commands_hint=list(ALLOWED_COMMANDS_HINT),
            success=command_result.success,
            error=command_result.error,
            active_incidents=[self._runtime.task_spec.task_id],
            hint=self._build_hint(),
            reward=self._normalize_public_score(self._runtime.cumulative_reward),
            done=done,
            metadata=metadata,
        )

    def _build_visible_health_summary(self) -> str:
        return self._backend.visible_health_summary()

    def _build_system_snapshot(self) -> str:
        assert self._runtime is not None
        return self._backend.visible_incident_snapshot(self._runtime.task_spec)

    def _build_hint(self) -> str | None:
        return None

    def _normalize_public_score(self, raw_value: float) -> float:
        return max(0.0, min(1.0, raw_value))

    def _build_reset_command_output(self, task_spec: TaskSpec) -> str:
        alert_body = task_spec.alert_message.replace("ALERT:", "", 1).strip()
        return (
            f"PAGERDUTY ALERT: {alert_body}\n\n"
            "You are the on-call production engineer.\n"
            "Investigate the incident, determine the root cause, and restore service.\n"
            "Use the available operational commands to diagnose, fix, and verify."
        )

    def _merge_reward_results(
        self,
        base_result: RewardResult,
        extra_result: RewardResult,
    ) -> RewardResult:
        breakdown = dict(base_result.breakdown)
        for key, value in extra_result.breakdown.items():
            breakdown[key] = breakdown.get(key, 0.0) + value
        breakdown["total"] = base_result.total + extra_result.total
        return RewardResult(
            total=base_result.total + extra_result.total,
            feedback=f"{base_result.feedback} {extra_result.feedback}".strip(),
            phase=extra_result.phase
            if extra_result.phase == "timeout"
            else base_result.phase,
            breakdown=breakdown,
            likely_fix_applied=base_result.likely_fix_applied,
            likely_verification=base_result.likely_verification
            or extra_result.likely_verification,
            blocked_repeat=base_result.blocked_repeat or extra_result.blocked_repeat,
        )
