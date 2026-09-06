"""
Helper functions for managing scheduled poll configuration and persistency.
"""

import asyncio
import copy
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import DEPARTMENT_HEAD_ROLE_ID, GUILD_ID, POLLS_FILE, STAFF_ROLE_ID
from utils.scheduled import (
    BERLIN_TZ,
    WEEKDAYS,
    JsonScheduleStore,
    member_has_any_role,
    normalize_weekday,
    now_berlin_iso,
    weekday_choices,
    weekday_name,
)

logger = logging.getLogger("munich_esports_bot.scheduled_polls")

DEFAULT_POLLS_DATA = {"next_scheduled_poll_id": 1, "scheduled_polls": {}}

POLLS_FLUSH_INTERVAL_SECONDS = 60
SCHEDULED_POLL_MANAGER_ROLE_IDS = {DEPARTMENT_HEAD_ROLE_ID, STAFF_ROLE_ID}
_polls_data_lock = asyncio.Lock()
_polls_data_cache: dict | None = None
_polls_data_dirty = False
_message_update_tasks: dict[int, asyncio.Task] = {}
_message_update_revisions: dict[int, int] = defaultdict(int)
_message_edit_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _default_polls_data() -> dict:
    return {
        "next_scheduled_poll_id": DEFAULT_POLLS_DATA["next_scheduled_poll_id"],
        "scheduled_polls": {},
    }


def _polls_store() -> JsonScheduleStore:
    return JsonScheduleStore(
        file_path=POLLS_FILE,
        default_factory=_default_polls_data,
        logger=logger,
        corrupt_log_message="Corrupt scheduled polls file detected. Preserving the file and refusing to load it.",
        read_error_log_message="Failed to read scheduled polls file due to an I/O error.",
        write_error_log_message="Failed to save scheduled polls data.",
    )


def _load_polls_data() -> dict:
    """
    Loads scheduled polls configuration from the JSON storage file.

    If the file does not exist, returns the default structure.
    If the file is corrupt or unreadable, logs and raises the error.
    """
    return _polls_store().load()


def _save_polls_data(data: dict) -> None:
    """
    Saves the scheduled polls configuration to the JSON storage file.
    """
    _polls_store().save(data)


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


# ---------------------------------------------------------------------------
# Authorization & validation helpers
# ---------------------------------------------------------------------------


def _can_manage_scheduled_polls(member: discord.Member) -> bool:
    """Check whether a Discord member can manage scheduled polls."""
    return member_has_any_role(member, SCHEDULED_POLL_MANAGER_ROLE_IDS)


def _normalize_weekday(day: str) -> str | None:
    """Normalize and validate a German weekday name, case-insensitively.

    Returns the capitalized weekday name if valid, otherwise None.
    """
    return normalize_weekday(day)


def _now_iso() -> str:
    return now_berlin_iso()


def _weekday_name(day: date) -> str:
    return weekday_name(day)


def _format_reminder_schedule(poll: dict) -> str:
    reminder_weekday = poll.get("reminder_weekday")
    reminder_hour = poll.get("reminder_hour")
    if reminder_weekday is None or reminder_hour is None:
        return "keine"
    return f"{reminder_weekday} um {reminder_hour:02d}:00"


def _weekdays_from_start(week_start_day: str) -> list[str]:
    week_start_day_idx = WEEKDAYS.index(week_start_day)
    return [WEEKDAYS[(week_start_day_idx + i) % len(WEEKDAYS)] for i in range(len(WEEKDAYS))]


# ---------------------------------------------------------------------------
# Embed & interactive view helpers
# ---------------------------------------------------------------------------


def _get_target_dates(
    post_date: date,
    week_start_day: str = "Montag",
) -> date:
    """Return the next target start date."""
    week_start_day_idx = WEEKDAYS.index(week_start_day)
    days_to_start = (week_start_day_idx - post_date.weekday()) % 7
    if days_to_start == 0:
        days_to_start = 7  # Always starting the week AFTER the poll
    return post_date + timedelta(days=days_to_start)


