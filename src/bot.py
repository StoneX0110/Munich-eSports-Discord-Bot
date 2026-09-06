"""
Munich eSports Discord Membership Bot

Syncs club membership roles from easyVerein, sends birthday greetings,
welcomes new club members, and celebrates membership anniversaries.
"""

import asyncio
import json
import logging
import random
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

import discord
from discord.ext import commands, tasks
from dotenv import find_dotenv, set_key
from easyverein import BearerToken, EasyvereinAPI
from easyverein.models import CustomField, Member

from config import (
    BIRTHDAY_CONSENT_FIELD_ID,
    DAILY_RUN_TIME,
    DISCORD_ID_FIELD_ID,
    DISCORD_TOKEN,
    EV_API_KEY,
    GENERAL_CHANNEL_ID,
    GUILD_ID,
    KNOWN_MEMBERS_FILE,
    LOG_DIR,
    MEMBER_CHANNEL_ID,
    MEMBERSHIP_ROLE_ID,
)
from messages import (
    ANNIVERSARY_MESSAGES_1Y,
    ANNIVERSARY_MESSAGES_MULTIPLE,
    ANNIVERSARY_MESSAGES_NY,
    BIRTHDAY_MESSAGES,
    BIRTHDAY_MESSAGES_MULTIPLE,
    WELCOME_MESSAGES,
    WELCOME_MESSAGES_MULTIPLE,
)
from utils.easyverein import fetch_active_members
from utils.persistence import atomic_write_json

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DIR = LOG_DIR


# 1. Main Bot & Everything Else Logger (Daily rotation)
class _BotLogFilter(logging.Filter):
    def filter(self, record):
        # Exclude voting and department logs from bot.log
        if record.name.startswith("munich_esports_bot.voting") or record.name.startswith(
            "munich_esports_bot.department"
        ):
            return False
        return True


logger = logging.getLogger("munich_esports_bot")


