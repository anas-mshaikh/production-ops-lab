# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Typed client wrapper for the Production Ops Lab environment."""

from typing import Any

from openenv.core import EnvClient
from openenv.core.client_types import StepResult

try:
    from .models import (
        ProductionOpsLabAction,
        ProductionOpsLabObservation,
        ProductionOpsState,
    )
except (ImportError, ModuleNotFoundError):
    from models import (
        ProductionOpsLabAction,
        ProductionOpsLabObservation,
        ProductionOpsState,
    )


class ProductionOpsLabEnv(
    EnvClient[ProductionOpsLabAction, ProductionOpsLabObservation, ProductionOpsState]
):
    """
    Typed client for Production Ops Lab.

    The client is async by default through ``EnvClient`` and can be used
    synchronously with ``.sync()``:

        >>> client = ProductionOpsLabEnv(base_url="http://localhost:8000").sync()
        >>> with client:
        ...     reset_result = client.reset(task_id="app_service_stopped")
        ...     step_result = client.step(
        ...         ProductionOpsLabAction(command="svc status app")
        ...     )
    """

    def _step_payload(self, action: ProductionOpsLabAction) -> dict[str, Any]:
        payload = {"command": action.command}
        if action.metadata:
            payload["metadata"] = action.metadata
        return payload

    def _parse_result(
        self,
        payload: dict[str, Any],
    ) -> StepResult[ProductionOpsLabObservation]:
        obs_data = dict(payload.get("observation", {}))
        reward = payload.get("reward", obs_data.get("reward"))
        done = payload.get("done", obs_data.get("done", False))
        obs_data["reward"] = reward
        obs_data["done"] = done
        observation = ProductionOpsLabObservation(**obs_data)
        return StepResult(observation=observation, reward=reward, done=done)

    def _parse_state(self, payload: dict[str, Any]) -> ProductionOpsState:
        return ProductionOpsState(**payload)
