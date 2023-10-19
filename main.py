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
        self.loop.create_task(self.do_routines())

    async def do_routines(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await self.congratulate_on_birthdays()
            await self.update_member_roles()
            await asyncio.sleep(86400)  # 24 hours

    # TODO change discord tag to discord ID for new members
    # TODO set checkbox if member is not on server yet
    async def update_member_roles(self):
        server = self.get_guild(711943678322540545)
        mitglied = server.get_role(711943678561746970)
        joined_members, left_members = api.get_member_joins_and_leaves()

        members = [member for member in joined_members if server.get_member(int(member))]
        for discord_id in members:
            member = server.get_member(int(discord_id))
            await member.add_roles(mitglied)

        members = [member for member in left_members if server.get_member(int(member))]
        for discord_id in members:
            member = server.get_member(int(discord_id))
            await member.remove_roles(mitglied)

    async def congratulate_on_birthdays(self):
        channel = self.get_channel(711943678566072455)  # orga-spam
        for member in api.get_birthday_members():
            discord_id = api.get_discord_id_if_allowed(member)
            if discord_id != 0:
                await channel.send(f"Happy Birthday <@{discord_id}>!")


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
client = MyClient(intents=intents)
client.run(DISCORD_TOKEN)
