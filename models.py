# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Public OpenEnv models for Production Ops Lab v1."""

from openenv.core.env_server.types import Action, Observation, State
from pydantic import Field


class ProductionOpsLabAction(Action):
    """
    Public action model for the constrained production-ops command surface.

    The inherited ``metadata`` field remains available for callers that need
    auxiliary request context, but the agent-facing contract is a single command.
    """

    command: str = Field(
        ...,
        min_length=1,
        description="Operational command to execute in the environment.",
    )


class ProductionOpsLabObservation(Observation):
    """
    Public observation returned after reset and each environment step.

    The inherited ``done``, ``reward``, and ``metadata`` fields come from
    OpenEnv's Observation base model and remain part of the stable public schema.
    """

    alert_message: str = Field(
        default="",
        description="Operator-facing alert summary for the current incident.",
    )
    command_output: str = Field(
        default="",
        description="Human-readable output from the last executed command.",
    )
    visible_health_summary: str = Field(
        default="",
        description="Safe summary of the visible system health state.",
    )
    system_snapshot: str = Field(
        default="",
        description="Richer public-safe operational snapshot for the current incident.",
    )
    steps_taken: int = Field(
        default=0,
        ge=0,
        description="Number of commands taken in the active episode.",
    )
    max_steps: int = Field(
        default=0,
        ge=0,
        description="Maximum allowed commands for the current episode.",
    )
    available_commands_hint: list[str] = Field(
        default_factory=list,
        description="Small hint list describing the supported command grammar.",
    )
    success: bool = Field(
        default=True,
        description="Whether the command executed successfully.",
    )
    error: str | None = Field(
        default=None,
        description="Safe error message for invalid or rejected commands.",
    )
    active_incidents: list[str] = Field(
        default_factory=list,
        description="Currently active incident identifiers exposed to the agent.",
    )
    hint: str | None = Field(
        default=None,
        description="Optional safe hint for non-strict modes. Unset in strict eval mode.",
    )


class ProductionOpsState(State):
    """
    Safe public environment state.

    This state deliberately excludes root cause, hidden grading truth, and
    accepted fix sequences. Only stable metadata that is safe to expose is kept.
    The inherited ``episode_id`` field remains available from the State base
    class for external coordination and debugging.
    """

    task_id: str = Field(default="", description="Identifier of the current task.")
    difficulty: str = Field(
        default="",
        description="Difficulty label for the current task.",
    )
    step_count: int = Field(
        default=0,
        ge=0,
        description="Number of steps taken in the current episode.",
    )
    max_steps: int = Field(
        default=0,
        ge=0,
        description="Maximum allowed commands for the active episode.",
    )
    cumulative_reward: float = Field(
        default=0.0,
        description="Normalized public score for the active episode, reported strictly inside (0,1).",
    )
    incident_resolved: bool = Field(
        default=False,
        description="Whether the incident has been fixed and verified.",
    )
    last_command: str = Field(
        default="",
        description="Last normalized command executed by the agent.",
    )
    command_history: list[str] = Field(
        default_factory=list,
        description="Normalized command history for the active episode.",
    )
