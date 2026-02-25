"""
Configuration constants for the Munich eSports Discord bot.
"""

import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
EV_API_KEY = os.getenv("EV_API_KEY", "")

# ---------------------------------------------------------------------------
# Discord IDs
# ---------------------------------------------------------------------------
GUILD_ID = 615552039027736595
MEMBERSHIP_ROLE_ID = 615555478210215936
GENERAL_CHANNEL_ID = 626072050989006859       # #general – birthdays & welcomes
MEMBER_CHANNEL_ID = 615563862426648580        # #member-general – anniversaries

# ---------------------------------------------------------------------------
# easyVerein custom field IDs
# ---------------------------------------------------------------------------
DISCORD_ID_FIELD_ID = 34867055        # "Discord-ID" – stores Discord user ID or tag
BIRTHDAY_CONSENT_FIELD_ID = 177910549  # "Zustimmung Geburtstagswünsche" – checkbox

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
KNOWN_MEMBERS_FILE = Path(__file__).resolve().parent / "known_members.json"

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
DAILY_RUN_TIME = time(hour=8, minute=0, second=0, tzinfo=ZoneInfo("Europe/Berlin"))
