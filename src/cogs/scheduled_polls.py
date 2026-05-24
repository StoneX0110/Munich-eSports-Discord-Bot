"""
Helper functions for managing scheduled poll configuration and persistency.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DEPARTMENT_HEAD_ROLE_ID, GUILD_ID, POLLS_FILE, STAFF_ROLE_ID

logger = logging.getLogger("munich_esports_bot.scheduled_polls")

DEFAULT_POLLS_DATA = {
    "next_scheduled_poll_id": 1,
    "scheduled_polls": {}
}

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
POLLS_FLUSH_INTERVAL_SECONDS = 60
SCHEDULED_POLL_MANAGER_ROLE_IDS = {DEPARTMENT_HEAD_ROLE_ID, STAFF_ROLE_ID}
_polls_data_lock = asyncio.Lock()
_polls_data_cache: dict | None = None
_polls_data_dirty = False


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_polls_data() -> dict:
    """
    Loads scheduled polls configuration from the JSON storage file.

    If the file does not exist, returns the default structure.
    If the file is corrupt or unreadable, logs the error and returns the default structure.
    """
    if not POLLS_FILE.exists():
        return _default_polls_data()
    try:
        return json.loads(POLLS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.exception("Corrupt scheduled polls file detected. Falling back to default empty structure.")
        return _default_polls_data()
    except OSError:
        logger.exception("Failed to read scheduled polls file due to an I/O error.")
        raise  # Raising prevents silent overwrite/data loss on subsequent save


def _default_polls_data() -> dict:
    return {
        "next_scheduled_poll_id": DEFAULT_POLLS_DATA["next_scheduled_poll_id"],
        "scheduled_polls": {},
    }


def _save_polls_data(data: dict) -> None:
    """
    Saves the scheduled polls configuration to the JSON storage file.
    """
    try:
        POLLS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.exception("Failed to save scheduled polls data.")
        raise


def _get_polls_data() -> dict:
    """Return cached scheduled poll data, loading it from disk once."""
    global _polls_data_cache
    if _polls_data_cache is None:
        _polls_data_cache = _load_polls_data()
    return _polls_data_cache


def _mark_polls_data_dirty() -> None:
    """Mark cached scheduled poll data as needing a periodic save."""
    global _polls_data_dirty
    _polls_data_dirty = True


def _flush_polls_data(force: bool = False) -> bool:
    """Persist cached scheduled poll data if dirty or force is requested."""
    global _polls_data_dirty
    if _polls_data_cache is None:
        return False
    if not force and not _polls_data_dirty:
        return False

    _save_polls_data(_polls_data_cache)
    _polls_data_dirty = False
    return True


def _reset_polls_data_cache() -> None:
    """Reset cached scheduled poll data. Intended for tests."""
    global _polls_data_cache, _polls_data_dirty
    _polls_data_cache = None
    _polls_data_dirty = False


# ---------------------------------------------------------------------------
# Authorization & validation helpers
# ---------------------------------------------------------------------------

def _can_manage_scheduled_polls(member: discord.Member) -> bool:
    """Check whether a Discord member can manage scheduled polls."""
    return any(r.id in SCHEDULED_POLL_MANAGER_ROLE_IDS for r in member.roles)


def _normalize_weekday(day: str) -> str | None:
    """Normalize and validate a German weekday name, case-insensitively.

    Returns the capitalized weekday name if valid, otherwise None.
    """
    for valid in WEEKDAYS:
        if day.lower() == valid.lower():
            return valid
    return None


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Europe/Berlin")).isoformat()


def _weekday_name(day: date) -> str:
    return WEEKDAYS[day.weekday()]


def _format_reminder_schedule(poll: dict) -> str:
    reminder_weekday = poll.get("reminder_weekday")
    reminder_hour = poll.get("reminder_hour")
    if reminder_weekday is None or reminder_hour is None:
        return "keine"
    return f"{reminder_weekday} um {reminder_hour:02d}:00"


# ---------------------------------------------------------------------------
# Embed & interactive view helpers
# ---------------------------------------------------------------------------


def _get_target_dates(post_date: date) -> tuple[date, list[str]]:
    """Return the Monday after the poll week and formatted day strings for that week."""
    days_to_monday = (0 - post_date.weekday()) % 7
    if days_to_monday == 0:
        days_to_monday = 7  # Always starting the week AFTER the poll
    target_monday = post_date + timedelta(days=days_to_monday)
    days_strs = []
    for i in range(7):
        d = target_monday + timedelta(days=i)
        days_strs.append(d.strftime("%d.%m."))
    return target_monday, days_strs


def _build_poll_embed(role_id: str | int, target_week_start_str: str, responses: dict) -> discord.Embed:
    start_date = date.fromisoformat(target_week_start_str)
    _, days_strs = _get_target_dates(start_date - timedelta(days=7))

    tallies = {day: [] for day in WEEKDAYS}
    no_time_list = []
    for uid, days in responses.items():
        if "Keine Zeit" in days:
            no_time_list.append(uid)
        else:
            for d in days:
                if d in tallies:
                    tallies[d].append(uid)

    lines = [
        f"**📊 Termine - Woche ab {start_date.strftime('%d.%m.%Y')}**",
        "",
    ]

    for idx, day in enumerate(WEEKDAYS):
        day_str = days_strs[idx]
        users = tallies[day]
        count = len(users)
        lines.append(f"📅 **{day} ({day_str}) [{count}]:**")
        if users:
            lines.append(", ".join(f"<@{u}>" for u in users))
        else:
            lines.append("• *Keiner*")
        lines.append("")

    lines.append(f"❌ **Keine Zeit [{len(no_time_list)}]:**")
    if no_time_list:
        lines.append(", ".join(f"<@{u}>" for u in no_time_list))
    else:
        lines.append("• *Keiner*")

    return discord.Embed(
        description="\n".join(lines),
        color=discord.Color.blue(),
    )


class ScheduledPollView(discord.ui.View):
    """Persistent button view for toggling availability on a scheduled poll."""

    def __init__(self, poll_id: str):
        super().__init__(timeout=None)
        self.poll_id = poll_id

        for day in WEEKDAYS:
            btn = discord.ui.Button(
                label=day[:2],
                style=discord.ButtonStyle.primary,
                custom_id=f"sp_{poll_id}_{day}",
            )
            btn.callback = self.make_callback(day)
            self.add_item(btn)

        no_time_btn = discord.ui.Button(
            label="Keine Zeit",
            style=discord.ButtonStyle.danger,
            custom_id=f"sp_{poll_id}_NoTime",
        )
        no_time_btn.callback = self.make_callback("Keine Zeit")
        self.add_item(no_time_btn)

    def make_callback(self, day_value: str):
        async def callback(interaction: discord.Interaction):
            user_id = str(interaction.user.id)

            async with _polls_data_lock:
                data = _get_polls_data()
                poll = data["scheduled_polls"].get(self.poll_id)
                if not poll or not poll.get("active_instance"):
                    poll_inactive = True
                    unauthorized = False
                    embed = None
                elif not any(r.id == poll["role_id"] for r in interaction.user.roles):
                    poll_inactive = False
                    unauthorized = True
                    embed = None
                else:
                    poll_inactive = False
                    unauthorized = False
                    instance = poll["active_instance"]
                    responses = instance["responses"]

                    user_res = responses.get(user_id, [])

                    if day_value == "Keine Zeit":
                        if "Keine Zeit" in user_res:
                            user_res = []
                        else:
                            user_res = ["Keine Zeit"]
                    else:
                        if "Keine Zeit" in user_res:
                            user_res = []
                        if day_value in user_res:
                            user_res.remove(day_value)
                        else:
                            user_res.append(day_value)

                    if user_res:
                        responses[user_id] = user_res
                    else:
                        responses.pop(user_id, None)

                    _mark_polls_data_dirty()
                    embed = _build_poll_embed(
                        poll["role_id"],
                        instance["target_week_start"],
                        responses,
                    )

            if poll_inactive:
                await interaction.response.send_message(
                    "❌ Diese Umfrage ist nicht mehr aktiv.",
                    ephemeral=True,
                )
                return

            if unauthorized:
                await interaction.response.send_message(
                    "❌ Nur Mitglieder der Umfrage-Rolle können abstimmen.",
                    ephemeral=True,
                )
                return

            await interaction.response.edit_message(embed=embed)

        return callback


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------

class ScheduledPollCog(commands.Cog):
    """Slash command group for managing recurring scheduled polls."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    poll_group = app_commands.Group(
        name="scheduled-poll",
        description="Verwaltung von wiederkehrenden Umfragen",
        guild_ids=[GUILD_ID],
    )

    # -----------------------------------------------------------------------
    # /scheduled-poll list
    # -----------------------------------------------------------------------
    @poll_group.command(
        name="list",
        description="Zeigt alle eingerichteten wiederkehrenden Umfragen an",
    )
    async def poll_list(self, interaction: discord.Interaction):
        if not _can_manage_scheduled_polls(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Umfragen verwalten.",
                ephemeral=True,
            )
            return

        async with _polls_data_lock:
            data = _get_polls_data()
            polls = data.get("scheduled_polls", {})

            if not polls:
                await interaction.response.send_message(
                    "Es sind keine wiederkehrenden Umfragen eingerichtet.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="📋 Wiederkehrende Umfragen",
                color=discord.Color.blue(),
            )
            for poll_id, poll in polls.items():
                role_mention = f"<@&{poll['role_id']}>"
                channel_mention = f"<#{poll['channel_id']}>"
                embed.add_field(
                    name=f"#{poll_id}",
                    value=(
                        f"**Rolle:** {role_mention}\n"
                        f"**Kanal:** {channel_mention}\n"
                        f"**Wochentag:** {poll['weekday']}\n"
                        f"**Reminder:** {_format_reminder_schedule(poll)}"
                    ),
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------------------------------------------
    # /scheduled-poll delete
    # -----------------------------------------------------------------------
    @poll_group.command(
        name="delete",
        description="Löscht eine wiederkehrende Umfrage",
    )
    @app_commands.describe(poll_id="ID der wiederkehrenden Umfrage")
    async def poll_delete(self, interaction: discord.Interaction, poll_id: int):
        if not _can_manage_scheduled_polls(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Umfragen verwalten.",
                ephemeral=True,
            )
            return

        poll_key = str(poll_id)
        async with _polls_data_lock:
            data = _get_polls_data()
            if poll_key not in data.get("scheduled_polls", {}):
                poll_missing = True
            else:
                poll_missing = False
                del data["scheduled_polls"][poll_key]
                _flush_polls_data(force=True)

        if poll_missing:
            await interaction.response.send_message(
                f"❌ Wiederkehrende Umfrage #{poll_id} nicht gefunden.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Wiederkehrende Umfrage #{poll_id} wurde gelöscht.",
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /scheduled-poll create
    # -----------------------------------------------------------------------
    @poll_group.command(
        name="create",
        description="Erstellt eine neue wiederkehrende Umfrage",
    )
    @app_commands.describe(
        role="Die Rolle, die für die Umfrage erwähnt wird",
        weekday="Wochentag der Umfrage (deutsch, z.B. Montag, Dienstag, ...)",
        reminder_weekday="Optionaler Wochentag für Reminder-Pings",
        reminder_hour="Optionale Uhrzeit für Reminder-Pings (0-23)",
    )
    async def poll_create(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        weekday: str,
        reminder_weekday: str | None = None,
        reminder_hour: int | None = None,
    ):
        if not _can_manage_scheduled_polls(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Umfragen verwalten.",
                ephemeral=True,
            )
            return

        normalized = _normalize_weekday(weekday)
        if normalized is None:
            valid_list = ", ".join(WEEKDAYS)
            await interaction.response.send_message(
                f"❌ Ungültiger Wochentag: `{weekday}`. Gültige Werte: {valid_list}",
                ephemeral=True,
            )
            return

        reminder_weekday = reminder_weekday.strip() if reminder_weekday else None
        has_reminder_weekday = reminder_weekday is not None
        has_reminder_hour = reminder_hour is not None
        if has_reminder_weekday != has_reminder_hour:
            await interaction.response.send_message(
                "❌ Reminder-Wochentag und Reminder-Uhrzeit müssen gemeinsam angegeben werden.",
                ephemeral=True,
            )
            return

        normalized_reminder_weekday = None
        if has_reminder_weekday:
            normalized_reminder_weekday = _normalize_weekday(reminder_weekday)
            if normalized_reminder_weekday is None:
                valid_list = ", ".join(WEEKDAYS)
                await interaction.response.send_message(
                    "❌ Ungültiger Reminder-Wochentag: "
                    f"`{reminder_weekday}`. Gültige Werte: {valid_list}",
                    ephemeral=True,
                )
                return

            if reminder_hour < 0 or reminder_hour > 23:
                await interaction.response.send_message(
                    "❌ Ungültige Reminder-Uhrzeit. Bitte gib eine Stunde von `0` bis `23` an.",
                    ephemeral=True,
                )
                return

        async with _polls_data_lock:
            data = _get_polls_data()
            poll_id = str(data["next_scheduled_poll_id"])
            data["next_scheduled_poll_id"] += 1

            data["scheduled_polls"][poll_id] = {
                "channel_id": interaction.channel_id,
                "role_id": role.id,
                "weekday": normalized,
                "reminder_weekday": normalized_reminder_weekday,
                "reminder_hour": reminder_hour,
                "created_by": interaction.user.id,
                "created_at": _now_iso(),
                "active_instance": None,
            }
            _flush_polls_data(force=True)

        reminder_schedule = _format_reminder_schedule(
            {
                "reminder_weekday": normalized_reminder_weekday,
                "reminder_hour": reminder_hour,
            }
        )
        await interaction.response.send_message(
            f"✅ Wiederkehrende Umfrage #{poll_id} erstellt!\n"
            f"**Rolle:** {role.mention}\n"
            f"**Kanal:** <#{interaction.channel_id}>\n"
            f"**Wochentag:** {normalized}\n"
            f"**Reminder:** {reminder_schedule}",
            ephemeral=True,
        )

    @poll_create.autocomplete("weekday")
    async def weekday_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=day, value=day)
            for day in WEEKDAYS
            if current.lower() in day.lower()
        ][:25]

    @poll_create.autocomplete("reminder_weekday")
    async def reminder_weekday_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=day, value=day)
            for day in WEEKDAYS
            if current.lower() in day.lower()
        ][:25]

    # -----------------------------------------------------------------------
    # Lifecycle & background scheduling
    # -----------------------------------------------------------------------
    async def cog_load(self):
        async with _polls_data_lock:
            data = _get_polls_data()
            active_poll_ids = [
                poll_id
                for poll_id, poll in data.get("scheduled_polls", {}).items()
                if poll.get("active_instance")
            ]

        for poll_id in active_poll_ids:
            self.bot.add_view(ScheduledPollView(poll_id))

        if not self.scheduled_poll_loop.is_running():
            self.scheduled_poll_loop.start()
        if not self.scheduled_poll_flush_loop.is_running():
            self.scheduled_poll_flush_loop.start()
        logger.info("Scheduled polls cog loaded; background loops started.")

    async def cog_unload(self):
        self.scheduled_poll_loop.cancel()
        self.scheduled_poll_flush_loop.cancel()
        async with _polls_data_lock:
            _flush_polls_data()
        logger.info("Scheduled polls cog unloaded; background loops stopped.")

    @tasks.loop(seconds=POLLS_FLUSH_INTERVAL_SECONDS)
    async def scheduled_poll_flush_loop(self):
        async with _polls_data_lock:
            flushed = _flush_polls_data()
        if flushed:
            logger.info("Flushed dirty scheduled poll vote data.")

    @tasks.loop(hours=1)
    async def scheduled_poll_loop(self):
        now = datetime.now(ZoneInfo("Europe/Berlin"))
        logger.info(
            "Scheduled polls tick. Hour: %d, Weekday: %s",
            now.hour,
            _weekday_name(now.date()),
        )
        if now.hour == 8:
            await self._handle_posting(now.date())
        await self._handle_reminders(now.date(), now.hour)

    async def _handle_posting(
        self,
        today: date,
        poll_id: str | None = None,
        force: bool = False,
    ):
        async with _polls_data_lock:
            data = _get_polls_data()
            changed = False
            today_weekday = _weekday_name(today)

            for current_poll_id, poll in data.get("scheduled_polls", {}).items():
                if poll_id is not None and current_poll_id != poll_id:
                    continue

                if not force and poll["weekday"] != today_weekday:
                    continue

                target_monday, _ = _get_target_dates(today)
                target_monday_str = target_monday.isoformat()
                if (
                    not force
                    and poll.get("active_instance")
                    and poll["active_instance"].get("target_week_start") == target_monday_str
                ):
                    continue

                channel = self.bot.get_channel(poll["channel_id"])
                if not channel:
                    logger.warning(
                        "Channel %s not found for scheduled poll #%s.",
                        poll["channel_id"],
                        current_poll_id,
                    )
                    continue

                if poll.get("active_instance"):
                    try:
                        old_inst = poll["active_instance"]
                        old_msg = await channel.fetch_message(old_inst["message_id"])
                        old_embed = old_msg.embeds[0]
                        old_embed.title = "🗳️ (Geschlossen) " + (old_embed.title or "")
                        old_embed.color = discord.Color.light_grey()
                        await old_msg.edit(embed=old_embed, view=None)
                    except Exception:
                        logger.warning(
                            "Failed to archive previous poll message %s.",
                            poll["active_instance"]["message_id"],
                            exc_info=True,
                        )

                role_id = poll["role_id"]

                embed = _build_poll_embed(role_id, target_monday_str, {})
                view = ScheduledPollView(current_poll_id)

                try:
                    msg = await channel.send(
                        content=f"<@&{role_id}>",
                        embed=embed,
                        view=view,
                    )
                    poll["active_instance"] = {
                        "message_id": msg.id,
                        "posted_at": _now_iso(),
                        "target_week_start": target_monday_str,
                        "reminded": False,
                        "responses": {},
                    }
                    changed = True
                    logger.info(
                        "Posted scheduled poll #%s to channel %s.",
                        current_poll_id,
                        channel.id,
                    )
                except discord.HTTPException:
                    logger.exception("Failed to post scheduled poll #%s.", current_poll_id)

            if changed:
                _flush_polls_data(force=True)

    async def _handle_reminders(
        self,
        today: date,
        current_hour: int | None = None,
        poll_id: str | None = None,
        force: bool = False,
    ):
        async with _polls_data_lock:
            data = _get_polls_data()
            changed = False
            today_weekday = _weekday_name(today)

            for current_poll_id, poll in data.get("scheduled_polls", {}).items():
                if poll_id is not None and current_poll_id != poll_id:
                    continue

                if not force:
                    if poll.get("reminder_weekday") != today_weekday:
                        continue
                    if poll.get("reminder_hour") != current_hour:
                        continue

                instance = poll.get("active_instance")
                if not instance or instance.get("reminded"):
                    continue

                channel = self.bot.get_channel(poll["channel_id"])
                guild = self.bot.get_guild(GUILD_ID)
                role = guild.get_role(poll["role_id"]) if guild else None

                if not channel or not role:
                    continue

                voted_users = set(instance["responses"].keys())
                non_voters = []
                for member in role.members:
                    if member.bot:
                        continue
                    if str(member.id) not in voted_users:
                        non_voters.append(member.mention)

                if not non_voters:
                    continue

                pings = " ".join(non_voters)
                reminder_msg = (
                    f"⚠️ **Erinnerung!** {pings}\n"
                    f"Bitte tragt euch noch in die Umfrage für nächste Woche ein! 🗳️\n"
                    f"Zur Umfrage: https://discord.com/channels/{GUILD_ID}/"
                    f"{poll['channel_id']}/{instance['message_id']}"
                )
                try:
                    await channel.send(reminder_msg)
                    instance["reminded"] = True
                    changed = True
                    logger.info("Sent reminder for poll #%s.", current_poll_id)
                except discord.HTTPException:
                    logger.exception(
                        "Failed to send reminder for poll #%s.", current_poll_id
                    )

            if changed:
                _flush_polls_data(force=True)

    # -----------------------------------------------------------------------
    # Developer verification commands
    # -----------------------------------------------------------------------
    @poll_group.command(
        name="trigger-post",
        description="[DEV] Postet eine geplante Umfrage sofort",
    )
    @app_commands.describe(poll_id="ID der wiederkehrenden Umfrage")
    async def trigger_post(self, interaction: discord.Interaction, poll_id: int):
        if not _can_manage_scheduled_polls(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Umfragen verwalten.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        poll_key = str(poll_id)
        async with _polls_data_lock:
            data = _get_polls_data()
            if poll_key not in data["scheduled_polls"]:
                poll_missing = True
            else:
                poll_missing = False

        if poll_missing:
            await interaction.followup.send(f"❌ Umfrage #{poll_id} nicht gefunden.")
            return

        await self._handle_posting(
            datetime.now(ZoneInfo("Europe/Berlin")).date(),
            poll_id=poll_key,
            force=True,
        )

        await interaction.followup.send(
            f"✅ Trigger-Post für Umfrage #{poll_id} ausgeführt!"
        )

    @poll_group.command(
        name="trigger-reminder",
        description="[DEV] Löst Erinnerungs-Pings sofort aus",
    )
    @app_commands.describe(poll_id="ID der wiederkehrenden Umfrage")
    async def trigger_reminder(self, interaction: discord.Interaction, poll_id: int):
        if not _can_manage_scheduled_polls(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Umfragen verwalten.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        poll_key = str(poll_id)
        async with _polls_data_lock:
            data = _get_polls_data()
            if poll_key not in data["scheduled_polls"]:
                poll_missing = True
                instance_missing = False
            else:
                poll_missing = False
                instance = data["scheduled_polls"][poll_key].get("active_instance")
                if not instance:
                    instance_missing = True
                else:
                    instance_missing = False
                    instance["reminded"] = False
                    _flush_polls_data(force=True)

        if poll_missing:
            await interaction.followup.send(f"❌ Umfrage #{poll_id} nicht gefunden.")
            return

        if instance_missing:
            await interaction.followup.send(
                "❌ Diese Umfrage hat aktuell keine aktive Nachricht."
            )
            return

        now = datetime.now(ZoneInfo("Europe/Berlin"))
        await self._handle_reminders(
            now.date(),
            now.hour,
            poll_id=poll_key,
            force=True,
        )

        await interaction.followup.send(
            f"✅ Trigger-Reminder für Umfrage #{poll_id} ausgeführt!"
        )


# ---------------------------------------------------------------------------
# Extension setup
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScheduledPollCog(bot))
