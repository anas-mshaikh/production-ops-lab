"""Small real-backend smoke path for local validation."""

from __future__ import annotations

import sys
from pathlib import Path


ENV_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENV_ROOT.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from production_ops_lab.models import ProductionOpsLabAction
from production_ops_lab.server.production_ops_environment import ProductionOpsEnvironment


def main() -> int:
    env = ProductionOpsEnvironment()
    try:
        observation = env.reset(task_id="app_service_stopped")
        print(f"RESET alert={observation.alert_message!r}")
        for command in ("svc status app", "svc restart app", "http check /health"):
            observation = env.step(ProductionOpsLabAction(command=command))
            print(
                f"STEP command={command!r} reward={observation.reward} "
                f"done={observation.done} output={observation.command_output!r}"
            )
        print(
            f"FINAL resolved={env.state.incident_resolved} "
            f"reward={env.state.cumulative_reward:.2f}"
        )
        return 0 if env.state.incident_resolved else 1
    finally:
        env.close()


if __name__ == "__main__":
    raise SystemExit(main())
