"""Team-management lifecycle and Discord permission rules."""

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import config
from cogs import teams
from cogs.teams import TeamCog


def _role(role_id, name, *, position=1):
    role = Mock(spec=discord.Role)
    role.id, role.name, role.position = role_id, name, position
    role.managed = False
    role.permissions = discord.Permissions.none()

    async def edit(**changes):
        role.name = changes.get("name", role.name)
        return role

    role.edit = AsyncMock(side_effect=edit)
    role.delete = AsyncMock()
    return role


def _channel(channel_id, name, category_id, channel_type, channels):
    channel = Mock(spec=channel_type)
    channel.id, channel.name, channel.category_id = channel_id, name, category_id
    channel.permissions_for.return_value = NS(manage_channels=True, manage_roles=True)
    channel.overwrites_for.side_effect = lambda _role: discord.PermissionOverwrite()
    channel.set_permissions = AsyncMock()
    channel.move = AsyncMock()

    async def edit(**changes):
        channel.name = changes.get("name", channel.name)
        return channel

    async def delete(**_changes):
        channels.remove(channel)

    channel.edit = AsyncMock(side_effect=edit)
    channel.delete = AsyncMock(side_effect=delete)
    return channel


def _environment(department="CS"):
    cfg = config.TEAM_DEPARTMENTS[department]
    roles, channels = [], []
    default_role = _role(config.GUILD_ID, "@everyone", position=0)
    lead = _role(cfg["lead_role_id"], f"{department} Leitung", position=10)
    member = _role(cfg["member_role_id"], department, position=5)
    bot_role = _role(999, "Bot", position=100)
    anchor = _role(cfg["role_anchor_id"], "Team anchor", position=50)
    roles.extend((default_role, lead, member, bot_role, anchor))
    category = Mock(spec=discord.CategoryChannel)
    category.id, category.name = cfg["category_id"], f"{department} Teams"
    category.permissions_for.return_value = NS(manage_channels=True)
    channels.append(category)

    guild = Mock(spec=discord.Guild)
    guild.id, guild.default_role = config.GUILD_ID, default_role
    guild.me = Mock(spec=discord.Member)
    guild.me.id = 999
    guild.me.roles = [default_role, bot_role]
    guild.me.top_role = bot_role
    guild.me.guild_permissions = NS(manage_roles=True, manage_channels=True)
    guild.fetch_roles = AsyncMock(side_effect=lambda: list(roles))
    shared = []
    for channel_id in cfg["shared_channel_ids"]:
        shared.append(_channel(channel_id, "Shared", 500, discord.TextChannel, shared))
    guild.fetch_channels = AsyncMock(side_effect=lambda: [*channels, *shared])

    next_id = iter(range(1000, 1100))

    async def create_role(**kwargs):
        for existing in roles:
            if existing.id != guild.id:
                existing.position += 1
        role = _role(next(next_id), kwargs["name"])
        roles.append(role)
        return role

    async def create_text_channel(name, **_kwargs):
        channel = _channel(next(next_id), name, category.id, discord.TextChannel, channels)
        channels.append(channel)
        return channel

    async def create_voice_channel(name, **_kwargs):
        channel = _channel(next(next_id), name, category.id, discord.VoiceChannel, channels)
        channels.append(channel)
        return channel

    guild.create_role = AsyncMock(side_effect=create_role)
    async def edit_role_positions(*, positions, reason):
        for role, position in positions.items():
            role.position = position

    guild.edit_role_positions = AsyncMock(side_effect=edit_role_positions)
    guild.create_text_channel = AsyncMock(side_effect=create_text_channel)
    guild.create_voice_channel = AsyncMock(side_effect=create_voice_channel)

    bot = NS()

    def interaction(*, authorized=True):
        user = Mock(spec=discord.Member)
        user.id = 42
        user.roles = [lead] if authorized else [member]
        result = Mock(spec=discord.Interaction)
        result.guild_id, result.guild, result.user = config.GUILD_ID, guild, user
        result.response = NS(defer=AsyncMock(), send_message=AsyncMock())
        result.followup = NS(send=AsyncMock())
        result.edit_original_response = AsyncMock()
        return result

    return NS(
        bot=bot, guild=guild, roles=roles, channels=channels, category=category,
        lead=lead, member=member, anchor=anchor, shared=shared, interaction=interaction,
    )


