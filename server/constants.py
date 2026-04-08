"""Small constants for the Production Ops Lab starter phase."""

DEFAULT_MAX_STEPS = 8
DIFFICULTY_MAX_STEPS = {
    "easy": 8,
    "medium": 10,
    "hard": 12,
}
DEFAULT_LOG_TAIL = 30
BACKEND_MODE_ENV_VAR = "PRODUCTION_OPS_BACKEND_MODE"
BACKEND_MODE_HF_LOCAL = "hf_local"
BACKEND_MODE_REAL = "real"
BACKEND_MODE_FAKE = "fake"

STARTER_TASK_IDS = (
    "app_service_stopped",
    "bad_env_db_url",
    "queue_backlog_due_to_worker_failure",
    "nginx_service_stopped",
    "postgres_service_stopped",
    "redis_service_stopped",
)

ALLOWED_COMMANDS_HINT = [
    "svc status <service>",
    "svc logs <service>",
    "svc restart <service>",
    "svc start <service>",
    "env show app",
    "env set app DATABASE_URL=<value>",
    "queue stats",
    "http check /health",
    "lab verify",
]

V1_OBJECTIVE_TEXT = (
    "Production Ops Lab v1 is a compact OpenEnv environment where an agent "
    "diagnoses and fixes incidents in a single-host modular monolith using a "
    "constrained production-ops command surface, deterministic reset, and "
    "programmatic grading."
)

STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_STOPPED = "stopped"
STATUS_STALE = "stale"
