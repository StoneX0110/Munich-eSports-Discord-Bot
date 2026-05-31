"""
Shared helpers for scheduled Discord cogs.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
BERLIN_TZ = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True)
class JsonScheduleStore:
    """JSON-backed storage helper for scheduled cog configuration."""

    file_path: Path
    default_factory: Callable[[], dict[str, Any]]
    logger: logging.Logger
    corrupt_log_message: str
    read_error_log_message: str
    write_error_log_message: str

    def load(self) -> dict[str, Any]:
        """Load JSON data, returning a fresh default for missing or corrupt files."""
        if not self.file_path.exists():
            return self.default_factory()
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.logger.exception(self.corrupt_log_message)
            return self.default_factory()
        except OSError:
            self.logger.exception(self.read_error_log_message)
            raise

    def save(self, data: dict[str, Any]) -> None:
        """Save JSON data with stable formatting."""
        try:
            self.file_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            self.logger.exception(self.write_error_log_message)
            raise


def normalize_weekday(day: str) -> str | None:
    """Normalize and validate a German weekday name, case-insensitively."""
    for valid in WEEKDAYS:
        if day.lower() == valid.lower():
            return valid
    return None


def weekday_name(day: date) -> str:
    return WEEKDAYS[day.weekday()]


def now_berlin_iso() -> str:
    return datetime.now(BERLIN_TZ).isoformat()


def member_has_any_role(member: discord.Member, role_ids: set[int]) -> bool:
    """Check whether a Discord member has any role from the provided ID set."""
    return any(role.id in role_ids for role in member.roles)


def weekday_choices(current: str) -> list[app_commands.Choice[str]]:
    """Return Discord autocomplete choices for German weekdays."""
    current = current.lower()
    return [
        app_commands.Choice(name=day, value=day)
        for day in WEEKDAYS
        if current in day.lower()
    ][:25]
