"""Tiny scheduler heartbeat process for Production Ops Lab."""

from __future__ import annotations

import os
import time

import redis


REDIS_URL = os.environ["REDIS_URL"]


def main() -> None:
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    while True:
        client.set("scheduler:last_seen", str(time.time()))
        time.sleep(2)


if __name__ == "__main__":
    main()
