"""Unit tests for the constrained command gateway."""

from __future__ import annotations

from production_ops_lab.server.command_gateway import CommandGateway
from production_ops_lab.server.world_spec import WORLD_SPEC


def test_aliases_normalize_to_canonical_commands() -> None:
    gateway = CommandGateway()

    status_command = gateway.parse("service status app")
    restart_command = gateway.parse("start worker")

    assert status_command.is_valid is True
    assert status_command.canonical_text == "svc status app"
    assert status_command.category == "investigate"

    assert restart_command.is_valid is True
    assert restart_command.canonical_text == "svc restart worker"
    assert restart_command.category == "fix"


def test_database_url_commands_are_sanitized_for_public_state() -> None:
    gateway = CommandGateway()

    correct_command = gateway.parse("env set app DATABASE_URL=correct")
    custom_command = gateway.parse(
        "env set app DATABASE_URL=postgresql://user:secret@db/internal"
    )

    assert correct_command.reward_key == "env set app database_url=healthy"
    assert correct_command.args["value"] == WORLD_SPEC.healthy_database_url
    assert correct_command.public_text == "env set app DATABASE_URL=correct"

    assert custom_command.reward_key == "env set app database_url=custom"
    assert custom_command.public_text == "env set app DATABASE_URL=<redacted>"
    assert "secret" not in custom_command.public_text


def test_invalid_commands_fail_safely() -> None:
    gateway = CommandGateway()

    invalid = gateway.parse("docker compose ps")

    assert invalid.is_valid is False
    assert invalid.category == "invalid"
    assert invalid.error == (
        "COMMAND ERROR: unsupported command. Use the documented production-ops surface only."
    )
