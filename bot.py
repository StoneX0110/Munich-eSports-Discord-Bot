"""
Munich eSports Discord Membership Bot

Syncs club membership roles from easyVerein, sends birthday greetings,
welcomes new club members, and celebrates membership anniversaries.
"""

import json
import logging
import random
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord
from discord.ext import commands, tasks
from dotenv import set_key, find_dotenv
from easyverein import BearerToken, EasyvereinAPI
from easyverein.models import CustomField, Member
from easyverein.models.member import MemberFilter

from config import (
    BIRTHDAY_CONSENT_FIELD_ID,
    DAILY_RUN_TIME,
    DISCORD_ID_FIELD_ID,
    DISCORD_TOKEN,
    EV_API_KEY,
    GENERAL_CHANNEL_ID,
    GUILD_ID,
    KNOWN_MEMBERS_FILE,
    MEMBER_CHANNEL_ID,
    MEMBERSHIP_ROLE_ID,
    VOTES_FILE,
)
from messages import (
    ANNIVERSARY_MESSAGES_1Y,
    ANNIVERSARY_MESSAGES_NY,
    BIRTHDAY_MESSAGES,
    WELCOME_MESSAGES,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# Console handler
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

# Rotating file handler (5 MB per file, 5 backups)
_file_handler = RotatingFileHandler(
    _LOG_DIR / "bot.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))

logging.getLogger().addHandler(_file_handler)  # attach to root logger
logger = logging.getLogger("munich_esports_bot")

# ---------------------------------------------------------------------------
# Discord bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True  # Required to iterate guild members & resolve tags

bot = commands.Bot(command_prefix="!", intents=intents)

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


ev_client = EasyvereinAPI(
    EV_API_KEY,
    api_version="v2.0",
    token_refresh_callback=_handle_token_refresh,
    auto_refresh_token=True,
)


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
    except Exception:
        logger.exception("Failed to load %s.", KNOWN_MEMBERS_FILE)
        return None


def _save_known_members(ids: set[int]) -> None:
    """Persist the set of known easyVerein member IDs to disk."""
    try:
        KNOWN_MEMBERS_FILE.write_text(
            json.dumps(sorted(ids)), encoding="utf-8"
        )
    except Exception:
        logger.exception("Failed to save %s.", KNOWN_MEMBERS_FILE)


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
        member_cf = ev_client.member.custom_field(ev_member.id)
        member_cf.ensure_set(DISCORD_ID_FIELD_ID, discord_user_id)
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
# Daily task – membership sync, birthdays, welcomes, anniversaries
# ---------------------------------------------------------------------------
@tasks.loop(time=DAILY_RUN_TIME)
async def daily_task():
    """Runs once per day at 08:00 CET: sync roles, birthdays, welcomes, anniversaries."""
    logger.info("Daily task started.")

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logger.error("Guild %s not found – skipping daily task.", GUILD_ID)
        return

    membership_role = guild.get_role(MEMBERSHIP_ROLE_ID)
    if membership_role is None:
        logger.error("Membership role %s not found – skipping.", MEMBERSHIP_ROLE_ID)
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
    query = (
        "{id,joinDate,resignationDate,contactDetails{dateOfBirth},"
        "customFields{customField{id,name},value}}"
    )
    today = datetime.now(DAILY_RUN_TIME.tzinfo).date()

    try:
        # 1. Members with NO resignation date (indefinite membership)
        search_indefinite = MemberFilter(
            resignationDate__isnull=True,
            isApplication=False,
        )
        members_indefinite = ev_client.member.get_all(query=query, search=search_indefinite)

        # 2. Members with FUTURE resignation date (still active until that date)
        search_future_resignation = MemberFilter(
            resignationDate__gte=today,
            isApplication=False,
        )
        members_resigning = ev_client.member.get_all(query=query, search=search_future_resignation)

        # Combine both lists (using a dict by ID to deduplicate just in case)
        ev_members_map = {m.id: m for m in members_indefinite + members_resigning}
        ev_members = list(ev_members_map.values())

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
            try:
                await member.add_roles(membership_role, reason="easyVerein membership sync")
                roles_added += 1
                logger.info("Added membership role to %s (%s).", member, member.id)
            except discord.HTTPException:
                logger.exception("Failed to add role to %s.", member)

        elif not is_active and has_role:
            if member.bot:
                continue
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
        for uid in birthday_discord_ids:
            member = guild.get_member(uid)
            if member is None:
                continue
            message = random.choice(BIRTHDAY_MESSAGES).format(mention=member.mention)
            try:
                await general_channel.send(message)
                logger.info("Sent birthday greeting to %s (%s).", member, uid)
            except discord.HTTPException:
                logger.exception("Failed to send birthday greeting to %s.", member)

    # ------------------------------------------------------------------
    # New club member welcome messages (in #general)
    # ------------------------------------------------------------------
    if general_channel:
        previous_known = _load_known_members()
        current_ids = {m.id for m in ev_members}

        if previous_known is None:
            logger.info(
                "First run: saving %d known members (no welcome messages sent).",
                len(current_ids),
            )
        else:
            new_member_ids = current_ids - previous_known
            if new_member_ids:
                logger.info("Detected %d new club member(s).", len(new_member_ids))

            for ev_member in ev_members:
                if ev_member.id not in new_member_ids:
                    continue

                discord_member = ev_to_discord.get(ev_member.id)
                if discord_member is None:
                    continue

                message = random.choice(WELCOME_MESSAGES).format(
                    mention=discord_member.mention
                )
                try:
                    await general_channel.send(message)
                    logger.info(
                        "Sent welcome message for new club member %s (%s).",
                        discord_member,
                        discord_member.id,
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Failed to send welcome message for %s.", discord_member
                    )

        _save_known_members(current_ids)

    # ------------------------------------------------------------------
    # Membership anniversary shoutouts (in #member-general)
    # ------------------------------------------------------------------
    if member_channel:
        for ev_member in ev_members:
            if not ev_member.joinDate:
                continue
            jd = ev_member.joinDate
            if jd.month == today.month and jd.day == today.day and jd.year < today.year:
                years = today.year - jd.year

                discord_member = ev_to_discord.get(ev_member.id)
                if discord_member is None:
                    continue

                templates = ANNIVERSARY_MESSAGES_1Y if years == 1 else ANNIVERSARY_MESSAGES_NY
                message = random.choice(templates).format(
                    mention=discord_member.mention, years=years
                )
                try:
                    await member_channel.send(message)
                    logger.info(
                        "Sent anniversary message to %s (%s) – %d year(s).",
                        discord_member,
                        discord_member.id,
                        years,
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Failed to send anniversary message to %s.", discord_member
                    )

    logger.info("Daily task finished.")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info("Bot is online as %s (ID: %s).", bot.user, bot.user.id)

    # Load voting cog and sync slash commands
    if not bot.get_cog("VotingCog"):
        await bot.load_extension("voting")
        logger.info("Voting cog loaded.")

    # Load department cog
    if not bot.get_cog("DepartmentCog"):
        await bot.load_extension("department")
        logger.info("Department cog loaded.")

    # Sync all slash commands to the guild
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    logger.info("Slash commands synced.")

    if not daily_task.is_running():
        daily_task.start()
        logger.info(
            "Daily task scheduled at %s Europe/Berlin every day.",
            DAILY_RUN_TIME.strftime("%H:%M"),
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN is not set. Exiting.")
        raise SystemExit(1)
    if not EV_API_KEY:
        logger.critical("EV_API_KEY is not set. Exiting.")
        raise SystemExit(1)

    bot.run(DISCORD_TOKEN, log_handler=None)