def _build_poll_embed(role_id: str | int, target_week_start_str: str, responses: dict) -> discord.Embed:
    start_date = date.fromisoformat(target_week_start_str)

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

    for i in range(7):
        current_date = start_date + timedelta(days=i)
        day = _weekday_name(current_date)
        day_str = current_date.strftime("%d.%m.")
        users = tallies[day]
        count = len(users)
        lines.append(f"📅 **{day} ({day_str}) [{count}]:**")
        if users:
            lines.append(_bounded_mentions(users))
        else:
            lines.append("• *Keiner*")
        lines.append("")

    lines.append(f"❌ **Keine Zeit [{len(no_time_list)}]:**")
    if no_time_list:
        lines.append(_bounded_mentions(no_time_list))
    else:
        lines.append("• *Keiner*")

    return discord.Embed(
        description="\n".join(lines),
        color=discord.Color.blue(),
    )


def _bounded_mentions(user_ids: list[str], limit: int = 400) -> str:
    shown: list[str] = []
    used = 0
    for user_id in user_ids:
        mention = f"<@{user_id}>"
        separator = 2 if shown else 0
        if used + separator + len(mention) > limit:
            break
        shown.append(mention)
        used += separator + len(mention)
    remaining = len(user_ids) - len(shown)
    return ", ".join(shown) + (f" … (+{remaining} weitere)" if remaining else "")


def _poll_list_embeds(polls: dict) -> list[discord.Embed]:
    pages = []
    embed = discord.Embed(title="📋 Wiederkehrende Umfragen", color=discord.Color.blue())
    for poll_id, poll in polls.items():
        name = f"#{poll_id}"
        value = (
            f"**Rolle:** <@&{poll['role_id']}>\n**Kanal:** <#{poll['channel_id']}>\n"
            f"**Postet am:** {poll['weekday']}\n"
            f"**Erster Tag der Spielwoche:** {poll.get('week_start_day', 'Montag')}\n"
            f"**Reminder:** {_format_reminder_schedule(poll)}"
        )
        if len(embed.fields) >= 25 or len(embed) + len(name) + len(value) > 6000:
            pages.append(embed)
            embed = discord.Embed(title="📋 Wiederkehrende Umfragen (Fortsetzung)", color=discord.Color.blue())
        embed.add_field(name=name, value=value, inline=False)
    pages.append(embed)
    return pages


def _reminder_messages(non_voters: list[str], poll: dict, instance: dict) -> list[str]:
    footer = (
        "\nBitte tragt euch noch in die Umfrage für nächste Woche ein! 🗳️\n"
        f"Zur Umfrage: https://discord.com/channels/{GUILD_ID}/{poll['channel_id']}/{instance['message_id']}"
    )
    prefix = "⚠️ **Erinnerung!** "
    messages, mentions = [], []
    for mention in non_voters:
        candidate = " ".join([*mentions, mention])
        if mentions and len(prefix) + len(candidate) + len(footer) > 2000:
            messages.append(prefix + " ".join(mentions) + footer)
            mentions = [mention]
        else:
            mentions.append(mention)
    if mentions:
        messages.append(prefix + " ".join(mentions) + footer)
    return messages


class ScheduledPollView(discord.ui.View):
    """Persistent button view for toggling availability on a scheduled poll."""

    def __init__(self, poll_id: str, week_start_day: str = "Montag"):
        super().__init__(timeout=None)
        self.poll_id = poll_id

        for day in _weekdays_from_start(week_start_day):
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
            await interaction.response.defer()
            user_id = str(interaction.user.id)
            message_id = interaction.message.id

            async with _polls_data_lock:
                data = _get_polls_data()
                poll = data["scheduled_polls"].get(self.poll_id)
                instance = poll.get("active_instance") if poll else None
                error = None
                if not instance or instance.get("message_id") != message_id:
                    error = "❌ Diese Umfrage ist nicht mehr aktiv."
                elif not any(r.id == poll["role_id"] for r in interaction.user.roles):
                    error = "❌ Nur Mitglieder der Umfrage-Rolle können abstimmen."
                else:
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
                    _message_update_revisions[message_id] += 1

            if error:
                await interaction.followup.send(error, ephemeral=True)
                return

            task = _message_update_tasks.get(message_id)
            if task is None or task.done():
                task = asyncio.create_task(self._update_message(interaction.message, message_id))
                _message_update_tasks[message_id] = task
            await asyncio.shield(task)

        return callback

    async def _update_message(self, message, message_id: int) -> None:
        try:
            await asyncio.sleep(0)
            while True:
                async with _message_edit_locks[message_id]:
                    async with _polls_data_lock:
                        revision = _message_update_revisions[message_id]
                        poll = _get_polls_data()["scheduled_polls"].get(self.poll_id)
                        instance = poll.get("active_instance") if poll else None
                        if not instance or instance.get("message_id") != message_id:
                            return
                        embed = _build_poll_embed(
                            poll["role_id"], instance["target_week_start"], instance["responses"]
                        )
                    await message.edit(embed=embed)
                if _message_update_revisions[message_id] == revision:
                    return
        finally:
            _message_update_tasks.pop(message_id, None)
            _message_update_revisions.pop(message_id, None)


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------


