"""Environment-level tests for Production Ops Lab v1."""

from __future__ import annotations

import pytest

from production_ops_lab.inference import DEFAULT_TASK_IDS, POLICIES
from production_ops_lab.models import (
    ProductionOpsLabAction,
    ProductionOpsLabObservation,
    ProductionOpsState,
)
from production_ops_lab.server.constants import ALLOWED_COMMANDS_HINT
from production_ops_lab.server.production_ops_environment import (
    ProductionOpsEnvironment,
)
from production_ops_lab.server.scenario_generator import ScenarioGenerator


def test_observation_model_has_safe_optional_defaults() -> None:
    observation = ProductionOpsLabObservation()

    assert observation.system_snapshot == ""
    assert observation.active_incidents == []
    assert observation.hint is None


def test_reset_returns_richer_typed_observation() -> None:
    env = ProductionOpsEnvironment()
    observation = env.reset(task_id="app_service_stopped")

    assert isinstance(observation, ProductionOpsLabObservation)
    assert observation.command_output.startswith("PAGERDUTY ALERT:")
    assert observation.alert_message.startswith("ALERT:")
    assert observation.visible_health_summary.startswith("VISIBLE HEALTH:")
    assert observation.system_snapshot.startswith("HTTP:")
    assert observation.active_incidents == ["app_service_stopped"]
    assert observation.hint is None
    assert observation.steps_taken == 0
    assert observation.max_steps == 8
    assert observation.available_commands_hint == ALLOWED_COMMANDS_HINT
    assert observation.success is True
    assert observation.error is None


def test_state_returns_safe_public_metadata_only() -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id="bad_env_db_url")
    env.step(
        ProductionOpsLabAction(
            command="env set app DATABASE_URL=postgresql://user:secret@db/internal"
        )
    )

    state = env.state
    state_dump = state.model_dump()

    assert isinstance(state, ProductionOpsState)
    assert state.task_id == "bad_env_db_url"
    assert state.step_count == 1
    assert "root_cause" not in state_dump
    assert "accepted_fix_commands" not in state_dump
    assert "secret" not in state.last_command
    assert "<redacted>" in state.last_command
    assert "secret" not in "".join(state.command_history)


def test_difficulty_scaled_max_steps_are_preserved() -> None:
    env = ProductionOpsEnvironment()

    easy = env.reset(task_id="app_service_stopped")
    medium = env.reset(task_id="bad_env_db_url")
    hard = env.reset(task_id="queue_backlog_due_to_worker_failure")
    ingress_easy = env.reset(task_id="nginx_service_stopped")
    postgres_medium = env.reset(task_id="postgres_service_stopped")
    redis_medium = env.reset(task_id="redis_service_stopped")

    assert easy.max_steps == 8
    assert medium.max_steps == 10
    assert hard.max_steps == 12
    assert ingress_easy.max_steps == 8
    assert postgres_medium.max_steps == 10
    assert redis_medium.max_steps == 10


@pytest.mark.parametrize(
    ("task_id", "fix_commands", "verify_command"),
    [
        ("app_service_stopped", ["svc restart app"], "http check /health"),
        ("nginx_service_stopped", ["svc restart nginx"], "http check /health"),
        (
            "bad_env_db_url",
            ["env set app DATABASE_URL=correct", "svc restart app"],
            "lab verify",
        ),
        ("postgres_service_stopped", ["svc restart postgres"], "lab verify"),
        ("redis_service_stopped", ["svc restart redis"], "lab verify"),
        (
            "queue_backlog_due_to_worker_failure",
            ["svc restart worker"],
            "queue stats",
        ),
    ],
)
def test_tasks_require_explicit_verification_for_success(
    task_id: str,
    fix_commands: list[str],
    verify_command: str,
) -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id=task_id)

    last_observation = None
    for command in fix_commands:
        last_observation = env.step(ProductionOpsLabAction(command=command))

    assert last_observation is not None
    assert last_observation.done is False
    assert env.state.incident_resolved is False

    verify_observation = env.step(ProductionOpsLabAction(command=verify_command))

    assert verify_observation.done is True
    assert env.state.incident_resolved is True
    assert 0.0 < env.state.cumulative_reward < 1.0


def test_diagnostic_steps_hide_system_snapshot_and_fix_steps_refresh_it() -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id="app_service_stopped")

    diagnostic = env.step(ProductionOpsLabAction(command="svc status app"))
    fix = env.step(ProductionOpsLabAction(command="svc restart app"))
    terminal = env.step(ProductionOpsLabAction(command="http check /health"))

    assert diagnostic.system_snapshot == ""
    assert fix.system_snapshot.startswith("HTTP:")
    assert terminal.system_snapshot.startswith("HTTP:")


