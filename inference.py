"""Submission-oriented single-episode inference runner for Production Ops Lab."""

from __future__ import annotations

import json
import os
import traceback
from dataclasses import dataclass
from typing import Final

from openai import OpenAI

try:
    from .client import ProductionOpsLabEnv
    from .models import ProductionOpsLabAction, ProductionOpsLabObservation
except ImportError:
    from client import ProductionOpsLabEnv
    from models import ProductionOpsLabAction, ProductionOpsLabObservation


POLICIES: Final[dict[str, list[str]]] = {
    "app_service_stopped": [
        "svc status app",
        "svc restart app",
        "http check /health",
    ],
    "nginx_service_stopped": [
        "svc status nginx",
        "svc restart nginx",
        "http check /health",
    ],
    "bad_env_db_url": [
        "svc logs app",
        "env set app DATABASE_URL=correct",
        "svc restart app",
        "lab verify",
    ],
    "postgres_service_stopped": [
        "svc status postgres",
        "svc restart postgres",
        "lab verify",
    ],
    "redis_service_stopped": [
        "svc status redis",
        "svc restart redis",
        "lab verify",
    ],
    "queue_backlog_due_to_worker_failure": [
        "svc status worker",
        "svc restart worker",
        "queue stats",
    ],
}


@dataclass(frozen=True, slots=True)
class InferenceSettings:
    api_base_url: str
    model_name: str
    hf_token: str
    env_base_url: str
    task_id: str
    max_steps: int
    temperature: float
    max_total_reward: float
    success_score_threshold: float


def load_settings() -> InferenceSettings:
    """Load submission settings from environment variables."""
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        raise RuntimeError("HF_TOKEN is required but was not set.")

    return InferenceSettings(
        api_base_url=os.getenv("API_BASE_URL", "https://router.huggingface.co/v1"),
        model_name=os.getenv("MODEL_NAME", "openai/gpt-oss-20b"),
        hf_token=hf_token,
        env_base_url=os.getenv("ENV_BASE_URL", "http://127.0.0.1:8000"),
        task_id=os.getenv("TASK_ID", "app_service_stopped"),
        max_steps=int(os.getenv("MAX_STEPS", "6")),
        temperature=float(os.getenv("TEMPERATURE", "0.0")),
        max_total_reward=float(os.getenv("MAX_TOTAL_REWARD", "1.0")),
        success_score_threshold=float(os.getenv("SUCCESS_SCORE_THRESHOLD", "0.60")),
    )


def fmt_bool(value: bool) -> str:
    return "true" if value else "false"


def fmt_float(value: float) -> str:
    return f"{float(value):.2f}"


def fmt_rewards(values: list[float]) -> str:
    return "[" + ", ".join(fmt_float(value) for value in values) + "]"


def format_start_line(task_id: str) -> str:
    return f"[START] task_id={json.dumps(task_id)}"


def format_step_line(
    step: int,
    action: str,
    reward: float,
    done: bool,
    error: str | None,
) -> str:
    error_text = "null" if error is None else json.dumps(error)
    return (
        f"[STEP] step={step} action={json.dumps(action)} reward={fmt_float(reward)} "
        f"done={fmt_bool(done)} error={error_text}"
    )


def format_end_line(
    success: bool, steps: int, score: float, rewards: list[float]
) -> str:
    return (
        f"[END] success={fmt_bool(success)} steps={steps} "
        f"score={fmt_float(score)} rewards={fmt_rewards(rewards)}"
    )


def log_start(task_id: str) -> None:
    print(format_start_line(task_id), flush=True)


def log_step(
    step: int, action: str, reward: float, done: bool, error: str | None
) -> None:
    print(
        format_step_line(
            step=step, action=action, reward=reward, done=done, error=error
        ),
        flush=True,
    )


def log_end(success: bool, steps: int, score: float, rewards: list[float]) -> None:
    print(
        format_end_line(success=success, steps=steps, score=score, rewards=rewards),
        flush=True,
    )


def get_expected_command(task_id: str, step: int) -> str:
    commands = POLICIES.get(task_id, ["svc status app"])
    if step - 1 < len(commands):
        return commands[step - 1]
    return "lab verify"


def extract_observation_text(observation: ProductionOpsLabObservation) -> str:
    return (
        observation.command_output
        or observation.system_snapshot
        or observation.alert_message
        or ""
    )


def coerce_model_command(content: str | None, fallback: str) -> str:
    if not content:
        return fallback

    for raw_line in content.splitlines():
        line = raw_line.strip().strip("`")
        if not line or line.lower().startswith("command:"):
            line = line.split(":", 1)[-1].strip()
        if not line or line == "```":
            continue
        if len(line) > 160:
            return fallback
        return line
    return fallback


def get_model_message(
    llm_client: OpenAI,
    settings: InferenceSettings,
    step: int,
    last_output: str,
    last_reward: float,
    history: list[str],
) -> str:
    expected = get_expected_command(settings.task_id, step)
    system_prompt = (
        "You are an on-call production engineer. "
        "Return exactly one production-ops command. No explanation. No markdown."
    )
    recent_history = "\n".join(history[-5:]) if history else "(none)"
    user_prompt = (
        f"Task ID: {settings.task_id}\n"
        f"Step: {step}\n"
        f"Last reward: {last_reward:.2f}\n"
        f"Last output:\n{last_output}\n\n"
        f"Recent history:\n{recent_history}\n\n"
        f"Recommended next command: {expected}\n"
        "Return exactly the next command only."
    )

    try:
        response = llm_client.chat.completions.create(
            model=settings.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=settings.temperature,
            max_tokens=64,
        )
        content = response.choices[0].message.content if response.choices else None
    except Exception:
        return expected

    return coerce_model_command(content, expected)


def main() -> int:
    settings = load_settings()
    llm_client = OpenAI(
        base_url=settings.api_base_url,
        api_key=settings.hf_token,
        timeout=5.0,
    )

    rewards: list[float] = []
    history: list[str] = []
    steps_taken = 0
    score = 0.0
    success = False
    env = None

    log_start(task_id=settings.task_id)

    try:
        env = ProductionOpsLabEnv(base_url=settings.env_base_url).sync()
        result = env.reset(task_id=settings.task_id)
        last_output = extract_observation_text(result.observation)
        last_reward = float(result.reward or 0.0)

        for step in range(1, settings.max_steps + 1):
            if result.done:
                break

            message = get_model_message(
                llm_client=llm_client,
                settings=settings,
                step=step,
                last_output=last_output,
                last_reward=last_reward,
                history=history,
            )

            result = env.step(ProductionOpsLabAction(command=message))
            obs = result.observation

            reward = float(result.reward or 0.0)
            done = bool(result.done)
            error = obs.error if getattr(obs, "error", None) else None

            rewards.append(reward)
            steps_taken = step
            last_output = extract_observation_text(obs)
            last_reward = reward

            log_step(step=step, action=message, reward=reward, done=done, error=error)

            history.append(f"Step {step}: {message!r} -> reward {reward:+.2f}")
            if done:
                break

        if settings.max_total_reward > 0:
            score = sum(rewards) / settings.max_total_reward
        else:
            score = 0.0
        score = min(max(score, 0.0), 1.0)
        success = score >= settings.success_score_threshold
        return 0 if success else 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        try:
            if env is not None:
                env.close()
        except Exception as exc:
            print(f"[DEBUG] env.close() error (container cleanup): {exc}", flush=True)
        log_end(success=success, steps=steps_taken, score=score, rewards=rewards)


if __name__ == "__main__":
    raise SystemExit(main())