class ScheduledPollCog(commands.Cog):
    """Slash command group for managing recurring scheduled polls."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lifecycle_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

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

        await interaction.response.defer(ephemeral=True)

        async with _polls_data_lock:
            polls = copy.deepcopy(_get_polls_data().get("scheduled_polls", {}))

        if not polls:
            await interaction.followup.send(
                "Es sind keine wiederkehrenden Umfragen eingerichtet.",
                ephemeral=True,
            )
            return
        for embed in _poll_list_embeds(polls):
            await interaction.followup.send(embed=embed, ephemeral=True)

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

        await interaction.response.defer(ephemeral=True)

        poll_key = str(poll_id)
        async with self._lifecycle_locks[poll_key]:
            async with _polls_data_lock:
                data = _get_polls_data()
                if poll_key not in data.get("scheduled_polls", {}):
                    poll_missing = True
                else:
                    poll_missing = False
                    deleted_poll = data["scheduled_polls"][poll_key]
                    del data["scheduled_polls"][poll_key]
                    _mark_polls_data_dirty()
                    _flush_polls_data(force=True)
                    logger.info(
                        "Deleted scheduled poll #%s by user %s from channel %s for role %s.",
                        poll_id,
                        interaction.user.id,
                        deleted_poll["channel_id"],
                        deleted_poll["role_id"],
                    )

        if poll_missing:
            await interaction.followup.send(
                f"❌ Wiederkehrende Umfrage #{poll_id} nicht gefunden.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
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
        posting_day="Wochentag, an dem die Umfrage automatisch gepostet wird",
        reminder_weekday="Optionaler Wochentag für Reminder-Pings",
        reminder_hour="Optionale Uhrzeit für Reminder-Pings (0-23)",
        week_start_day="Optionaler erster Tag der Spielwoche (Standard: Montag)",
    )
    async def poll_create(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        posting_day: str,
        reminder_weekday: str | None = None,
        reminder_hour: int | None = None,
        week_start_day: str | None = None,
    ):
        if not _can_manage_scheduled_polls(interaction.user):
            await interaction.response.send_message(
                "❌ Nur Abteilungsleiter oder Staff können wiederkehrende Umfragen verwalten.",
                ephemeral=True,
            )
            return

        normalized = _normalize_weekday(posting_day)
        if normalized is None:
            valid_list = ", ".join(WEEKDAYS)
            await interaction.response.send_message(
                f"❌ Ungültiger Posting-Wochentag: `{posting_day}`. Gültige Werte: {valid_list}",
                ephemeral=True,
            )
            return

        week_start_day = week_start_day.strip() if week_start_day else "Montag"
        normalized_week_start_day = _normalize_weekday(week_start_day)
        if normalized_week_start_day is None:
            valid_list = ", ".join(WEEKDAYS)
            await interaction.response.send_message(
                f"❌ Ungültiger erster Tag der Spielwoche: `{week_start_day}`. Gültige Werte: {valid_list}",
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
                    f"❌ Ungültiger Reminder-Wochentag: `{reminder_weekday}`. Gültige Werte: {valid_list}",
                    ephemeral=True,
                )
                return

            if reminder_hour < 0 or reminder_hour > 23:
                await interaction.response.send_message(
                    "❌ Ungültige Reminder-Uhrzeit. Bitte gib eine Stunde von `0` bis `23` an.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)
        async with _polls_data_lock:
            data = _get_polls_data()
            poll_id = str(data["next_scheduled_poll_id"])
            data["next_scheduled_poll_id"] += 1

            data["scheduled_polls"][poll_id] = {
                "channel_id": interaction.channel_id,
                "role_id": role.id,
                "weekday": normalized,
                "week_start_day": normalized_week_start_day,
                "reminder_weekday": normalized_reminder_weekday,
                "reminder_hour": reminder_hour,
                "created_by": interaction.user.id,
                "created_at": _now_iso(),
                "active_instance": None,
            }
            _mark_polls_data_dirty()
            _flush_polls_data(force=True)
            logger.info(
                "Created scheduled poll #%s by user %s in channel %s for role %s; "
                "posting_day=%s, week_start_day=%s, reminder_weekday=%s, reminder_hour=%s.",
                poll_id,
                interaction.user.id,
                interaction.channel_id,
                role.id,
                normalized,
                normalized_week_start_day,
                normalized_reminder_weekday,
                reminder_hour,
            )

        reminder_schedule = _format_reminder_schedule(
            {
                "reminder_weekday": normalized_reminder_weekday,
                "reminder_hour": reminder_hour,
            }
        )
        await interaction.followup.send(
            f"✅ Wiederkehrende Umfrage #{poll_id} erstellt!\n"
            f"**Rolle:** {role.mention}\n"
            f"**Kanal:** <#{interaction.channel_id}>\n"
            f"**Postet am:** {normalized}\n"
            f"**Erster Tag der Spielwoche:** {normalized_week_start_day}\n"
            f"**Reminder:** {reminder_schedule}",
            ephemeral=True,
        )

    @poll_create.autocomplete("posting_day")
    async def posting_day_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return weekday_choices(current)

    @poll_create.autocomplete("reminder_weekday")
    async def reminder_weekday_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return weekday_choices(current)

    @poll_create.autocomplete("week_start_day")
    async def week_start_day_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return weekday_choices(current)

    # -----------------------------------------------------------------------
    # Lifecycle & background scheduling
    # -----------------------------------------------------------------------
    async def cog_load(self):
        async with _polls_data_lock:
            data = _get_polls_data()
            active_poll_ids = [
                poll_id for poll_id, poll in data.get("scheduled_polls", {}).items() if poll.get("active_instance")
            ]

        for poll_id in active_poll_ids:
            poll = data["scheduled_polls"][poll_id]
            instance = poll["active_instance"]
            week_start_day = _weekday_name(date.fromisoformat(instance["target_week_start"]))
            self.bot.add_view(ScheduledPollView(poll_id, week_start_day))

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
        now = datetime.now(BERLIN_TZ)
        logger.info(
            "Scheduled polls tick. Hour: %d, Weekday: %s",
            now.hour,
            _weekday_name(now.date()),
        )
        if now.hour == 8:
            await self._handle_posting(now.date())
        await self._handle_reminders(now.date(), now.hour)

    @scheduled_poll_loop.before_loop
    async def before_scheduled_poll_loop(self):
        await self.bot.wait_until_ready()

    async def _handle_posting(
        self,
        today: date,
        poll_id: str | None = None,
        force: bool = False,
    ):
        if getattr(self.bot, "dry_run", False) is True:
            return
        async with _polls_data_lock:
            poll_ids = list(_get_polls_data().get("scheduled_polls", {}))
        for current_poll_id in poll_ids:
            if poll_id is not None and current_poll_id != poll_id:
                continue
            async with self._lifecycle_locks[current_poll_id]:
                async with _polls_data_lock:
                    live = _get_polls_data().get("scheduled_polls", {}).get(current_poll_id)
                    if not live or (not force and live["weekday"] != _weekday_name(today)):
                        continue
                    poll = copy.deepcopy(live)
                target_start = _get_target_dates(today, poll.get("week_start_day", "Montag"))
                target_start_str = target_start.isoformat()
                old_inst = poll.get("active_instance")
                if not force and old_inst and old_inst.get("target_week_start") == target_start_str:
                    continue
                channel = self.bot.get_channel(poll["channel_id"])
                if not channel:
                    continue
                try:
                    msg = await channel.send(
                        content=f"<@&{poll['role_id']}>",
                        embed=_build_poll_embed(poll["role_id"], target_start_str, {}),
                        view=ScheduledPollView(current_poll_id, _weekday_name(target_start)),
                    )
                except discord.HTTPException:
                    logger.exception("Failed to post scheduled poll #%s.", current_poll_id)
                    continue
                new_instance = {
                    "message_id": msg.id, "posted_at": _now_iso(),
                    "target_week_start": target_start_str, "reminded": False, "responses": {},
                }
                async with _polls_data_lock:
                    # The lifecycle lock excludes replacement/deletion; votes may
                    # still update the old instance while channel.send is pending.
                    live = _get_polls_data()["scheduled_polls"][current_poll_id]
                    old_inst = live.get("active_instance")
                    live["active_instance"] = new_instance
                    _mark_polls_data_dirty()
                    _flush_polls_data(force=True)
                if old_inst:
                    try:
                        async with _message_edit_locks[old_inst["message_id"]]:
                            old_msg = await channel.fetch_message(old_inst["message_id"])
                            old_embed = _build_poll_embed(
                                poll["role_id"], old_inst["target_week_start"], old_inst["responses"]
                            )
                            old_embed.title = "🗳️ (Geschlossen)"
                            old_embed.color = discord.Color.light_grey()
                            await old_msg.edit(embed=old_embed, view=None)
                    except Exception:
                        logger.warning("Failed to archive previous poll message %s.", old_inst["message_id"], exc_info=True)

    async def _handle_reminders(
        self,
        today: date,
        current_hour: int | None = None,
        poll_id: str | None = None,
        force: bool = False,
    ):
        if getattr(self.bot, "dry_run", False) is True:
            return
        async with _polls_data_lock:
            poll_ids = list(_get_polls_data().get("scheduled_polls", {}))
        today_weekday = _weekday_name(today)
        for current_poll_id in poll_ids:
            async with self._lifecycle_locks[current_poll_id]:
                async with _polls_data_lock:
                    live = _get_polls_data().get("scheduled_polls", {}).get(current_poll_id)
                    poll = copy.deepcopy(live) if live else None
                if not poll:
                    continue
                if poll_id is not None and current_poll_id != poll_id:
                    continue

                if not force:
                    if poll.get("reminder_weekday") != today_weekday:
                        continue
                    if poll.get("reminder_hour") != current_hour:
                        continue

                instance = poll.get("active_instance")
                if not instance or (instance.get("reminded") and not force):
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

                try:
                    for reminder_msg in _reminder_messages(non_voters, poll, instance):
                        await channel.send(reminder_msg)
                    async with _polls_data_lock:
                        live = _get_polls_data().get("scheduled_polls", {}).get(current_poll_id)
                        live_instance = live.get("active_instance") if live else None
                        if live_instance and live_instance.get("message_id") == instance["message_id"]:
                            live_instance["reminded"] = True
                            _mark_polls_data_dirty()
                            _flush_polls_data(force=True)
                    logger.info(
                        "Marked reminder sent for scheduled poll #%s; message_id=%s.",
                        current_poll_id,
                        instance["message_id"],
                    )
                except discord.HTTPException:
                    logger.exception("Failed to send reminder for poll #%s.", current_poll_id)

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
            datetime.now(BERLIN_TZ).date(),
            poll_id=poll_key,
            force=True,
        )

        await interaction.followup.send(f"✅ Trigger-Post für Umfrage #{poll_id} ausgeführt!")

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
                    logger.info(
                        "Triggered reminder for scheduled poll #%s by user %s; message_id=%s.",
                        poll_id,
                        interaction.user.id,
                        instance["message_id"],
                    )

        if poll_missing:
            await interaction.followup.send(f"❌ Umfrage #{poll_id} nicht gefunden.")
            return

        if instance_missing:
            await interaction.followup.send("❌ Diese Umfrage hat aktuell keine aktive Nachricht.")
            return

        now = datetime.now(BERLIN_TZ)
        await self._handle_reminders(
            now.date(),
            now.hour,
            poll_id=poll_key,
            force=True,
        )

        await interaction.followup.send(f"✅ Trigger-Reminder für Umfrage #{poll_id} ausgeführt!")


# ---------------------------------------------------------------------------
# Extension setup
# ---------------------------------------------------------------------------


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScheduledPollCog(bot))
