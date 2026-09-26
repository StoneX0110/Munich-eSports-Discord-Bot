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
GENERAL_CHANNEL_ID = 626072050989006859  # #general – birthdays & welcomes
MEMBER_CHANNEL_ID = 615563862426648580  # #member-general – anniversaries
HONEYPOT_CHANNEL_ID = 1504391371719446540  # spam trap – any post here triggers ban
HONEYPOT_SPARE_AFTER_DAYS = 30
MOD_CHANNEL_ID = 615559692101353513  # moderation channel
DEPARTMENT_HEAD_ROLE_ID = 748509968172449802  # "Abteilungsleiter"
STAFF_ROLE_ID = 622890975718670336  # "Staff"
# Department-specific lead roles.
DEPARTMENT_ROLES = {
    615553053042540564: "Counter Strike",
    748502331661746247: "League Of Legends",
    748502603121295382: "Smash",
    748502802761777182: "Rocket League",
    1360191097359564982: "TCG",
    748502652069085264: "VALORANT",
    748503435040522320: "Verwaltung",
}
DEPARTMENT_ROLE_IDS = {name: role_id for role_id, name in DEPARTMENT_ROLES.items()}

# ---------------------------------------------------------------------------
# easyVerein custom field IDs
# ---------------------------------------------------------------------------
DISCORD_ID_FIELD_ID = 34867055  # "Discord-ID" – stores Discord user ID or tag
BIRTHDAY_CONSENT_FIELD_ID = 177910549  # "Zustimmung Geburtstagswünsche" – checkbox
ABTEILUNGEN_FIELD_ID = 34866629  # "Abteilungen" – multi-select department membership

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
KNOWN_MEMBERS_FILE = DATA_DIR / "known_members.json"
VOTES_FILE = DATA_DIR / "votes.json"
POLLS_FILE = DATA_DIR / "scheduled_polls.json"
REMINDERS_FILE = DATA_DIR / "scheduled_reminders.json"
TEAMS_FILE = DATA_DIR / "teams.json"

# Team management. Lead roles authorize commands; member roles receive read-only
# visibility for their department's team voice channels.
TEAM_DEPARTMENTS = {
    "CS": {
        "lead_role_id": DEPARTMENT_ROLE_IDS["Counter Strike"],
        "member_role_id": 615555652168712198,
        "category_id": 745719790886322236,
        "role_anchor_id": 959438343123374110,
        "shared_channel_ids": [808382021751668766, 750294711222272072],
    },
    "LoL": {
        "lead_role_id": DEPARTMENT_ROLE_IDS["League Of Legends"],
        "member_role_id": 615556231595163671,
        "category_id": 745719870540218380,
        "role_anchor_id": 782001965239762974,
        "shared_channel_ids": [750295373016465428, 824252780486721547],
    },
    "RL": {
        "lead_role_id": DEPARTMENT_ROLE_IDS["Rocket League"],
        "member_role_id": 615556346753974283,
        "category_id": 745720098492514376,
        "role_anchor_id": 761650394160955412,
        "shared_channel_ids": [750295768535007264, 847788445668474881],
    },
    "VAL": {
        "lead_role_id": DEPARTMENT_ROLE_IDS["VALORANT"],
        "member_role_id": 698069905614045215,
        "category_id": 745720315056750632,
        "role_anchor_id": 746420761845039235,
        "shared_channel_ids": [750296042825842748],
    },
    "TCG": {
        "lead_role_id": DEPARTMENT_ROLE_IDS["TCG"],
        "member_role_id": 1339594646866755654,
        "category_id": 1468688088082546748,
        "role_anchor_id": 1468689086637408431,
        "shared_channel_ids": [1470705070113820796, 1474316746751217832, 1468688284753465435],
    },
}

# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
DAILY_RUN_TIME = time(hour=8, minute=0, second=0, tzinfo=ZoneInfo("Europe/Berlin"))
