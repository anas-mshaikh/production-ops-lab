"""Minimal monolith app for Production Ops Lab."""

from __future__ import annotations

import json
import logging
import os
import time

import psycopg
import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("production_ops_lab.app")

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
QUEUE_NAME = os.environ.get("QUEUE_NAME", "application_jobs")

app = FastAPI(title="Production Ops Lab Monolith")


class ApplicationCreate(BaseModel):
    candidate_id: int
    role: str = "ops-engineer"


def _db_connection():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _heartbeat_status(client: redis.Redis, key: str) -> str:
    raw = client.get(f"{key}:last_seen")
    if raw is None:
        return "stale"
    age = time.time() - float(raw)
    return "healthy" if age <= 5 else "stale"


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with _db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        _redis_client().ping()
        return {"status": "ok", "database": "ok", "redis": "ok"}
    except Exception as exc:  # pragma: no cover - exercised by integration tests
        logger.exception("database connection failed during health check: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="database connection failed",
        ) from exc


@app.get("/candidates")
def candidates(q: str = "") -> dict[str, object]:
    try:
        with _db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, email
                    FROM candidates
                    WHERE lower(name) LIKE %s
                    ORDER BY id
                    """,
                    (f"%{q.lower()}%",),
                )
                rows = cur.fetchall()
    except Exception as exc:  # pragma: no cover - exercised by integration tests
        logger.exception("candidate search failed: %s", exc)
        raise HTTPException(status_code=503, detail="candidate search unavailable") from exc

    return {
        "items": [
            {"id": row[0], "name": row[1], "email": row[2]}
            for row in rows
        ]
    }


@app.post("/applications")
def create_application(payload: ApplicationCreate) -> dict[str, object]:
    try:
        with _db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO applications (candidate_id, role, status)
                    VALUES (%s, %s, 'queued')
                    RETURNING id
                    """,
                    (payload.candidate_id, payload.role),
                )
                application_id = int(cur.fetchone()[0])
        queue = _redis_client()
        queue.rpush(
            QUEUE_NAME,
            json.dumps(
                {
                    "application_id": application_id,
                    "candidate_id": payload.candidate_id,
                    "role": payload.role,
                }
            ),
        )
        logger.info("application enqueued id=%s candidate_id=%s", application_id, payload.candidate_id)
    except Exception as exc:  # pragma: no cover - exercised by integration tests
        logger.exception("application creation failed: %s", exc)
        raise HTTPException(status_code=503, detail="application creation unavailable") from exc

    return {"application_id": application_id, "status": "queued"}


@app.get("/internal/queue-stats")
def queue_stats() -> dict[str, object]:
    queue = _redis_client()
    pending_jobs = int(queue.llen(QUEUE_NAME))
    worker_status = _heartbeat_status(queue, "worker")
    scheduler_status = _heartbeat_status(queue, "scheduler")

    with _db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM notifications")
            notifications_count = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM applications WHERE status = 'processed'")
            processed_applications = int(cur.fetchone()[0])

    return {
        "pending_jobs": pending_jobs,
        "worker_status": worker_status,
        "scheduler_status": scheduler_status,
        "notifications_count": notifications_count,
        "processed_applications": processed_applications,
    }
