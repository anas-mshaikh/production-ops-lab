"""End-to-end smoke test for the submission inference script."""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ENV_ROOT = Path(__file__).resolve().parents[1]


def _get_free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise AssertionError(f"Server at {base_url} did not become ready in time.")


def test_inference_script_runs_single_episode_with_submission_logs() -> None:
    try:
        port = _get_free_port()
    except PermissionError as exc:
        raise AssertionError(f"Local port binding is unavailable in this environment: {exc}") from exc

    base_url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [
            "python3",
            "-m",
            "uvicorn",
            "server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ENV_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ),
    )

    try:
        _wait_for_server(base_url)
        result = subprocess.run(
            ["python3", "inference.py"],
            cwd=ENV_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env={
                **os.environ,
                "ENV_BASE_URL": base_url,
                "TASK_ID": "app_service_stopped",
                "HF_TOKEN": "dummy-token",
                "API_BASE_URL": "http://127.0.0.1:9/v1",
                "MODEL_NAME": "openai/gpt-oss-20b",
                "MAX_STEPS": "6",
                "MAX_TOTAL_REWARD": "1.0",
                "SUCCESS_SCORE_THRESHOLD": "0.60",
            },
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    assert result.returncode == 0, result.stderr
    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]

    assert stdout_lines[0].startswith("[START] task_id=")
    assert stdout_lines[-1].startswith("[END] success=")
    assert sum(1 for line in stdout_lines if line.startswith("[START]")) == 1
    assert sum(1 for line in stdout_lines if line.startswith("[END]")) == 1
    assert sum(1 for line in stdout_lines if line.startswith("[STEP]")) >= 1
    assert "TASK_START" not in result.stdout
    assert "TASK_END" not in result.stdout
    assert "BASELINE_COMPLETE" not in result.stdout

    end_line = stdout_lines[-1]
    match = re.search(r"score=([0-9]+\.[0-9]{2})", end_line)
    assert match is not None
    score = float(match.group(1))
    assert 0.0 <= score <= 1.0
