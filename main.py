import json
import asyncio
import discord
import easyVereinAPIHandler as api

with open('config.json', 'r') as f:
    config = json.load(f)

DISCORD_TOKEN = config['discord_token']


class MyClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.message_id == 1161782437798494228:
            channel = self.get_channel(711943678566072455)
            await channel.send(f"<@{payload.user_id}> kriegt jetzt Geburtstagswünsche! :)")

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.message_id == 1161782437798494228:
            channel = self.get_channel(711943678566072455)
            await channel.send(f"<@{payload.user_id}> kriegt jetzt keine Geburtstagswünsche mehr :(")

    async def setup_hook(self):
        self.loop.create_task(self.congratulate_on_birthdays())

    async def congratulate_on_birthdays(self):
        await self.wait_until_ready()
        channel = self.get_channel(711943678566072455)  # orga-spam
        while not self.is_closed():
            for member in api.get_birthday_members():
                await channel.send(f"Happy Birthday <@{api.get_discord_id(member)}>!")
            await asyncio.sleep(86400)  # 24 hours


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
client = MyClient(intents=intents)
client.run(DISCORD_TOKEN)
