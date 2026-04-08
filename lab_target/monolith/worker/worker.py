"""Background worker for Production Ops Lab."""

from __future__ import annotations

import json
import os
import time

import psycopg
import redis


DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
QUEUE_NAME = os.environ.get("QUEUE_NAME", "application_jobs")


def _db_connection():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _process_job(payload: dict[str, object]) -> None:
    application_id = int(payload["application_id"])
    with _db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE applications
                SET status = 'processed', processed_at = NOW()
                WHERE id = %s
                """,
                (application_id,),
            )
            cur.execute(
                """
                INSERT INTO notifications (application_id, message)
                VALUES (%s, %s)
                """,
                (application_id, f"Application {application_id} processed"),
            )


def main() -> None:
    while True:
        try:
            queue = _redis_client()
            queue.set("worker:last_seen", str(time.time()))
            job = queue.blpop(QUEUE_NAME, timeout=1)
            if job is None:
                continue

            payload = json.loads(job[1])
            _process_job(payload)
            queue.set("worker:last_seen", str(time.time()))
            print(f"WORKER LOG: processed application_id={payload['application_id']}", flush=True)
        except Exception as exc:  # pragma: no cover - exercised by integration tests
            print(f"WORKER LOG: processing failed: {exc}", flush=True)
            time.sleep(1.5)


if __name__ == "__main__":
    main()