def test_unfinished_episode_followed_by_reset_is_finalized_as_abandoned() -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id="app_service_stopped")
    env.step(ProductionOpsLabAction(command="svc status app"))

    env.reset(task_id="bad_env_db_url")

    assert env._completed_transcripts[-1].scenario_id == "app_service_stopped"
    assert env._completed_transcripts[-1].end_reason == "abandoned"
    assert env._completed_transcripts[-1].resolved is False


def test_repeated_commands_are_penalized_and_then_blocked() -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id="app_service_stopped")

    first = env.step(ProductionOpsLabAction(command="svc status nginx"))
    second = env.step(ProductionOpsLabAction(command="svc status nginx"))
    third = env.step(ProductionOpsLabAction(command="svc status nginx"))

    assert first.reward == pytest.approx(0.01)
    assert second.reward == pytest.approx(0.01)
    assert third.reward == pytest.approx(0.01)
    assert first.metadata["raw_step_reward"] == -0.10
    assert second.metadata["raw_step_reward"] == pytest.approx(-0.20)
    assert third.metadata["raw_step_reward"] == pytest.approx(-0.50)
    assert 0.0 < first.metadata["reported_score"] < 1.0
    assert third.error == "Repeated command blocked."
    assert third.command_output.startswith("BLOCKED:")
    assert env.state.incident_resolved is False


def test_timeout_ends_clearly_negative() -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id="app_service_stopped")

    observation = None
    for _ in range(env.state.max_steps):
        observation = env.step(ProductionOpsLabAction(command="svc status nginx"))

    assert observation is not None
    assert observation.done is True
    assert env.state.incident_resolved is False
    assert 0.0 < env.state.cumulative_reward < 1.0
    assert env.state.cumulative_reward < 0.60
    assert env._completed_transcripts[-1].total_reward <= -0.25


def test_reward_breakdowns_are_deterministic_for_same_command_path() -> None:
    commands = [
        "svc logs app",
        "env set app DATABASE_URL=correct",
        "svc restart app",
        "lab verify",
    ]
    env_one = ProductionOpsEnvironment()
    env_two = ProductionOpsEnvironment()

    env_one.reset(task_id="bad_env_db_url")
    env_two.reset(task_id="bad_env_db_url")

    breakdowns_one: list[dict[str, float]] = []
    breakdowns_two: list[dict[str, float]] = []

    for command in commands:
        breakdowns_one.append(
            env_one.step(ProductionOpsLabAction(command=command)).metadata[
                "reward_breakdown"
            ]
        )
        breakdowns_two.append(
            env_two.step(ProductionOpsLabAction(command=command)).metadata[
                "reward_breakdown"
            ]
        )

    assert breakdowns_one == breakdowns_two


def test_completed_episode_records_a_transcript_with_steps() -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id="app_service_stopped")
    env.step(ProductionOpsLabAction(command="svc status app"))
    env.step(ProductionOpsLabAction(command="svc restart app"))
    env.step(ProductionOpsLabAction(command="http check /health"))

    transcript = env._completed_transcripts[-1]

    assert transcript.end_reason == "resolved"
    assert transcript.resolved is True
    assert transcript.steps_used == 3
    assert transcript.steps
    assert env.state.cumulative_reward == pytest.approx(0.99)
    assert transcript.steps[0].phase == "triage"
    assert transcript.steps[-1].phase == "verification"


def test_public_score_is_strictly_inside_the_unit_interval() -> None:
    env = ProductionOpsEnvironment()
    env.reset(task_id="app_service_stopped")
    env.step(ProductionOpsLabAction(command="svc status app"))
    env.step(ProductionOpsLabAction(command="svc restart app"))
    observation = env.step(ProductionOpsLabAction(command="http check /health"))

    assert 0.0 < observation.reward < 1.0
    assert observation.reward == pytest.approx(0.99)
    assert 0.0 < env.state.cumulative_reward < 1.0
    assert observation.metadata["raw_cumulative_reward"] >= 1.0


def test_default_submission_triplet_final_scores_are_strictly_inside_unit_interval() -> None:
    env = ProductionOpsEnvironment()
    try:
        for task_id in DEFAULT_TASK_IDS:
            observation = env.reset(task_id=task_id)
            for command in POLICIES[task_id]:
                observation = env.step(ProductionOpsLabAction(command=command))
                if observation.done:
                    break

            assert observation.done is True
            assert 0.0 < observation.reward < 1.0
            assert 0.0 < env.state.cumulative_reward < 1.0
    finally:
        env.close()


def test_scenario_selection_is_deterministic() -> None:
    generator = ScenarioGenerator()

    assert generator.select(task_id="bad_env_db_url").task_id == "bad_env_db_url"
    assert generator.select(seed=4).task_id == "postgres_service_stopped"
    assert [generator.select().task_id for _ in range(7)] == [
        "app_service_stopped",
        "bad_env_db_url",
        "queue_backlog_due_to_worker_failure",
        "nginx_service_stopped",
        "postgres_service_stopped",
        "redis_service_stopped",
        "app_service_stopped",
    ]
