"""Safe command parsing and normalization for Production Ops Lab."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .constants import DEFAULT_LOG_TAIL
from .world_spec import WORLD_SPEC

CommandCategory = Literal["investigate", "fix", "verify", "invalid"]

_SERVICE_NAMES = set(WORLD_SPEC.service_names)
_HEALTHY_VALUE_ALIASES = {"correct", "healthy", WORLD_SPEC.healthy_database_url.lower()}
_BROKEN_VALUE_ALIASES = {"broken", "bad", WORLD_SPEC.broken_database_url.lower()}


@dataclass(frozen=True, slots=True)
class NormalizedCommand:
    """Internal normalized command representation."""

    category: CommandCategory
    verb: str
    target: str
    args: dict[str, str | int] = field(default_factory=dict)
    canonical_text: str = ""
    public_text: str = ""
    reward_key: str = ""
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.category != "invalid"


class CommandGateway:
    """Parse raw commands into a strict internal representation."""

    def parse(self, raw_command: str) -> NormalizedCommand:
        cleaned = " ".join(raw_command.strip().split())
        if not cleaned:
            return self._invalid("COMMAND ERROR: command is required.")

        aliased = self._normalize_aliases(cleaned)

        svc_match = re.fullmatch(
            r"(?i)svc\s+(status|logs|restart|start)\s+([a-z_]+)(?:\s+--tail\s+(\d+))?",
            aliased,
        )
        if svc_match:
            verb = svc_match.group(1).lower()
            service = svc_match.group(2).lower()
            tail = svc_match.group(3)
            if service not in _SERVICE_NAMES:
                return self._invalid(
                    f"COMMAND ERROR: unknown service {service!r}.",
                    public_text=f"svc {verb} {service}",
                )
            if verb == "start":
                verb = "restart"
            category: CommandCategory = "fix" if verb == "restart" else "investigate"
            canonical = f"svc {verb} {service}"
            args: dict[str, str | int] = {}
            if tail and verb == "logs":
                args["tail"] = int(tail)
                canonical = f"{canonical} --tail {int(tail)}"
            return NormalizedCommand(
                category=category,
                verb=verb,
                target=service,
                args=args,
                canonical_text=canonical,
                public_text=canonical,
                reward_key=f"svc {verb} {service}",
            )

        if re.fullmatch(r"(?i)env\s+show\s+app", aliased):
            return NormalizedCommand(
                category="investigate",
                verb="show",
                target="app",
                canonical_text="env show app",
                public_text="env show app",
                reward_key="env show app",
            )

        env_set_match = re.fullmatch(
            r"(?i)env\s+set\s+app\s+database_url=(.+)",
            aliased,
        )
        if env_set_match:
            raw_value = env_set_match.group(1).strip()
            normalized_value, public_value, reward_key = self._normalize_database_url_value(raw_value)
            return NormalizedCommand(
                category="fix",
                verb="set",
                target="app",
                args={"key": "DATABASE_URL", "value": normalized_value, "raw_value": raw_value},
                canonical_text=f"env set app database_url={normalized_value}",
                public_text=f"env set app DATABASE_URL={public_value}",
                reward_key=reward_key,
            )

        http_match = re.fullmatch(r"(?i)http\s+check\s+(\S+)", aliased)
        if http_match:
            path = http_match.group(1)
            if path != "/health":
                return self._invalid(
                    "COMMAND ERROR: only http check /health is supported in v1.",
                    public_text=f"http check {path}",
                )
            return NormalizedCommand(
                category="verify",
                verb="check",
                target=path,
                canonical_text="http check /health",
                public_text="http check /health",
                reward_key="http check /health",
            )

        if re.fullmatch(r"(?i)queue\s+stats", aliased):
            return NormalizedCommand(
                category="verify",
                verb="stats",
                target="queue",
                canonical_text="queue stats",
                public_text="queue stats",
                reward_key="queue stats",
            )

        if re.fullmatch(r"(?i)lab\s+verify", aliased):
            return NormalizedCommand(
                category="verify",
                verb="verify",
                target="lab",
                canonical_text="lab verify",
                public_text="lab verify",
                reward_key="lab verify",
            )

        return self._invalid(
            "COMMAND ERROR: unsupported command. Use the documented production-ops surface only.",
            public_text=cleaned.lower(),
        )

    def _normalize_aliases(self, command: str) -> str:
        lowered = command.lower()
        if lowered.startswith("service status "):
            return "svc status " + command[len("service status ") :]
        if lowered.startswith("service logs "):
            return "svc logs " + command[len("service logs ") :]
        if lowered.startswith("restart "):
            return "svc restart " + command[len("restart ") :]
        if lowered.startswith("start "):
            return "svc start " + command[len("start ") :]
        return command

    def _normalize_database_url_value(self, value: str) -> tuple[str, str, str]:
        lowered = value.lower()
        if lowered in _HEALTHY_VALUE_ALIASES:
            return WORLD_SPEC.healthy_database_url, "correct", "env set app database_url=healthy"
        if lowered in _BROKEN_VALUE_ALIASES:
            return WORLD_SPEC.broken_database_url, "broken", "env set app database_url=broken"
        return value, "<redacted>", "env set app database_url=custom"

    def _invalid(self, message: str, public_text: str = "") -> NormalizedCommand:
        return NormalizedCommand(
            category="invalid",
            verb="invalid",
            target="",
            canonical_text="",
            public_text=public_text,
            reward_key="invalid",
            error=message,
        )


def default_log_tail() -> int:
    """Expose the default logs tail for backends and tests."""
    return DEFAULT_LOG_TAIL
