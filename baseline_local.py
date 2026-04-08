"""Local deterministic multi-task baseline runner for Production Ops Lab v1."""

from __future__ import annotations

import argparse
from typing import Final

try:
    from .client import ProductionOpsLabEnv
    from .models import ProductionOpsLabAction
except ImportError:
    from client import ProductionOpsLabEnv
    from models import ProductionOpsLabAction


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


def run_task(client, task_id: str) -> bool:
    reset_result = client.reset(task_id=task_id)
    state = client.state()
    commands = POLICIES[task_id]
    print(f"TASK_START task_id={task_id} difficulty={state.difficulty}")
    print(
        "RESET"
        f" reward={reset_result.reward} done={reset_result.done}"
        f" alert={reset_result.observation.alert_message!r}"
    )

    last_result = reset_result
    for command in commands:
        last_result = client.step(ProductionOpsLabAction(command=command))
        print(
            "STEP"
            f" task_id={task_id}"
            f" command={command!r}"
            f" reward={last_result.reward}"
            f" done={last_result.done}"
            f" output={last_result.observation.command_output!r}"
        )
        if last_result.done:
            break

    final_state = client.state()
    print(
        "TASK_END"
        f" task_id={task_id}"
        f" resolved={final_state.incident_resolved}"
        f" cumulative_reward={final_state.cumulative_reward:.2f}"
        f" steps={final_state.step_count}"
        f" commands={final_state.command_history!r}"
    )
    return final_state.incident_resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL for a running Production Ops Lab server.",
    )
    args = parser.parse_args()

    client = ProductionOpsLabEnv(base_url=args.base_url).sync()
    with client:
        outcomes = [run_task(client, task_id) for task_id in POLICIES]

    if all(outcomes):
        print("BASELINE_COMPLETE resolved_all=True")
        return 0

    print("BASELINE_COMPLETE resolved_all=False")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
