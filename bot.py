"""
Munich eSports Discord Membership Bot

Syncs club membership roles from easyVerein and sends birthday greetings.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import random
from datetime import datetime, time
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks
from dotenv import load_dotenv, set_key, find_dotenv
from easyverein import BearerToken, EasyvereinAPI
from easyverein.models import CustomField, Member
from easyverein.models.member import MemberFilter

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
EV_API_KEY = os.getenv("EV_API_KEY", "")

GUILD_ID = 615552039027736595
MEMBERSHIP_ROLE_ID = 615555478210215936
BIRTHDAY_CHANNEL_ID = 626072050989006859

# easyVerein custom field IDs
DISCORD_ID_FIELD_ID = 34867055        # "Discord-ID" – stores Discord user ID or tag
BIRTHDAY_CONSENT_FIELD_ID = 177910549  # "Zustimmung Geburtstagswünsche" – checkbox

# The daily task runs at 08:00 Berlin time (handles CET/CEST automatically)
DAILY_RUN_TIME = time(hour=8, minute=0, second=0, tzinfo=ZoneInfo("Europe/Berlin"))

# ---------------------------------------------------------------------------
# Birthday greeting messages (randomly selected)
# ---------------------------------------------------------------------------
BIRTHDAY_MESSAGES = [
    "🎂 Happy Birthday, {mention}! 🎉 The Munich eSports team wishes you a wonderful day!",
    "🥳 It's {mention}'s birthday today! Have an amazing day! 🎈🎁",
    "🎉 Happy Birthday, {mention}! May your day be full of GGs and epic wins! 🏆",
    "🥳 Happy Birthday, {mention}! 🎮 Time to celebrate – you've leveled up! 🆙",
    "🎂 Cheers to {mention}! 🥂 Wishing you the best birthday ever!",
    "🎊 {mention} just unlocked a new year! Happy Birthday! 🔓🎉",
    "🎁 Happy Birthday, {mention}! Hope your day is as legendary as a pentakill! 🏅",
    "🎶 {mention} is celebrating today! Happy Birthday from all of Munich eSports! 🎂",
    "🥳 Level up! {mention} gained +1 year of awesomeness. Happy Birthday! 🆙✨",
    "🎂 Happy Birthday, {mention}! May your ping be low and your FPS be high today! 📶",
    "🎉 It's a big day for {mention}! Wishing you nothing but W's on your birthday! 🏆",
    "🎮 Happy Birthday, {mention}! Time to drop in and celebrate! 🪂🎂",
    "🎂 Another year, another rank up! Happy Birthday, {mention}! 🌟",
    "🥳 {mention}, it's YOUR day! Happy Birthday – enjoy every moment! 🎈🎁",
    "🎊 GG WP, {mention}! You've completed another year. Happy Birthday! 🎂🏆",
    "🎮 Happy Birthday, {mention}! May today's loot drops be extra generous! 🎁✨",
    "🎉 The whole squad wishes you a Happy Birthday, {mention}! 🫡🎂",
    "🥳 {mention} has entered a new season of life! Happy Birthday! 🎉",
    "🔁 Respawn complete – {mention} is back for another epic year! Happy Birthday! 🔄🎈",
    "🎁 Happy Birthday, {mention}! Wishing you a day full of clutch plays and good vibes! 🎯🥳",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# Console handler (same as before)
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
# Discord client setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True  # Required to iterate guild members & resolve tags

client = discord.Client(intents=intents)

# ---------------------------------------------------------------------------
# easyVerein client (with automatic token refresh)
# ---------------------------------------------------------------------------
_dotenv_path = find_dotenv()


def _handle_token_refresh(new_token: BearerToken) -> None:
    """Called automatically when the easyVerein API token is refreshed.

    Persists the new token to .env so it survives bot restarts.
    """
    global EV_API_KEY
    EV_API_KEY = new_token.Bearer
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


async def _resolve_discord_tag(guild: discord.Guild, tag: str) -> discord.Member | None:
    """
    Try to find a guild member by their Discord username / display name.
    """
    tag_lower = tag.strip().lower()
    for m in guild.members:
        if m.name.lower() == tag_lower:
            return m
    return None


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
# Daily task – membership sync + birthday greetings
# ---------------------------------------------------------------------------
@tasks.loop(time=DAILY_RUN_TIME)
async def daily_task():
    """Runs once per day at 08:00 CET: sync roles and send birthday greetings."""
    logger.info("Daily task started.")

    guild = client.get_guild(GUILD_ID)
    if guild is None:
        logger.error("Guild %s not found – skipping daily task.", GUILD_ID)
        return

    membership_role = guild.get_role(MEMBERSHIP_ROLE_ID)
    if membership_role is None:
        logger.error("Membership role %s not found – skipping.", MEMBERSHIP_ROLE_ID)
        return

    birthday_channel = guild.get_channel(BIRTHDAY_CHANNEL_ID)

    # ------------------------------------------------------------------
    # Fetch all active members from easyVerein
    # ------------------------------------------------------------------
    query = (
        "{id,resignationDate,contactDetails{dateOfBirth},"
        "customFields{customField{id,name},value}}"
    )
    today = datetime.now(ZoneInfo("Europe/Berlin")).date()

    try:
        # 1. Members with NO resignation date (indefinite membership)
        search_indefinite = MemberFilter(
            resignationDate__isnull=True,
            isApplication=False,
        )
        members_indefinite = ev_client.member.get_all(query=query, search=search_indefinite)

        # 2. Members with FUTURE resignation date (still active until that date)
        # Note: We filter for resignationDate >= today
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
    # Build mappings: Discord user ID → ev_member
    # ------------------------------------------------------------------

    # Sets for role sync
    active_discord_ids: set[int] = set()  # numeric Discord user IDs of active members
    # Members whose tags we resolved (need easyVerein update)
    tag_resolved: list[tuple] = []  # [(ev_member, discord_member), ...]
    # Birthday candidates
    birthday_discord_ids: set[int] = set()

    for ev_member in ev_members:
        discord_value = _get_custom_field_value(ev_member, DISCORD_ID_FIELD_ID)
        if not discord_value:
            continue

        discord_member: discord.Member | None = None

        if _is_numeric_discord_id(discord_value):
            discord_member = guild.get_member(int(discord_value))
        else:
            # It's a tag/username – try to resolve
            discord_member = await _resolve_discord_tag(guild, discord_value)
            if discord_member:
                tag_resolved.append((ev_member, discord_member))

        if discord_member is None:
            continue

        active_discord_ids.add(discord_member.id)

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
            # Don't remove roles from bots
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
    # Birthday greetings
    # ------------------------------------------------------------------
    if birthday_channel and birthday_discord_ids:
        for uid in birthday_discord_ids:
            member = guild.get_member(uid)
            if member is None:
                continue
            message = random.choice(BIRTHDAY_MESSAGES).format(mention=member.mention)
            try:
                await birthday_channel.send(message)
                logger.info("Sent birthday greeting to %s (%s).", member, uid)
            except discord.HTTPException:
                logger.exception("Failed to send birthday greeting to %s.", member)

    logger.info("Daily task finished.")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@client.event
async def on_ready():
    logger.info("Bot is online as %s (ID: %s).", client.user, client.user.id)
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

    client.run(DISCORD_TOKEN, log_handler=None)
