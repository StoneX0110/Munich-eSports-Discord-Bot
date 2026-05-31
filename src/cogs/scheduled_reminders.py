"""
Helper functions and slash commands for recurring role-ping reminders.
"""

import asyncio
import logging
from datetime import date, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DEPARTMENT_HEAD_ROLE_ID, GUILD_ID, REMINDERS_FILE, STAFF_ROLE_ID
from utils.scheduled import (
    BERLIN_TZ,
    JsonScheduleStore,
    WEEKDAYS,
    member_has_any_role,
    normalize_weekday,
    now_berlin_iso,
    weekday_choices,
    weekday_name,
)

logger = logging.getLogger("munich_esports_bot.scheduled_reminders")

DEFAULT_REMINDERS_DATA = {
    "next_scheduled_reminder_id": 1,
    "scheduled_reminders": {},
}

SCHEDULED_REMINDER_MANAGER_ROLE_IDS = {DEPARTMENT_HEAD_ROLE_ID, STAFF_ROLE_ID}
MAX_DISCORD_MESSAGE_LENGTH = 2000
_reminders_data_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _default_reminders_data() -> dict:
    return {
        "next_scheduled_reminder_id": DEFAULT_REMINDERS_DATA["next_scheduled_reminder_id"],
        "scheduled_reminders": {},
    }


def _reminders_store() -> JsonScheduleStore:
    return JsonScheduleStore(
        file_path=REMINDERS_FILE,
        default_factory=_default_reminders_data,
        logger=logger,
        corrupt_log_message="Corrupt scheduled reminders file detected. Falling back to default empty structure.",
        read_error_log_message="Failed to read scheduled reminders file due to an I/O error.",
        write_error_log_message="Failed to save scheduled reminders data.",
    )


def _load_reminders_data() -> dict:
    """
    Loads scheduled reminder configuration from the JSON storage file.

    If the file does not exist, returns the default structure.
    If the file is corrupt, logs the error and returns the default structure.
    """
    return _reminders_store().load()


def _save_reminders_data(data: dict) -> None:
    """Saves the scheduled reminders configuration to the JSON storage file."""
    _reminders_store().save(data)


# ---------------------------------------------------------------------------
# Authorization, validation, and formatting helpers
# ---------------------------------------------------------------------------

def _can_manage_scheduled_reminders(member: discord.Member) -> bool:
    """Check whether a Discord member can manage scheduled reminders."""
    return member_has_any_role(member, SCHEDULED_REMINDER_MANAGER_ROLE_IDS)


def _normalize_weekday(day: str) -> str | None:
    """Normalize and validate a German weekday name, case-insensitively."""
    return normalize_weekday(day)


def _now_iso() -> str:
    return now_berlin_iso()


def _weekday_name(day: date) -> str:
    return weekday_name(day)


def _format_schedule(reminder: dict) -> str:
    return f"{reminder['weekday']} um {reminder['hour']:02d}:00"


def _format_last_sent(reminder: dict) -> str:
    return reminder.get("last_sent_date") or "nie"


def _message_preview(message: str, limit: int = 160) -> str:
    preview = " ".join(message.split())
    if len(preview) <= limit:
        return preview
    return preview[: limit - 3] + "..."


def _build_reminder_content(role_mention: str, message: str) -> str:
    return f"{role_mention}\n{message}"


def _allowed_mentions_for(role: discord.Role) -> discord.AllowedMentions:
    return discord.AllowedMentions(
        everyone=False,
        users=False,
        roles=[role],
        replied_user=False,
    )


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------

