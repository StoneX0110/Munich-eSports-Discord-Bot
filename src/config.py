"""
Configuration constants for the Munich eSports Discord bot.
"""

import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

SRC_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_ROOT.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
EV_API_KEY = os.getenv("EV_API_KEY", "")

# ---------------------------------------------------------------------------
# Discord IDs
# ---------------------------------------------------------------------------
GUILD_ID = 615552039027736595
MEMBERSHIP_ROLE_ID = 615555478210215936
GENERAL_CHANNEL_ID = 626072050989006859       # #general – birthdays & welcomes
MEMBER_CHANNEL_ID = 615563862426648580        # #member-general – anniversaries
HONEYPOT_CHANNEL_ID = 1504391371719446540     # spam trap – any post here triggers ban
HONEYPOT_SPARE_AFTER_DAYS = 30
MOD_CHANNEL_ID = 615559692101353513  # moderation channel
DEPARTMENT_HEAD_ROLE_ID = 748509968172449802  # "Abteilungsleiter"
STAFF_ROLE_ID = 622890975718670336  # "Staff"
DEPARTMENT_ROLES = {
    615553053042540564: "Counter Strike",
    748502331661746247: "League Of Legends",
    748502603121295382: "Smash",
    748502802761777182: "Rocket League",
    1360191097359564982: "TCG",
    748502652069085264: "VALORANT",
    748503435040522320: "Verwaltung",
}

# ---------------------------------------------------------------------------
# easyVerein custom field IDs
# ---------------------------------------------------------------------------
DISCORD_ID_FIELD_ID = 34867055        # "Discord-ID" – stores Discord user ID or tag
BIRTHDAY_CONSENT_FIELD_ID = 177910549  # "Zustimmung Geburtstagswünsche" – checkbox
ABTEILUNGEN_FIELD_ID = 34866629       # "Abteilungen" – multi-select department membership

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
KNOWN_MEMBERS_FILE = DATA_DIR / "known_members.json"
VOTES_FILE = DATA_DIR / "votes.json"
POLLS_FILE = DATA_DIR / "scheduled_polls.json"

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
DAILY_RUN_TIME = time(hour=8, minute=0, second=0, tzinfo=ZoneInfo("Europe/Berlin"))
