"""
Honeypot channel: ban anyone who posts in the trap channel and log to mod channel.
"""

import logging

import discord
from discord.ext import commands

from config import GUILD_ID, HONEYPOT_CHANNEL_ID, MOD_CHANNEL_ID

logger = logging.getLogger("munich_esports_bot.honeypot")


async def _notify_mod_channel(
    mod_channel: discord.abc.Messageable,
    message: discord.Message,
) -> None:
    if message.is_forwardable():
        try:
            await message.forward(mod_channel)
            await mod_channel.send("🔨 BANNED")
            return
        except discord.HTTPException:
            logger.exception("Forward to mod channel failed; sending fallback.")
    preview = message.content or "(content unavailable)"
    text = (
        "**Honeypot** (could not forward)\n"
        f"User: {message.author} (`{message.author.id}`)\n"
        f"Jump: {message.jump_url}\n"
        f"Preview: {preview[:1500]}"
    )
    await mod_channel.send(text)


class HoneypotCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        if (
            message.author.id == self.bot.user.id
            or message.webhook_id is not None
            or guild is None
            or guild.id != GUILD_ID
            or message.channel.id != HONEYPOT_CHANNEL_ID
        ):
            return

        mod_channel = guild.get_channel(MOD_CHANNEL_ID)
        if mod_channel is None or not isinstance(mod_channel, discord.abc.Messageable):
            logger.error(
                "Honeypot mod log channel %s missing or not messageable; skipping ban for user %s.",
                MOD_CHANNEL_ID,
                message.author.id,
            )
            return

        member = message.author

        try:
            await _notify_mod_channel(mod_channel, message)
        except discord.HTTPException:
            logger.exception("Failed to notify mod channel for honeypot hit.")

        try:
            await member.ban(
                reason="Honeypot channel post",
                delete_message_days=1,
            )
            logger.info("Banned %s (%s) for honeypot post.", member, member.id)
        except discord.Forbidden:
            logger.exception("Missing permissions to ban %s for honeypot post.", member.id)
        except discord.HTTPException:
            logger.exception("Ban failed for honeypot user %s.", member.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HoneypotCog(bot))