class ScheduledReminderCog(commands.Cog):
    """Slash command group for managing recurring message reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    reminder_group = app_commands.Group(
        name="scheduled-reminder",
        description="Verwaltung von wiederkehrenden Reminder-Nachrichten",
        guild_ids=[GUILD_ID],
    )

    # -----------------------------------------------------------------------
    # /scheduled-reminder list
    # -----------------------------------------------------------------------
    @reminder_group.command(
        name="list",
        description="Zeigt alle eingerichteten wiederkehrenden Reminder an",
    )
    async def reminder_list(self, interaction: discord.Interaction):
        if not _can_manage_scheduled_reminders(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Reminder verwalten.",
                ephemeral=True,
            )
            return

        async with _reminders_data_lock:
            data = _load_reminders_data()
            reminders = data.get("scheduled_reminders", {})

            if not reminders:
                await interaction.response.send_message(
                    "Es sind keine wiederkehrenden Reminder eingerichtet.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="📋 Wiederkehrende Reminder",
                color=discord.Color.blue(),
            )
            for reminder_id, reminder in reminders.items():
                role_mention = f"<@&{reminder['role_id']}>"
                channel_mention = f"<#{reminder['channel_id']}>"
                embed.add_field(
                    name=f"#{reminder_id}",
                    value=(
                        f"**Rolle:** {role_mention}\n"
                        f"**Kanal:** {channel_mention}\n"
                        f"**Zeitplan:** {_format_schedule(reminder)}\n"
                        f"**Zuletzt gesendet:** {_format_last_sent(reminder)}\n"
                        f"**Nachricht:** {_message_preview(reminder['message'])}"
                    ),
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /scheduled-reminder delete
    # -----------------------------------------------------------------------
    @reminder_group.command(
        name="delete",
        description="Löscht einen wiederkehrenden Reminder",
    )
    @app_commands.describe(reminder_id="ID des wiederkehrenden Reminders")
    async def reminder_delete(self, interaction: discord.Interaction, reminder_id: int):
        if not _can_manage_scheduled_reminders(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Reminder verwalten.",
                ephemeral=True,
            )
            return

        reminder_key = str(reminder_id)
        async with _reminders_data_lock:
            data = _load_reminders_data()
            if reminder_key not in data.get("scheduled_reminders", {}):
                await interaction.response.send_message(
                    f"❌ Wiederkehrender Reminder #{reminder_id} nicht gefunden.",
                    ephemeral=True,
                )
            else:
                deleted_reminder = data["scheduled_reminders"][reminder_key]
                del data["scheduled_reminders"][reminder_key]
                _save_reminders_data(data)
                logger.info(
                    "Deleted scheduled reminder #%s by user %s from channel %s for role %s.",
                    reminder_id,
                    interaction.user.id,
                    deleted_reminder["channel_id"],
                    deleted_reminder["role_id"],
                )
                await interaction.response.send_message(
                    f"✅ Wiederkehrender Reminder #{reminder_id} wurde gelöscht.",
                    ephemeral=True,
                )

    # -----------------------------------------------------------------------
    # /scheduled-reminder create
    # -----------------------------------------------------------------------
    @reminder_group.command(
        name="create",
        description="Erstellt einen neuen wiederkehrenden Reminder",
    )
    @app_commands.describe(
        role="Die Rolle, die für den Reminder erwähnt wird",
        weekday="Wochentag, an dem der Reminder automatisch gesendet wird",
        hour="Uhrzeit für den Reminder im 24h-Format (0-23)",
        message="Nachricht, die unter dem Rollen-Ping gesendet wird",
    )
    async def reminder_create(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        weekday: str,
        hour: int,
        message: str,
    ):
        if not _can_manage_scheduled_reminders(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Reminder verwalten.",
                ephemeral=True,
            )
            return

        normalized_weekday = _normalize_weekday(weekday)
        if normalized_weekday is None:
            valid_list = ", ".join(WEEKDAYS)
            await interaction.response.send_message(
                f"❌ Ungültiger Reminder-Wochentag: `{weekday}`. Gültige Werte: {valid_list}",
                ephemeral=True,
            )
            return

        if hour < 0 or hour > 23:
            await interaction.response.send_message(
                "❌ Ungültige Reminder-Uhrzeit. Bitte gib eine Stunde von `0` bis `23` an.",
                ephemeral=True,
            )
            return

        message = message.strip()
        if not message:
            await interaction.response.send_message(
                "❌ Nachricht darf nicht leer sein.",
                ephemeral=True,
            )
            return

        content = _build_reminder_content(role.mention, message)
        if len(content) > MAX_DISCORD_MESSAGE_LENGTH:
            await interaction.response.send_message(
                "❌ Nachricht ist zu lang. Rollen-Ping und Nachricht dürfen zusammen "
                f"maximal {MAX_DISCORD_MESSAGE_LENGTH} Zeichen haben.",
                ephemeral=True,
            )
            return

        async with _reminders_data_lock:
            data = _load_reminders_data()
            reminder_id = str(data["next_scheduled_reminder_id"])
            data["next_scheduled_reminder_id"] += 1

            data["scheduled_reminders"][reminder_id] = {
                "channel_id": interaction.channel_id,
                "role_id": role.id,
                "weekday": normalized_weekday,
                "hour": hour,
                "message": message,
                "created_by": interaction.user.id,
                "created_at": _now_iso(),
                "last_sent_date": None,
                "last_sent_at": None,
            }
            _save_reminders_data(data)
            logger.info(
                "Created scheduled reminder #%s by user %s in channel %s for role %s; "
                "weekday=%s, hour=%s.",
                reminder_id,
                interaction.user.id,
                interaction.channel_id,
                role.id,
                normalized_weekday,
                hour,
            )

        await interaction.response.send_message(
            f"✅ Wiederkehrender Reminder #{reminder_id} erstellt!\n"
            f"**Rolle:** {role.mention}\n"
            f"**Kanal:** <#{interaction.channel_id}>\n"
            f"**Zeitplan:** {normalized_weekday} um {hour:02d}:00\n"
            f"**Nachricht:** {_message_preview(message)}",
            ephemeral=True,
        )

    @reminder_create.autocomplete("weekday")
    async def weekday_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return weekday_choices(current)

    # -----------------------------------------------------------------------
    # Lifecycle & background scheduling
    # -----------------------------------------------------------------------
    async def cog_load(self):
        if not self.scheduled_reminder_loop.is_running():
            self.scheduled_reminder_loop.start()
        logger.info("Scheduled reminders cog loaded; background loop started.")

    async def cog_unload(self):
        self.scheduled_reminder_loop.cancel()
        logger.info("Scheduled reminders cog unloaded; background loop stopped.")

    @tasks.loop(hours=1)
    async def scheduled_reminder_loop(self):
        now = datetime.now(BERLIN_TZ)
        logger.info(
            "Scheduled reminders tick. Hour: %d, Weekday: %s",
            now.hour,
            _weekday_name(now.date()),
        )
        await self._handle_sending(now.date(), now.hour)

    @scheduled_reminder_loop.before_loop
    async def before_scheduled_reminder_loop(self):
        await self.bot.wait_until_ready()

    async def _handle_sending(
        self,
        today: date,
        current_hour: int | None = None,
        reminder_id: str | None = None,
        force: bool = False,
    ) -> bool:
        today_weekday = _weekday_name(today)
        today_str = today.isoformat()

        async with _reminders_data_lock:
            data = _load_reminders_data()
            due_reminders = []

            for current_reminder_id, reminder in data.get("scheduled_reminders", {}).items():
                if reminder_id is not None and current_reminder_id != reminder_id:
                    continue

                if not force:
                    if reminder["weekday"] != today_weekday:
                        continue
                    if reminder["hour"] != current_hour:
                        continue
                    if reminder.get("last_sent_date") == today_str:
                        continue

                due_reminders.append((current_reminder_id, reminder.copy()))

        sent_reminders = []
        for current_reminder_id, reminder in due_reminders:
            channel = self.bot.get_channel(reminder["channel_id"])
            guild = self.bot.get_guild(GUILD_ID)
            role = guild.get_role(reminder["role_id"]) if guild else None

            if not channel or not role:
                logger.warning(
                    "Channel or role not found for scheduled reminder #%s; channel_id=%s, role_id=%s.",
                    current_reminder_id,
                    reminder["channel_id"],
                    reminder["role_id"],
                )
                continue

            role_mention = getattr(role, "mention", f"<@&{reminder['role_id']}>")
            content = _build_reminder_content(role_mention, reminder["message"])

            try:
                await channel.send(
                    content,
                    allowed_mentions=_allowed_mentions_for(role),
                )
                sent_at = _now_iso()
                sent_reminders.append((current_reminder_id, sent_at))
                logger.info(
                    "Sent scheduled reminder #%s to channel %s for role %s.",
                    current_reminder_id,
                    reminder["channel_id"],
                    reminder["role_id"],
                )
            except discord.HTTPException:
                logger.exception("Failed to send scheduled reminder #%s.", current_reminder_id)

        if sent_reminders:
            async with _reminders_data_lock:
                data = _load_reminders_data()
                reminders = data.get("scheduled_reminders", {})
                changed = False

                for current_reminder_id, sent_at in sent_reminders:
                    reminder = reminders.get(current_reminder_id)
                    if reminder is None:
                        continue

                    reminder["last_sent_date"] = today_str
                    reminder["last_sent_at"] = sent_at
                    changed = True

                if changed:
                    _save_reminders_data(data)
                    return True

        return False

    # -----------------------------------------------------------------------
    # Developer verification command
    # -----------------------------------------------------------------------
    @reminder_group.command(
        name="trigger-send",
        description="[DEV] Sendet einen geplanten Reminder sofort",
    )
    @app_commands.describe(reminder_id="ID des wiederkehrenden Reminders")
    async def trigger_send(self, interaction: discord.Interaction, reminder_id: int):
        if not _can_manage_scheduled_reminders(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Reminder verwalten.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        reminder_key = str(reminder_id)
        async with _reminders_data_lock:
            data = _load_reminders_data()
            reminder_missing = reminder_key not in data["scheduled_reminders"]

        if reminder_missing:
            await interaction.followup.send(f"❌ Reminder #{reminder_id} nicht gefunden.")
            return

        now = datetime.now(BERLIN_TZ)
        sent = await self._handle_sending(
            now.date(),
            now.hour,
            reminder_id=reminder_key,
            force=True,
        )

        if sent:
            await interaction.followup.send(
                f"✅ Trigger-Send für Reminder #{reminder_id} ausgeführt!"
            )
        else:
            await interaction.followup.send(
                f"⚠️ Trigger-Send für Reminder #{reminder_id} ausgeführt, "
                "aber keine Nachricht wurde gesendet."
            )


# ---------------------------------------------------------------------------
# Extension setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScheduledReminderCog(bot))
