import json
import os
import asyncio

import discord

with open('config.json', 'r') as f:
    config = json.load(f)


class MyClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_hook(self) -> None:
        # create the background task and run it in the background
        self.loop.create_task(self.my_background_task())

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def my_background_task(self):
        await self.wait_until_ready()
        channel = self.get_channel(711943678566072455)  # orga-spam
        while not self.is_closed():
            vorstand = self.get_guild(711943678322540545).get_role(711943678566072337).members  # abteilungsleitung
            for member in vorstand:
                await channel.send(f"Happy Birthday <@{member.id}>!")
            await asyncio.sleep(10)


intents = discord.Intents.default()
intents.members = True
client = MyClient(intents=intents)
client.run(config['token'])

