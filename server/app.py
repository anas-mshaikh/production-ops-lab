# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""FastAPI entrypoint for the Production Ops Lab environment."""

try:
    from openenv.core.env_server.http_server import create_app
except Exception as e:  # pragma: no cover
    raise ImportError(
        "openenv is required for the web interface. Install dependencies with '\n    uv sync\n'"
    ) from e

try:
    from ..models import ProductionOpsLabAction, ProductionOpsLabObservation
    from .production_ops_environment import ProductionOpsEnvironment
except (ImportError, ModuleNotFoundError):
    from models import ProductionOpsLabAction, ProductionOpsLabObservation
    from server.production_ops_environment import ProductionOpsEnvironment


app = create_app(
    ProductionOpsEnvironment,
    ProductionOpsLabAction,
    ProductionOpsLabObservation,
    env_name="production_ops_lab",
    max_concurrent_envs=4,
)


def main(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI server for local development and smoke tests."""
    import argparse
    import sys

    import uvicorn

    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser()
        parser.add_argument("--host", default=host)
        parser.add_argument("--port", type=int, default=port)
        args = parser.parse_args()
        host = args.host
        port = args.port

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
