"""Canonical healthy-world definition for the real v1 monolith."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """Structured description of a service in the real target world."""

    name: str
    purpose: str
    expected_status: str
    dependencies: tuple[str, ...]
    health_checks: tuple[str, ...] = ()
    host_port: int | None = None
    container_port: int | None = None
    health_path: str | None = None


@dataclass(frozen=True, slots=True)
class SmokeCheckSpec:
    """Reusable healthy-world smoke check definition."""

    name: str
    description: str
    category: str


@dataclass(frozen=True, slots=True)
class HealthyWorldSpec:
    """Source of truth for the healthy real-world topology."""

    monolith_root: Path
    runtime_dir: Path
    app_env_template: Path
    worker_env_template: Path
    healthy_database_url: str
    broken_database_url: str
    redis_url: str
    queue_name: str
    default_runtime_env: tuple[str, ...]
    services: tuple[ServiceSpec, ...]
    smoke_checks: tuple[SmokeCheckSpec, ...]

    @property
    def service_names(self) -> tuple[str, ...]:
        return tuple(service.name for service in self.services)

    @property
    def app_env_runtime(self) -> Path:
        return self.runtime_dir / "app.env"

    @property
    def worker_env_runtime(self) -> Path:
        return self.runtime_dir / "worker.env"

    @property
    def runtime_ignore_file(self) -> Path:
        return self.runtime_dir / ".gitignore"


ENV_ROOT = Path(__file__).resolve().parents[1]
MONOLITH_ROOT = ENV_ROOT / "lab_target" / "monolith"
RUNTIME_DIR = MONOLITH_ROOT / "runtime"

HEALTHY_DATABASE_URL = "postgresql://app:app@postgres:5432/production_ops"
BROKEN_DATABASE_URL = "postgresql://app:app@postgres:5432/production_ops_broken"
REDIS_URL = "redis://redis:6379/0"

WORLD_SPEC = HealthyWorldSpec(
    monolith_root=MONOLITH_ROOT,
    runtime_dir=RUNTIME_DIR,
    app_env_template=MONOLITH_ROOT / "app" / ".env.template",
    worker_env_template=MONOLITH_ROOT / "worker" / ".env.template",
    healthy_database_url=HEALTHY_DATABASE_URL,
    broken_database_url=BROKEN_DATABASE_URL,
    redis_url=REDIS_URL,
    queue_name="application_jobs",
    default_runtime_env=(
        f"DATABASE_URL={HEALTHY_DATABASE_URL}",
        f"REDIS_URL={REDIS_URL}",
        "QUEUE_NAME=application_jobs",
    ),
    services=(
        ServiceSpec(
            name="nginx",
            purpose="Ingress proxy that exposes the monolith health and API surface.",
            expected_status="healthy",
            dependencies=("app",),
            health_checks=("http:/health",),
            host_port=18080,
            container_port=80,
            health_path="/health",
        ),
        ServiceSpec(
            name="app",
            purpose="FastAPI modular monolith that serves health, read, write, and internal queue paths.",
            expected_status="healthy",
            dependencies=("postgres", "redis"),
            health_checks=("http:/health",),
            host_port=18081,
            container_port=8000,
            health_path="/health",
        ),
        ServiceSpec(
            name="postgres",
            purpose="Persistent relational store for candidates, applications, and processed notifications.",
            expected_status="healthy",
            dependencies=(),
            health_checks=("db:ping",),
            host_port=15432,
            container_port=5432,
        ),
        ServiceSpec(
            name="redis",
            purpose="Queue and heartbeat store for async processing.",
            expected_status="healthy",
            dependencies=(),
            health_checks=("redis:ping",),
            host_port=16379,
            container_port=6379,
        ),
        ServiceSpec(
            name="worker",
            purpose="Background worker that drains queued application jobs and persists processed results.",
            expected_status="healthy",
            dependencies=("postgres", "redis"),
            health_checks=("queue:worker_heartbeat",),
        ),
        ServiceSpec(
            name="scheduler",
            purpose="Tiny scheduler heartbeat process to keep the six-service topology realistic.",
            expected_status="healthy",
            dependencies=("redis",),
            health_checks=("scheduler:heartbeat",),
        ),
    ),
    smoke_checks=(
        SmokeCheckSpec(
            name="ingress_health",
            description="GET /health through nginx returns 200.",
            category="health",
        ),
        SmokeCheckSpec(
            name="read_candidates",
            description="GET /candidates?q=seed returns seeded rows from Postgres.",
            category="read",
        ),
        SmokeCheckSpec(
            name="write_application",
            description="POST /applications writes a row and enqueues background work.",
            category="write",
        ),
        SmokeCheckSpec(
            name="async_processing",
            description="Queued work drains and produces a processed notification marker.",
            category="async",
        ),
    ),
)

BASELINE_STATE = {
    service.name: service.expected_status for service in WORLD_SPEC.services
}

SERVICE_HEALTH_CHECKS = tuple(
    check_name
    for service in WORLD_SPEC.services
    for check_name in service.health_checks
)

BUSINESS_SMOKE_CHECKS = tuple(check.name for check in WORLD_SPEC.smoke_checks)


def sanitize_database_url(value: str) -> str:
    """Redact credentials from a DATABASE_URL before returning it publicly."""
    if "@" not in value or "://" not in value:
        return value
    scheme, remainder = value.split("://", 1)
    credentials, host_part = remainder.split("@", 1)
    username = credentials.split(":", 1)[0]
    return f"{scheme}://{username}:***@{host_part}"