def configure_logging() -> None:
    """Configure file logging at process startup rather than module import."""
    if getattr(configure_logging, "configured", False):
        return
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
    formatter = logging.Formatter(_LOG_FORMAT)
    bot_handler = TimedRotatingFileHandler(
        _LOG_DIR / "bot.log", when="midnight", interval=1, backupCount=14, encoding="utf-8"
    )
    bot_handler.setFormatter(formatter)
    bot_handler.addFilter(_BotLogFilter())
    logging.getLogger().addHandler(bot_handler)
    for name, filename in (
        ("munich_esports_bot.voting", "voting.log"),
        ("munich_esports_bot.department", "department.log"),
    ):
        handler = RotatingFileHandler(
            _LOG_DIR / filename, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logging.getLogger(name).addHandler(handler)
    configure_logging.configured = True

# ---------------------------------------------------------------------------
# Discord bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True  # Required to iterate guild members & resolve tags

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

# ---------------------------------------------------------------------------
# easyVerein client (with automatic token refresh)
# ---------------------------------------------------------------------------
_dotenv_path = find_dotenv()


def _handle_token_refresh(new_token: BearerToken) -> None:
    """Called automatically when the easyVerein API token is refreshed.

    Persists the new token to .env so it survives bot restarts.
    """
    if _dotenv_path:
        set_key(_dotenv_path, "EV_API_KEY", new_token.Bearer)
    logger.info("easyVerein API token was refreshed and saved to .env.")


ev_client: EasyvereinAPI | None = None


def initialize_easyverein_client() -> EasyvereinAPI:
    """Create the API client at startup, after configuration has been validated."""
    global ev_client
    ev_client = EasyvereinAPI(
        EV_API_KEY,
        api_version="v2.0",
        token_refresh_callback=_handle_token_refresh,
        auto_refresh_token=True,
    )
    bot.ev_client = ev_client
    return ev_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_known_members() -> set[int] | None:
    """Load the set of known easyVerein member IDs from disk.

    Returns None if the file does not exist yet (first run).
    """
    if not KNOWN_MEMBERS_FILE.exists():
        return None
    try:
        data = json.loads(KNOWN_MEMBERS_FILE.read_text(encoding="utf-8"))
        return set(data)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.exception("Failed to load %s; preserving the existing file.", KNOWN_MEMBERS_FILE)
        raise


def _save_known_members(ids: set[int]) -> None:
    """Persist the set of known easyVerein member IDs to disk."""
    try:
        atomic_write_json(KNOWN_MEMBERS_FILE, sorted(ids))
    except OSError:
        logger.exception("Failed to save %s.", KNOWN_MEMBERS_FILE)
        raise


def _get_custom_field_value(member: Member, field_id: int) -> str | None:
    """Extract the value of a specific custom field from a member object."""
    if not member.customFields:
        return None

    for mcf in member.customFields:
        cf = mcf.customField
        if isinstance(cf, CustomField) and cf.id == field_id:
            return mcf.value
    return None


def _is_numeric_discord_id(value: str) -> bool:
    """Check whether a string looks like a numeric Discord user ID."""
    return value.isdigit() and len(value) >= 15


def _resolve_discord_tag(guild: discord.Guild, tag: str) -> discord.Member | None:
    """
    Try to find a guild member by their Discord username / display name.
    """
    tag_lower = tag.strip().lower()
    for m in guild.members:
        if m.name.lower() == tag_lower:
            return m
    return None


def _resolve_discord_member(
    guild: discord.Guild,
    ev_member: Member,
) -> tuple[discord.Member | None, str | None]:
    """Resolve an easyVerein member to a Discord guild member.

    Looks up the Discord-ID custom field and resolves it to a guild member,
    handling both numeric IDs and username tags.

    Returns a tuple of (discord_member, raw_discord_value).  Both are None
    if the member has no Discord-ID custom field set.
    """
    discord_value = _get_custom_field_value(ev_member, DISCORD_ID_FIELD_ID)
    if not discord_value:
        return None, None

    if _is_numeric_discord_id(discord_value):
        return guild.get_member(int(discord_value)), discord_value

    return _resolve_discord_tag(guild, discord_value), discord_value


async def _update_ev_discord_id(ev_member: Member, discord_user_id: str) -> None:
    """
    Update the Discord-ID custom field in easyVerein with the numeric user ID.
    """
    try:

        def _do_update():
            member_cf = ev_client.member.custom_field(ev_member.id)
            member_cf.ensure_set(DISCORD_ID_FIELD_ID, discord_user_id)

        await asyncio.to_thread(_do_update)
        logger.info(
            "Updated easyVerein Discord-ID for member %s to %s",
            ev_member.id,
            discord_user_id,
        )
    except Exception:
        logger.exception(
            "Failed to update Discord-ID in easyVerein for member %s",
            ev_member.id,
        )


# ---------------------------------------------------------------------------
# Daily task - membership sync, birthdays, welcomes, anniversaries
# ---------------------------------------------------------------------------
@tasks.loop(time=DAILY_RUN_TIME)
async def daily_task():
    """Runs once per day at 08:00 CET: sync roles, birthdays, welcomes, anniversaries."""
    dry_run = getattr(bot, "dry_run", False)
    logger.info("Daily task started. (dry_run=%s)", dry_run)

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logger.error("Guild %s not found - skipping daily task.", GUILD_ID)
        return

    membership_role = guild.get_role(MEMBERSHIP_ROLE_ID)
    if membership_role is None:
        logger.error("Membership role %s not found - skipping.", MEMBERSHIP_ROLE_ID)
        return

    general_channel = guild.get_channel(GENERAL_CHANNEL_ID)
    member_channel = guild.get_channel(MEMBER_CHANNEL_ID)

    if not general_channel:
        logger.warning("General channel %s not found.", GENERAL_CHANNEL_ID)
    if not member_channel:
        logger.warning("Member channel %s not found.", MEMBER_CHANNEL_ID)

    # ------------------------------------------------------------------
    # Fetch all active members from easyVerein
    # ------------------------------------------------------------------
    query = "{id,joinDate,resignationDate,contactDetails{dateOfBirth},customFields{customField{id,name},value}}"
    today = datetime.now(DAILY_RUN_TIME.tzinfo).date()

    try:
        ev_members = await fetch_active_members(ev_client, query=query, today=today)
    except Exception:
        logger.exception("Failed to fetch members from easyVerein.")
        return

    logger.info("Fetched %d active members from easyVerein.", len(ev_members))

    # ------------------------------------------------------------------
    # Build mappings: easyVerein member → Discord member
    # ------------------------------------------------------------------

    # Sets for role sync
    active_discord_ids: set[int] = set()  # numeric Discord user IDs of active members
    # Members whose tags we resolved (need easyVerein update)
    tag_resolved: list[tuple] = []  # [(ev_member, discord_member), ...]
    # Birthday candidates
    birthday_discord_ids: set[int] = set()
    # Map ev_member.id → resolved discord.Member (reused later for welcomes & anniversaries)
    ev_to_discord: dict[int, discord.Member] = {}

    for ev_member in ev_members:
        discord_member, discord_value = _resolve_discord_member(guild, ev_member)

        # If it was a tag (not a numeric ID) and we resolved it, queue an update
        if discord_member and discord_value and not _is_numeric_discord_id(discord_value):
            tag_resolved.append((ev_member, discord_member))

        if discord_member is None:
            continue

        active_discord_ids.add(discord_member.id)
        ev_to_discord[ev_member.id] = discord_member

        # Check birthday consent & date
        consent = _get_custom_field_value(ev_member, BIRTHDAY_CONSENT_FIELD_ID)
        if consent == "True":
            cd = ev_member.contactDetails
            if cd and cd.dateOfBirth:
                dob = cd.dateOfBirth
                if dob.month == today.month and dob.day == today.day:
                    birthday_discord_ids.add(discord_member.id)

    # ------------------------------------------------------------------
    # Role sync
    # ------------------------------------------------------------------
    roles_added = 0
    roles_removed = 0

    for member in guild.members:
        has_role = membership_role in member.roles
        is_active = member.id in active_discord_ids

        if is_active and not has_role:
            if dry_run:
                logger.info("DRY RUN: Would add membership role to %s (%s).", member, member.id)
                roles_added += 1
            else:
                try:
                    await member.add_roles(membership_role, reason="easyVerein membership sync")
                    roles_added += 1
                    logger.info("Added membership role to %s (%s).", member, member.id)
                except discord.HTTPException:
                    logger.exception("Failed to add role to %s.", member)

        elif not is_active and has_role:
            if member.bot:
                continue
            if dry_run:
                logger.info("DRY RUN: Would remove membership role from %s (%s).", member, member.id)
                roles_removed += 1
            else:
                try:
                    await member.remove_roles(membership_role, reason="easyVerein membership sync")
                    roles_removed += 1
                    logger.info("Removed membership role from %s (%s).", member, member.id)
                except discord.HTTPException:
                    logger.exception("Failed to remove role from %s.", member)

    logger.info(
        "Role sync complete: %d added, %d removed.",
        roles_added,
        roles_removed,
    )

    # ------------------------------------------------------------------
    # Update easyVerein for resolved tags → numeric IDs
    # ------------------------------------------------------------------
    for ev_member, discord_member in tag_resolved:
        if dry_run:
            logger.info("DRY RUN: Would update EV Discord-ID for %s to %s.", ev_member.id, discord_member.id)
        else:
            await _update_ev_discord_id(ev_member, str(discord_member.id))

    if tag_resolved:
        logger.info(
            "Resolved %d Discord tag(s) to numeric IDs in easyVerein.",
            len(tag_resolved),
        )

    # ------------------------------------------------------------------
    # Birthday greetings (in #general)
    # ------------------------------------------------------------------
    if general_channel and birthday_discord_ids:
        birthday_members = []
        for uid in birthday_discord_ids:
            member = guild.get_member(uid)
            if member:
                birthday_members.append(member)

        if birthday_members:
            if len(birthday_members) == 1:
                message = random.choice(BIRTHDAY_MESSAGES).format(mention=birthday_members[0].mention)
            else:
                members_list = "\n".join(f"- {m.mention}" for m in birthday_members)
                message = random.choice(BIRTHDAY_MESSAGES_MULTIPLE).format(members=members_list)

            if dry_run:
                logger.info(
                    "DRY RUN: Would send birthday greeting(s) for %d member(s). Message:\n%s",
                    len(birthday_members),
                    message,
                )
            else:
                try:
                    await general_channel.send(message)
                    logger.info("Sent birthday greeting(s) for %d member(s).", len(birthday_members))
                except discord.HTTPException:
                    logger.exception("Failed to send birthday greeting(s).")

    # ------------------------------------------------------------------
    # New club member welcome messages (in #general)
    # ------------------------------------------------------------------
    if general_channel:
        try:
            previous_known = _load_known_members()
            known_members_loaded = True
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.error("Skipping welcome detection because known-members state could not be loaded.")
            previous_known = None
            known_members_loaded = False
        current_ids = {m.id for m in ev_members}

        if known_members_loaded and previous_known is None:
            logger.info(
                "First run: saving %d known members (no welcome messages sent).",
                len(current_ids),
            )
        elif known_members_loaded:
            new_member_ids = current_ids - previous_known
            if new_member_ids:
                logger.info("Detected %d new club member(s).", len(new_member_ids))

            new_discord_members = []
            for ev_member in ev_members:
                if ev_member.id not in new_member_ids:
                    continue

                discord_member = ev_to_discord.get(ev_member.id)
                if discord_member:
                    new_discord_members.append(discord_member)

            if new_discord_members:
                if len(new_discord_members) == 1:
                    m = new_discord_members[0]
                    message = random.choice(WELCOME_MESSAGES).format(mention=m.mention)
                else:
                    members_list = "\n".join(f"- {m.mention}" for m in new_discord_members)
                    message = random.choice(WELCOME_MESSAGES_MULTIPLE).format(members=members_list)

                if dry_run:
                    logger.info(
                        "DRY RUN: Would send welcome message(s) for %d member(s). Message:\n%s",
                        len(new_discord_members),
                        message,
                    )
                else:
                    try:
                        await general_channel.send(message)
                        logger.info("Sent welcome message(s) for %d member(s).", len(new_discord_members))
                    except discord.HTTPException:
                        logger.exception("Failed to send welcome message(s).")

        if dry_run:
            logger.info("DRY RUN: Would save %d known member IDs to %s.", len(current_ids), KNOWN_MEMBERS_FILE)
        elif known_members_loaded:
            try:
                _save_known_members(current_ids)
            except OSError:
                logger.error("Known-members state was not updated; the daily task will continue.")

    # ------------------------------------------------------------------
    # Membership anniversary shoutouts (in #member-general)
    # ------------------------------------------------------------------
    if member_channel:
        anniversaries_by_year = {}
        for ev_member in ev_members:
            if not ev_member.joinDate:
                continue
            jd = ev_member.joinDate
            if jd.month == today.month and jd.day == today.day and jd.year < today.year:
                years = today.year - jd.year

                discord_member = ev_to_discord.get(ev_member.id)
                if discord_member:
                    anniversaries_by_year.setdefault(years, []).append(discord_member)

        if anniversaries_by_year:
            total_anniversaries = sum(len(m) for m in anniversaries_by_year.values())

            if total_anniversaries == 1:
                years = next(iter(anniversaries_by_year.keys()))
                discord_member = anniversaries_by_year[years][0]
                templates = ANNIVERSARY_MESSAGES_1Y if years == 1 else ANNIVERSARY_MESSAGES_NY
                message = random.choice(templates).format(mention=discord_member.mention, years=years)
            else:
                anniversary_lines = []
                for years in sorted(anniversaries_by_year.keys()):
                    members = anniversaries_by_year[years]
                    mentions_str = ", ".join(m.mention for m in members)
                    if years == 1:
                        anniversary_lines.append(f"- 1 Jahr: {mentions_str}")
                    else:
                        anniversary_lines.append(f"- {years} Jahre: {mentions_str}")

                members_list = "\n".join(anniversary_lines)
                message = random.choice(ANNIVERSARY_MESSAGES_MULTIPLE).format(members=members_list)

            if dry_run:
                logger.info(
                    "DRY RUN: Would send anniversary message(s) for %d member(s). Message:\n%s",
                    total_anniversaries,
                    message,
                )
            else:
                try:
                    await member_channel.send(message)
                    logger.info("Sent anniversary message(s) for %d member(s).", total_anniversaries)
                except discord.HTTPException:
                    logger.exception("Failed to send anniversary message(s).")

    logger.info("Daily task finished.")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
async def _setup_hook():
    """Load extensions and sync commands - runs once before on_ready."""
    if getattr(bot, "dry_run", False):
        return
    await bot.load_extension("cogs.voting")
    logger.info("Voting cog loaded.")
    await bot.load_extension("cogs.department")
    logger.info("Department cog loaded.")
    await bot.load_extension("cogs.honeypot")
    logger.info("Honeypot cog loaded.")
    await bot.load_extension("cogs.scheduled_polls")
    logger.info("Scheduled polls cog loaded.")
    await bot.load_extension("cogs.scheduled_reminders")
    logger.info("Scheduled reminders cog loaded.")
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    logger.info("Slash commands synced.")


bot.setup_hook = _setup_hook


@bot.event
async def on_ready():
    logger.info("Bot is online as %s (ID: %s).", bot.user, bot.user.id)

    if getattr(bot, "dry_run", False):
        logger.info("Executing dry-run...")
        await daily_task()
        logger.info("Dry-run complete. Shutting down.")
        await bot.close()
        return

    if not daily_task.is_running():
        daily_task.start()
        logger.info(
            "Daily task scheduled at %s Europe/Berlin every day.",
            DAILY_RUN_TIME.strftime("%H:%M"),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    if "--dry-run" in args:
        bot.dry_run = True

    if not DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN is not set. Exiting.")
        raise SystemExit(1)
    if not EV_API_KEY:
        logger.critical("EV_API_KEY is not set. Exiting.")
        raise SystemExit(1)

    initialize_easyverein_client()

    bot.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