async def _confirm_delete(cog, env, team="Fire"):
    request = env.interaction()
    await cog.delete.callback(cog, request, team, None)
    view = request.followup.send.call_args.kwargs["view"]
    await view.confirm.callback(env.interaction())


def test_create_rename_and_confirmed_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(teams, "TEAMS_FILE", tmp_path / "teams.json")
    env = _environment()

    async def run():
        cog = TeamCog(env.bot)
        created = env.interaction()
        await cog.create.callback(cog, created, "Fire", None)

        created_role = env.roles[-1]
        assert env.guild.create_role.call_args.kwargs == {
            "name": "CS Team Fire",
            "colour": discord.Colour(0x8ABEFF),
            "permissions": discord.Permissions.none(),
            "mentionable": True,
            "reason": "/team create: 42, CS, Fire",
        }
        text_overwrites = env.guild.create_text_channel.call_args.kwargs["overwrites"]
        assert text_overwrites[env.guild.default_role].view_channel is False
        assert text_overwrites[env.lead].view_channel is True
        assert text_overwrites[created_role].send_messages is True
        assert env.member not in text_overwrites
        voice_overwrites = env.guild.create_voice_channel.call_args.kwargs["overwrites"]
        assert voice_overwrites[env.member].view_channel is True
        assert voice_overwrites[env.member].connect is False
        assert voice_overwrites[env.lead].connect is True
        assert voice_overwrites[created_role].connect is True

        renamed = env.interaction()
        await cog.rename.callback(cog, renamed, "Fire", "Smoking Raccoons", None)
        assert created_role.name == "CS Team Smoking Raccoons"
        assert [c.name for c in env.channels if isinstance(c, discord.TextChannel)] == ["smoking-raccoons"]
        assert [c.name for c in env.channels if isinstance(c, discord.VoiceChannel)] == ["Smoking Raccoons"]

        request = env.interaction()
        await cog.delete.callback(cog, request, "Smoking Raccoons", None)
        confirmation = request.followup.send.call_args.kwargs["view"]
        assert created_role.delete.await_count == 0
        await confirmation.confirm.callback(env.interaction())
        assert created_role.delete.await_count == 1
        assert not [c for c in env.channels if isinstance(c, (discord.TextChannel, discord.VoiceChannel))]
        assert json.loads(teams.TEAMS_FILE.read_text()) == {}

    asyncio.run(run())


@pytest.mark.parametrize(
    ("department", "old_name", "new_name", "old_role", "new_role", "old_texts", "new_texts", "separator_names"),
    [
        ("RL", "Main", "Academy", "RL Main Team", "RL Academy Team", ["main"], ["academy"], None),
        (
            "VAL",
            "Fire",
            "Academy",
            "VAL Team Fire",
            "VAL Academy Team",
            ["fire-termine", "fire-chat", "fire-vods", "fire-strats"],
            ["academy-termine", "academy-chat", "academy-vods", "academy-strats"],
            ("━━━━fire━━━━", "━━━━academy━━━━"),
        ),
    ],
)
def test_existing_team_is_adopted_by_exact_pattern(
    tmp_path,
    monkeypatch,
    department,
    old_name,
    new_name,
    old_role,
    new_role,
    old_texts,
    new_texts,
    separator_names,
):
    monkeypatch.setattr(teams, "TEAMS_FILE", tmp_path / "teams.json")
    env = _environment(department)
    role = _role(2000, old_role)
    text_channels = [
        _channel(2001 + index, name, env.category.id, discord.TextChannel, env.channels)
        for index, name in enumerate(old_texts)
    ]
    voice = _channel(2010, old_name, env.category.id, discord.VoiceChannel, env.channels)
    env.roles.append(role)
    env.channels.extend((*text_channels, voice))
    separator = None
    if separator_names:
        separator = _channel(2011, separator_names[0], env.category.id, discord.TextChannel, env.channels)
        env.channels.append(separator)

    async def run():
        cog = TeamCog(env.bot)
        await cog.rename.callback(cog, env.interaction(), old_name, new_name, None)
        assert role.name == new_role
        assert [channel.name for channel in text_channels] == new_texts
        assert voice.name == new_name
        if separator:
            assert separator.name == separator_names[1]
        await _confirm_delete(cog, env, new_name)
        role.delete.assert_awaited_once()
        assert env.channels == [env.category]

    asyncio.run(run())


