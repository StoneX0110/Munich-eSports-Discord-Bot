"""
Department-related commands for the Munich eSports Discord bot.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands
from easyverein.models.member import MemberFilter

from config import (
    ABTEILUNGEN_FIELD_ID,
    DEPARTMENT_ROLES,
    DAILY_RUN_TIME,
    GUILD_ID,
)

logger = logging.getLogger("munich_esports_bot.department")


class DepartmentCog(commands.Cog):
    """Slash commands for department management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ev_client = bot.ev_client

    abteilung_group = app_commands.Group(
        name="abteilung",
        description="Abteilungs-Befehle",
        guild_ids=[GUILD_ID],
    )

    @abteilung_group.command(
        name="mitglieder",
        description="Lässt dich prüfen, wie viele aktive Mitglieder deine Abteilung hat",
    )
    async def abteilung_mitglieder(self, interaction: discord.Interaction):
        # 1. Determine the user's department based on their roles
        dept_name = None
        for role in interaction.user.roles:
            if role.id in DEPARTMENT_ROLES:
                dept_name = DEPARTMENT_ROLES[role.id]
                break
                
        if not dept_name:
            await interaction.response.send_message(
                "❌ Du scheinst keine Abteilungsleitung zu sein, oder deine Rolle wurde nicht konfiguriert.",
                ephemeral=True
            )
            return

        # Defer response as the API call might take a moment
        await interaction.response.defer(ephemeral=True)

        # 2. Fetch all active members from easyVerein
        # Using the same logic as the daily sync (indefinite + future resignation)
        query = "{id,resignationDate,customFields{customField{id,name},value,selectedOptions{id,value}}}"
        today = datetime.now(ZoneInfo("Europe/Berlin")).date()

        try:
            search_indefinite = MemberFilter(resignationDate__isnull=True, isApplication=False)
            members_indefinite = await asyncio.to_thread(
                self.ev_client.member.get_all, query=query, search=search_indefinite,
            )

            search_future_resignation = MemberFilter(resignationDate__gte=today, isApplication=False)
            members_resigning = await asyncio.to_thread(
                self.ev_client.member.get_all, query=query, search=search_future_resignation,
            )

            # Deduplicate by ID
            active_members = list({m.id: m for m in members_indefinite + members_resigning}.values())
        except Exception:
            logger.exception("Failed to fetch members from easyVerein for /abteilung mitglieder.")
            await interaction.followup.send("❌ Fehler beim Abrufen der Mitglieder aus easyVerein.")
            return

        # 3. Calculate count based on department
        if dept_name == "Verwaltung":
            # "Verwaltung" sees the count of all active members
            count = len(active_members)
            await interaction.followup.send(f"Der Verein hat aktuell **{count}** aktive Mitglieder.", ephemeral=True)
        else:
            # Other departments filter by the Abteilungen custom field
            count = 0
            for member in active_members:
                if not member.customFields:
                    continue
                for cf in member.customFields:
                    # Depending on how the API model parses it, the ID is within the nested customField object
                    if getattr(cf.customField, "id", None) == ABTEILUNGEN_FIELD_ID or getattr(cf, "customField", None) == ABTEILUNGEN_FIELD_ID:
                        if getattr(cf, "selectedOptions", None):
                            for opt in cf.selectedOptions:
                                if getattr(opt, "value", None) == dept_name:
                                    count += 1
                                    break
                        break

            await interaction.followup.send(f"Die Abteilung **{dept_name}** hat aktuell **{count}** aktive Mitglieder.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DepartmentCog(bot))
