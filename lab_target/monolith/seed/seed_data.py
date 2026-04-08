"""Deterministically reseed the monolith database and queue state."""

from __future__ import annotations

import os

import psycopg
import redis


DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
QUEUE_NAME = os.environ.get("QUEUE_NAME", "application_jobs")


def main() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE notifications, applications, candidates RESTART IDENTITY CASCADE")
            cur.execute(
                """
                INSERT INTO candidates (name, email)
                VALUES
                    ('Seed Candidate', 'seed@example.com'),
                    ('Asha Ops', 'asha.ops@example.com')
                """
            )

    queue = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    queue.delete(QUEUE_NAME, "worker:last_seen", "scheduler:last_seen")


if __name__ == "__main__":
    main()