def test_valorant_separator_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(teams, "TEAMS_FILE", tmp_path / "teams.json")
    env = _environment("VAL")

    async def run():
        cog = TeamCog(env.bot)
        await cog.create.callback(cog, env.interaction(), "Fire", None)
        assert [call.args[0] for call in env.guild.create_text_channel.await_args_list] == [
            "━━━━fire━━━━", "fire-termine", "fire-chat", "fire-vods", "fire-strats",
        ]
        separator = next(c for c in env.channels if c.name == "━━━━fire━━━━")
        first_text = next(c for c in env.channels if c.name == "fire-termine")
        assert separator.move.call_args.kwargs["before"] is first_text
        overwrites = env.guild.create_text_channel.call_args_list[0].kwargs["overwrites"]
        assert overwrites[env.roles[-1]].view_channel is True
        assert overwrites[env.roles[-1]].send_messages is False
        assert env.member not in overwrites

    asyncio.run(run())


@pytest.mark.parametrize("department", config.TEAM_DEPARTMENTS)
def test_new_role_is_placed_below_anchor_and_granted_shared_access(tmp_path, monkeypatch, department):
    monkeypatch.setattr(teams, "TEAMS_FILE", tmp_path / "teams.json")
    env = _environment(department)
    for shared in env.shared:
        shared.overwrites_for.side_effect = lambda _: discord.PermissionOverwrite(attach_files=False)
    anchor_position_before_creation = env.anchor.position

    async def run():
        cog = TeamCog(env.bot)
        await cog.create.callback(cog, env.interaction(), "Fire", None)
        role = env.roles[-1]
        assert env.anchor.position == anchor_position_before_creation + 1
        assert env.guild.edit_role_positions.call_args.kwargs["positions"] == {role: env.anchor.position - 1}
        for shared in env.shared:
            shared.set_permissions.assert_awaited_once()
            assert shared.set_permissions.call_args.args == (role,)
            overwrite = shared.set_permissions.call_args.kwargs["overwrite"]
            assert overwrite.view_channel is True
            assert overwrite.read_message_history is True
            assert overwrite.send_messages is True
            assert overwrite.attach_files is False
            shared.edit.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    "problem",
    [
        "unauthorized",
        "category_permissions",
        "missing_anchor",
        "high_anchor",
        "missing_shared",
        "shared_permissions",
    ],
)
def test_creation_rejects_invalid_context(tmp_path, monkeypatch, problem):
    monkeypatch.setattr(teams, "TEAMS_FILE", tmp_path / "teams.json")
    env = _environment()
    if problem == "category_permissions":
        env.category.permissions_for.return_value = NS(manage_channels=False)
    elif problem == "missing_anchor":
        env.roles.remove(env.anchor)
    elif problem == "high_anchor":
        env.anchor.position = env.guild.me.top_role.position + 1
    elif problem == "missing_shared":
        env.shared.pop()
    elif problem == "shared_permissions":
        env.shared[0].permissions_for.return_value.manage_roles = False

    async def run():
        cog = TeamCog(env.bot)
        interaction = env.interaction(authorized=problem != "unauthorized")
        await cog.create.callback(cog, interaction, "Fire", None)
        assert interaction.followup.send.call_args.args[0].startswith("❌")
        env.guild.create_role.assert_not_awaited()
        assert not teams.TEAMS_FILE.exists()

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["create", "rename", "delete"])
def test_partial_operations_remain_recoverable_after_restart(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(teams, "TEAMS_FILE", tmp_path / "teams.json")
    env = _environment("VAL")
    failure = discord.HTTPException(NS(status=500, reason="Server error"), "try again")

    async def run():
        cog = TeamCog(env.bot)
        if operation == "create":
            # Fail after the role and all text channels have been persisted.
            env.guild.create_voice_channel.side_effect = failure
        await cog.create.callback(cog, env.interaction(), "Fire", None)
        role = env.roles[-1]
        record = teams.load_teams()[str(role.id)]
        assert len(record["texts"]) == 4
        assert (record["voice"] is None) == (operation == "create")

        if operation == "rename":
            voice = next(c for c in env.channels if isinstance(c, discord.VoiceChannel))
            original_edit = voice.edit.side_effect
            voice.edit.side_effect = failure
            failed = env.interaction()
            await cog.rename.callback(cog, failed, "Fire", "Academy", None)
            assert failed.followup.send.call_args.args[0].startswith("❌")
            assert teams.load_teams()[str(role.id)]["name"] == "Fire"
            voice.edit.side_effect = original_edit
            cog = TeamCog(env.bot)
            await cog.rename.callback(cog, env.interaction(), "Fire", "Academy", None)
            assert teams.load_teams()[str(role.id)]["name"] == "Academy"
            assert voice.name == "Academy"
            assert role.name == "VAL Academy Team"

            # The previous name is free and must not overwrite the renamed team.
            await cog.create.callback(cog, env.interaction(), "Fire", None)
            assert {team["name"] for team in teams.load_teams().values()} == {"Fire", "Academy"}
            return

        if operation == "delete":
            text_channels = [c for c in env.channels if c.id in record["texts"]]
            original_delete = text_channels[1].delete.side_effect
            text_channels[1].delete.side_effect = failure
            await _confirm_delete(cog, env)
            assert text_channels[0] not in env.channels
            assert teams.load_teams()  # IDs remain available for the retry.
            role.delete.assert_not_awaited()
            text_channels[1].delete.side_effect = original_delete

        cog = TeamCog(env.bot)
        await _confirm_delete(cog, env)
        assert teams.load_teams() == {}
        role.delete.assert_awaited_once()
        assert env.channels == [env.category]

    asyncio.run(run())


@pytest.mark.parametrize("change", ["rename", "revoke_lead", "move_channel"])
def test_delete_confirmation_revalidates_targets_and_authorization(tmp_path, monkeypatch, change):
    monkeypatch.setattr(teams, "TEAMS_FILE", tmp_path / "teams.json")
    env = _environment()

    async def run():
        cog = TeamCog(env.bot)
        await cog.create.callback(cog, env.interaction(), "Fire", None)
        requested = env.interaction()
        await cog.delete.callback(cog, requested, "Fire", None)
        view = requested.followup.send.call_args.kwargs["view"]
        confirmation = env.interaction()
        if change == "rename":
            await cog.rename.callback(cog, env.interaction(), "Fire", "Academy", None)
        elif change == "revoke_lead":
            confirmation.user.roles = [env.member]
        else:
            env.channels[-1].category_id = 123

        await view.confirm.callback(confirmation)

        assert confirmation.followup.send.call_args.args[0].startswith("❌")
        env.roles[-1].delete.assert_not_awaited()
        assert len(env.channels) == 3
        assert teams.load_teams()

    asyncio.run(run())
