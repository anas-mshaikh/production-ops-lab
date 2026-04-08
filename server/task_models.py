"""Private task and reward models for server-side environment logic."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Immutable definition of a starter task."""

    task_id: str
    incident_type: str
    difficulty: str
    title: str
    component: str
    service: str
    params: dict[str, str | int]
    alert_message: str
    initial_visible_symptom: str
    root_cause: str
    correct_fix_description: str
    accepted_fix_commands: tuple[str, ...]
    required_fix_commands: tuple[str, ...]
    expected_triage_path: tuple[str, ...]
    expected_investigation_commands: tuple[str, ...]
    root_cause_signal_commands: tuple[str, ...]
    expected_fix_path: tuple[str, ...]
    expected_verification_path: tuple[str, ...]
    verification_commands: tuple[str, ...]
    red_herrings: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    recent_event_lines: tuple[str, ...]
    tags: tuple[str, ...]
    max_steps: int


@dataclass(slots=True)
class TaskRuntime:
    """Mutable runtime data for the current episode."""

    task_spec: TaskSpec
    is_fixed: bool = False
    is_verified: bool = False
    candidate_resolved: bool = False
    verification_required: bool = False
    repeated_commands: dict[str, int] = field(default_factory=dict)
    reward_breakdown: dict[str, float] = field(default_factory=dict)
    internal_flags: dict[str, bool | int | float | str] = field(default_factory=dict)
    seen_triage_commands: set[str] = field(default_factory=set)
    seen_investigation_commands: set[str] = field(default_factory=set)
    seen_evidence_commands: set[str] = field(default_factory=set)
    seen_fix_commands: set[str] = field(default_factory=set)
    seen_verification_commands: set[str] = field(default_factory=set)
    cumulative_reward: float = 0.0

    def next_repeat_count(self, command: str) -> int:
        """Return the next repeat count without mutating runtime state."""
        return self.repeated_commands.get(command, 0) + 1

    def record_command(self, command: str, count: int | None = None) -> int:
        """Track how many times a normalized command has been used."""
        next_count = count if count is not None else self.next_repeat_count(command)
        self.repeated_commands[command] = next_count
        return next_count

    def add_reward(self, key: str, value: float) -> None:
        """Accumulate reward contribution under a named bucket."""
        self.reward_breakdown[key] = self.reward_breakdown.get(key, 0.0) + value


@dataclass(frozen=True, slots=True)
class EpisodeStepRecord:
    """Internal structured record for a single environment step."""

    step_number: int
    raw_command: str
    normalized_command: str
    phase: str
    output_preview: str
    reward: float
    cumulative_reward: float
    feedback: str
    breakdown: dict[str, float]
    success: bool
    blocked_repeat: bool = False


@dataclass(slots=True)
class EpisodeTranscript:
    """Internal transcript for an episode lifecycle."""

    episode_id: str
    scenario_id: str
    difficulty: str
    alert: str
    root_cause: str
    correct_fix_description: str
    initial_observation: dict[str, object]
    total_reward: float = 0.0
    resolved: bool = False
    end_reason: str = "in_progress"
    steps_used: int = 0
    reset_duration: float = 0.0
    post_fix_convergence_duration: float = 0.0
    steps: list[EpisodeStepRecord] = field(default_factory=list)
